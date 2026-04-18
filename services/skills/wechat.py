"""
微信公众号搜索 Skill - 通过搜狗微信搜索获取公众号文章

搜狗微信搜索 (weixin.sogou.com) 可按关键词搜索公众号文章，
覆盖面广，是获取微信公众号内容最稳定的公开渠道。

注意：搜狗有频率限制，本 Skill 采用顺序请求 + 延时策略避免触发反爬。
"""

import asyncio
import datetime
import json
import logging
import random
import re
import time
from difflib import SequenceMatcher
from html import unescape
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse

import httpx

from services.fetcher import (
    Article,
    NewsSkill,
    clean_title,
    normalize_summary,
)

logger = logging.getLogger(__name__)

# 搜狗反爬页面特征词
_BLOCK_INDICATORS = ["antispider", "verify", "captcha", "验证", "频繁"]

# 搜狗域名前缀，用于补全相对链接
_SOGOU_BASE = "https://weixin.sogou.com"

# 反爬冷却秒数（跨实例共享）
_ANTISPIDER_COOLDOWN_SECONDS = 300
_GLOBAL_BLOCKED_UNTIL = 0.0


def _get_blocked_until(local_until: float = 0.0) -> float:
    """读取全局+实例两级冷却时间，返回更晚的时间点"""
    return max(float(local_until or 0.0), float(_GLOBAL_BLOCKED_UNTIL or 0.0))


def _enter_antispider_cooldown(reason: str, seconds: int = _ANTISPIDER_COOLDOWN_SECONDS) -> None:
    """进入反爬冷却窗口，减少连续请求导致的雪崩拦截"""
    global _GLOBAL_BLOCKED_UNTIL
    until = time.time() + max(int(seconds), 60)
    if until > _GLOBAL_BLOCKED_UNTIL:
        _GLOBAL_BLOCKED_UNTIL = until
    remain = max(int(_GLOBAL_BLOCKED_UNTIL - time.time()), 0)
    logger.warning(f"[微信公众号] 命中反爬，进入冷却 {remain}s，原因: {reason}")


def _is_antispider_page(text: str, url: str = "") -> bool:
    """判断响应是否为搜狗反爬页"""
    if "antispider" in (url or "").lower():
        return True
    html_lower = (text or "").lower()
    return any(kw in html_lower for kw in _BLOCK_INDICATORS)

def _decode_js_url(raw: str) -> str:
    """清理 JS/HTML 中提取到的 URL 片段

    注意：不要直接 html.unescape 全量解码 URL。
    否则 `&timestamp` 会被误识别为 `&times`，变成 `×tamp`，导致微信链接参数错误。
    """
    if not raw:
        return ""
    val = raw.strip().strip("\"'")
    # 仅做与 URL 相关的安全替换，避免把参数名误解码
    val = val.replace("&amp;", "&").replace("&#38;", "&")
    val = val.replace("&quot;", "\"").replace("&#34;", "\"")
    val = val.replace("\\/", "/")
    val = val.replace("\\x26", "&").replace("\\u0026", "&")
    # 兼容历史错误数据：曾把 &timestamp 解码成 ×tamp
    val = val.replace("×tamp", "&timestamp")
    return val


