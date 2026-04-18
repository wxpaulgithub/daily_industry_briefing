"""
通用 RSS 关键词过滤 Skill 基类
"""
import calendar
import logging
import re
from typing import Any

import feedparser
import httpx

from services.fetcher import Article, NewsSkill, clean_title, normalize_summary

logger = logging.getLogger(__name__)


class RSSKeywordSkill(NewsSkill):
    """基于 RSS/Atom 的通用技能基类"""

    skill_name: str = "RSS通用"
    source_name_fallback: str = "RSS"
    feed_sources: list[dict[str, str]] = []
    include_keywords: list[str] = []
    exclude_keywords: list[str] = []
    max_per_feed: int = 10

    @property
    def name(self) -> str:
        return self.skill_name

    @property
    def search_queries(self) -> list[dict]:
        # 复用框架中的 search_queries 结构：keyword 字段存放 feed URL
        return [
            {"keyword": item["url"], "label": item["label"]}
            for item in self.feed_sources
            if item.get("url")
        ]

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        """抓取单个 RSS 源并做关键词过滤"""
        feed_url = keyword.strip()
        if not feed_url:
            return []

        try:
            resp = await client.get(feed_url, headers={"Accept": "application/rss+xml, application/xml, text/xml"})
            text = resp.text
            feed = feedparser.parse(text)
            entries = feed.entries or []
            return self._entries_to_articles(entries, min(count, self.max_per_feed), feed_url, feed)
        except Exception as e:
            logger.warning(f"[{self.name}] RSS 抓取失败 [{feed_url}]: {e}")
            return []

    async def fetch_all(self, client: httpx.AsyncClient) -> list[Article]:
        all_articles: list[Article] = []
        for source in self.search_queries:
            batch = await self.fetch(client, source["keyword"], count=self.max_per_feed)
            for article in batch:
                article.skill_name = self.name
            all_articles.extend(batch)
        logger.info(f"[{self.name}] 采集完成，获取 {len(all_articles)} 条原始资讯")
        return all_articles

    def _entries_to_articles(
        self,
        entries: list[Any],
        count: int,
        feed_url: str,
        feed_meta: Any,
    ) -> list[Article]:
        articles: list[Article] = []
        source_name = self._infer_source_name(feed_meta, feed_url)

        for entry in entries:
            if len(articles) >= count:
                break

            title = clean_title(str(entry.get("title", "")).strip())
            url = str(entry.get("link", "")).strip()
            summary_raw = str(entry.get("summary", "") or entry.get("description", "")).strip()
            summary = normalize_summary(self._strip_html(summary_raw))

            if not title or not url:
                continue

            content_text = f"{title} {summary}".lower()
            if self.exclude_keywords and any(kw.lower() in content_text for kw in self.exclude_keywords):
                continue
            if self.include_keywords and not any(kw.lower() in content_text for kw in self.include_keywords):
                continue

            published_ts, published = self._extract_published(entry)
            image_url = self._extract_image(entry, summary_raw)

            articles.append(
                Article(
                    title=title,
                    url=url,
                    summary=summary,
                    source_name=source_name,
                    image_url=image_url,
                    published=published,
                    published_ts=published_ts,
                )
            )

        return articles

    def _infer_source_name(self, feed_meta: Any, feed_url: str) -> str:
        title = str(feed_meta.get("feed", {}).get("title", "")).strip() if isinstance(feed_meta, dict) else ""
        if title:
            return title
        m = re.search(r"https?://([^/]+)", feed_url)
        return m.group(1) if m else self.source_name_fallback

    @staticmethod
    def _strip_html(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text or "")

    @staticmethod
    def _extract_published(entry: Any) -> tuple[float, str]:
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

    @staticmethod
    def _extract_image(entry: Any, summary_html: str) -> str:
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
