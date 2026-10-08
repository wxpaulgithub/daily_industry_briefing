"""Isolated real benchmark and offline reporting; never sends WeCom."""
import argparse
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.opportunity.ai_client import create_llm_provider
from services.opportunity.ai_researcher import research_opportunities
from services.opportunity.evaluation import pipeline_metrics, quality_metrics, validate_dataset, longitudinal_metrics
from services.opportunity.facts import SHANGHAI, normalize_url
from services.opportunity.fetcher import load_config
from services.opportunity.models import OpportunityCandidate, ProjectOpportunity
from services.opportunity.runtime import UsageTracker, local_now, read_json, write_json
from services.opportunity.settings import OpportunitySettings
from services.opportunity.verifier import SourceDocument, SourceVerifier


class FixedSearch:
    async def discover(self, prompt):
        return []

    async def find_sources(self, candidate):
        return []


class FrozenVerifier(SourceVerifier):
    def __init__(self, config, cases):
        super().__init__(config)
        for case in cases:
            for row in case["documents"]:
                doc = SourceDocument(**{key: value for key, value in row.items() if key in SourceDocument.__dataclass_fields__})
                url = normalize_url(doc.url)
                if url in self.cache and self.cache[url].text != doc.text:
                    raise ValueError("conflicting frozen source versions")
                self.cache[url] = doc
            primary = next((self.cache[normalize_url(row["url"])] for row in case["documents"] if not row.get("parent_url")), None)
            if primary:
                self.cache.setdefault(normalize_url(case["url"]), primary)

    async def fetch(self, url):
        return self.cache.get(normalize_url(url))  # Missing originals never trigger network.

    async def fetch_attachments(self, documents):
        return [doc for doc in self.cache.values() if doc.parent_url in {parent.url for parent in documents}]


async def prepare(args, data):
    cases = validate_dataset(data)
    if args.fetch_sources:
        verifier = SourceVerifier(load_config())
        for case in cases:
            doc = await verifier.fetch(case["url"])
            docs = [doc] if doc else []
            docs.extend(await verifier.fetch_attachments(docs))
            case["documents"] = [asdict(doc) for doc in docs]
        data["captured_at"] = local_now().isoformat()
    validate_dataset(data, require_frozen=True)
    output = args.output or args.candidates.with_name(args.candidates.stem + "-frozen.json")
    write_json(output, data)
    print(f"Frozen benchmark: {output}; cases={len(cases)}")


