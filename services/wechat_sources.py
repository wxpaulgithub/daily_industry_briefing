"""
公众号来源配置读取

用途：
1. 以配置文件驱动公众号白名单（替代硬编码）
2. 支持一套账号同时投放到多个范围（wechat/local）
3. 支持按范围覆盖默认搜索词
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import LOCAL_WECHAT_WHITELIST, WECHAT_SOURCES_FILE

logger = logging.getLogger(__name__)

VALID_SCOPES = {"wechat", "local"}


@dataclass(frozen=True)
class WeChatSource:
    name: str
    scopes: tuple[str, ...]
    aliases: tuple[str, ...]
    rss_url: str = ""


_CACHE_LOCK = threading.Lock()
_CACHE_MTIME: float | None = None
_CACHE_SOURCES: list[WeChatSource] = []
_CACHE_QUERIES: dict[str, list[dict[str, str]]] = {}


def _normalize_scope(scope: str) -> str:
    v = (scope or "").strip().lower()
    return v if v in VALID_SCOPES else "wechat"


def _normalize_queries(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        keyword = str(item.get("keyword", "")).strip()
        label = str(item.get("label", "")).strip() or keyword
        if not keyword:
            continue
        result.append({"keyword": keyword, "label": label})
    return result


def _load_from_file(path: Path) -> tuple[list[WeChatSource], dict[str, list[dict[str, str]]]]:
    if not path.exists():
        return [], {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[公众号配置] 读取失败，已回退默认配置: {e}")
        return [], {}

    if not isinstance(raw, dict):
        logger.warning("[公众号配置] 顶层结构必须是 JSON object，已回退默认配置")
        return [], {}

    sources: list[WeChatSource] = []
    for item in raw.get("sources", []) or []:
        if not isinstance(item, dict):
            continue
        enabled = bool(item.get("enabled", True))
        if not enabled:
            continue

        name = str(item.get("name", "")).strip()
        if not name:
            continue

        scopes_raw = item.get("scopes", ["wechat"])
        scopes_list = scopes_raw if isinstance(scopes_raw, list) else [scopes_raw]
        scopes = tuple(
            s for s in (_normalize_scope(str(x)) for x in scopes_list)
            if s in VALID_SCOPES
        )
        if not scopes:
            scopes = ("wechat",)

        aliases_raw = item.get("aliases", [])
        aliases = tuple(
            str(x).strip() for x in (aliases_raw if isinstance(aliases_raw, list) else [])
            if str(x).strip()
        )
        rss_url = str(item.get("rss_url", "")).strip()
        sources.append(WeChatSource(name=name, scopes=scopes, aliases=aliases, rss_url=rss_url))

    queries: dict[str, list[dict[str, str]]] = {}
    raw_queries = raw.get("queries", {})
    if isinstance(raw_queries, dict):
        for scope in VALID_SCOPES:
            queries[scope] = _normalize_queries(raw_queries.get(scope, []))

    return sources, queries


def _ensure_loaded() -> None:
    global _CACHE_MTIME, _CACHE_SOURCES, _CACHE_QUERIES
    path = WECHAT_SOURCES_FILE

    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except Exception:
        mtime = None

    with _CACHE_LOCK:
        if _CACHE_MTIME == mtime:
            return
        sources, queries = _load_from_file(path)
        _CACHE_SOURCES = sources
        _CACHE_QUERIES = queries
        _CACHE_MTIME = mtime
        logger.info(
            f"[公众号配置] 已加载: sources={len(sources)}, "
            f"wechat_queries={len(queries.get('wechat', []))}, "
            f"local_queries={len(queries.get('local', []))}"
        )


def get_scope_sources(scope: str) -> list[WeChatSource]:
    """获取指定 scope 的启用账号列表"""
    _ensure_loaded()
    target = _normalize_scope(scope)
    return [s for s in _CACHE_SOURCES if target in s.scopes]


def get_scope_rss_sources(scope: str) -> list[WeChatSource]:
    """获取指定 scope 下配置了 rss_url 的账号"""
    return [s for s in get_scope_sources(scope) if (s.rss_url or "").strip()]


def get_scope_queries(scope: str) -> list[dict[str, str]]:
    """获取指定 scope 的搜索词覆盖配置"""
    _ensure_loaded()
    target = _normalize_scope(scope)
    return list(_CACHE_QUERIES.get(target, []))


def has_scope_sources(scope: str) -> bool:
    return len(get_scope_sources(scope)) > 0


def source_match_scope(source_name: str, scope: str) -> bool:
    """
    判断来源是否命中指定 scope 的账号配置
    - 若该 scope 未配置账号：
      - local 回退到 LOCAL_WECHAT_WHITELIST（兼容旧行为）
      - wechat 返回 True（不过滤）
    """
    src = (source_name or "").strip().lower()
    if not src:
        return False

    target = _normalize_scope(scope)
    sources = get_scope_sources(target)
    if not sources:
        if target == "local":
            return any(k.lower() in src for k in LOCAL_WECHAT_WHITELIST)
        return True

    for s in sources:
        candidates = (s.name, *s.aliases)
        if any(c.lower() in src for c in candidates if c):
            return True
    return False
