"""
知乎发现 Skill
用于辅助发现行业讨论与案例，不参与国内/本地主资讯池。
"""
import logging
import json
import re
import time
from urllib.parse import quote_plus

import httpx

from config import (
    ZHIHU_COOKIE,
    ZHIHU_COOKIE_ALERT_THRESHOLD_MINUTES,
    ZHIHU_COOKIE_CHECK_INTERVAL_MINUTES,
)
from services.cookie_store import get_site_cookie
from services.fetcher import Article, clean_title, normalize_summary
from services.notifier import send_alert, should_alert_persistent_signal
from services.skills.discover_base import DiscoverSearchSkillBase

logger = logging.getLogger(__name__)
_ZHIHU_FALLBACK_IMAGE = "/static/discover-zhihu.svg"


class ZhihuDiscoverSkill(DiscoverSearchSkillBase):
    """知乎关键词发现（辅助信源）"""
    def __init__(self) -> None:
        self._cookie = ""
        self._cookie_checked_at = 0.0
        self._cookie_valid: bool | None = None
        self._cookie_last_status_code: int | None = None
        self._cookie_last_error = ""

    @property
    def name(self) -> str:
        return "知乎发现"

    @property
    def search_queries(self) -> list[dict]:
        return [
            {"keyword": "智能仓储 立体库 堆垛机", "label": "仓储"},
            {"keyword": "WMS WCS AGV AMR", "label": "系统"},
            {"keyword": "物流自动化 工厂改造", "label": "改造"},
            {"keyword": "仓储项目 招标 中标", "label": "项目"},
        ]

    async def _ensure_cookie_state(self, client: httpx.AsyncClient) -> None:
        """检测 cookie 登录态是否可用（带缓存与告警）"""
        current_cookie = get_site_cookie("zhihu", fallback=ZHIHU_COOKIE)
        if current_cookie != self._cookie:
            self._cookie = current_cookie
            self._cookie_checked_at = 0.0
            self._cookie_valid = None
            self._cookie_last_status_code = None
            self._cookie_last_error = ""

        threshold_hours = max(ZHIHU_COOKIE_ALERT_THRESHOLD_MINUTES / 60, 1 / 60)
        if not self._cookie:
            self._cookie_valid = False
            self._cookie_last_status_code = None
            self._cookie_last_error = ""
            should_alert_persistent_signal("zhihu_cookie_invalid", False, threshold_hours)
            return

        now = time.time()
        if now - self._cookie_checked_at < max(ZHIHU_COOKIE_CHECK_INTERVAL_MINUTES * 60, 60):
            return
        self._cookie_checked_at = now
        self._cookie_last_error = ""
        try:
            resp = await client.get(
                "https://www.zhihu.com/api/v4/me",
                headers={
                    "Cookie": self._cookie,
                    "Referer": "https://www.zhihu.com/",
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=10.0,
            )
            self._cookie_last_status_code = resp.status_code
            self._cookie_valid = resp.status_code == 200
            if self._cookie_valid:
                should_alert_persistent_signal("zhihu_cookie_invalid", False, threshold_hours)
                logger.info(f"[{self.name}] 检测到知乎登录态可用（cookie）")
                return

            logger.warning(f"[{self.name}] 知乎登录态不可用，状态码 {resp.status_code}")
            should_alert, elapsed_hours = should_alert_persistent_signal(
                "zhihu_cookie_invalid",
                True,
                threshold_hours,
            )
            if should_alert:
                elapsed_minutes = elapsed_hours * 60
                await send_alert(
                    client,
                    "zhihu_cookie_invalid",
                    (
                        f"知乎登录态 Cookie 已连续 {elapsed_minutes:.0f} 分钟不可用"
                        f"（最近一次状态码 {resp.status_code}），发现页已降级到 HTML/Bing 兜底，"
                        f"知乎来源的结果质量和覆盖可能下降。"
                    ),
                    title="知乎 Cookie 失效告警",
                    action_hint="请更新 runtime/cookie.txt 中 [zhihu] 段的 Cookie，或执行 python scripts/update_cookie.py --site zhihu",
                )
        except Exception as e:
            self._cookie_valid = False
            self._cookie_last_status_code = None
            self._cookie_last_error = repr(e)
            logger.warning(f"[{self.name}] 知乎登录态检测失败: {e}")

    async def check_cookie_health(self, client: httpx.AsyncClient) -> dict:
        """返回当前知乎 Cookie 健康状态，供独立健康检查复用"""
        await self._ensure_cookie_state(client)
        status = "not_configured"
        if self._cookie:
            if self._cookie_last_error:
                status = "check_failed"
            elif self._cookie_valid:
                status = "valid"
            else:
                status = "invalid"
        return {
            "status": status,
            "has_cookie": bool(self._cookie),
            "valid": self._cookie_valid,
            "last_status_code": self._cookie_last_status_code,
            "last_error": self._cookie_last_error,
            "checked_at": int(time.time()),
        }

    @staticmethod
    def _normalize_target_url(target: dict) -> str:
        """将知乎搜索对象 URL 规范化为可直接打开的网页链接"""
        url = str(target.get("url", "")).strip()
        t = str(target.get("type", "")).strip().lower()
        tid = str(target.get("id", "")).strip()
        qid = str(((target.get("question") or {}).get("id", ""))).strip()
        aid = str(((target.get("answer") or {}).get("id", ""))).strip()

        normalized = ZhihuDiscoverSkill._normalize_zhihu_web_url(
            url=url,
            target_type=t,
            target_id=tid,
            question_id=qid,
            answer_id=aid,
        )
        if normalized:
            return normalized

        return ""

    @staticmethod
    def _normalize_zhihu_web_url(
        url: str,
        target_type: str = "",
        target_id: str = "",
        question_id: str = "",
        answer_id: str = "",
    ) -> str:
        """强制将知乎 URL 归一成网页可访问地址（避免输出 api/v4 JSON 链接）"""
        u = (url or "").strip()
        t = (target_type or "").strip().lower()
        tid = (target_id or "").strip()
        qid = (question_id or "").strip()
        aid = (answer_id or "").strip()

        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("/"):
            u = "https://www.zhihu.com" + u

        # 常见 API URL -> Web URL
        m_q = re.search(r"/api/v4/questions/(\d+)", u)
        if m_q:
            return f"https://www.zhihu.com/question/{m_q.group(1)}"

        m_a = re.search(r"/api/v4/articles/(\d+)", u)
        if m_a:
            return f"https://zhuanlan.zhihu.com/p/{m_a.group(1)}"

        # answer API：优先拼 question/answer，否则退化为 answer 页面
        m_ans = re.search(r"/api/v4/answers/(\d+)", u)
        if m_ans:
            ans_id = m_ans.group(1)
            if qid.isdigit():
                return f"https://www.zhihu.com/question/{qid}/answer/{ans_id}"
            return f"https://www.zhihu.com/answer/{ans_id}"

        if (
            "zhihu.com/question/" in u
            or "zhuanlan.zhihu.com/p/" in u
            or "zhihu.com/p/" in u
            or "zhihu.com/answer/" in u
        ):
            return u

        # 基于对象类型和 id 补足网页 URL
        if t == "answer":
            if qid.isdigit() and tid.isdigit():
                return f"https://www.zhihu.com/question/{qid}/answer/{tid}"
            if qid.isdigit():
                return f"https://www.zhihu.com/question/{qid}"
            if tid.isdigit():
                return f"https://www.zhihu.com/answer/{tid}"
        if t in {"article", "zhuanlan"} and tid.isdigit():
            return f"https://zhuanlan.zhihu.com/p/{tid}"
        if t == "question" and tid.isdigit():
            return f"https://www.zhihu.com/question/{tid}"

        # 最后兜底：纯数字 id 按 question 处理
        if tid.isdigit():
            return f"https://www.zhihu.com/question/{tid}"
        return ""

    def _build_headers(self) -> dict:
        headers = {
            "Referer": "https://www.zhihu.com/",
            "Accept": "application/json, text/plain, */*",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        articles: list[Article] = []
        search_url = f"https://www.zhihu.com/search?type=content&q={quote_plus(keyword)}"
        await self._ensure_cookie_state(client)

        # 优先尝试知乎搜索 API
        try:
            api_resp = await client.get(
                "https://www.zhihu.com/api/v4/search_v3",
                params={
                    "t": "general",
                    "q": keyword,
                    "correction": "1",
                    "offset": "0",
                    "limit": str(count),
                },
                headers=self._build_headers(),
                timeout=12.0,
            )
            if api_resp.status_code == 200:
                data = api_resp.json()
                for row in data.get("data", []) or []:
                    target = (row or {}).get("object") or {}
                    title = clean_title(str(target.get("title", "")).strip())
                    if not title:
                        continue
                    url = self._normalize_target_url(target)
                    if not url:
                        continue
                    excerpt = normalize_summary(str(target.get("excerpt", "")).strip())
                    image_url = (
                        str(target.get("thumbnail", "")).strip()
                        or str(target.get("image_url", "")).strip()
                        or str(target.get("cover_url", "")).strip()
                    )
                    author = ""
                    author_obj = target.get("author") or {}
                    if isinstance(author_obj, dict):
                        author = str(author_obj.get("name", "")).strip()
                        if not image_url:
                            image_url = str(author_obj.get("avatar_url", "")).strip()
                    articles.append(
                        Article(
                            title=title,
                            url=url,
                            summary=excerpt,
                            source_name=f"知乎/{author}".strip("/"),
                            image_url=image_url or _ZHIHU_FALLBACK_IMAGE,
                            published="",
                            published_ts=0.0,
                            skill_name=self.name,
                            region_scope=self.region_scope,
                        )
                    )
        except Exception as e:
            logger.debug(f"[{self.name}] API 解析失败，尝试 HTML 兜底: {e}")

        if articles:
            return articles[:count]

        # HTML 兜底 1：尝试从页面状态数据中提取结构化结果
        try:
            html_resp = await client.get(
                search_url,
                headers=self._build_headers(),
                timeout=12.0,
            )
            html = html_resp.text
            articles.extend(self._parse_html_state_results(html, count))

            # HTML 兜底 2：抽取 question 链接（弱结构化）
            if not articles:
                links = re.findall(r'https://www\.zhihu\.com/question/\d+(?:/answer/\d+)?', html)
                # 兼容知乎页面中 JSON 转义链接: https:\/\/www.zhihu.com\/question\/...
                escaped_links = re.findall(
                    r"https:\\/\\/www\\.zhihu\\.com\\/(?:question\\/\\d+(?:\\/answer\\/\\d+)?|zhuanlan\\/p\\/\\d+)",
                    html,
                )
                for e in escaped_links:
                    links.append(e.replace("\\/", "/"))
                seen: set[str] = set()
                for link in links:
                    if link in seen:
                        continue
                    seen.add(link)
                    articles.append(
                        Article(
                            title=f"知乎相关讨论：{keyword}",
                            url=link,
                            summary="来自知乎搜索结果，点击查看详情。",
                            source_name="知乎",
                            image_url=_ZHIHU_FALLBACK_IMAGE,
                            published="",
                            published_ts=0.0,
                            skill_name=self.name,
                            region_scope=self.region_scope,
                        )
                    )
                    if len(articles) >= count:
                        break
        except Exception as e:
            logger.warning(f"[{self.name}] 搜索失败 [{keyword}]: {e}")

        # 保底：至少返回该关键词的知乎搜索入口，避免发现页无结果
        if not articles:
            articles.append(
                self._search_entry_fallback(
                    keyword=keyword,
                    search_url=search_url,
                    source_name="知乎",
                    fallback_image=_ZHIHU_FALLBACK_IMAGE,
                    summary="未解析到结构化条目，点击进入知乎搜索结果页查看更多内容。",
                )
            )

        if len(articles) <= 1:
            bing_items = await self._fallback_via_bing(
                client=client,
                keyword=keyword,
                site_query="site:zhihu.com/question OR site:zhuanlan.zhihu.com/p OR site:zhihu.com/p",
                domain_markers=("zhihu.com/question", "zhuanlan.zhihu.com/p/", "zhihu.com/p/"),
                source_name="知乎",
                fallback_image=_ZHIHU_FALLBACK_IMAGE,
                count=count,
            )
            if bing_items:
                normalized_items: list[Article] = []
                for a in bing_items:
                    web_url = self._normalize_zhihu_web_url(a.url)
                    if not web_url:
                        continue
                    a.url = web_url
                    normalized_items.append(a)
                articles = normalized_items or articles

        return articles[:count]

    @staticmethod
    def _walk_dicts(obj):
        if isinstance(obj, dict):
            yield obj
            for v in obj.values():
                yield from ZhihuDiscoverSkill._walk_dicts(v)
        elif isinstance(obj, list):
            for it in obj:
                yield from ZhihuDiscoverSkill._walk_dicts(it)

    def _parse_html_state_results(self, html: str, count: int) -> list[Article]:
        """从知乎搜索页内嵌状态数据解析结构化结果（匿名场景常见兜底）"""
        if not html:
            return []

        payloads: list[str] = []
        m1 = re.search(
            r'<script[^>]*id="js-initialData"[^>]*>\s*(\{.*?\})\s*</script>',
            html,
            re.DOTALL,
        )
        if m1:
            payloads.append(m1.group(1))
        m2 = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>\s*(\{.*?\})\s*</script>',
            html,
            re.DOTALL,
        )
        if m2:
            payloads.append(m2.group(1))

        out: list[Article] = []
        seen: set[str] = set()

        for raw in payloads:
            try:
                data = json.loads(raw)
            except Exception:
                continue

            for node in self._walk_dicts(data):
                title = clean_title(str(node.get("title", "")).strip())
                if not title:
                    continue
                url = str(node.get("url", "")).strip()
                if not url:
                    qid = str(node.get("id", "")).strip()
                    if qid.isdigit():
                        url = f"https://www.zhihu.com/question/{qid}"
                if not url:
                    continue
                if url.startswith("/"):
                    url = "https://www.zhihu.com" + url
                url = self._normalize_zhihu_web_url(url)
                if not url:
                    continue
                if "zhihu.com" not in url or url in seen:
                    continue
                seen.add(url)

                excerpt = normalize_summary(str(node.get("excerpt", "")).strip())
                image_url = (
                    str(node.get("thumbnail", "")).strip()
                    or str(node.get("image_url", "")).strip()
                    or str(node.get("cover_url", "")).strip()
                    or _ZHIHU_FALLBACK_IMAGE
                )
                out.append(
                    Article(
                        title=title,
                        url=url,
                        summary=excerpt,
                        source_name="知乎",
                        image_url=image_url,
                        published="",
                        published_ts=0.0,
                        skill_name=self.name,
                        region_scope=self.region_scope,
                    )
                )
                if len(out) >= count:
                    return out

        return out
