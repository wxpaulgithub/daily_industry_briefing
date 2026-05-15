"""
微信公众号 RSS 授权健康检查
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

from config import (
    REQUEST_TIMEOUT,
    RSS_AUTH_HEALTH_CHECK_INTERVAL_HOURS,
    USER_AGENT,
    WERSS_TOKEN_EXPIRY_AUTHORIZATION,
    WERSS_TOKEN_EXPIRY_URL,
    WERSS_TOKEN_WARNING_HOURS,
)
from services.notifier import (
    has_sent_once_for_marker,
    mark_sent_once_for_marker,
    send_alert,
    send_test_alert,
)
from services.skills.wechat_rss import WeChatRssSkill

logger = logging.getLogger(__name__)

_is_checking = False
_last_result: dict = {}


def _parse_expiry_timestamp(value: Any) -> float | None:
    """将常见格式的到期时间转换为时间戳"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        return ts / 1000 if ts > 10_000_000_000 else ts
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_expiry_timestamp(float(text))

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def _find_expiry_value(data: Any) -> Any:
    """从 we-mp-rss 返回 JSON 中递归寻找常见到期时间字段"""
    keys = {
        "expiry",
        "expire",
        "expires_at",
        "expiry_time",
        "expire_time",
        "expired_at",
        "token_expire_time",
        "token_expires_at",
    }
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in keys:
                return value
        for value in data.values():
            found = _find_expiry_value(value)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_expiry_value(item)
            if found is not None:
                return found
    return None


async def _fetch_werss_token_expiry(client: httpx.AsyncClient) -> dict:
    """读取 we-mp-rss 授权管理中的 token 预计到期时间"""
    if not WERSS_TOKEN_EXPIRY_URL:
        return {"status": "not_configured"}

    headers = {}
    if WERSS_TOKEN_EXPIRY_AUTHORIZATION:
        headers["Authorization"] = WERSS_TOKEN_EXPIRY_AUTHORIZATION

    try:
        resp = await client.get(WERSS_TOKEN_EXPIRY_URL, headers=headers, timeout=10.0)
        if resp.status_code != 200:
            return {"status": "http_error", "status_code": resp.status_code}
        data = resp.json()
    except Exception as e:
        return {"status": "error", "error": repr(e)}

    raw_expiry = _find_expiry_value(data)
    expiry_ts = _parse_expiry_timestamp(raw_expiry)
    if not expiry_ts:
        return {"status": "missing_expiry", "raw_expiry": raw_expiry}

    remaining_hours = (expiry_ts - time.time()) / 3600
    return {
        "status": "ok",
        "expiry_ts": expiry_ts,
        "expiry_time": datetime.fromtimestamp(expiry_ts).strftime("%Y-%m-%d %H:%M:%S"),
        "remaining_hours": round(remaining_hours, 2),
        "raw_expiry": raw_expiry,
    }


def _calc_base_interval_hours(remaining_hours: float | None) -> float:
    """按 token 剩余时间计算 RSS 异常确认检测间隔"""
    if remaining_hours is None:
        return RSS_AUTH_HEALTH_CHECK_INTERVAL_HOURS
    if remaining_hours > 24:
        return 12
    if remaining_hours > 6:
        return 6
    return 3


def _calc_next_interval_hours(remaining_hours: float | None) -> float:
    """兼顾动态检测频率和 12h/3h 预警触发点"""
    base_interval = _calc_base_interval_hours(remaining_hours)
    if remaining_hours is None or remaining_hours <= 0:
        return base_interval

    candidates = [base_interval]
    for threshold in sorted(WERSS_TOKEN_WARNING_HOURS, reverse=True):
        if remaining_hours > threshold:
            candidates.append(max(remaining_hours - threshold, 1 / 12))
    return max(min(candidates), 1 / 12)


async def _send_token_expiry_warnings(client: httpx.AsyncClient, token_status: dict) -> None:
    """按照 12h/3h 阈值发送 token 预计到期预警"""
    if token_status.get("status") != "ok":
        return

    remaining_hours = float(token_status.get("remaining_hours") or 0)
    if remaining_hours <= 0:
        return

    expiry_ts = int(float(token_status["expiry_ts"]))
    expiry_time = token_status.get("expiry_time") or ""
    for threshold in sorted(WERSS_TOKEN_WARNING_HOURS, reverse=True):
        if remaining_hours > threshold:
            continue
        alert_key = f"token_expiry_warning_{threshold:g}h"
        marker = f"{expiry_ts}:{threshold:g}"
        if has_sent_once_for_marker(alert_key, marker):
            continue
        sent = await send_alert(
            client,
            alert_key,
            f"we-mp-rss 授权预计将在 {remaining_hours:.1f} 小时后到期"
            f"（预计到期时间：{expiry_time}）。这是提前预警，真实失效仍以 RSS 异常检测为准。",
            force=True,
        )
        if sent:
            mark_sent_once_for_marker(alert_key, marker)


async def run_wechat_auth_health_check() -> dict:
    """独立执行公众号 RSS 授权健康检查，不生成每日简讯页面"""
    global _is_checking, _last_result
    if _is_checking:
        return {"status": "already_checking", "last_result": _last_result}

    _is_checking = True
    started_at = time.time()
    try:
        skill = WeChatRssSkill(fetch_covers=False)
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            token_status = await _fetch_werss_token_expiry(client)
            await _send_token_expiry_warnings(client, token_status)
            articles = await skill.fetch_all(client)

        remaining_hours = token_status.get("remaining_hours") if token_status.get("status") == "ok" else None
        next_interval_hours = _calc_next_interval_hours(remaining_hours)
        result = {
            "status": "ok",
            "article_count": len(articles),
            "token": token_status,
            "next_check_interval_hours": round(next_interval_hours, 2),
            "duration_seconds": round(time.time() - started_at, 2),
            "checked_at": int(time.time()),
        }
        _last_result = result
        logger.info(f"[公众号RSS健康检查] 完成: {result}")
        return result
    except Exception as e:
        result = {
            "status": "error",
            "error": repr(e),
            "next_check_interval_hours": RSS_AUTH_HEALTH_CHECK_INTERVAL_HOURS,
            "duration_seconds": round(time.time() - started_at, 2),
            "checked_at": int(time.time()),
        }
        _last_result = result
        logger.error(f"[公众号RSS健康检查] 失败: {e}", exc_info=True)
        return result
    finally:
        _is_checking = False


async def send_alert_test_message() -> dict:
    """发送一条告警测试通知"""
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:
        sent = await send_test_alert(client)
    return {"status": "sent" if sent else "not_sent", "sent": sent}


def get_wechat_auth_health_status() -> dict:
    """返回健康检查运行状态"""
    return {
        "checking": _is_checking,
        "last_result": _last_result,
    }
