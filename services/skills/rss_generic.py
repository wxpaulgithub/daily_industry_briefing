"""RSS/HTML feeds with publisher attribution, bounded enrichment and source diagnostics."""
import asyncio
import calendar
import logging
import re
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
from lxml import html as lhtml
from lxml.etree import ParserError

from services.fetcher import Article, NewsSkill, clean_title, normalize_summary
from services.news_metadata import clean_text, parse_date, unique_date, DATE, REVERSE_DATE, primary_metadata, read_public_text, SourceReadError
from services.national_news import source_domain, assess, quality_score

logger = logging.getLogger(__name__)


class RSSKeywordSkill(NewsSkill):
    skill_name = "RSS通用"
    source_name_fallback = "RSS"
    feed_sources = []
    include_keywords = []
    exclude_keywords = []
    max_per_feed = 10
    detail_limit = 4
    response_limit = 2 * 1024 * 1024
    check_dns = True

    @property
    def name(self):
        return self.skill_name

    @property
    def region_scope(self):
        return "national"

    @property
    def search_queries(self):
        return [{"keyword": item["url"], "label": item["label"]} for item in self.feed_sources if item.get("url")]

    async def _get_text(self, client, url, timeout=10):
        return await read_public_text(client, url, timeout=timeout, size_limit=self.response_limit, check_dns=self.check_dns)

    async def fetch(self, client, keyword, count=10):
        source = next((s for s in self.feed_sources if s["url"] == keyword), {"url": keyword, "label": self.source_name_fallback})
        report = {"url": keyword, "publisher": source.get("publisher", source["label"]), "status": "fetching", "parsed": 0, "returned": 0, "enriched": 0, "detail_failures": {}}
        if not hasattr(self, "source_diagnostics"):
            self.source_diagnostics = []
        self.source_diagnostics.append(report)
        try:
            text, final_url = await self._get_text(client, keyword)
            feed = feedparser.parse(text)
            entries = feed.entries or []
            if not entries:
                entries = self._parse_html_as_entries(final_url, text, source.get("article_pattern", ""))
            report["parsed"] = len(entries)
            if not entries:
                report["status"] = "dynamic_page_unparsed" if "v-for" in text or "{{" in text else "no_article_links"
                return []
            articles = self._entries_to_articles(entries, count, keyword, feed)
            # At most four article requests per listing, only useful items lacking date/content.
            enrichment = [a for a in articles if assess(a)[1] >= .38 and (not a.published_ts or not a.summary)][:self.detail_limit]
            async def enrich(article):
                try:
                    body, resolved_url = await self._get_text(client, article.url, timeout=5)
                    metadata = primary_metadata(body, resolved_url)
                    if not article.published_ts and metadata["published_ts"]:
                        article.published_ts, article.published = metadata["published_ts"], metadata["published"]
                    if not article.summary and metadata["summary"]:
                        article.summary = normalize_summary(metadata["summary"])
                    if not article.image_url and metadata["image_url"]:
                        article.image_url = metadata["image_url"]
                    report["enriched"] += 1
                except (httpx.HTTPError, ValueError, ParserError) as exc:
                    error = f"http_{exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else (str(exc) if isinstance(exc, SourceReadError) else type(exc).__name__)
                    report["detail_failures"][error] = report["detail_failures"].get(error, 0) + 1
            await asyncio.gather(*(enrich(a) for a in enrichment))
            report["returned"] = len(articles)
            report["status"] = "ok" if articles else "keyword_filtered_empty"
            return articles
        except httpx.HTTPStatusError as exc:
            report["status"] = f"http_{exc.response.status_code}"
        except asyncio.CancelledError:
            report["status"] = "timeout"
            raise
        except (httpx.HTTPError, ValueError, ParserError) as exc:
            report["status"] = str(exc) if isinstance(exc, SourceReadError) else type(exc).__name__
        finally:
            logger.info("[国内来源/%s] %s status=%s parsed=%s returned=%s", self.name, report["publisher"], report["status"], report["parsed"], report["returned"])
        return []

    async def fetch_all(self, client):
        self.source_diagnostics = []
        semaphore = asyncio.Semaphore(4)
        async def fetch_one(source):
            async with semaphore:
                try:
                    return await asyncio.wait_for(self.fetch(client, source["keyword"], self.max_per_feed), timeout=20)
                except asyncio.TimeoutError:
                    return []
        results = await asyncio.gather(*(fetch_one(source) for source in self.search_queries))
        articles = [a for batch in results for a in batch]
        for article in articles:
            article.skill_name, article.region_scope = self.name, self.region_scope
        logger.info("[%s] 采集完成，获取 %s 条原始资讯", self.name, len(articles))
        return articles

    def _entries_to_articles(self, entries, count, feed_url, feed_meta):
        articles = []
        source = next((s for s in self.feed_sources if s["url"] == feed_url), {})
        name = self._infer_source_name(feed_meta, feed_url)
        for entry in entries[:120]:
            title = clean_text(clean_title(str(entry.get("title", ""))))
            url = urljoin(feed_url, str(entry.get("link", "")))
            summary_raw = str(entry.get("summary", "") or entry.get("description", ""))
            summary = normalize_summary(self._strip_html(summary_raw))
            if not title or urlsplit(url).scheme not in ("http", "https"):
                continue
            text = (title + " " + summary).lower()
            if any(kw.lower() in text for kw in self.exclude_keywords):
                continue
            if self.include_keywords and not any(kw.lower() in text for kw in self.include_keywords):
                continue
            ts, published = self._extract_published(entry)
            same_publisher = source_domain(url) == source_domain(feed_url)
            articles.append(Article(title, url, summary=summary, source_name=name if same_publisher else source_domain(url), image_url=self._extract_image(entry, summary_raw), published=published, published_ts=ts, source_domain=source_domain(url), source_group=source.get("group", "") if same_publisher else "", source_kind=source.get("kind", "") if same_publisher else "", collection_url=feed_url, skill_name=self.name, region_scope=self.region_scope))
        articles.sort(key=quality_score, reverse=True)
        return articles[:min(count, self.max_per_feed)]

    def _infer_source_name(self, feed_meta, feed_url):
        source = next((s for s in self.feed_sources if s["url"] == feed_url), {})
        if source:
            return source.get("publisher", source["label"])
        title = feed_meta.get("feed", {}).get("title", "") if isinstance(feed_meta, dict) else ""
        return clean_text(title) or source_domain(feed_url) or self.source_name_fallback

    def _parse_html_as_entries(self, base_url, text, article_pattern=""):
        if not text:
            return []
        root = lhtml.fromstring(text)
        for node in root.xpath('//script|//style|//nav|//footer'):
            node.drop_tree()
        entries, positions = [], {}
        for anchor in root.xpath('//a[@href]'):
            url = urljoin(base_url, anchor.get("href"))
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname or url.split("#")[0] == base_url.split("#")[0] or anchor.get("href", "").startswith("#"):
                continue
            if article_pattern and not re.search(article_pattern, parts.path, re.I):
                continue
            if not article_pattern and (parts.path.endswith((".pdf", ".zip")) or re.search(r"(?:list|tags|login|register|about|contact)(?:\d|/|\.|$)", parts.path, re.I)):
                continue
            title = clean_text(anchor.get("title", ""))
            if not title:
                heading_nodes = anchor.xpath('.//h1|.//h2|.//h5|.//h6|.//*[contains(@class,"title") or contains(@class,"tit")]')
                title = next((clean_text(n.text_content()) for n in heading_nodes if len(clean_text(n.text_content())) >= 8 and not DATE.fullmatch(clean_text(n.text_content()))), "")
            if not title:
                title = clean_text(anchor.text_content())
            if len(title) < 8 or len(title) > 180 or "{{" in title:
                continue
            if url in positions:
                continue
            container = anchor
            for _ in range(4):
                parent = container.getparent()
                if parent is None or parent.tag in ("body", "html", "main", "nav"):
                    break
                urls = {urljoin(base_url, a.get("href")) for a in parent.xpath('.//a[@href]') if len(clean_text(a.get("title", "") or a.text_content())) >= 8 and not re.search(r"tags", a.get("href", ""))}
                if len(urls - {url}) or len(clean_text(parent.text_content())) > 1800:
                    break
                container = parent
                if container.tag in ("li", "article"):
                    break
            summary_nodes = container.xpath('.//*[contains(@class,"intro") or contains(@class,"brief") or contains(@class,"summary") or contains(@class,"excerpt") or contains(@class,"richText") or contains(@class,"rwznr")]')
            summary = clean_text(summary_nodes[0].text_content()) if summary_nodes else ""
            date_values = []
            for node in container.iter():
                if not isinstance(node.tag, str) or node.tag in ("a", "img", "script", "style"):
                    continue
                value = clean_text(" ".join(node.itertext()))
                classes = (node.get("class", "") or "").lower()
                if len(value) < 50 and (DATE.fullmatch(value) or REVERSE_DATE.fullmatch(value)):
                    date_values.append(value)
                elif any(label in classes for label in ("date", "time", "publish")) and len(value) < 50:
                    date_values.extend(m.group(0) for m in DATE.finditer(value))
            # Some corporate cards split MM-DD and YYYY into adjacent date nodes.
            date_sections = container.xpath('.//*[contains(@class,"c3l") or contains(@class,"date")]')
            for node in date_sections:
                value = clean_text(" ".join(node.itertext()))
                if REVERSE_DATE.fullmatch(value):
                    date_values.append(value)
            ts, published = unique_date(date_values)
            if not summary and title == clean_text(anchor.text_content()):
                title = re.sub(r"^20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?\s*", "", title)
            images = container.xpath('.//img/@data-original|.//img/@src')
            positions[url] = len(entries)
            entries.append({"title": title, "link": url, "summary": summary, "published_ts": ts, "published": published, "image": urljoin(base_url, images[0]) if images else ""})
            if len(entries) >= 120:
                break
        return entries

    @staticmethod
    def _strip_html(text):
        return re.sub(r"<[^>]+>", "", text or "")

    @staticmethod
    def _extract_published(entry):
        if entry.get("published_ts"):
            return float(entry["published_ts"]), entry.get("published", "")
        value = entry.get("published_parsed")
        if value:
            return float(calendar.timegm(value)), str(entry.get("published", ""))[:19]
        # Do not infer publication from RSS updated/dateModified fields.
        return parse_date(str(entry.get("published", "")))

    @staticmethod
    def _extract_image(entry, summary_html):
        if entry.get("image"):
            return entry["image"]
        media = entry.get("media_content", []) or []
        if media and isinstance(media[0], dict) and media[0].get("url"):
            return media[0]["url"]
        for link in entry.get("links", []) or []:
            if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image/"):
                return link.get("href", "")
        match = re.search(r'<img[^>]+src="([^"]+)"', summary_html or "", re.I)
        return match[1] if match else ""
