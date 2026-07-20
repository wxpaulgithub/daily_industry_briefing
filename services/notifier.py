"""
RSS 授权失效告警通知工具

支持通道：
  - 企业微信机器人 Webhook
  - Server酱 (ServerChan / SCT)

设计原则：
  1. 复用调用方传入的 httpx.AsyncClient，不额外建连
  2. 内置冷却机制，同一类告警在 ALERT_COOLDOWN_HOURS 内不重复发送
  3. 告警状态持久化到 runtime/alert_status.json，重启后仍保持冷却
  4. 所有异常内部消化，绝不影响主采集流程
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from config import (
    ALERT_COOLDOWN_HOURS,
    ALERT_STATUS_FILE,
    SERVERCHAN_KEY,
    WECOM_MENTIONED_LIST,
    WECOM_MENTIONED_MOBILE_LIST,
    WECOM_WEBHOOK_URL,
)

logger = logging.getLogger(__name__)

# 告警关键词：RSS 内容中出现以下词视为授权失效信号
# 注意：避免使用“扫码”这类高频词（正常文章常出现“扫码入园/领券/点餐”等），
# 改用更具体的失效占位语以降低误报；真失效另由“本轮正常文章数≈0”兜底判定
# （见 wechat_rss.py 的 _check_auth_status）。
AUTH_FAIL_KEYWORDS = [
    "扫码登录",
    "扫码授权",
    "请扫码",
    "重新扫码",
    "登录过期",
    "登录已过期",
    "请重新登录",
    "重新登录",
    "重新授权",
    "验证码",
]


def _split_csv(value: str) -> list[str]:
    """解析逗号分隔的环境变量配置"""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


# ===== 冷却状态管理 =====

def _load_status() -> dict:
    """读取告警状态文件"""
    try:
        if ALERT_STATUS_FILE.exists():
            return json.loads(ALERT_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_status(data: dict) -> None:
    """写入告警状态文件"""
    try:
        ALERT_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALERT_STATUS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[Notifier] 写入告警状态文件失败: {e}")


def _is_in_cooldown(alert_type: str) -> bool:
    """检查指定类型的告警是否在冷却期内"""
    status = _load_status()
    last_ts = status.get(f"last_alert_{alert_type}", 0.0)
    cooldown_seconds = ALERT_COOLDOWN_HOURS * 3600
    return (time.time() - last_ts) < cooldown_seconds


def _mark_alerted(alert_type: str) -> None:
    """标记指定类型的告警已发送"""
    status = _load_status()
    status[f"last_alert_{alert_type}"] = time.time()
    _save_status(status)


def has_sent_once_for_marker(alert_key: str, marker: str) -> bool:
    """检查某个标记周期内的一次性提醒是否已发送"""
    status = _load_status()
    return status.get(f"once_marker_{alert_key}") == marker


def mark_sent_once_for_marker(alert_key: str, marker: str) -> None:
    """标记某个标记周期内的一次性提醒已发送"""
    status = _load_status()
    status[f"once_marker_{alert_key}"] = marker
    _save_status(status)


def should_alert_persistent_signal(
    signal_key: str,
    active: bool,
    threshold_hours: float,
) -> tuple[bool, float]:
    """判断持续性异常是否已经超过阈值"""
    status = _load_status()
    since_key = f"signal_since_{signal_key}"
    now_ts = time.time()
    if not active:
        if since_key in status:
            status.pop(since_key, None)
            _save_status(status)
        return False, 0.0

    since_ts = float(status.get(since_key) or now_ts)
    if since_key not in status:
        status[since_key] = since_ts
        _save_status(status)

    elapsed_hours = (now_ts - since_ts) / 3600
    return elapsed_hours >= threshold_hours, elapsed_hours


# ===== 发送通道 =====

async def _send_wecom(
    client: httpx.AsyncClient,
    title: str,
    content: str,
    action_hint: str = "",
) -> bool:
    """发送企业微信机器人消息"""
    if not WECOM_WEBHOOK_URL:
        return False

    mentioned_list = _split_csv(WECOM_MENTIONED_LIST)
    mentioned_mobile_list = _split_csv(WECOM_MENTIONED_MOBILE_LIST)
    text = f"{title}\n\n{content}"
    if action_hint:
        text += f"\n\n{action_hint}"
    payload = {
        "msgtype": "text",
        "text": {
            "content": text,
        },
    }
    if mentioned_list:
        payload["text"]["mentioned_list"] = mentioned_list
    if mentioned_mobile_list:
        payload["text"]["mentioned_mobile_list"] = mentioned_mobile_list

    try:
        resp = await client.post(WECOM_WEBHOOK_URL, json=payload, timeout=10.0)
        ok = False
        err_info = ""
        if resp.status_code == 200:
            try:
                data = resp.json()
                ok = data.get("errcode") == 0
                if not ok:
                    err_info = f" errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
            except Exception as e:
                err_info = f" 响应不是 JSON: {e}"
        else:
            err_info = f" status={resp.status_code}"
        if ok:
            logger.info("[Notifier] 企业微信告警发送成功")
        else:
            logger.warning(f"[Notifier] 企业微信告警响应异常:{err_info}")
        return ok
    except Exception as e:
        logger.warning(f"[Notifier] 企业微信发送失败: {e}")
        return False


async def _send_serverchan(client: httpx.AsyncClient, title: str, content: str) -> bool:
    """发送 Server酱 消息"""
    if not SERVERCHAN_KEY:
        return False
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    try:
        resp = await client.post(
            url,
            data={"title": title, "desp": content},
            timeout=10.0,
        )
        ok = resp.status_code == 200
        if ok:
            try:
                data = resp.json()
                ok = data.get("code") in (0, None)
            except Exception:
                pass
        if ok:
            logger.info("[Notifier] Server酱告警发送成功")
        else:
            logger.warning(f"[Notifier] Server酱告警响应异常: {resp.status_code}")
        return ok
    except Exception as e:
        logger.warning(f"[Notifier] Server酱发送失败: {e}")
        return False


# ===== 对外接口 =====

async def send_alert(
    client: httpx.AsyncClient,
    alert_type: str,
    message: str,
    *,
    force: bool = False,
    title: str = "微信公众号 RSS 授权告警",
    action_hint: str = "请前往 we-mp-rss 后台重新扫码授权",
) -> bool:
    """发送告警消息（自动冷却去重）"""
    if (not force) and _is_in_cooldown(alert_type):
        logger.debug(f"[Notifier] 告警 [{alert_type}] 在冷却期内，跳过")
        return False

    sent = False
    desp = message if not action_hint else f"{message}\n\n{action_hint}"

    if WECOM_WEBHOOK_URL:
        if await _send_wecom(client, title, message, action_hint):
            sent = True
    if SERVERCHAN_KEY:
        if await _send_serverchan(client, title, desp):
            sent = True

    if sent and not force:
        _mark_alerted(alert_type)
        logger.warning(f"[Notifier] 告警已发送 [{alert_type}]: {message}")
    elif sent:
        logger.warning(f"[Notifier] 告警已发送（绕过冷却）[{alert_type}]: {message}")
    elif not WECOM_WEBHOOK_URL and not SERVERCHAN_KEY:
        logger.warning(
            f"[Notifier] 检测到授权异常但未配置告警通道 [{alert_type}]: {message}"
        )
    return sent


async def send_test_alert(client: httpx.AsyncClient) -> bool:
    """发送测试通知，绕过冷却且不写入告警状态"""
    return await send_alert(
        client,
        "test",
        "这是一条测试通知，用于验证 RSS 授权告警通道是否可达。",
        force=True,
    )


def check_keyword_in_content(title: str, summary: str) -> str | None:
    """检查文章标题/摘要中是否包含授权失效关键词"""
    text = f"{title} {summary}".lower()
    for kw in AUTH_FAIL_KEYWORDS:
        if kw.lower() in text:
            return kw
    return None
