"""Save-first orchestration shared by scheduler, CLI and manual research."""
import asyncio
from dataclasses import replace

from .fetcher import fetch_opportunities
from .ai_researcher import rank_opportunities
from .company_fit import load_company_profile
from .models import OpportunityBatch
from .project_memory import apply_history, load_memory
from .publisher import publish_today
from .runtime import local_now, read_json, update_status
from .settings import OpportunitySettings
from .storage import load_snapshot, save_snapshot

_run_lock = asyncio.Lock()


async def run_opportunity_pipeline(settings=None, *, publish: bool | None = None):
    settings = settings or OpportunitySettings.from_env()
    if not settings.enabled:
        update_status(settings, "IDLE", mode="disabled")
        return []
    async with _run_lock:
        date_str = local_now().date().isoformat()
        try:
            try:
                items = await asyncio.wait_for(fetch_opportunities(settings=settings), settings.task_timeout)
            except asyncio.TimeoutError:
                # Retain durable data when research times out; if absent, run V1.
                items = load_snapshot(date_str, settings.output_dir)
                if not items:
                    items = await asyncio.wait_for(fetch_opportunities(settings=replace(settings, ai_enabled=False)), 90)
                observations = getattr(items, "observations", items)
                for item in observations:
                    item.research_mode = "fallback"
                    item.risk_flags = list(dict.fromkeys([*item.risk_flags, "task_timeout"]))
                apply_history(observations, load_memory(settings.output_dir))
                ranked = rank_opportunities(observations, load_company_profile(settings.config_dir), settings.display_limit)
                items = OpportunityBatch(ranked, observations)
                update_status(settings, "RANKING", mode="fallback", fallback_reason="task_timeout")
            if not items and read_json(settings.runtime_dir / "opportunity_ai_status.json").get("mode") == "fallback":
                cached = load_snapshot(date_str, settings.output_dir)
                if cached:
                    for item in cached:
                        item.research_mode = "fallback"
                        item.risk_flags = list(dict.fromkeys([*item.risk_flags, "cached_snapshot_fallback"]))
                    observed = [*getattr(items, "observations", []), *cached]
                    apply_history(observed, load_memory(settings.output_dir))
                    items = OpportunityBatch(rank_opportunities(observed, load_company_profile(settings.config_dir), settings.display_limit), observed)
                    update_status(settings, "RANKING", cached_fallback=True)
            update_status(settings, "SAVING")
            save_snapshot(items, date_str, settings.output_dir)
            publish_result = {"status": "gray_mode"}
            should_publish = settings.auto_publish if publish is None else publish
            if should_publish:
                update_status(settings, "PUBLISHING")
                try:
                    publish_result = await publish_today(settings, date_str=date_str)
                except Exception as exc:
                    publish_result = {"status": "failed", "reason": type(exc).__name__}
            saved = load_snapshot(date_str, settings.output_dir)
            update_status(settings, "DONE", last_success=local_now().isoformat(), last_count=len(saved),
                          last_publish_status=publish_result["status"])
            return saved
        except Exception as exc:
            update_status(settings, "FAILED", error=type(exc).__name__)
            raise
