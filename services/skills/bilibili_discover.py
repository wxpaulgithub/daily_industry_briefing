"""
B站发现 Skill
用于辅助发现行业案例与视频内容，不参与国内/本地主资讯池。
"""
import logging
import re
from datetime import datetime
from urllib.parse import quote_plus

import httpx

from config import BILIBILI_COOKIE
from services.cookie_store import get_site_cookie
from services.fetcher import Article, clean_title, normalize_summary
from services.skills.discover_base import DiscoverSearchSkillBase

logger = logging.getLogger(__name__)
_BILIBILI_FALLBACK_IMAGE = "/static/discover-bilibili.svg"


class BilibiliDiscoverSkill(DiscoverSearchSkillBase):
    """B站关键词发现（辅助信源）"""

    @property
    def name(self) -> str:
        return "B站发现"

    @property
    def search_queries(self) -> list[dict]:
        return [
            {"keyword": "智能仓储 立体库", "label": "仓储"},
            {"keyword": "WMS WCS AGV", "label": "系统"},
            {"keyword": "物流自动化 项目案例", "label": "案例"},
            {"keyword": "工厂自动化 改造", "label": "改造"},
        ]

    @staticmethod
    def _strip_keyword_tag(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text or "")

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        articles: list[Article] = []
        search_url = f"https://search.bilibili.com/all?keyword={quote_plus(keyword)}"
        bilibili_cookie = get_site_cookie("bilibili", fallback=BILIBILI_COOKIE)
        try:
            headers = {
                "Referer": "https://search.bilibili.com/",
                "Origin": "https://search.bilibili.com",
                "Accept": "application/json, text/plain, */*",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            }
            if bilibili_cookie:
                headers["Cookie"] = bilibili_cookie
            resp = await client.get(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={
                    "search_type": "video",
                    "keyword": keyword,
                    "page": "1",
                    "order": "pubdate",
                },
                headers=headers,
                timeout=12.0,
            )
            if resp.status_code != 200:
                logger.warning(f"[{self.name}] API 请求失败 [{keyword}] status={resp.status_code}")
                payload = {}
            else:
                payload = resp.json()
            if int(payload.get("code", 0) or 0) != 0:
                logger.warning(f"[{self.name}] API 返回异常 code={payload.get('code')} keyword=[{keyword}]")
                payload = {}
            items = ((payload or {}).get("data") or {}).get("result") or []
            for item in items:
                title = clean_title(self._strip_keyword_tag(str(item.get("title", "")).strip()))
                if not title:
                    continue
                bvid = str(item.get("bvid", "")).strip()
                arcurl = str(item.get("arcurl", "")).strip()
                url = arcurl or (f"https://www.bilibili.com/video/{bvid}" if bvid else "")
                if not url:
                    continue
                summary = normalize_summary(self._strip_keyword_tag(str(item.get("description", "")).strip()))
                source_name = str(item.get("author", "")).strip() or "哔哩哔哩"
                image_url = str(item.get("pic", "")).strip()
                if image_url.startswith("//"):
                    image_url = "https:" + image_url
                pubdate = float(item.get("pubdate", 0) or 0)
                published = datetime.fromtimestamp(pubdate).strftime("%Y-%m-%d") if pubdate > 0 else ""
                articles.append(
                    Article(
                        title=title,
                        url=url,
                        summary=summary,
                        source_name=f"B站/{source_name}",
                        image_url=image_url or _BILIBILI_FALLBACK_IMAGE,
                        published=published,
                        published_ts=pubdate,
                        skill_name=self.name,
                        region_scope=self.region_scope,
                    )
                )
                if len(articles) >= count:
                    break
        except Exception as e:
            logger.warning(f"[{self.name}] 搜索失败 [{keyword}]: {e}")

        # API 失败后的兜底1：直接抓 B站搜索页中的视频链接
        if not articles:
            try:
                web_resp = await client.get(
                    search_url,
                    headers={
                        "Referer": "https://search.bilibili.com/",
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36"
                        ),
                    },
                    timeout=12.0,
                )
                html = web_resp.text
                seen: set[str] = set()
                for m in re.finditer(r'https://www\.bilibili\.com/video/BV[0-9A-Za-z]+', html):
                    url = m.group(0)
                    if url in seen:
                        continue
                    seen.add(url)
                    bvid = ""
                    m_bv = re.search(r"/video/(BV[0-9A-Za-z]+)", url)
                    if m_bv:
                        bvid = m_bv.group(1)
                    articles.append(
                        Article(
                            # 标题需具备区分度，避免后续全局去重把同关键词结果合并成 1 条
                            title=f"B站视频：{keyword} [{bvid or url[-10:]}]",
                            url=url,
                            summary="来自B站搜索结果的相关视频。",
                            source_name="B站",
                            image_url=_BILIBILI_FALLBACK_IMAGE,
                            published="",
                            published_ts=0.0,
                            skill_name=self.name,
                            region_scope=self.region_scope,
                        )
                    )
                    if len(articles) >= count:
                        break
            except Exception:
                pass

        # 保底：至少返回该关键词的 B站搜索入口，避免发现页无结果
        if not articles:
            articles.append(
                self._search_entry_fallback(
                    keyword=keyword,
                    search_url=search_url,
                    source_name="B站",
                    fallback_image=_BILIBILI_FALLBACK_IMAGE,
                    summary="未解析到结构化条目，点击进入B站搜索结果页查看更多内容。",
                )
            )

        if len(articles) <= 1:
            bing_items = await self._fallback_via_bing(
                client=client,
                keyword=keyword,
                site_query="site:bilibili.com/video",
                domain_markers=("bilibili.com/video",),
                source_name="B站",
                fallback_image=_BILIBILI_FALLBACK_IMAGE,
                count=count,
            )
            articles = bing_items or articles
        return articles[:count]
