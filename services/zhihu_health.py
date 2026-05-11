"""
知乎 Cookie 健康检查
"""
from __future__ import annotations

import logging
import time

import httpx

from config import REQUEST_TIMEOUT, USER_AGENT
from services.skills.zhihu_discover import ZhihuDiscoverSkill

logger = logging.getLogger(__name__)

_is_checking = False
_last_result: dict = {}


async def run_zhihu_cookie_health_check() -> dict:
    """独立执行知乎 Cookie 健康检查，不抓取发现页正文"""
    global _is_checking, _last_result
    if _is_checking:
        return {"status": "already_checking", "last_result": _last_result}

    _is_checking = True
    started_at = time.time()
    try:
        skill = ZhihuDiscoverSkill()
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            state = await skill.check_cookie_health(client)

        result = {
            **state,
            "duration_seconds": round(time.time() - started_at, 2),
        }
        _last_result = result
        logger.info(f"[知乎 Cookie 健康检查] 完成: {result}")
        return result
    except Exception as e:
        result = {
            "status": "error",
            "error": repr(e),
            "duration_seconds": round(time.time() - started_at, 2),
            "checked_at": int(time.time()),
        }
        _last_result = result
        logger.error(f"[知乎 Cookie 健康检查] 失败: {e}", exc_info=True)
        return result
    finally:
        _is_checking = False


def get_zhihu_cookie_health_status() -> dict:
    """返回知乎 Cookie 健康检查运行状态"""
    return {
        "checking": _is_checking,
        "last_result": _last_result,
    }
