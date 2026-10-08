import asyncio
import json
import logging
from pathlib import Path

import httpx

from config import CONFIG_DATA_DIR, MAX_OPPORTUNITIES, REQUEST_TIMEOUT, USER_AGENT
from .enrichment import extract_budget, extract_deadline, extract_location, extract_owner
from .models import OpportunityCandidate, ProjectOpportunity
from .rules import candidate_text, classify_stage, classify_type, is_too_old, relevance, should_keep
from .scoring import final_score, source_score, urgency_score
from .sources import OfficialBiddingSource, OpportunitySearchSource

logger = logging.getLogger(__name__)
CONFIG_PATH = CONFIG_DATA_DIR / "opportunity_queries.json"


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        return {"official_sources": [], "queries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def build_opportunity(candidate: OpportunityCandidate, *, include_inactive: bool = False) -> ProjectOpportunity | None:
    text = candidate_text(candidate)
    relevance_value, matched, risks = relevance(candidate)
    stage = classify_stage(text)
    allowed = should_keep(relevance_value, stage) or (include_inactive and relevance_value >= 38 and stage == "CLOSED")
    if not allowed or (not include_inactive and is_too_old(stage, candidate.published_ts)):
        return None
    project_type = classify_type(text)
    province, city = extract_location(text)
    deadline = extract_deadline(text)
    source_url = candidate.verified_url or candidate.url
    source_value = source_score(candidate.source_name, source_url)
    urgency_value = urgency_score(stage, candidate.published_ts, deadline)
    from .project_memory import notice_version
    return ProjectOpportunity(
        notice_version=notice_version(candidate.title, candidate.published, source_url),
        title=candidate.title,
        source_url=source_url,
        source_name=candidate.source_name,
        discovery_url=candidate.url if candidate.source_name.startswith("搜索发现") or candidate.discovery_method == "ai_search" else "",
        owner=extract_owner(text), province=province, city=city,
        published=candidate.published, published_ts=candidate.published_ts,
        deadline=deadline, budget=extract_budget(text), summary=candidate.summary,
        project_type=project_type, stage=stage,
        relevance_score=relevance_value, urgency_score=urgency_value, source_score=source_value,
        final_score=final_score(relevance_value, urgency_value, source_value),
        matched_keywords=matched, risk_flags=risks,
    )


def process_candidates(candidates: list[OpportunityCandidate], limit: int = MAX_OPPORTUNITIES) -> list[ProjectOpportunity]:
    unique_urls: set[str] = set()
    by_key: dict[str, ProjectOpportunity] = {}
    for candidate in candidates:
        if not candidate.url or candidate.url in unique_urls:
            continue
        unique_urls.add(candidate.url)
        item = build_opportunity(candidate)
        if item is None:
            continue
        old = by_key.get(item.project_key)
        if old is None or item.final_score > old.final_score:
            by_key[item.project_key] = item
    return sorted(by_key.values(), key=lambda x: (x.stage != "PROCUREMENT", -x.final_score))[:limit]


async def _enrich_candidates(client: httpx.AsyncClient, candidates: list[OpportunityCandidate], config: dict | None = None) -> int:
    """Fetch detail text only for plausible candidates; facts remain source-derived."""
    plausible = [candidate for candidate in candidates if relevance(candidate)[0] >= 25][:60]
    semaphore = asyncio.Semaphore(8)
    from .verifier import SourceVerifier
    verifier = SourceVerifier(config or {}, client)

    async def enrich(candidate: OpportunityCandidate) -> bool:
        async with semaphore:
            try:
                document = await verifier.fetch(candidate.url)
                if not document:
                    return False
                text = document.text
                from .facts import content_signature
                candidate.content_hash = content_signature(text) if not document.attachment_urls and not document.risk_flags else ""
                candidate.verified_url = document.url
                candidate.content = text[:8000]
                if not candidate.summary:
                    candidate.summary = text[:260] + ("…" if len(text) > 260 else "")
                return True
            except Exception:
                return False

    if not plausible:
        return 0
    results = await asyncio.gather(*(enrich(candidate) for candidate in plausible))
    return sum(bool(result) for result in results)


async def fetch_candidates(config: dict, *, include_existing_skills: bool = True, usage=None) -> list[OpportunityCandidate]:
    sources = [
        OfficialBiddingSource(config.get("official_sources") or []),
        OpportunitySearchSource((config.get("queries") or [])[:1] if usage else config.get("queries") or [], usage),
    ]
    if include_existing_skills:
        from .sources.existing_skills import ExistingSkillsSource
        sources.append(ExistingSkillsSource(offline=usage is not None))
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        batches = await asyncio.gather(*(source.fetch(client) for source in sources), return_exceptions=True)
        candidates = [item for batch in batches if not isinstance(batch, BaseException) for item in batch]
        enriched_count = await _enrich_candidates(client, candidates, config)
    logger.info("[Opportunity] source candidates=%d", len(candidates))
    logger.info("[Opportunity] detail enriched=%d", enriched_count)
    return candidates


async def fetch_opportunities(config_path: Path = CONFIG_PATH, *, settings=None) -> list[ProjectOpportunity]:
    from .ai_researcher import research_opportunities
    from .settings import OpportunitySettings
    settings = settings or OpportunitySettings.from_env()
    return await research_opportunities(load_config(config_path), settings)
