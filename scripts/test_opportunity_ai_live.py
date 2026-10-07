"""Explicit, paid AI smoke test/A-B evaluation. Never sends WeCom messages."""
import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.opportunity.ai_client import create_llm_provider
from services.opportunity.ai_researcher import research_opportunities
from services.opportunity.fetcher import load_config
from services.opportunity.models import OpportunityCandidate
from services.opportunity.runtime import UsageTracker, local_now, read_json, write_json
from services.opportunity.settings import OpportunitySettings
from services.opportunity.verifier import SourceVerifier


class FixedSearch:
    """Fair comparisons use identical candidate sources, not independent discovery."""
    async def discover(self, prompt):
        return []

    async def find_sources(self, candidate):
        return []


def quality_metrics(cases, items):
    by_url = {item.discovery_url or item.source_url: item for item in items}
    labeled = [case for case in cases if "expected_relevant" in case]
    positives = [case for case in labeled if case["expected_relevant"]]
    predictions = [case for case in labeled if case["url"] in by_url]
    true_positives = sum(bool(case["expected_relevant"]) for case in predictions)
    expected_top = {case["url"] for case in cases if case.get("expected_top5")}
    stage_cases = [case for case in cases if case.get("expected_stage") and case["url"] in by_url]
    return {
        "labeled_cases": len(labeled),
        "precision": true_positives / len(predictions) if predictions else None,
        "recall": true_positives / len(positives) if positives else None,
        "stage_accuracy": sum(by_url[case["url"]].stage == case["expected_stage"] for case in stage_cases) / len(stage_cases) if stage_cases else None,
        "official_source_rate": sum(bool(item.official_source_url) for item in items) / len(items) if items else None,
        "top5_overlap": len(expected_top & {item.discovery_url or item.source_url for item in items[:5]}) / len(expected_top) if expected_top else None,
        "fact_accuracy": {
            field: (
                sum(getattr(by_url[case["url"]], field) == case["expected_" + field] for case in cases if "expected_" + field in case and case["url"] in by_url)
                / sum("expected_" + field in case and case["url"] in by_url for case in cases)
                if any("expected_" + field in case and case["url"] in by_url for case in cases) else None
            ) for field in ("budget", "owner", "deadline")
        },
    }


async def run(args):
    settings = OpportunitySettings.from_env()
    config = load_config()
    names = ["openai", "glm"] if args.compare else [args.provider or settings.provider]
    for name in names:
        if not settings.configured(name):
            raise SystemExit(f"{name} 未配置 API Key 和商机模型名。")
    cases = []
    candidates = None
    if args.candidates:
        data = json.loads(args.candidates.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("dataset_type") == "synthetic_regression":
            raise SystemExit("模拟回归样本不能用于真实 API 质量验收；请提供真实公告 URL 和人工标注。")
        cases = data if isinstance(data, list) else data.get("cases", [])
        cases = cases[:50]
        allowed = OpportunityCandidate.__dataclass_fields__
        candidates = [OpportunityCandidate(**{key: value for key, value in case.items() if key in allowed}) for case in cases]
    if args.compare and not candidates:
        raise SystemExit("--compare 需要 --candidates，以保证两个供应商分析同一批项目。")
    report = {}
    verifier = SourceVerifier(config)
    stamp = local_now().strftime("%Y%m%d-%H%M%S")
    for name in names:
        provider_settings = replace(settings, provider=name, fallback_provider="", auto_publish=False,
            runtime_dir=settings.runtime_dir / "evaluations" / f"{stamp}-{name}",
            output_dir=settings.runtime_dir / "evaluations" / f"{stamp}-{name}" / "snapshots",
            research_limit=len(candidates) if candidates else args.limit,
            candidate_limit=max(len(candidates or []), settings.candidate_limit),
            display_limit=max(len(candidates or []), settings.display_limit),
            max_llm_calls=max(2 * len(candidates or []), settings.max_llm_calls))
        tracker = UsageTracker(provider_settings)
        provider = create_llm_provider(provider_settings, tracker)
        started = time.monotonic()
        items = await research_opportunities(config, provider_settings, candidates=candidates,
            provider=provider, search=FixedSearch() if candidates else None, verifier=verifier)
        elapsed = round(time.monotonic() - started, 2)
        report[name] = {"duration_seconds": elapsed, "count": len(items), "metrics": quality_metrics(cases, items),
                        "usage": read_json(provider_settings.runtime_dir / "opportunity_usage.json"),
                        "items": [item.to_dict() for item in items]}
        print(f"{name}: {len(items)} 条，耗时 {elapsed}s，LLM 尝试 {tracker.calls} 次，搜索 {tracker.search_calls} 次")
    path = settings.runtime_dir / "evaluations" / f"comparison-{stamp}.json"
    write_json(path, report)
    print(f"报告：{path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="明确允许调用真实收费 API")
    parser.add_argument("--provider", choices=["openai", "glm"])
    parser.add_argument("--compare", action="store_true", help="同一批真实候选的 OpenAI/GLM A-B 比较")
    parser.add_argument("--candidates", type=Path, help="真实候选及人工标注 JSON，最多50条")
    parser.add_argument("--limit", type=int, default=3, choices=range(1, 31))
    args = parser.parse_args()
    if not args.live:
        parser.exit(message="未调用真实 API。需要联调时显式传入 --live；本脚本始终不发送企业微信。\n")
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(args))
