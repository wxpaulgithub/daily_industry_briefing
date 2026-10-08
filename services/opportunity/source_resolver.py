"""Bounded source rescue for high-value candidates, using existing providers."""
import asyncio
import re
from urllib.parse import urlsplit
from .models import OpportunityCandidate
from .facts import compact, normalize_url
from .enrichment import extract_owner, extract_location
from .verifier import SourceDocument, source_tier

CORE_TERMS = ("WMS", "WCS", "堆垛机", "立体库", "输送", "粮库", "仓储", "生产物流", "物料", "码垛", "AGV")

def resolver_queries(candidate, config):
    text = candidate.title + " " + candidate.summary + " " + candidate.content
    owner = extract_owner(text)
    province, city = extract_location(text)
    terms = " ".join(term for term in CORE_TERMS if term.lower() in text.lower()) or "仓储 生产物流"
    queries = []
    if owner:
        queries.append(("owner_equipment", f'"{owner}" {terms} 采购 招标 最新公告'))
    domains = {urlsplit(row).hostname for row in [candidate.url, candidate.verified_url] if row}
    for domain in sorted(domain for domain in domains if domain):
        if source_tier("https://" + domain, config) == 1:
            queries.append(("official_domain", f'site:{domain} {owner} {terms} 采购 最新公告'))
    if province or city:
        queries.append(("region_equipment", f'{province} {city} {terms} 采购意向 企业采购 公告'))
    if not queries:
        core = re.sub(r'[-—|].*$', '', candidate.title).strip()
        queries.append(("project_core", f'{core} {terms} 企业采购平台 最新公告'))
    return queries

class SourceResolver:
    def __init__(self, search, verifier, settings, usage):
        self.search, self.verifier, self.settings, self.usage = search, verifier, settings, usage
        self.traces = {}
        # Keep baseline lookup capacity for later waves; rescues get only the
        # verification budget above the hard research cap.
        self.rescue_remaining = max(0, settings.verification_search_budget - settings.research_hard_limit * getattr(search, "query_cost", 1))

    async def resolve(self, candidate):
        sources = [candidate]
        documents, fetched_urls, selected_urls = [], set(), set()
        trace = self.traces[candidate.url] = {"attempts": [], "raw_documents": 0}
        async def fetch_available(limit=4):
            ranked = sorted(sources, key=lambda row: (row.url != candidate.url, source_tier(row.url, self.verifier.config)))
            urls = list(dict.fromkeys(normalize_url(row.url) for row in ranked if normalize_url(row.url)))
            urls = [url for url in urls if url not in selected_urls][:min(limit, max(8-len(selected_urls), 0))]
            selected_urls.update(urls)
            fetched = await asyncio.gather(*(self.verifier.fetch(url) for url in urls))
            for url, doc in zip(urls, fetched):
                if doc:
                    fetched_urls.add(url)
                    documents.append(doc)
            trace["raw_documents"] = len(documents)
        def add_sources(rows):
            sources.extend(rows)
            for row in [entry for source in rows for entry in source.search_sources]:
                url = normalize_url(row.get("url", ""))
                if url:
                    sources.append(OpportunityCandidate(row.get("title") or candidate.title, url,
                        summary=row.get("snippet", ""), snippet_origin=row.get("origin", ""), discovery_method="ai_search"))
        def has_related_document():
            from .project_memory import title_identity
            identity = title_identity(candidate.title)
            return any(compact(candidate.title) in compact(doc.text) or
                       (identity and identity in title_identity(doc.text)) for doc in documents if doc.retrieval_method in {"html", "pdf"})
        # Always try the discovered URL first; shared tool sources must not crowd it out.
        await fetch_available(limit=1)
        add_sources([OpportunityCandidate(candidate.title, candidate.url, search_sources=candidate.search_sources)])
        try:
            rows = await self.search.find_sources(candidate)
            trace["attempts"].append({"strategy": "full_title", "returned": len(rows)})
            add_sources(rows)
            await fetch_available(limit=3)
        except Exception as exc:
            trace["attempts"].append({"strategy": "full_title", "error": type(exc).__name__})
        if not has_related_document() and candidate.triage_score >= self.settings.resolver_min_score:
            for strategy, query in resolver_queries(candidate, self.verifier.config)[:max(0,self.settings.resolver_max_searches-1)]:
                cost = getattr(self.search, "query_cost", 1)
                if self.rescue_remaining < cost or self.usage.remaining("verification") < cost:
                    trace["rescue_stop"] = "verification_budget_reserved_or_exhausted"
                    break
                self.rescue_remaining -= cost
                try:
                    rows = await self.search.search_query(query, phase="verification")
                    trace["attempts"].append({"strategy": strategy, "returned": len(rows)})
                    add_sources(rows)
                    await fetch_available(limit=2)
                except Exception as exc:
                    trace["attempts"].append({"strategy": strategy, "error": type(exc).__name__})
                if has_related_document():
                    break
        documents.extend(await self.verifier.fetch_attachments(list(documents)))
        for source in sources:
            url = normalize_url(source.url)
            excerpt = (source.summary or source.content or "").strip()
            if url not in selected_urls or url in fetched_urls or len(excerpt) < 50:
                continue
            if source.snippet_origin not in {"glm_web_search", "existing_search", "openai_web_search"}:
                continue
            if any(normalize_url(doc.url) == url for doc in documents):
                continue
            documents.append(SourceDocument(url, f"{source.title}\n{excerpt}"[:3000], source.title, "search_excerpt",
                                            risk_flags=self.verifier.failures.get(url, [])))
        trace["source_urls"] = sorted(selected_urls)
        trace["related_raw_document"] = has_related_document()
        return documents
