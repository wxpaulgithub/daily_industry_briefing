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
from pathlib import Path

import httpx

from config import (
    ALERT_COOLDOWN_HOURS,
    ALERT_STATUS_FILE,
    SERVERCHAN_KEY,
    WECOM_WEBHOOK_URL,
)

logger = logging.getLogger(__name__)

# 告警关键词：RSS 内容中出现以下词视为授权失效信号
AUTH_FAIL_KEYWORDS = ["扫码", "登录过期", "重新授权", "验证码", "请重新登录"]


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


# ===== 发送通道 =====

async def _send_wecom(client: httpx.AsyncClient, content: str) -> bool:
    """发送企业微信机器人消息（Markdown 格式）"""
    if not WECOM_WEBHOOK_URL:
        return False
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": (
                "### ⚠️ 微信公众号 RSS 授权告警\n"
                f"> {content}\n\n"
                f"> 请前往 we-mp-rss 后台重新扫码授权"
            ),
        },
    }
    try:
        resp = await client.post(WECOM_WEBHOOK_URL, json=payload, timeout=10.0)
        ok = resp.status_code == 200
        if ok:
            logger.info("[Notifier] 企业微信告警发送成功")
        else:
            logger.warning(f"[Notifier] 企业微信告警响应异常: {resp.status_code}")
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
) -> bool:
    """发送告警消息（自动冷却去重）

    参数:
        client: 复用的 httpx 异步客户端
        alert_type: 告警类型标识（如 "keyword"、"silence"），用于独立控制冷却
        message: 告警内容描述

    返回:
        是否成功发送了至少一条通知
    """
    if _is_in_cooldown(alert_type):
        logger.debug(f"[Notifier] 告警 [{alert_type}] 在冷却期内，跳过")
        return False

    sent = False
    title = "微信公众号 RSS 授权告警"

    # 尝试所有配置的通道
    if WECOM_WEBHOOK_URL:
        if await _send_wecom(client, message):
            sent = True
    if SERVERCHAN_KEY:
        if await _send_serverchan(client, title, message):
            sent = True

    if sent:
        _mark_alerted(alert_type)
        logger.warning(f"[Notifier] 告警已发送 [{alert_type}]: {message}")
    elif not WECOM_WEBHOOK_URL and not SERVERCHAN_KEY:
        # 未配置任何通道，仅做日志记录
        logger.warning(
            f"[Notifier] 检测到授权异常但未配置告警通道 [{alert_type}]: {message}"
        )
    return sent


def check_keyword_in_content(title: str, summary: str) -> str | None:
    """检查文章标题/摘要中是否包含授权失效关键词

    返回:
        命中的关键词，未命中返回 None
    """
    text = f"{title} {summary}".lower()
    for kw in AUTH_FAIL_KEYWORDS:
        if kw.lower() in text:
            return kw
    return None
