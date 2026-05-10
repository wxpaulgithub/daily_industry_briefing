"""
微信公众号 RSS 授权健康检查
"""
from __future__ import annotations

import logging
import time

import httpx

from config import REQUEST_TIMEOUT, USER_AGENT
from services.notifier import send_test_alert
from services.skills.wechat_rss import WeChatRssSkill

logger = logging.getLogger(__name__)

_is_checking = False
_last_result: dict = {}


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
            articles = await skill.fetch_all(client)

        result = {
            "status": "ok",
            "article_count": len(articles),
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
