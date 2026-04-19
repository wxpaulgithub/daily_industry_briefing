"""
Cookie 文件读取工具

支持文件格式:
  [zhihu]
  z_c0=...; d_c0=...; _xsrf=...

  [bilibili]
  SESSDATA=...; bili_jct=...
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from config import COOKIE_FILE

logger = logging.getLogger(__name__)

_CACHE_MTIME: float = -1.0
_CACHE_DATA: dict[str, str] = {}


def _normalize_cookie(cookie: str) -> str:
    """规范化 cookie 字符串，去掉多余空白与重复分号"""
    c = (cookie or "").strip()
    if not c:
        return ""
    c = re.sub(r"\s+", " ", c)
    c = re.sub(r"\s*;\s*", "; ", c)
    c = re.sub(r"(;\s*){2,}", "; ", c)
    return c.strip(" ;")


def _parse_cookie_text(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    current = ""
    chunks: dict[str, list[str]] = {}

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith(";"):
            continue

        if line.startswith("[") and line.endswith("]") and len(line) > 2:
            current = line[1:-1].strip().lower()
            if current:
                chunks.setdefault(current, [])
            continue

        if not current:
            # 无分节内容直接忽略，避免误解析
            continue

        chunks[current].append(line)

    for site, lines in chunks.items():
        cookie = _normalize_cookie(" ".join(lines))
        if cookie:
            data[site] = cookie
    return data


def load_cookie_map(force: bool = False, path: Path | None = None) -> dict[str, str]:
    """读取并缓存 cookie 文件"""
    global _CACHE_MTIME, _CACHE_DATA
    file_path = path or COOKIE_FILE

    try:
        stat = file_path.stat()
    except FileNotFoundError:
        if force:
            _CACHE_MTIME = -1.0
            _CACHE_DATA = {}
        return dict(_CACHE_DATA)

    mtime = stat.st_mtime
    if (not force) and _CACHE_MTIME == mtime:
        return dict(_CACHE_DATA)

    try:
        text = file_path.read_text(encoding="utf-8")
        _CACHE_DATA = _parse_cookie_text(text)
        _CACHE_MTIME = mtime
    except Exception as e:
        logger.warning(f"[CookieStore] 读取 cookie 文件失败: {e}")
    return dict(_CACHE_DATA)


def get_site_cookie(site: str, fallback: str = "") -> str:
    """获取指定站点 cookie，文件优先，环境变量回退"""
    key = (site or "").strip().lower()
    if not key:
        return _normalize_cookie(fallback)
    data = load_cookie_map()
    return _normalize_cookie(data.get(key, "") or fallback)