def _extract_real_wechat_url(text: str) -> str:
    """从搜狗跳转页 HTML/JS 中提取真实微信文章链接"""
    if not text:
        return ""

    # 1) 经典拼接写法：url += '...'
    parts = re.findall(r"url\s*\+=\s*'([^']*)'", text)
    if parts:
        candidate = _decode_js_url("".join(parts))
        if candidate.startswith("http"):
            return candidate

    # 2) 直接跳转写法：window.location.replace('...') / location.href='...'
    direct_patterns = [
        r"window\.location\.replace\(\s*'([^']+)'\s*\)",
        r'window\.location\.replace\(\s*"([^"]+)"\s*\)',
        r"window\.location\.href\s*=\s*'([^']+)'",
        r'window\.location\.href\s*=\s*"([^"]+)"',
        r"location\.href\s*=\s*'([^']+)'",
        r'location\.href\s*=\s*"([^"]+)"',
    ]
    for pat in direct_patterns:
        m = re.search(pat, text)
        if m:
            candidate = _decode_js_url(m.group(1))
            if candidate.startswith("http"):
                return candidate

    # 2.5) 变量赋值 + location 使用变量：var jump='...'; location.href=jump;
    var_map: dict[str, str] = {}
    for stmt in re.split(r";\s*", text):
        s = (stmt or "").strip()
        if not s:
            continue

        m_assign = re.match(r"(?:var\s+)?([A-Za-z_]\w*)\s*=\s*(['\"])(.*?)\2$", s, re.DOTALL)
        if m_assign:
            var_map[m_assign.group(1)] = _decode_js_url(m_assign.group(3))
            continue

        m_unescape = re.match(
            r"(?:var\s+)?([A-Za-z_]\w*)\s*=\s*unescape\(\s*(['\"])(.*?)\2\s*\)$",
            s,
            re.DOTALL,
        )
        if m_unescape:
            raw = m_unescape.group(3)
            try:
                var_map[m_unescape.group(1)] = _decode_js_url(unquote(raw))
            except Exception:
                var_map[m_unescape.group(1)] = _decode_js_url(raw)
            continue

        m_plus_lit = re.match(r"([A-Za-z_]\w*)\s*\+=\s*(['\"])(.*?)\2$", s, re.DOTALL)
        if m_plus_lit:
            name = m_plus_lit.group(1)
            var_map[name] = var_map.get(name, "") + _decode_js_url(m_plus_lit.group(3))
            continue

        m_plus_var = re.match(r"([A-Za-z_]\w*)\s*\+=\s*([A-Za-z_]\w*)$", s)
        if m_plus_var:
            name = m_plus_var.group(1)
            src = m_plus_var.group(2)
            var_map[name] = var_map.get(name, "") + var_map.get(src, "")
            continue

    var_jump_patterns = [
        r"window\.location\.replace\(\s*([A-Za-z_]\w*)\s*\)",
        r"window\.location\.href\s*=\s*([A-Za-z_]\w*)",
        r"location\.href\s*=\s*([A-Za-z_]\w*)",
    ]
    for pat in var_jump_patterns:
        m = re.search(pat, text)
        if not m:
            continue
        key = m.group(1)
        candidate = _decode_js_url(var_map.get(key, ""))
        if candidate.startswith("http"):
            return candidate

    # 3) 页面中直接包含 mp.weixin.qq.com/s?...（含转义形式）
    m = re.search(r"https?:\\/\\/mp\\.weixin\\.qq\\.com\\/s\?[^\"'<>\s]+", text)
    if m:
        return _decode_js_url(m.group(0))

    m = re.search(r"https?://mp\.weixin\.qq\.com/s\?[^\"'<>\s]+", text)
    if m:
        return _decode_js_url(m.group(0))

    return ""


