"""Fetch public source pages and accept factual fields only with matching quotes."""
import asyncio
from html.parser import HTMLParser
from html import unescape
import ipaddress
import socket
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urljoin

import httpx
import trafilatura

from config import USER_AGENT
from .facts import budget_amount, compact, normalize_url, parse_date
from .models import ProjectOpportunity
from .sources.bidding import OfficialBiddingSource
from .rules import classify_stage


@dataclass
class SourceDocument:
    url: str
    text: str
    title: str = ""
    retrieval_method: str = "html"


class _VisibleText(HTMLParser):
    """Small dependency-free fallback for pages trafilatura cannot parse."""
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
        self.in_title = False
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if not self.skip_depth:
            self.parts.append(data)

    def result(self):
        text = re.sub(r"\s+", " ", unescape(" ".join(self.parts))).strip()
        title = re.sub(r"\s+", " ", unescape(" ".join(self.title_parts))).strip()
        return text, title


def source_tier(url: str, config: dict) -> int:
    host = (urlsplit(url).hostname or "").lower()
    official = {"ccgp.gov.cn", "ggzy.gov.cn", "cebpubservice.com", *config.get("trusted_domains", [])}
    if host.endswith(".gov.cn") or any(host == domain or host.endswith("." + domain) for domain in official):
        return 1
    if any(host == domain or host.endswith("." + domain) for domain in config.get("authoritative_domains", [])):
        return 2
    return 3


async def _public_host(url: str) -> bool:
    host = urlsplit(url).hostname
    try:
        rows = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return bool(rows) and all(ipaddress.ip_address(row[4][0]).is_global for row in rows)
    except (OSError, ValueError):
        return False


