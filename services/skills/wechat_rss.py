"""
微信公众号 RSS 采集 Skill（轻量版）

借鉴 we-mp-rss 思路：
1. 上游先把指定公众号转为 RSS
2. 本系统只消费 RSS 并做筛选聚合

特点：
- 配置驱动（config_data/wechat_sources.json）
- 轻量实现，不引入 we-mp-rss 的重功能
"""

from __future__ import annotations

import calendar
import logging
import re
from html import unescape

import feedparser
import httpx

from services.fetcher import Article, NewsSkill, clean_title, normalize_summary
from services.wechat_sources import get_scope_rss_sources

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def _extract_published(entry: dict) -> tuple[float, str]:
    try:
        if getattr(entry, "published_parsed", None):
            ts = float(calendar.timegm(entry.published_parsed))
            return ts, str(entry.get("published", ""))[:19]
        if getattr(entry, "updated_parsed", None):
            ts = float(calendar.timegm(entry.updated_parsed))
            return ts, str(entry.get("updated", ""))[:19]
    except Exception:
        pass
    return 0.0, ""


def _extract_image(entry: dict, summary_html: str) -> str:
    try:
        media = entry.get("media_content", []) or []
        if media and isinstance(media, list) and isinstance(media[0], dict):
            url = str(media[0].get("url", "")).strip()
            if url:
                return url
        links = entry.get("links", []) or []
        for item in links:
            if not isinstance(item, dict):
                continue
            if item.get("rel") == "enclosure" and str(item.get("type", "")).startswith("image/"):
                url = str(item.get("href", "")).strip()
                if url:
                    return url
        m = re.search(r'<img[^>]+src="([^"]+)"', summary_html or "", re.IGNORECASE)
        if m:
            return m.group(1).strip()
    except Exception:
        return ""
    return ""


class WeChatRssSkill(NewsSkill):
    @property
    def name(self) -> str:
        return "公众号RSS"

    @property
    def region_scope(self) -> str:
        return "wechat"

    @property
    def search_queries(self) -> list[dict]:
        # 该 Skill 不走关键词检索，保持接口兼容即可
        return []

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        return []

    async def _fetch_single_source(self, src: object, client: httpx.AsyncClient) -> list[Article]:
        articles: list[Article] = []
        feed_url = (getattr(src, "rss_url", "") or "").strip()
        if not feed_url:
            return articles
        try:
            resp = await client.get(
                feed_url,
                headers={
                    "Accept": "application/rss+xml, application/xml, text/xml, text/html",
                    "Referer": feed_url,
                },
                timeout=30.0,
            )
            feed = feedparser.parse(resp.text or "")
            entries = feed.entries or []
            if not entries:
                return articles

            scopes = getattr(src, "scopes", [])
            is_local_source = "local" in scopes
            wechat_category = "local" if is_local_source else "industry"
            article_scope = "local" if is_local_source else self.region_scope

            for entry in entries[:20]:
                title = clean_title(str(entry.get("title", "")).strip())
                url = str(entry.get("link", "")).strip()
                summary_html = str(entry.get("summary", "") or entry.get("description", "")).strip()
                summary = normalize_summary(_strip_html(unescape(summary_html)))
                if not title or not url:
                    continue
                published_ts, published = _extract_published(entry)
                image_url = _extract_image(entry, summary_html)
                articles.append(
                    Article(
                        title=title,
                        url=url,
                        summary=summary,
                        source_name=getattr(src, "name", ""),
                        image_url=image_url,
                        published=published,
                        published_ts=published_ts,
                        skill_name=self.name,
                        region_scope=article_scope,
                        wechat_category=wechat_category,
                    )
                )
        except Exception as e:
            logger.warning(f"[公众号RSS] 抓取失败 [{getattr(src, 'name', '')}] {feed_url}: {e}")
        return articles

    async def fetch_all(self, client: httpx.AsyncClient) -> list[Article]:
        import asyncio
        articles: list[Article] = []
        source_map: dict[str, object] = {}
        for scope in ("wechat", "local"):
            for src in get_scope_rss_sources(scope):
                key = f"{src.name}|{src.rss_url}"
                source_map[key] = src
        source_list = list(source_map.values())
        if not source_list:
            logger.info("[公众号RSS] 未配置 rss_url，跳过")
            return []

        local_source_count = sum(1 for src in source_list if "local" in getattr(src, "scopes", []))
        wechat_source_count = sum(1 for src in source_list if "wechat" in getattr(src, "scopes", []))
        logger.info(
            f"[公众号RSS] 来源诊断: total_sources={len(source_list)}, "
            f"local_sources={local_source_count}, wechat_sources={wechat_source_count}"
        )

        tasks = [self._fetch_single_source(src, client) for src in source_list]
        results = await asyncio.gather(*tasks)
        for batch in results:
            articles.extend(batch)

        local_article_count = sum(1 for a in articles if (a.region_scope or "").strip().lower() == "local")
        wechat_article_count = sum(1 for a in articles if (a.region_scope or "").strip().lower() == "wechat")
        logger.info(
            f"[公众号RSS] 文章诊断: total_articles={len(articles)}, "
            f"local_articles={local_article_count}, wechat_articles={wechat_article_count}"
        )
        logger.info(f"[公众号RSS] 采集完成，获取 {len(articles)} 条原始资讯")
        return articles