async def run(args):
    settings = OpportunitySettings.from_env()
    data = json.loads(args.candidates.read_text(encoding="utf-8")) if args.candidates else None
    if args.prepare:
        if not data:
            raise ValueError("--prepare needs --candidates")
        await prepare(args, data)
        return
    if args.mode == "longitudinal":
        snapshots = [(path.stem, [ProjectOpportunity.from_dict(row) for row in read_json(path, [])])
                     for path in args.snapshots.glob("????-??-??.json")]
        output = args.output or settings.runtime_dir / "evaluations" / "longitudinal.json"
        write_json(output, longitudinal_metrics(snapshots))
        print(output)
        return
    cases = validate_dataset(data, require_frozen=args.mode == "research") if data else []
    if args.mode == "research" and not cases:
        raise ValueError("research needs a real frozen --candidates dataset")
    evaluation_date = data["evaluation_date"] if data else local_now().date().isoformat()
    if args.mode == "discovery" and evaluation_date != local_now().date().isoformat():
        raise ValueError("live discovery requires today's evaluation date")
    if args.results:
        report = quality_metrics(cases, [ProjectOpportunity.from_dict(row) for row in read_json(args.results, [])])
        write_json(args.output or args.results.with_name(args.results.stem + "-metrics.json"), report)
        return
    if not args.live:
        print("No paid API called. Use --prepare/--results offline, or explicit --live.")
        return
    names = ["openai", "glm"] if args.compare else [args.provider or settings.provider]
    for name in names:
        if not settings.configured(name):
            raise ValueError(f"{name}: API key and opportunity model required")
    config = load_config()
    report = {"mode": args.mode, "evaluation_date": evaluation_date,
              "recall_scope": "fixed candidate retention" if args.mode == "research" else "labeled pool coverage; not exhaustive discovery recall",
              "dataset_cases": len(cases), "providers": {}}
    stamp = local_now().strftime("%Y%m%d-%H%M%S-%f")
    for name in names:
        isolated = settings.runtime_dir / "evaluations" / f"{stamp}-{name}"
        count = len(cases) if cases else args.limit
        provider_settings = replace(settings, provider=name, fallback_provider="", auto_publish=False,
            runtime_dir=isolated, output_dir=isolated / "snapshots", research_limit=count,
            research_hard_limit=count, verified_target=count, triage_enabled=args.mode != "research",
            search_provider=getattr(args, "search_provider", None) or settings.search_provider,
            candidate_limit=max(count, settings.candidate_limit), display_limit=max(count, settings.display_limit),
            max_llm_calls=max(2 * count, settings.max_llm_calls))
        tracker = UsageTracker(provider_settings)
        provider = create_llm_provider(provider_settings, tracker)
        candidates = None
        if args.mode == "research":
            candidates = [OpportunityCandidate(**{key: value for key, value in deepcopy(case).items()
                         if key in OpportunityCandidate.__dataclass_fields__}) for case in cases]
        verifier = FrozenVerifier(config, cases) if candidates is not None else SourceVerifier(config)
        now = datetime.strptime(evaluation_date, "%Y-%m-%d").replace(hour=8, tzinfo=SHANGHAI)
        started = time.monotonic()
        with ExitStack() as stack:
            for module in ("ai_researcher", "publisher", "storage", "runtime"):
                stack.enter_context(patch(f"services.opportunity.{module}.local_now", return_value=now))
            stack.enter_context(patch("services.opportunity.rules.time.time", return_value=now.timestamp()))
            items = await research_opportunities(config, provider_settings, candidates=candidates,
                provider=provider, search=FixedSearch() if candidates is not None else None, verifier=verifier)
        report["providers"][name] = {"duration_seconds": round(time.monotonic()-started, 2),
            "count": len(items), "metrics": quality_metrics(cases, items),
            "pipeline_metrics": pipeline_metrics(cases, read_json(isolated / "opportunity_research_diagnostics.json")),
            "diagnostics": read_json(isolated / "opportunity_research_diagnostics.json"),
            "status": read_json(isolated / "opportunity_ai_status.json"),
            "usage": read_json(isolated / "opportunity_usage.json"), "items": [item.to_dict() for item in items],
            "limits": {"research": count, "discovery_search": settings.discovery_search_budget,
                       "verification_search": settings.verification_search_budget, "total_search": settings.total_search_budget}}
        print(f"{name}: {len(items)} items; llm={tracker.calls}; search={tracker.search_calls}")
    output = args.output or settings.runtime_dir / "evaluations" / f"comparison-{stamp}.json"
    write_json(output, report)
    print(f"Report: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--provider", choices=["openai", "glm"])
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--search-provider", choices=["auto", "openai", "glm", "existing"], help="Independent search provider; --provider selects the analysis model")
    parser.add_argument("--mode", choices=["research", "discovery", "longitudinal"], default=None)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--fetch-sources", action="store_true", help="Explicitly retrieve original pages; no paid API")
    parser.add_argument("--results", type=Path, help="Offline metrics on a saved array of results")
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=3, choices=range(1, 101))
    args = parser.parse_args()
    if args.mode is None:
        args.mode = "research" if args.candidates else "discovery"
    if args.mode == "longitudinal" and not args.snapshots:
        parser.error("longitudinal requires --snapshots")
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(run(args))
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")