class SourceVerifier:
    def __init__(self, config: dict, client=None, check_dns: bool = True):
        self.config = config
        self.client = client
        self.check_dns = check_dns
        self.cache = {}
        self._inflight = {}

    async def fetch(self, url: str) -> SourceDocument | None:
        url = normalize_url(url)
        if not url:
            return None
        if url in self.cache:
            return self.cache[url]
        if url in self._inflight:
            return await self._inflight[url]
        task = asyncio.create_task(self._fetch(url))
        self._inflight[url] = task
        try:
            result = await task
            self.cache[url] = result
            if result:
                self.cache[result.url] = result
            return result
        finally:
            self._inflight.pop(url, None)

    async def _fetch(self, url):
        if self.client:
            return await self._fetch_with_client(self.client, url)
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}, follow_redirects=False) as client:
            return await self._fetch_with_client(client, url)

    async def _fetch_with_client(self, client, url):
        try:
            for _ in range(5):
                if not normalize_url(url) or (self.check_dns and not await _public_host(url)):
                    return None
                async with client.stream("GET", url, follow_redirects=False) as response:
                    if response.is_redirect:
                        url = normalize_url(urljoin(url, response.headers.get("location", "")))
                        continue
                    if response.status_code != 200:
                        return None
                    if "html" not in response.headers.get("content-type", "").lower():
                        return None
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 2_000_000:
                            return None
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    decoded_response = httpx.Response(200, headers=response.headers, content=raw)
                    html = OfficialBiddingSource._decode_html(decoded_response, url)
                text = trafilatura.extract(html, include_links=False, include_images=False) or ""
                text = " ".join(text.split())
                title = ""
                if len(text) < 60:
                    parser = _VisibleText()
                    parser.feed(html)
                    text, title = parser.result()
                text = text[:16000]
                return SourceDocument(url, text, title) if len(text) >= 60 else None
        except (httpx.HTTPError, UnicodeError):
            return None
        return None

    def verify(self, candidate, research, documents: list[SourceDocument]):
        accepted = []
        document_map = {normalize_url(doc.url): doc for doc in documents}
        for evidence in research.evidence:
            doc = document_map.get(normalize_url(evidence.source_url))
            if not doc or len(compact(evidence.quote)) < 4 or compact(evidence.quote) not in compact(doc.text):
                continue
            value = evidence.value
            if evidence.field in {"published", "deadline"}:
                date = parse_date(value)
                if not date:
                    continue
                source_dates = [parse_date(match.group(0)) for match in re.finditer(r"20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?(?:[ T\s]+\d{1,2}[:：]\d{2}(?:[:：]\d{2})?)?", evidence.quote)]
                if not any(source_date == date for source_date in source_dates):
                    continue
                if evidence.field == "deadline" and not any(word in evidence.quote for word in ("截止", "报名")):
                    continue
            elif evidence.field == "stage":
                deterministic_stage = classify_stage(evidence.quote)
                if deterministic_stage != "UNKNOWN" and deterministic_stage != value:
                    continue
            elif not value or compact(value) not in compact(evidence.quote):
                continue
            accepted.append(evidence.model_dump())
        def fact(field, expected):
            matching = [row for row in accepted if row["field"] == field and row["value"] == expected]
            matching.sort(key=lambda row: source_tier(row["source_url"], self.config))
            return expected if matching else ""
        title = fact("title", research.title)
        if not research.is_real_project or not title:
            return None
        stage = fact("stage", research.stage) or "UNKNOWN"
        linked = sorted({row["source_url"] for row in accepted}, key=lambda url: (source_tier(url, self.config), url))
        title_urls = sorted({row["source_url"] for row in accepted if row["field"] == "title" and row["value"] == title},
                            key=lambda url: (source_tier(url, self.config), url))
        primary = title_urls[0]
        primary_doc = document_map.get(normalize_url(primary))
        is_preview = any(
            (document_map.get(normalize_url(row["source_url"])) is not None
             and document_map[normalize_url(row["source_url"])].retrieval_method == "search_excerpt")
            for row in accepted
        )
        primary_is_excerpt = bool(primary_doc and primary_doc.retrieval_method == "search_excerpt")
        tier = 3 if is_preview else source_tier(primary, self.config)
        published = fact("published", research.published)
        published_date = parse_date(published)
        full_text = " ".join(doc.text for doc in documents)
        scopes = [scope for scope in research.technical_scope if scope and compact(scope) in compact(full_text)]
        item = ProjectOpportunity(
            title=title, source_url=primary,
            source_name=("搜索摘要（原文未读取）" if primary_is_excerpt else "含搜索摘要证据（部分字段待核验）") if is_preview
                        else ("官方原始来源" if tier == 1 else candidate.source_name),
            discovery_url=candidate.url, official_source_url=primary if tier == 1 and not is_preview else "",
            owner=fact("owner", research.owner), province=fact("province", research.province), city=fact("city", research.city),
            published=published, published_ts=published_date.timestamp() if published_date else 0,
            budget=fact("budget", research.budget_text), deadline=fact("deadline", research.deadline),
            summary=research.summary, project_type=research.project_type, stage=stage,
            technical_scope=scopes, relevance_score=research.warehouse_relevance,
            source_score={1: 100., 2: 75., 3: 40.}[tier], source_tier=tier,
            opportunity_reason=research.opportunity_reason, entry_point=research.entry_point,
            evidence=accepted, source_urls=linked, research_mode="preview" if is_preview else "ai",
        )
        if is_preview:
            item.risk_flags.append("search_excerpt_only")
            item.risk_flags.append("original_page_unavailable" if primary_is_excerpt else "search_excerpt_supports_some_fields")
        if tier != 1:
            item.risk_flags.append("official_source_not_found")
        if stage == "UNKNOWN":
            item.risk_flags.append("stage_not_verified")
        if not published:
            item.risk_flags.append("published_date_not_verified")
        for field, value in (("owner", research.owner), ("budget", research.budget_text), ("deadline", research.deadline), ("published", research.published)):
            if value and not fact(field, value):
                item.risk_flags.append("unsupported_" + field)
        allowed_dates = {date.date() for date in (parse_date(item.published), parse_date(item.deadline)) if date}
        def supported_date(match):
            date = parse_date(match.group(0))
            return match.group(0) if date and date.date() in allowed_dates else "日期待核实"
        for field in ("summary", "opportunity_reason", "entry_point"):
            text = getattr(item, field)
            text = re.sub(r"[0-9]+(?:[,.][0-9]+)*\s*(?:亿元|万元|万|元)",
                          lambda match: match.group(0) if item.budget and budget_amount(match.group(0)) == budget_amount(item.budget) else "金额待核实", text)
            text = re.sub(r"20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?", supported_date, text)
            for fact_field, value in (("owner", research.owner), ("province", research.province), ("city", research.city)):
                if value and not getattr(item, fact_field):
                    text = text.replace(value, "待核实")
            setattr(item, field, text)
        return item
