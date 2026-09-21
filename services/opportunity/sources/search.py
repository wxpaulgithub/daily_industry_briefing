"""Search is discovery-only; downstream scoring keeps official sources preferred."""
import asyncio
import logging
import re
from html import unescape
from urllib.parse import quote_plus, urlparse

import httpx

from ..models import OpportunityCandidate

logger = logging.getLogger(__name__)


class OpportunitySearchSource:
    name = "search_discovery"

    def __init__(self, queries: list[str]):
        self.queries = queries

    async def fetch(self, client: httpx.AsyncClient) -> list[OpportunityCandidate]:
        tasks = []
        for query in self.queries:
            tasks.extend((self._fetch_toutiao(client, query), self._fetch_official_search(client, query)))
        batches = await asyncio.gather(*tasks)
        return [row for batch in batches for row in batch]

    async def _fetch_toutiao(self, client: httpx.AsyncClient, query: str) -> list[OpportunityCandidate]:
        try:
            response = await client.get(
                "https://www.toutiao.com/api/search/content/",
                params={"keyword": query, "pd": "information", "source": "input", "dvpf": "pc", "aid": "4916", "page_num": "0", "count": "15"},
                headers={"Referer": "https://www.toutiao.com/"},
            )
            payload = response.json() if response.status_code == 200 else {}
            rows = []
            for item in payload.get("data") or []:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                url = str(item.get("share_url") or item.get("source_url") or "").strip()
                if title and url:
                    rows.append(OpportunityCandidate(
                        title=title, url=url,
                        summary=str(item.get("abstract") or "").strip(),
                        source_name=f"搜索发现/{str(item.get('media_name') or '').strip()}".rstrip("/"),
                        published=str(item.get("datetime") or "")[:16],
                        published_ts=float(item.get("publish_time") or 0),
                    ))
            return rows
        except Exception as exc:
            logger.warning("[Opportunity/%s] 今日头条 %s: %s", self.name, query, exc)
            return []

    async def _fetch_official_search(self, client: httpx.AsyncClient, query: str) -> list[OpportunityCandidate]:
        search_query = f"{query} (site:ccgp.gov.cn OR site:ggzy.gov.cn OR site:cebpubservice.com)"
        try:
            response = await client.get(
                f"https://www.bing.com/search?q={quote_plus(search_query)}",
                headers={"Referer": "https://www.bing.com/", "Accept-Language": "zh-CN,zh;q=0.9"},
            )
            rows: list[OpportunityCandidate] = []
            for block in re.findall(r'<li class="b_algo".*?</li>', response.text, re.I | re.S):
                match = re.search(r'<h2>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.I | re.S)
                if not match:
                    continue
                url = unescape(match.group(1)).strip()
                host = urlparse(url).netloc.lower()
                if not any(domain in host for domain in ("ccgp.gov.cn", "ggzy.gov.cn", "cebpubservice.com")):
                    continue
                title = re.sub(r"<[^>]+>", "", unescape(match.group(2)))
                title = re.sub(r"\s+", " ", title).strip()
                snippet_match = re.search(r'<p>(.*?)</p>', block, re.I | re.S)
                snippet = re.sub(r"<[^>]+>", " ", unescape(snippet_match.group(1))) if snippet_match else ""
                if title:
                    rows.append(OpportunityCandidate(
                        title=title, url=url, summary=" ".join(snippet.split()),
                        source_name="搜索发现/官方公告",
                    ))
            return rows[:10]
        except Exception as exc:
            logger.warning("[Opportunity/%s] 官方搜索 %s: %s", self.name, query, exc)
            return []