def _extract_og_url(text: str) -> str:
    """从 HTML meta 中提取 og:url"""
    if not text:
        return ""
    patterns = [
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return _decode_js_url(m.group(1))
    return ""


def _extract_msg_link(text: str) -> str:
    """从页面脚本变量中提取 msg_link"""
    if not text:
        return ""
    patterns = [
        r'msg_link\s*=\s*"([^"]+)"',
        r"msg_link\s*=\s*'([^']+)'",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return _decode_js_url(m.group(1))
    return ""


def _extract_mp_links(text: str) -> list[str]:
    """从文本中提取所有 mp.weixin.qq.com/s 链接"""
    if not text:
        return []
    links: list[str] = []
    for m in re.findall(r"https?:\\/\\/mp\\.weixin\\.qq\\.com\\/s\\?[^\"'<>\s]+", text):
        links.append(_decode_js_url(m))
    for m in re.findall(r"https?://mp\.weixin\.qq\.com/s\?[^\"'<>\s]+", text):
        links.append(_decode_js_url(m))
    return links


def _extract_url_param_from_sogou(link_url: str) -> str:
    """从搜狗 /link?url=... 参数中尝试提取真实链接"""
    try:
        parsed = urlparse(link_url)
        q = parse_qs(parsed.query, keep_blank_values=True)
        raw = q.get("url", [])
        if not raw:
            return ""
        return _decode_js_url(unquote(raw[0]))
    except Exception:
        return ""


def _build_sogou_search_fallback_url(article: Article) -> str:
    """解析失败时的兜底跳转：回到搜狗文章搜索结果页"""
    q = f"{(article.source_name or '').strip()} {(article.title or '').strip()}".strip()
    if not q:
        q = (article.title or "").strip()
    return f"https://weixin.sogou.com/weixin?type=2&query={quote_plus(q)}&ie=utf8"


def _normalize_match_text(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[，。、“”‘’！？：；,.!?;:·\-_/（）()\[\]【】]", "", s)
    return s


def _title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _extract_account_profile_url(html: str) -> str:
    """从搜狗公众号搜索结果页提取公众号主页链接"""
    if not html:
        return ""
    patterns = [
        r'uigs="account_name_0"[^>]*href="([^"]+)"',
        r'<a[^>]*class="news-box"[^>]*href="([^"]+)"',
        r'href="(/gzh\?[^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if not m:
            continue
        url = _decode_js_url(m.group(1))
        if url.startswith("/"):
            url = _SOGOU_BASE + url
        if url.startswith("http"):
            return url
    return ""


def _extract_history_from_profile_html(html: str) -> list[dict]:
    """从公众号主页中提取历史文章（标题+链接+时间）"""
    if not html:
        return []

    msg_json = ""

    # 形态1: var msgList = '...';
    m = re.search(r"var\s+msgList\s*=\s*'(.+?)';", html, re.DOTALL)
    if m:
        raw = m.group(1)
        try:
            msg_json = raw.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
        except Exception:
            msg_json = raw
        msg_json = msg_json.replace("\r", "").replace("\n", "")
    else:
        # 形态2: var msgList = {...};
        m2 = re.search(r"var\s+msgList\s*=\s*(\{.*?\});", html, re.DOTALL)
        if m2:
            msg_json = m2.group(1)

    if not msg_json:
        return []

    try:
        data = json.loads(msg_json)
    except Exception:
        return []

    result: list[dict] = []
    for item in data.get("list", []) or []:
        ext = item.get("app_msg_ext_info", {}) or {}
        comm = item.get("comm_msg_info", {}) or {}

        def _push(entry: dict, ts: float) -> None:
            title = str(entry.get("title", "")).strip()
            url = _decode_js_url(str(entry.get("content_url", "")).strip())
            if not title or not url:
                return
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = "https://mp.weixin.qq.com" + url
            result.append({
                "title": title,
                "title_norm": _normalize_match_text(title),
                "url": url,
                "published_ts": float(ts or 0),
            })

        ts = float(comm.get("datetime", 0) or ext.get("datetime", 0) or 0)
        _push(ext, ts)
        for sub in ext.get("multi_app_msg_item_list", []) or []:
            _push(sub or {}, ts)
    return result


def _match_history_article(target: Article, history: list[dict]) -> str:
    """在公众号历史文章中匹配目标文章，返回最佳链接"""
    if not history:
        return ""
    target_norm = _normalize_match_text(target.title)
    best_url = ""
    best_score = 0.0

    for item in history:
        sim = _title_similarity(target_norm, item.get("title_norm", ""))
        score = sim
        hts = float(item.get("published_ts", 0) or 0)
        if target.published_ts > 0 and hts > 0:
            delta = abs(target.published_ts - hts)
            if delta <= 86400:
                score += 0.2
            elif delta <= 3 * 86400:
                score += 0.1
        if score > best_score:
            best_score = score
            best_url = item.get("url", "")

    if best_score >= 0.62:
        return best_url
    return ""


def _canonical_mp_url(raw_url: str) -> str:
    """将微信文章链接规范化为稳定参数形式：__biz/mid/idx/sn"""
    if not raw_url:
        return ""
    try:
        u = _decode_js_url(raw_url)
        parsed = urlparse(u)
        if "mp.weixin.qq.com" not in parsed.netloc:
            return ""
        if not parsed.path.startswith("/s"):
            return ""

        q = parse_qs(parsed.query, keep_blank_values=True)

        def _first(name: str) -> str:
            vals = q.get(name, [])
            return vals[0] if vals else ""

        core = {
            "__biz": _first("__biz"),
            "mid": _first("mid"),
            "idx": _first("idx"),
            "sn": _first("sn"),
        }
        # 放宽：只要有 __biz + mid 就认为可构成稳定主键，idx/sn 尽量带上
        if not (core["__biz"] and core["mid"]):
            return ""
        keep = {k: v for k, v in core.items() if v}
        return "https://mp.weixin.qq.com/s?" + urlencode(keep)
    except Exception:
        return ""


def _normalize_mp_url_loose(raw_url: str) -> str:
    """宽松规范化：保留可访问性，移除易失临时参数"""
    if not raw_url:
        return ""
    try:
        u = _decode_js_url(raw_url)
        parsed = urlparse(u)
        if "mp.weixin.qq.com" not in parsed.netloc or not parsed.path.startswith("/s"):
            return ""

        q = parse_qs(parsed.query, keep_blank_values=True)
        drop_keys = {
            "src", "scene", "timestamp", "ver", "signature", "new",
            "srcid", "token", "lang", "from", "clicktime", "enterid",
        }
        keep_pairs = []
        for k, vals in q.items():
            if k in drop_keys:
                continue
            if not vals:
                continue
            keep_pairs.append((k, vals[0]))

        # 绝不返回裸 /s，必须至少保留核心参数线索
        if not keep_pairs:
            return ""
        keys = {k for k, _ in keep_pairs}
        if "__biz" not in keys and "mid" not in keys and "sn" not in keys:
            return ""
        return "https://mp.weixin.qq.com/s?" + urlencode(keep_pairs)
    except Exception:
        return ""


def _best_mp_url(candidates: list[str]) -> tuple[str, str]:
    """从候选链接中选出可用的稳定微信文章链接，返回 (url, mode)"""
    # 先严格规范化
    for c in candidates:
        stable = _canonical_mp_url(c)
        if stable:
            return stable, "strict"
    # 再宽松规范化
    for c in candidates:
        loose = _normalize_mp_url_loose(c)
        if loose:
            return loose, "loose"
    return "", "none"


class WeChatSkill(NewsSkill):
    """微信公众号搜索数据源（通过搜狗微信搜索）"""

    @property
    def name(self) -> str:
        return "微信公众号"

    @property
    def search_queries(self) -> list[dict]:
        return [
            {"keyword": "智能仓储 立体仓库 堆垛机", "label": "智能仓储"},
            {"keyword": "智能制造 工业自动化", "label": "智能制造"},
            {"keyword": "AGV 物流机器人 仓储机器人", "label": "AGV物流"},
            {"keyword": "WMS WCS MES 数字化工厂", "label": "数字化"},
            {"keyword": "中鼎集成 昆船智能 北自科技 兰剑", "label": "厂商A"},
            {"keyword": "今天国际 井松智能 音飞储存 德马科技", "label": "厂商B"},
            {"keyword": "极智嘉 海柔创新 快仓 海康机器人", "label": "厂商C"},
            {"keyword": "工业自动化展 智能制造展 物流展", "label": "工业展览"},
        ]

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        """从搜狗微信搜索获取公众号文章"""
        articles = []
        try:
            blocked_until = _get_blocked_until(getattr(self, "_blocked_until", 0.0))
            if blocked_until > time.time():
                remain = max(int(blocked_until - time.time()), 0)
                logger.warning(f"[微信公众号] 处于反爬冷却期（剩余 {remain}s），跳过关键词 [{keyword}]")
                return []

            resp = await client.get(
                "https://weixin.sogou.com/weixin",
                params={
                    "type": "2",  # type=2 搜索文章（非公众号）
                    "query": keyword,
                    "ie": "utf8",
                    "s_from": "input",
                    "_sug_": "n",
                    "_sug_type": "",
                },
                headers={
                    "Referer": "https://weixin.sogou.com/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                follow_redirects=True,
            )

            html = resp.text

            # 检测反爬拦截页面
            if _is_antispider_page(html, str(resp.url)):
                # 命中反爬后进入冷却，避免连续请求把整轮都打成 antispider
                self._blocked_until = time.time() + _ANTISPIDER_COOLDOWN_SECONDS
                _enter_antispider_cooldown("关键词搜索结果页触发 antispider")
                logger.warning(f"[微信公众号] 搜狗触发反爬限制，跳过关键词 [{keyword}]")
                return []

            articles = self._parse_html(html)

            # 解析搜狗跳转链接，获取真实的微信文章 URL
            if articles:
                await self._resolve_redirects(client, articles)

        except Exception as e:
            logger.warning(f"[微信公众号] 搜索失败 [{keyword}]: {e}")

        return articles[:count]

    async def _resolve_redirects(self, client: httpx.AsyncClient, articles: list[Article]) -> None:
        """解析搜狗跳转链接，提取真实的微信文章 URL"""
        before_sogou = sum(1 for a in articles if a.url and "sogou.com/link" in a.url)
        stats = {"strict": 0, "loose": 0, "history": 0, "failed": 0}
        profile_cache: dict[str, str] = {}
        history_cache: dict[str, list[dict]] = {}

        async def _resolve_via_history(article: Article) -> str:
            """搜狗跳转失败后的兜底：根据公众号历史发布回溯匹配"""
            source = (article.source_name or "").strip()
            if not source:
                return ""

            profile_url = profile_cache.get(source)
            if profile_url is None:
                try:
                    resp = await client.get(
                        "https://weixin.sogou.com/weixin",
                        params={
                            "type": "1",  # 搜公众号
                            "query": source,
                            "ie": "utf8",
                            "_sug_": "n",
                            "_sug_type": "",
                        },
                        headers={"Referer": "https://weixin.sogou.com/"},
                    )
                    if _is_antispider_page(resp.text, str(resp.url)):
                        self._blocked_until = time.time() + _ANTISPIDER_COOLDOWN_SECONDS
                        _enter_antispider_cooldown(f"公众号主页检索触发 antispider [{source}]")
                        return ""
                    profile_url = _extract_account_profile_url(resp.text)
                except Exception:
                    profile_url = ""
                profile_cache[source] = profile_url

            if not profile_url:
                return ""

            history = history_cache.get(profile_url)
            if history is None:
                try:
                    profile_resp = await client.get(
                        profile_url,
                        headers={"Referer": "https://weixin.sogou.com/"},
                        follow_redirects=True,
                    )
                    if _is_antispider_page(profile_resp.text, str(profile_resp.url)):
                        self._blocked_until = time.time() + _ANTISPIDER_COOLDOWN_SECONDS
                        _enter_antispider_cooldown(f"公众号历史页触发 antispider [{source}]")
                        return ""
                    history = _extract_history_from_profile_html(profile_resp.text)
                except Exception:
                    history = []
                history_cache[profile_url] = history

            if not history:
                return ""
            return _match_history_article(article, history)

        async def _resolve_one(article: Article) -> None:
            if not article.url or "sogou.com/link" not in article.url:
                return
            try:
                candidates: list[str] = []
                # 直接从搜狗参数尝试提取
                param_url = _extract_url_param_from_sogou(article.url)
                if param_url:
                    candidates.append(param_url)

                # 第1阶段：纯本地拼接/规范化，不发网络请求
                best, mode = _best_mp_url(candidates)
                if best:
                    article.url = best
                    stats[mode] += 1
                    return

                # 先尝试不跟随重定向，直接读取 Location
                resp = await client.get(
                    article.url,
                    headers={"Referer": "https://weixin.sogou.com/"},
                    follow_redirects=False,
                )
                if _is_antispider_page(resp.text, str(resp.url)):
                    self._blocked_until = time.time() + _ANTISPIDER_COOLDOWN_SECONDS
                    _enter_antispider_cooldown("文章跳转页触发 antispider")
                    stats["failed"] += 1
                    return
                location = resp.headers.get("location") or resp.headers.get("Location")
                if location:
                    loc = _decode_js_url(location)
                    if loc.startswith("//"):
                        loc = "https:" + loc
                    candidates.append(loc)

                # 再从页面脚本中提取真实链接
                real_url = _extract_real_wechat_url(resp.text)
                if real_url:
                    candidates.append(real_url)

                # 从 og:url 提取
                og_url = _extract_og_url(resp.text)
                if og_url:
                    candidates.append(og_url)
                msg_link = _extract_msg_link(resp.text)
                if msg_link:
                    candidates.append(msg_link)
                candidates.extend(_extract_mp_links(resp.text))

                # 第2阶段：首轮响应即可解析成功则不再发额外请求
                best, mode = _best_mp_url(candidates)
                if best:
                    article.url = best
                    stats[mode] += 1
                    return

                # 按“最笨但最稳”的方式：跟随跳转拿最终 URL
                follow_resp = await client.get(
                    article.url,
                    headers={"Referer": "https://weixin.sogou.com/"},
                    follow_redirects=True,
                )
                if _is_antispider_page(follow_resp.text, str(follow_resp.url)):
                    self._blocked_until = time.time() + _ANTISPIDER_COOLDOWN_SECONDS
                    _enter_antispider_cooldown("跟随跳转触发 antispider")
                    stats["failed"] += 1
                    return
                final_url = str(follow_resp.url)
                if final_url:
                    candidates.append(final_url)
                follow_og = _extract_og_url(follow_resp.text)
                if follow_og:
                    candidates.append(follow_og)
                follow_msg_link = _extract_msg_link(follow_resp.text)
                if follow_msg_link:
                    candidates.append(follow_msg_link)
                candidates.extend(_extract_mp_links(follow_resp.text))

                best, mode = _best_mp_url(candidates)
                if best:
                    article.url = best
                    stats[mode] += 1
                    return

                # 兜底：若候选里有 mp 链接，尽量保留一个宽松规范化结果，避免全量丢失
                for c in candidates:
                    loose = _normalize_mp_url_loose(c)
                    if loose:
                        article.url = loose
                        stats["loose"] += 1
                        return

                # 再兜底：按公众号历史发布回溯匹配
                hist_url = await _resolve_via_history(article)
                if hist_url:
                    hist_best, hist_mode = _best_mp_url([hist_url])
                    article.url = hist_best or hist_url
                    if hist_mode in ("strict", "loose"):
                        stats[hist_mode] += 1
                    else:
                        stats["history"] += 1
                    return
                stats["failed"] += 1
            except Exception:
                stats["failed"] += 1
                return

        # 串行 + 轻微抖动，降低短时间内批量请求触发 antispider 的概率
        for a in articles:
            await _resolve_one(a)
            await asyncio.sleep(random.uniform(0.2, 0.6))

        # 兜底：仍然是 sogou 跳转链的文章会导致前端点击 403
        # 不直接丢弃，降级为“搜狗搜索结果页”链接，保证可点击可追溯
        before = len(articles)
        unresolved = [a for a in articles if a.url and "sogou.com/link" in a.url]
        for a in unresolved:
            a.url = _build_sogou_search_fallback_url(a)
        dropped = 0
        resolved = max(before_sogou - len(unresolved), 0)
        success_rate = (resolved / before_sogou * 100) if before_sogou else 100.0
        logger.info(
            f"[微信公众号] 链接解析成功率: {resolved}/{before_sogou} "
            f"({success_rate:.1f}%), 过滤未解出 {dropped} 条"
        )
        logger.info(
            f"[微信公众号] 解析明细: strict={stats['strict']}, "
            f"loose={stats['loose']}, history={stats['history']}, failed={stats['failed']}"
        )
        if unresolved:
            logger.warning(f"[微信公众号] 未解出 {len(unresolved)} 条，已降级为搜狗搜索结果页链接")

    async def fetch_all(self, client: httpx.AsyncClient) -> list[Article]:
        """顺序搜索所有关键词（搜狗有频率限制，不宜并发）"""
        all_articles: list[Article] = []

        for query in self.search_queries:
            blocked_until = _get_blocked_until(getattr(self, "_blocked_until", 0.0))
            if blocked_until > time.time():
                remain = max(int(blocked_until - time.time()), 0)
                logger.warning(f"[微信公众号] 已命中反爬，提前结束本轮公众号检索，等待冷却（剩余 {remain}s）")
                break
            try:
                batch = await self.fetch(client, query["keyword"], count=10)
                all_articles.extend(batch)
                logger.debug(f"[微信公众号] 关键词 [{query['label']}] 获取 {len(batch)} 条")
                # 每组关键词间隔拉长并加入抖动，降低触发反爬概率
                await asyncio.sleep(random.uniform(4.5, 7.0))
            except Exception as e:
                logger.warning(f"[微信公众号] 关键词 [{query['keyword']}] 失败: {e}")
                continue

        logger.info(f"[{self.name}] 采集完成，获取 {len(all_articles)} 条原始资讯")
        for a in all_articles:
            a.skill_name = self.name
            a.region_scope = self.region_scope
        return all_articles

    # ===== HTML 解析 =====

    def _parse_html(self, html: str) -> list[Article]:
        """解析搜狗微信搜索结果页 HTML

        搜狗结果结构:
          <ul class="news-list">
            <li id="sogou_vr_...">
              <div class="img-box"><a><img src="..."></a></div>
              <div class="txt-box">
                <h3><a href="/link?url=...">标题</a></h3>
                <p class="txt-info">摘要</p>
                <div class="s-p">
                  <span class="all-time-y2">公众号名称</span>
                  <span class="s2"><script>document.write(timeConvert('ts'))</script></span>
                </div>
              </div>
            </li>
          </ul>
        """
        articles = []

        # 精确匹配 news-list 内的 <li>（以 sogou_vr 开头的 id），避免误匹配 <link> 等
        blocks = re.findall(
            r'<li\s+id="sogou_vr[^"]*"[^>]*>(.*?)</li>',
            html,
            re.DOTALL,
        )
        if not blocks:
            return articles

        for block in blocks:
            try:
                article = self._parse_block(block)
                if article:
                    articles.append(article)
            except Exception:
                continue

        return articles

    @staticmethod
    def _clean_url(raw_url: str) -> str:
        """清理 URL：解码 HTML 实体 + 补全相对路径"""
        # 不使用 html.unescape 全量解码，避免 &timestamp 被误判为 &times
        url = _decode_js_url(raw_url.strip())
        if url.startswith("/"):
            url = _SOGOU_BASE + url
        return url

    def _parse_block(self, block: str) -> Optional[Article]:
        """解析单条文章 HTML 块"""
        # --- 标题和链接 ---
        title_match = re.search(
            r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        if not title_match:
            return None

        url = self._clean_url(title_match.group(1))
        inline_mp_candidates = _extract_mp_links(block)
        if inline_mp_candidates:
            best_inline, _ = _best_mp_url(inline_mp_candidates)
            if best_inline:
                url = best_inline
        # 清理标题中的高亮标签 <!--red_beg-->...<!--red_end--> 和 <em>
        title_html = title_match.group(2)
        title_html = re.sub(r'<!--(?:red_beg|red_end)-->', '', title_html)
        title = clean_title(re.sub(r'<[^>]+>', '', title_html).strip())
        if not title or not url:
            return None

        # --- 摘要 ---
        summary = ""
        summary_match = re.search(
            r'<p[^>]*class="txt-info"[^>]*>(.*?)</p>',
            block, re.DOTALL,
        )
        if summary_match:
            raw = summary_match.group(1)
            raw = re.sub(r'<!--(?:red_beg|red_end)-->', '', raw)
            raw = re.sub(r'<[^>]+>', '', raw).strip()
            summary = normalize_summary(raw)

        # --- 来源（公众号名称）---
        # 搜狗结构: <span class="all-time-y2">公众号名称</span>
        source_name = ""
        src_match = re.search(
            r'<span[^>]*class="all-time-y2"[^>]*>(.*?)</span>',
            block, re.DOTALL,
        )
        if src_match:
            source_name = re.sub(r'<[^>]+>', '', src_match.group(1)).strip()

        # --- 发布时间 ---
        # 搜狗用 <script>document.write(timeConvert('unix_timestamp'))</script>
        published = ""
        published_ts = 0.0
        ts_match = re.search(
            r"timeConvert\('(\d+)'\)",
            block,
        )
        if ts_match:
            try:
                published_ts = float(ts_match.group(1))
                # 转为可读日期
                dt = datetime.datetime.fromtimestamp(published_ts)
                published = dt.strftime("%Y-%m-%d")
            except (ValueError, OSError):
                published_ts = 0.0

        # --- 缩略图 ---
        image_url = ""
        img_match = re.search(
            r'<div[^>]*class="img-box"[^>]*>.*?<img[^>]*src="([^"]+)"',
            block,
            re.DOTALL,
        )
        if img_match:
            raw_src = img_match.group(1).strip()
            # 解码 HTML 实体（&amp; → &），补全协议
            image_url = unescape(raw_src)
            if image_url.startswith("//"):
                image_url = "https:" + image_url

        return Article(
            title=title,
            url=url,
            summary=summary,
            source_name=source_name,
            image_url=image_url,
            published=published,
            published_ts=published_ts,
        )
    @property
    def region_scope(self) -> str:
        return "national"
