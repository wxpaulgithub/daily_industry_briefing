"""Fetch public source pages and accept factual fields only with matching quotes."""
import asyncio
from html.parser import HTMLParser
from html import unescape
import ipaddress
import socket
import re
import json
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass, field
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
    pages: list[dict] = field(default_factory=list)
    attachment_urls: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    parent_url: str = ""

    def research_text(self):
        if not self.pages:
            return self.text[:16000]
        preferred = sorted(self.pages, key=lambda page: (not any(term in page["text"] for term in
            ("预算", "截止", "采购人", "技术规格", "堆垛机", "WMS", "澄清")), page["page"]))
        blocks, remaining = [], 24000
        for page in preferred:
            text = page["text"][:min(remaining, 6000)]
            blocks.append(f"[PDF page {page['page']}] {text}")
            remaining -= len(text)
            if remaining <= 0:
                break
        return "\n".join(blocks)


class _Attachments(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.urls = base, []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        url = normalize_url(urljoin(self.base, href))
        if url and urlsplit(url).path.lower().endswith(".pdf") and url not in self.urls:
            self.urls.append(url)


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
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    def domains(key):
        return {str(domain).strip().lower().rstrip(".") for domain in config.get(key, [])
                if re.fullmatch(r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", str(domain).strip())}
    official = {"ccgp.gov.cn", "ggzy.gov.cn", "cebpubservice.com", *domains("trusted_domains")}
    if host.endswith(".gov.cn") or any(host == domain or host.endswith("." + domain) for domain in official):
        return 1
    if any(host == domain or host.endswith("." + domain) for domain in domains("authoritative_domains")):
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
        self.failures = {}

    async def fetch(self, url: str) -> SourceDocument | None:
        url = normalize_url(url)
        if not url:
            return None
        if url in self.cache:
            return self.cache[url]
        if url in self._inflight:
            return await self._inflight[url]
        task = asyncio.create_task(asyncio.wait_for(self._fetch(url), 30))
        self._inflight[url] = task
        try:
            try:
                result = await task
            except asyncio.TimeoutError:
                self.failures[url] = ["source_fetch_timeout"]
                result = None
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
                    content_type = response.headers.get("content-type", "").lower()
                    is_pdf = "application/pdf" in content_type or urlsplit(url).path.lower().endswith(".pdf")
                    if "html" not in content_type and not is_pdf:
                        return None
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > (10_000_000 if is_pdf else 2_000_000):
                            self.failures[normalize_url(url)] = ["pdf_size_limit" if is_pdf else "html_size_limit"]
                            return None
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    if is_pdf:
                        return await self._pdf_document(url, raw)
                    decoded_response = httpx.Response(200, headers=response.headers, content=raw)
                    html = OfficialBiddingSource._decode_html(decoded_response, url)
                text = trafilatura.extract(html, include_links=False, include_images=False) or ""
                text = " ".join(text.split())
                title = ""
                if len(text) < 60:
                    parser = _VisibleText()
                    parser.feed(html)
                    text, title = parser.result()
                flags = ["html_text_truncated"] if len(text) > 16000 else []
                parser = _Attachments(url)
                parser.feed(html)
                return SourceDocument(url, text[:16000], title, attachment_urls=parser.urls[:2], risk_flags=flags) if len(text) >= 60 else None
        except (httpx.HTTPError, UnicodeError):
            return None
        return None

    async def _pdf_document(self, url, raw):
        def parse():
            return subprocess.run([sys.executable, "-m", "services.opportunity.pdf_extract"], input=raw,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
                cwd=Path(__file__).resolve().parents[2], check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        try:
            data = json.loads((await asyncio.to_thread(parse)).stdout)
        except (subprocess.SubprocessError, ValueError):
            self.failures[normalize_url(url)] = ["pdf_parse_failed"]
            return None
        flags = data["risk_flags"]
        text = "\n".join(page["text"] for page in data["pages"])
        if len(text) < 60:
            self.failures[normalize_url(url)] = flags or ["pdf_text_unavailable"]
            return None
        return SourceDocument(url, text, retrieval_method="pdf", pages=data["pages"], risk_flags=flags)

    async def fetch_attachments(self, documents):
        result = []
        for parent in documents:
            for url in parent.attachment_urls[:2]:
                doc = await self.fetch(url)
                if doc:
                    from dataclasses import replace
                    result.append(replace(doc, parent_url=parent.url))
                else:
                    parent.risk_flags.extend(self.failures.get(normalize_url(url), ["attachment_unavailable"]))
        return result

    def verify(self, candidate, research, documents: list[SourceDocument]):
        accepted = []
        document_map = {normalize_url(doc.url): doc for doc in documents}
        from .project_memory import title_identity
        candidate_identity = title_identity(candidate.title)
        result_identity = title_identity(research.title)
        if (candidate.title != candidate.url and candidate_identity and result_identity
                and candidate_identity not in result_identity and result_identity not in candidate_identity):
            return None
        related = {normalize_url(doc.url) for doc in documents if compact(research.title) in compact(doc.text)}
        related.update(normalize_url(doc.url) for doc in documents if normalize_url(doc.parent_url) in related)
        for evidence in research.evidence:
            if normalize_url(evidence.source_url) not in related:
                continue
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
            row = evidence.model_dump()
            if doc.pages:
                page = next((page["page"] for page in doc.pages if compact(evidence.quote) in compact(page["text"])), None)
                if page is None:
                    continue
                row["page"] = page
            row["retrieval_method"] = doc.retrieval_method
            accepted.append(row)
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
        full_text = " ".join(doc.text for doc in documents if normalize_url(doc.url) in related)
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
            verification_status="preview" if is_preview else "verified",
        )
        item.risk_flags.extend(sorted({flag for doc in documents for flag in doc.risk_flags}))
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
