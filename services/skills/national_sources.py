"""Independent public industry feeds and the LET organizer's public news endpoint."""
import json
import logging
from urllib.parse import urljoin, urlsplit, urlencode

import httpx

from config import CONFIG_DATA_DIR
from services.fetcher import Article, NewsSkill
from services.news_metadata import clean_text, parse_date, read_public_text, SourceReadError
from services.national_news import CORE, RELATED, CONTEXT, assess
from .rss_generic import RSSKeywordSkill

logger = logging.getLogger(__name__)


class NationalDirectSkill(RSSKeywordSkill):
    skill_name = "行业直采"
    include_keywords = list(CORE + RELATED + CONTEXT)
    max_per_feed = 12

    def __init__(self):
        data = json.loads((CONFIG_DATA_DIR / "national_sources.json").read_text(encoding="utf-8"))
        self.feed_sources = [s for s in data.get("sources", []) if s.get("label") and urlsplit(s.get("url", "")).scheme in ("http", "https")]


class OrganizerNewsSkill(NewsSkill):
    """Use the exact anonymous endpoint used by the official LET website, without JS execution."""
    name = "展会主办方"
    region_scope = "national"
    check_dns = True
    endpoint = "https://www.chinalet.cn/home/jiekou/handle.ashx"
    search_queries = [{"keyword": endpoint, "label": "LET展会官网"}]

    async def fetch(self, client, keyword, count=10):
        report = {"url": self.endpoint, "publisher": "LET展会官网", "status": "fetching", "parsed": 0, "returned": 0}
        self.source_diagnostics = [report]
        try:
            query = urlencode({"method": "xwnews", "page": "1", "limit": str(min(count, 12)), "type1": "动态", "type2": "展会动态"})
            text, _ = await read_public_text(client, self.endpoint + "?" + query, timeout=12, check_dns=self.check_dns)
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("invalid_news_response")
            if data.get("code") != "001":
                raise ValueError("invalid_news_response")
            rows = (data.get("data") or {}).get("zslist", [])
            if not isinstance(rows, list):
                raise ValueError("invalid_news_rows")
            report["parsed"] = len(rows)
            articles = []
            for row in rows[:12]:
                if not isinstance(row, dict):
                    continue
                identifier = str(row.get("id", ""))
                title = clean_text(row.get("wzmc", ""))
                if not identifier.isdigit() or not title:
                    continue
                ts, published = parse_date(str(row.get("addtime", "")))
                image = urljoin("https://www.chinalet.cn/", row.get("wznr", "")) if row.get("wznr") else ""
                if urlsplit(image).scheme not in ("http", "https"):
                    image = ""
                article = Article(title=title, url="https://www.chinalet.cn/home/news_x.html?id=" + identifier, source_name="LET展会官网", source_domain="chinalet.cn", source_kind="organizer", collection_url=self.endpoint, published_ts=ts, published=published, image_url=image, skill_name=self.name, region_scope="national")
                if not assess(article)[2]:
                    articles.append(article)
            report["returned"] = len(articles)
            report["status"] = "ok" if articles else "no_relevant_news"
            return articles
        except httpx.HTTPStatusError as exc:
            report["status"] = f"http_{exc.response.status_code}"
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            report["status"] = str(exc) if isinstance(exc, SourceReadError) else type(exc).__name__
        finally:
            logger.info("[国内来源/展会主办方] LET status=%s returned=%s", report["status"], report["returned"])
        return []
