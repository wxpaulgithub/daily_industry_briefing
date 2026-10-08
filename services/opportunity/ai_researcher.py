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
from .rules import HARD_EXCLUDES, EDITORIAL_EXCLUDES, candidate_text, is_too_old, relevance, exclusion_terms, candidate_stage
from .runtime import UsageTracker, local_now, read_json, update_status, write_json
from .scoring import score_v2
from .search import create_search_provider
from .verifier import SourceDocument, SourceVerifier, source_tier

logger = logging.getLogger(__name__)


def prefilter(candidates, limit=60, config=None, trace=None):
    """Only hard exclusions; weak keywords/unknown stages reach semantic research."""
    by_url = {}
    for candidate in candidates:
        url = normalize_url(candidate.url)
        text = candidate_text(candidate)
        if not url or exclusion_terms(text, (*HARD_EXCLUDES, *EDITORIAL_EXCLUDES)):
            if trace is not None and url in trace:
                trace[url].update(pipeline_stage="prefilter", drop_reason="hard_exclusion")
            continue
        from .freshness_guard import candidate_title_risk, VERSION_TERMS
        if candidate_title_risk(candidate):
            candidate.freshness_flags = list(dict.fromkeys(candidate.freshness_flags + ["historical_title_year"]))
            if not any(term in candidate.title for term in VERSION_TERMS):
                if trace is not None and url in trace:
                    trace[url].update(pipeline_stage="prefilter", drop_reason="historical_title_year")
                continue
        if candidate.published_ts and time.time() - candidate.published_ts > 365 * 86400:
            if trace is not None and url in trace:
                trace[url].update(pipeline_stage="prefilter", drop_reason="old_publication")
            continue
        stage = candidate_stage(candidate)
        # Publication hints screen stale leads, but never become verified facts.
        if stage in {"PROCUREMENT", "EARLY_SIGNAL"} and is_too_old(stage, candidate.published_ts):
            from .enrichment import extract_deadline
            deadline = extract_deadline(text)
            if not deadline or deadline_expired(deadline, local_now()):
                if trace is not None and url in trace:
                    trace[url].update(pipeline_stage="prefilter", drop_reason="stale_publication")
                continue
        candidate.url = url
        old = by_url.get(url)
        if old:
            channels = list(dict.fromkeys(old.discovery_channels + candidate.discovery_channels))
            old.discovery_channels = candidate.discovery_channels = channels
        if old is None or len(candidate.content) > len(old.content):
            by_url[url] = candidate
        if trace is not None and url in trace:
            trace[url].update(pipeline_stage="prefilter", drop_reason="", channels=by_url[url].discovery_channels)
    def weight(candidate):
        score = relevance(candidate)[0]
        if candidate.discovery_method == "ai_search":
            score += 10
        if candidate.published_ts and time.time() - candidate.published_ts <= 15 * 86400:
            score += 15
        score += {1: 25, 2: 12, 3: 0}[source_tier(candidate.verified_url or candidate.url, config or {})]
        if candidate.content and candidate.verified_url:
            score += 10
        return score
    ranked = sorted(by_url.values(), key=weight, reverse=True)
    if len(ranked) <= limit:
        return ranked
    broad = [candidate for candidate in ranked if relevance(candidate)[0] < 38]
    quota = min(len(broad), max(1, limit // 4))
    representatives = []
    seen_channels = set()
    for candidate in ranked:
        if set(candidate.discovery_channels) - seen_channels:
            representatives.append(candidate)
            seen_channels.update(candidate.discovery_channels)
    selected = []
    for candidate in representatives + ranked[:max(0, limit - quota - len(representatives))]:
        if candidate not in selected and len(selected) < limit - quota:
            selected.append(candidate)
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


def rank_opportunities(items, profile, limit, config=None, decisions=None):
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
        from .freshness_guard import stale_title_risk
        if stale_title_risk(item.title, item.published_ts, item.deadline, now):
            item.risk_flags = list(dict.fromkeys(item.risk_flags + ["historical_title_year"]))
            if decisions is not None:
                decisions[item.project_key] = "freshness_reject"
            continue
        if item.published_ts > now.timestamp() + 86400:
            if decisions is not None:
                decisions[item.project_key] = "future_publication"
            continue
        stale = is_too_old(item.stage, item.published_ts, now.timestamp())
        if item.stage == "PROCUREMENT" and item.deadline and not deadline_expired(item.deadline, now):
            stale = False
        if item.stage in {"CLOSED", "AWARD"} or deadline_expired(item.deadline, now) or stale:
            if decisions is not None:
                decisions[item.project_key] = "freshness_or_inactive_reject"
            continue
        if item.stage != "EARLY_SIGNAL" and item.published_ts and now.timestamp() - item.published_ts > 15 * 86400 and not item.deadline:
            if decisions is not None:
                decisions[item.project_key] = "old_without_deadline"
            continue
        if item.relevance_score < 38:
            if decisions is not None:
                decisions[item.project_key] = "low_relevance"
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
            if decisions is not None:
                decisions[item.project_key] = "low_priority"
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
    ranked = sorted(by_key.values(), key=lambda item: (not needs_report(item), -item.final_score))
    if decisions is not None:
        for item in ranked[limit:]:
            decisions[item.project_key] = "ranking_limit"
    return ranked[:limit]


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


async def research_candidate(candidate, provider, search, verifier, profile, feedback, resolver=None):
    from .models import OpportunityCandidate
    if resolver is not None:
        documents = await resolver.resolve(candidate)
    else:
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
    if item is None and resolver is not None:
        resolver.traces.setdefault(candidate.url, {})["verification_rejected"] = True
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
                  fallback_reason="", last_count=0, last_ai_candidates=0, last_researched=0, last_research_completed=0,
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
        from .search.coverage import CHANNELS
        coverage = getattr(search, "coverage_report", {})
        fixed_statuses = [coverage[key].get("status") for key in CHANNELS if key in coverage]
        bad_statuses = {"failed", "budget_exhausted", "not_searched"}
        discovery_status = ("failed" if search_failed or (fixed_statuses and all(status in bad_statuses for status in fixed_statuses))
                            else "partial" if any(status in bad_statuses for status in fixed_statuses) else "completed")
        search_failed = discovery_status == "failed"
        merged = candidates + ai_candidates
        update_status(settings, "PREFILTERING", raw_candidates=len(merged), last_ai_candidates=len(ai_candidates),
                      search_failed=search_failed, discovery_status=discovery_status, discovery_coverage=coverage)
        pipeline = {normalize_url(row.url): {"title": row.title, "url": normalize_url(row.url),
                    "channels": row.discovery_channels, "pipeline_stage": "discovery", "drop_reason": ""}
                    for row in merged if normalize_url(row.url)}
        pool = prefilter(merged, max(len(merged), settings.candidate_limit), config, pipeline)
        pending, reused = split_reusable(pool, memory, local_now().timestamp(), settings.memory_recheck_hours)
        filtered = prefilter(pending, settings.candidate_limit, config, pipeline)
        filtered_urls = {row.url for row in filtered}
        for row in pending:
            if row.url in pipeline and row.url not in filtered_urls and not pipeline[row.url]["drop_reason"]:
                pipeline[row.url].update(pipeline_stage="candidate_pool", drop_reason="candidate_pool_limit")
        for row in pool:
            if row.url not in {item.url for item in pending} and row.url in pipeline:
                pipeline[row.url].update(pipeline_stage="memory", drop_reason="reused_verified_snapshot")
        for item in reused:
            text = " ".join(source.content for source in pool if source.verified_url == item.source_url)
            score_v2(item, profile, text, now_ts=local_now().timestamp())
        from .triage import triage_candidates
        triage_rejected = []
        triage_mode = "disabled"
        queue = list(filtered)
        if settings.triage_enabled and filtered:
            update_status(settings, "TRIAGING", prefiltered=len(filtered))
            try:
                queue, triage_rejected = await triage_candidates(filtered, provider, profile)
                triage_mode = "ai"
            except Exception as exc:
                triage_mode = "rules_fallback"
                logger.warning("[OpportunityAI] triage unavailable (%s)", type(exc).__name__)
                queue = research_selection(filtered, len(filtered))
        for row in filtered:
            pipeline[row.url].update(pipeline_stage="triage", triage_score=row.triage_score,
                                    triage_reason=row.triage_reason,
                                    triage_accepted=row not in triage_rejected)
        for row in triage_rejected:
            pipeline[row.url]["drop_reason"] = "triage_reject"
        hard_limit = settings.research_hard_limit
        soft_limit = min(settings.research_limit, hard_limit)
        for row in queue[hard_limit:]:
            pipeline[row.url].update(pipeline_stage="research_queue", drop_reason="research_hard_limit")
        queue = queue[:hard_limit]
        selected, results = [], []
        update_status(settings, "RESEARCHING", prefiltered=len(filtered), last_reused=len(reused),
                      last_researched=len(queue), triage_mode=triage_mode, triage_accepted=len(queue),
                      provider_used=provider.name)
        verifier = verifier or SourceVerifier(config)
        semaphore = asyncio.Semaphore(settings.concurrency)
        errors = []
        diagnostics = []
        from .source_resolver import SourceResolver
        resolver = SourceResolver(search, verifier, settings, usage)
        trace = {"last_run": local_now().isoformat(),
            "discovered_candidates": [{"title": row.title, "url": row.url, "published": row.published}
                                      for row in ai_candidates],
            "research_candidates": [{"title": row.title, "url": row.url} for row in queue],
            "discovery_coverage": coverage, "discovery_status": discovery_status, "triage_mode": triage_mode,
            "pipeline": list(pipeline.values())}
        write_json(settings.runtime_dir / "opportunity_research_diagnostics.json", {
            **trace, "completed": 0, "planned": len(queue), "candidates": []})
        async def research_one(candidate):
            async with semaphore:
                result = None
                error_code = ""
                try:
                    result = await research_candidate(candidate, provider, search, verifier, profile, feedback, resolver)
                except Exception as exc:
                    error_code = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
                    errors.append(error_code)
                    logger.warning("[OpportunityAI] research failed (%s)", error_code)
                    if settings.fallback_provider and settings.fallback_provider != provider.name:
                        try:
                            alternative = create_llm_provider(settings, usage, settings.fallback_provider)
                            result = await research_candidate(candidate, alternative, search, verifier, profile, feedback, resolver)
                        except Exception:
                            pass
                outcome = ("semantic_rejection" if result is False else "unavailable" if result is None else result.research_mode)
                source_trace = resolver.traces.get(candidate.url, {})
                reason = ("semantic_reject" if result is False else
                          "verifier_reject" if source_trace.get("verification_rejected") else
                          "research_error" if error_code and result is None else
                          "source_unavailable" if result is None and not source_trace.get("raw_documents") else
                          "verifier_reject" if result is None else "")
                pipeline[candidate.url].update(pipeline_stage="research" if reason in {"semantic_reject", "research_error"} else "verifier", drop_reason=reason)
                diagnostics.append({"title": candidate.title, "url": candidate.url,
                    "publication_hint": candidate.published, "outcome": outcome, "error_code": error_code,
                    "project_key": getattr(result, "project_key", ""),
                    "risk_flags": getattr(result, "risk_flags", []),
                    "stage": getattr(result, "stage", ""), "published": getattr(result, "published", ""),
                    "deadline": getattr(result, "deadline", ""), "priority": getattr(result, "priority", ""),
                    "final_score": getattr(result, "final_score", None), "source_resolution": source_trace})
                update_status(settings, "RESEARCHING", last_research_completed=len(diagnostics),
                    last_web_search_calls=usage.search_calls, last_llm_calls=usage.calls,
                    **usage.search_summary())
                write_json(settings.runtime_dir / "opportunity_research_diagnostics.json", {
                    **trace, "completed": len(diagnostics),
                    "planned": len(queue), "candidates": diagnostics})
                logger.info("[OpportunityAI] research progress=%d/%d outcome=%s", len(diagnostics), len(selected), outcome)
                return result
        rejected_keys = {row.get("project_key") for row in feedback if row.get("decision") in {"reject", "irrelevant"}}
        def verified_count():
            active = rank_opportunities([item for item in results if item], profile, hard_limit, config)
            return sum(item.research_mode == "ai" and item.verification_status == "verified"
                       and item.priority in {"A", "B"} and bool(item.published_ts or item.deadline)
                       and item.project_key not in rejected_keys for item in active)
        stop_reason, wave_count = "queue_exhausted", 0
        for offset in range(0, len(queue), settings.research_wave_size):
            if verified_count() >= settings.verified_target:
                stop_reason = "verified_target_met"
                break
            if usage.calls >= settings.max_llm_calls:
                stop_reason = "llm_budget_exhausted"
                break
            remaining_time = settings.task_timeout - (time.monotonic() - started)
            if remaining_time <= 0 or (len(selected) >= soft_limit and remaining_time < settings.request_timeout):
                stop_reason = "task_time_budget"
                break
            wave = queue[offset:offset + settings.research_wave_size]
            selected.extend(wave)
            wave_count += 1
            update_status(settings, "RESEARCHING", research_wave=wave_count, research_stop_reason="", verified_target_count=verified_count())
            results.extend(await asyncio.gather(*(research_one(candidate) for candidate in wave)))
        if verified_count() >= settings.verified_target:
            stop_reason = "verified_target_met"
        elif len(selected) >= hard_limit:
            stop_reason = "research_hard_limit"
        for row in queue[len(selected):]:
            pipeline[row.url].update(pipeline_stage="research_queue", drop_reason=stop_reason)
        update_status(settings, "RESEARCHING", last_researched=len(selected), research_wave=wave_count,
                      research_stop_reason=stop_reason, verified_target_count=verified_count())
        update_status(settings, "VERIFYING", last_verified=sum(getattr(item, "research_mode", "") == "ai" for item in results),
                      last_preview=sum(getattr(item, "research_mode", "") == "preview" for item in results),
                      failed_research=len(errors))
        items = list(reused)
        for candidate, result in zip(selected, results):
            if result is False:
                continue
            if result is None:
                allowed_fallback = can_rule_fallback(candidate) and not resolver.traces.get(candidate.url, {}).get("verification_rejected")
                fallback = build_opportunity(candidate, include_inactive=True) if allowed_fallback else None
                if fallback:
                    fallback.research_mode = "fallback"
                    fallback.risk_flags.append("research_unavailable")
                    items.append(fallback)
            else:
                items.append(result)
        ai_succeeded = (any(result is not None for result in results)
                        or any(row.get("verification_rejected") for row in resolver.traces.values()))
        has_ai = any(item.research_mode == "ai" for item in items)
        has_preview = any(item.research_mode == "preview" for item in items)
        has_fallback = any(item.research_mode == "fallback" for item in items)
        normal_empty = (not queue or bool(reused)) and not search_failed
        mode = ("mixed" if (has_fallback and (has_ai or ai_succeeded)) or (has_ai and has_preview)
                else "preview" if has_preview else "ai" if has_ai or ai_succeeded or normal_empty else "fallback")
        if not ai_succeeded and not normal_empty and not items:
            # Only wholly unavailable AI falls back to all candidates; explicit
            # semantic rejections remain excluded.
            rejected_urls = {candidate.url for candidate, result in zip(selected, results) if result is False}
            rejected_urls.update(row.url for row in triage_rejected)
            rejected_urls.update(url for url, row in resolver.traces.items() if row.get("verification_rejected"))
            items = _rules_fallback([candidate for candidate in merged if candidate.url not in rejected_urls], profile, config, settings.display_limit, settings.output_dir)
        update_status(settings, "RANKING", mode=mode,
                      fallback_reason="discovery_search_unavailable" if discovery_status == "failed" else "source_pages_unavailable" if mode == "preview" else "research_unavailable" if mode == "fallback" else "")
        observations = getattr(items, "observations", items)
        apply_history(observations, memory)
        ranking_decisions = {}
        ranked = rank_opportunities(observations, profile, settings.display_limit, config, ranking_decisions)
        rejected_keys = {row.get("project_key") for row in feedback if row.get("decision") in {"reject", "irrelevant"}}
        ranked = [item for item in ranked if item.project_key not in rejected_keys]
        update_status(settings, "RANKING", last_count=len(ranked), duration_seconds=round(time.monotonic() - started, 2))
        retained = {item.project_key for item in ranked}
        for row in diagnostics:
            row["retained"] = bool(row["project_key"] and row["project_key"] in retained)
            record = pipeline[row["url"]]
            if row["retained"]:
                record.update(pipeline_stage="retained", drop_reason="")
            elif row["project_key"]:
                record.update(pipeline_stage="ranking", drop_reason="feedback_reject" if row["project_key"] in rejected_keys
                              else ranking_decisions.get(row["project_key"], "ranking_duplicate_or_reject"))
            row.update(pipeline_stage=record["pipeline_stage"], drop_reason=record["drop_reason"])
        for key, coverage in trace["discovery_coverage"].items():
            rows = [row for row in pipeline.values() if key in row["channels"]]
            coverage["candidate_pool"] = sum(row["pipeline_stage"] not in {"prefilter", "candidate_pool"} for row in rows)
            coverage["triage_accepted"] = sum(row.get("triage_accepted", False) for row in rows)
            coverage["researched"] = sum(row["url"] in {item.url for item in selected} for row in rows)
            coverage["retained"] = sum(row["pipeline_stage"] == "retained" for row in rows)
        write_json(settings.runtime_dir / "opportunity_research_diagnostics.json", {
            **trace, "completed": len(diagnostics),
            "planned": len(queue), "candidates": diagnostics,
            "final_count": len(ranked), "research_waves": wave_count, "research_stop_reason": stop_reason,
            "pipeline": list(pipeline.values()), "drop_reason_counts": {reason: sum(row["drop_reason"] == reason for row in pipeline.values())
                for reason in {row["drop_reason"] for row in pipeline.values()} if reason},
            "source_failures": getattr(verifier, "failures", {})})
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
