"""High-trust procurement discovery from configured official listing pages."""
import logging
import re
from urllib.parse import urljoin

import httpx

from ..models import OpportunityCandidate

logger = logging.getLogger(__name__)


class OfficialBiddingSource:
    name = "official_bidding"

    def __init__(self, sources: list[dict]):
        self.sources = sources

    async def fetch(self, client: httpx.AsyncClient) -> list[OpportunityCandidate]:
        results: list[OpportunityCandidate] = []
        for source in self.sources:
            url = str(source.get("url", "")).strip()
            label = str(source.get("label", "官方公告")).strip()
            if not url:
                continue
            try:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                html = self._decode_html(response, url)
                results.extend(self._extract_links(html, str(response.url), label))
            except Exception as exc:
                logger.warning("[Opportunity/%s] %s: %s", self.name, label, exc)
        return results

    @staticmethod
    def _decode_html(response: httpx.Response, url: str) -> str:
        # ccgp pages are sometimes GB18030 while declaring UTF-8.
        if "ccgp.gov.cn" in url:
            try:
                decoded = response.content.decode("gb18030")
                if "采购" in decoded or "公告" in decoded:
                    return decoded
            except UnicodeDecodeError:
                pass
        return response.text

    @staticmethod
    def _extract_links(html: str, base_url: str, label: str) -> list[OpportunityCandidate]:
        rows: list[OpportunityCandidate] = []
        for href, raw_title in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
            title = re.sub(r"<[^>]+>", " ", raw_title)
            title = re.sub(r"\s+", " ", title).strip()
            if len(title) < 8:
                continue
            if not any(x.lower() in title.lower() for x in ("仓储", "立体库", "物流自动化", "WMS", "WCS", "AGV", "堆垛", "输送")):
                continue
            rows.append(OpportunityCandidate(title=title, url=urljoin(base_url, href), source_name=label))
        return rows[:80]
