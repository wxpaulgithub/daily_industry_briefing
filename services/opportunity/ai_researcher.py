"""Independent AI Discovery/Research flow, with source validation and V1 fallback."""
import asyncio
import json
import logging
import time
from dataclasses import asdict

from .ai_client import create_llm_provider
from .ai_prompt import RESEARCH_INSTRUCTIONS
from .company_fit import load_company_profile
from .facts import compact, deadline_expired, normalize_url
from .fetcher import build_opportunity, fetch_candidates
from .models import OpportunityBatch, is_unverified
from .project_memory import apply_history, load_memory, needs_report, recent_memory, split_reusable, notice_version
from .providers.base import ProviderError
from .rules import HARD_EXCLUDES, EDITORIAL_EXCLUDES, candidate_text, is_too_old, relevance, exclusion_terms
from .runtime import UsageTracker, local_now, read_json, update_status
from .scoring import score_v2
from .search import create_search_provider
from .verifier import SourceDocument, SourceVerifier, source_tier

logger = logging.getLogger(__name__)


def prefilter(candidates, limit=40):
    """Only hard exclusions; weak keywords/unknown stages reach semantic research."""
    by_url = {}
    for candidate in candidates:
        url = normalize_url(candidate.url)
        text = candidate_text(candidate)
        if not url or exclusion_terms(text, (*HARD_EXCLUDES, *EDITORIAL_EXCLUDES)):
            continue
        if candidate.published_ts and time.time() - candidate.published_ts > 365 * 86400:
            continue
        candidate.url = url
        old = by_url.get(url)
        if old is None or len(candidate.content) > len(old.content):
            by_url[url] = candidate
    def weight(candidate):
        score = relevance(candidate)[0]
        if candidate.discovery_method == "ai_search":
            score += 20
        if candidate.published_ts and time.time() - candidate.published_ts <= 15 * 86400:
            score += 15
        return score
    ranked = sorted(by_url.values(), key=weight, reverse=True)
    if len(ranked) <= limit:
        return ranked
    broad = [candidate for candidate in ranked if relevance(candidate)[0] < 38]
    quota = min(len(broad), max(1, limit // 4))
    selected = ranked[:limit - quota]
    for candidate in broad[:quota] + ranked:
        if candidate not in selected:
            selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def research_selection(candidates, limit):
    # Reserve a third of research slots for real projects with less familiar wording.
    broad = [candidate for candidate in candidates if relevance(candidate)[0] < 38]
    quota = min(len(broad), max(1, limit // 3))
    chosen = candidates[:max(limit - quota, 0)]
    for candidate in broad[:quota] + candidates:
        if candidate not in chosen:
            chosen.append(candidate)
        if len(chosen) >= limit:
            break
    return chosen[:limit]


def rank_opportunities(items, profile, limit, config=None):
    now = local_now()
    latest = {}
    stage_order = {"UNKNOWN": 0, "EARLY_SIGNAL": 1, "PROCUREMENT": 2, "AWARD": 3, "CLOSED": 4}
    def version(item):
        return item.published_ts, item.source_score, stage_order[item.stage]
    for item in items:
        old = latest.get(item.project_key)
        if old is None or version(item) > version(old):
            latest[item.project_key] = item
    by_key = {}
    for item in latest.values():
        if item.published_ts > now.timestamp() + 86400:
            continue
        if item.stage in {"CLOSED", "AWARD"} or deadline_expired(item.deadline, now) or is_too_old(item.stage, item.published_ts, now.timestamp()):
            continue
        if item.stage != "EARLY_SIGNAL" and item.published_ts and now.timestamp() - item.published_ts > 15 * 86400 and not item.deadline:
            continue
        if item.relevance_score < 38:
            continue
        if is_unverified(item):
            item.research_mode = "preview"
            item.verification_status = "preview"
            item.official_source_url = ""
        if item.research_mode != "ai":
            tier = 3 if item.research_mode == "preview" else source_tier(item.source_url, config or {})
            item.source_tier = tier
            item.source_score = {1: 100., 2: 75., 3: 40.}[tier]
            if tier == 1 and item.research_mode != "preview":
                item.official_source_url = item.source_url
            elif "official_source_not_found" not in item.risk_flags:
                item.risk_flags.append("official_source_not_found")
            score_v2(item, profile, now_ts=now.timestamp())
        if item.research_mode == "preview":
            item.final_score = min(item.final_score, 64.0)
            item.priority = "WATCH"
        if item.priority == "DROP":
            continue
        if not item.published_ts and not item.deadline:
            # Without a source date or deadline, recency cannot be asserted.
            item.final_score = min(item.final_score, 64.0)
            item.priority = "WATCH"
            if "published_date_not_verified" not in item.risk_flags:
                item.risk_flags.append("published_date_not_verified")
        old = by_key.get(item.project_key)
        if not old or (item.source_score, item.final_score) > (old.source_score, old.final_score):
            by_key[item.project_key] = item
    # The page and WeCom share this order: newly reportable items precede
    # already-reported projects retained for continued reading.
    return sorted(by_key.values(), key=lambda item: (not needs_report(item), -item.final_score))[:limit]


def _feedback(config_dir):
    data = read_json(config_dir / "opportunity_feedback.json", [])
    rows = data.get("decisions", []) if isinstance(data, dict) else data
    return rows[-50:] if isinstance(rows, list) else []


def can_rule_fallback(candidate):
    is_search = (candidate.discovery_method == "ai_search" or candidate.snippet_origin
                 or candidate.source_name.startswith("搜索发现"))
    return not is_search or bool(candidate.verified_url and candidate.content
                                 and compact(candidate.title) in compact(candidate.content))


def _rules_fallback(candidates, profile, config, limit, output_dir=None):
    eligible = [candidate for candidate in candidates if can_rule_fallback(candidate)]
    items = [item for candidate in eligible if (item := build_opportunity(candidate, include_inactive=True))]
    for item in items:
        item.research_mode = "fallback"
        item.risk_flags.append("ai_unavailable")
    if output_dir is not None:
        apply_history(items, load_memory(output_dir))
    return OpportunityBatch(rank_opportunities(items, profile, limit, config), observations=items)


async def research_candidate(candidate, provider, search, verifier, profile, feedback):
    from .models import OpportunityCandidate
    sources = [candidate]
    try:
        sources.extend(await search.find_sources(candidate))
    except Exception:
        pass  # Search failure still allows analysis of the original page.
    metadata = [row for source in sources for row in source.search_sources]
    for row in metadata:
        url = normalize_url(row.get("url", ""))
        if url:
            sources.append(OpportunityCandidate(row.get("title") or candidate.title, url,
                summary=row.get("snippet", ""), snippet_origin=row.get("origin", ""), discovery_method="ai_search"))
    # Put official domains first so a search result list full of reposts cannot
    # crowd the original procurement notice out of the small fetch budget.
    sources = sorted(enumerate(sources), key=lambda pair: (
        source_tier(pair[1].url, verifier.config),
        0 if normalize_url(pair[1].url) == normalize_url(candidate.url) else 1,
        pair[0],
    ))
    sources = [source for _, source in sources]
    urls = list(dict.fromkeys(source.url for source in sources))[:4]
    selected_urls = {normalize_url(url) for url in urls}
    fetched = await asyncio.gather(*(verifier.fetch(url) for url in urls))
    fetched_urls = {normalize_url(url) for url, doc in zip(urls, fetched) if doc}
    documents = [doc for doc in fetched if doc]
    documents.extend(await verifier.fetch_attachments(list(documents)))
    # Search snippets are usable evidence when a site blocks direct retrieval.
    # Keep their provenance explicit; the verifier will label them as previews.
    for source in sources:
        url = normalize_url(source.url)
        excerpt = (source.summary or source.content or "").strip()
        if url not in selected_urls or url in fetched_urls or len(excerpt) < 50:
            continue
        if source.snippet_origin not in {"glm_web_search", "existing_search", "openai_web_search"}:
            continue
        text = f"{source.title}\n{excerpt}"[:3000]
        documents.append(SourceDocument(url, text, source.title, "search_excerpt",
                                        risk_flags=verifier.failures.get(url, [])))
    if not documents:
        return None
    if candidate.discovery_method == "ai_search":
        matching = [doc for doc in documents if doc.retrieval_method in {"html", "pdf"} and compact(candidate.title) in compact(doc.text)]
        if matching:
            matching.sort(key=lambda doc: source_tier(doc.url, verifier.config))
            candidate.content = matching[0].text
            candidate.summary = matching[0].text[:260]
            candidate.verified_url = matching[0].url
            from .facts import content_signature
            candidate.content_hash = content_signature(matching[0].text)
            candidate.published = ""
            candidate.published_ts = 0  # Model discovery dates are not verified facts.
    payload = {
        "candidate": asdict(candidate), "company_profile": profile, "recent_feedback": feedback,
        "sources": [{"url": doc.url, "title": doc.title, "retrieval_method": doc.retrieval_method,
                     "text": doc.research_text()} for doc in documents],
        "today": local_now().date().isoformat(),
    }
    research = await provider.research(RESEARCH_INSTRUCTIONS, json.dumps(payload, ensure_ascii=False))
    if not research.is_real_project or research.warehouse_relevance < 60:
        return False  # Semantic rejection must not be reintroduced by rule fallback.
    item = verifier.verify(candidate, research, documents)
    if item:
        item.provider = provider.name
        item.last_researched_at = local_now().timestamp()
        item.notice_version = notice_version(item.title, item.published, item.source_url)
        item.source_content_hash = candidate.content_hash if not any(doc.attachment_urls for doc in documents) else ""
        score_v2(item, profile, " ".join(doc.text for doc in documents if normalize_url(doc.url) in {normalize_url(url) for url in item.source_urls}))
        if item.research_mode == "preview":
            item.final_score = min(item.final_score, 64.0)
            item.priority = "WATCH"
    return item


async def research_opportunities(config, settings, *, candidates=None, provider=None, search=None, verifier=None):
    started = time.monotonic()
    usage = provider.usage if provider else UsageTracker(settings)
    profile = load_company_profile(settings.config_dir)
    memory = load_memory(settings.output_dir)
    feedback = _feedback(settings.config_dir)
    update_status(settings, "DISCOVERING", last_run=local_now().isoformat(), mode="ai",
                  fallback_reason="", last_count=0, last_ai_candidates=0, last_researched=0,
                  last_verified=0, last_preview=0, last_web_search_calls=0, last_llm_calls=0, cached_fallback=False, error="")
    try:
        if candidates is None:
            candidates = await fetch_candidates(config, usage=usage)
        if not settings.ai_enabled:
            raise ProviderError("ai_disabled")
        if provider is None:
            try:
                provider = create_llm_provider(settings, usage)
            except ProviderError:
                if not settings.fallback_provider:
                    raise
                provider = create_llm_provider(settings, usage, settings.fallback_provider)
        search = search or create_search_provider(settings, usage, provider, config.get("queries", []))
        prompt = json.dumps({"today": local_now().date().isoformat(), "company_profile": profile,
                             "recent_feedback": feedback,
                             "recent_projects": recent_memory(memory, local_now().timestamp(), settings.memory_days, settings.memory_limit),
                             "known_candidates": [asdict(item) for item in candidates[:20]]}, ensure_ascii=False)
        ai_candidates = []
        search_failed = False
        try:
            ai_candidates = await search.discover(prompt)
        except Exception:
            search_failed = True
        merged = candidates + ai_candidates
        update_status(settings, "PREFILTERING", raw_candidates=len(merged), last_ai_candidates=len(ai_candidates), search_failed=search_failed)
        pool = prefilter(merged, max(len(merged), settings.candidate_limit))
        pending, reused = split_reusable(pool, memory, local_now().timestamp(), settings.memory_recheck_hours)
        filtered = prefilter(pending, settings.candidate_limit)
        for item in reused:
            text = " ".join(source.content for source in pool if source.verified_url == item.source_url)
            score_v2(item, profile, text, now_ts=local_now().timestamp())
        selected = research_selection(filtered, settings.research_limit)
        update_status(settings, "RESEARCHING", prefiltered=len(filtered), last_reused=len(reused), last_researched=len(selected), provider_used=provider.name)
        verifier = verifier or SourceVerifier(config)
        semaphore = asyncio.Semaphore(settings.concurrency)
        errors = []
        async def research_one(candidate):
            async with semaphore:
                try:
                    return await research_candidate(candidate, provider, search, verifier, profile, feedback)
                except Exception as exc:
                    errors.append(type(exc).__name__)
                    logger.warning("[OpportunityAI] research failed (%s)", type(exc).__name__)
                    if settings.fallback_provider and settings.fallback_provider != provider.name:
                        try:
                            alternative = create_llm_provider(settings, usage, settings.fallback_provider)
                            return await research_candidate(candidate, alternative, search, verifier, profile, feedback)
                        except Exception:
                            pass
                    return None
        results = await asyncio.gather(*(research_one(candidate) for candidate in selected))
        update_status(settings, "VERIFYING", last_verified=sum(getattr(item, "research_mode", "") == "ai" for item in results),
                      last_preview=sum(getattr(item, "research_mode", "") == "preview" for item in results),
                      failed_research=len(errors))
        items = list(reused)
        for candidate, result in zip(selected, results):
            if result is False:
                continue
            if result is None:
                allowed_fallback = can_rule_fallback(candidate)
                fallback = build_opportunity(candidate, include_inactive=True) if allowed_fallback else None
                if fallback:
                    fallback.research_mode = "fallback"
                    fallback.risk_flags.append("research_unavailable")
                    items.append(fallback)
            else:
                items.append(result)
        ai_succeeded = any(result is not None for result in results)
        has_ai = any(item.research_mode == "ai" for item in items)
        has_preview = any(item.research_mode == "preview" for item in items)
        has_fallback = any(item.research_mode == "fallback" for item in items)
        normal_empty = not selected and not search_failed
        mode = ("mixed" if (has_fallback and (has_ai or ai_succeeded)) or (has_ai and has_preview)
                else "preview" if has_preview else "ai" if has_ai or ai_succeeded or normal_empty else "fallback")
        if not ai_succeeded and not normal_empty and not items:
            # Only wholly unavailable AI falls back to all candidates; explicit
            # semantic rejections remain excluded.
            rejected_urls = {candidate.url for candidate, result in zip(selected, results) if result is False}
            items = _rules_fallback([candidate for candidate in merged if candidate.url not in rejected_urls], profile, config, settings.display_limit, settings.output_dir)
        update_status(settings, "RANKING", mode=mode,
                      fallback_reason="source_pages_unavailable" if mode == "preview" else "research_unavailable" if mode == "fallback" else "")
        observations = getattr(items, "observations", items)
        apply_history(observations, memory)
        ranked = rank_opportunities(observations, profile, settings.display_limit, config)
        rejected_keys = {row.get("project_key") for row in feedback if row.get("decision") in {"reject", "irrelevant"}}
        ranked = [item for item in ranked if item.project_key not in rejected_keys]
        update_status(settings, "RANKING", last_count=len(ranked), duration_seconds=round(time.monotonic() - started, 2))
        logger.info("[OpportunityAI] raw=%d prefilter=%d discovered=%d research=%d verified=%d final=%d duration=%.1fs",
                    len(merged), len(filtered), len(ai_candidates), len(selected), sum(bool(item) for item in results), len(ranked), time.monotonic() - started)
        return OpportunityBatch(ranked, observations=observations)
    except ProviderError as exc:
        batch = _rules_fallback(candidates, profile, config, settings.display_limit, settings.output_dir)
        rejected_keys = {row.get("project_key") for row in feedback if row.get("decision") in {"reject", "irrelevant"}}
        ranked = [item for item in batch if item.project_key not in rejected_keys]
        update_status(settings, "RANKING", mode="fallback", fallback_reason=str(exc), last_count=len(ranked),
                      duration_seconds=round(time.monotonic() - started, 2))
        return OpportunityBatch(ranked, observations=batch.observations)
    finally:
        usage.save()
        current = read_json(settings.runtime_dir / "opportunity_ai_status.json")
        update_status(settings, current.get("stage", "RANKING"), last_web_search_calls=usage.search_calls, last_llm_calls=usage.calls, **usage.search_summary())
