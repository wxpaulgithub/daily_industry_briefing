"""Offline project-level evaluation; no provider or publisher calls."""
from collections import Counter
from datetime import datetime

from .facts import normalize_url, budget_amount, parse_date, compact
from .models import is_unverified
from .project_memory import material_hash, title_identity


def validate_dataset(data, require_frozen=False):
    if not isinstance(data, dict) or data.get("dataset_type") != "real_benchmark":
        raise ValueError("dataset_type must be real_benchmark; synthetic data is not a quality benchmark")
    datetime.strptime(data.get("evaluation_date", ""), "%Y-%m-%d")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("real benchmark needs non-empty human labeled cases")
    identities = set()
    for case in cases:
        if not isinstance(case.get("expected_relevant"), bool) or not case.get("human_reason"):
            raise ValueError("each case needs expected_relevant and human_reason")
        if not normalize_url(case.get("url", "")) or not case.get("title"):
            raise ValueError("each case needs a public URL and title")
        identity = case.get("project_id") or normalize_url(case["url"])
        if identity in identities:
            raise ValueError("duplicate labeled project identity")
        identities.add(identity)
        if require_frozen:
            docs = case.get("documents") or []
            if not docs or not any(doc.get("text") for doc in docs):
                raise ValueError("research requires frozen source documents; run --prepare --fetch-sources first")
            for doc in docs:
                if not normalize_url(doc.get("url", "")) or doc.get("retrieval_method", "html") not in {"html", "pdf"}:
                    raise ValueError("frozen sources require public URLs and original html/pdf text")
    return cases


def case_match(case, item):
    aliases = {normalize_url(url) for url in [case["url"], *case.get("url_aliases", [])]} - {""}
    item_urls = {normalize_url(url) for url in [item.discovery_url, item.source_url, *item.source_urls]} - {""}
    if aliases & item_urls:
        return True
    if case.get("project_key") == item.project_key:
        return True
    return (title_identity(case["title"]) == title_identity(item.title)
            and (not case.get("expected_owner") or compact(case["expected_owner"]) == compact(item.owner)))


def equivalent(field, actual, expected):
    if field == "budget":
        left, right = budget_amount(actual), budget_amount(expected)
        if left is not None and right is not None:
            return left == right
    if field == "deadline":
        left, right = parse_date(actual), parse_date(expected)
        if left and right:
            return left == right
    return compact(actual) == compact(expected)


def quality_metrics(cases, items):
    labeled = [case for case in cases if "expected_relevant" in case]
    official_items = [item for item in items if item.research_mode == "ai" and not is_unverified(item)]
    matched = {index: next((item for item in official_items if case_match(case, item)), None)
               for index, case in enumerate(labeled)}
    positive = [index for index, case in enumerate(labeled) if case["expected_relevant"]]
    predictions = [index for index, item in matched.items() if item is not None]
    tp = sum(labeled[index]["expected_relevant"] for index in predictions)
    errors = {key: [] for key in ("missed_high_value", "false_positive", "wrong_stage", "wrong_facts",
                                  "bad_ranking", "official_source_missing")}
    for index, case in enumerate(labeled):
        item = matched[index]
        label = case.get("project_id") or case["url"]
        if case["expected_relevant"] and item is None:
            errors["missed_high_value"].append(label)
        elif item and not case["expected_relevant"]:
            errors["false_positive"].append(label)
        if item and case.get("expected_stage") and item.stage != case["expected_stage"]:
            errors["wrong_stage"].append(label)
        if item and not item.official_source_url:
            errors["official_source_missing"].append(label)
    facts, completeness, denominators = {}, {}, {}
    for field in ("budget", "owner", "deadline"):
        indexes = [i for i in positive if labeled[i].get("expected_" + field)]
        available = [i for i in indexes if matched[i] and getattr(matched[i], field)]
        correct = sum(equivalent(field, getattr(matched[i], field), labeled[i]["expected_" + field]) for i in available)
        facts[field] = correct / len(available) if available else None
        completeness[field] = len(available) / len(indexes) if indexes else None
        denominators[field] = {"expected": len(indexes), "available": len(available), "correct": correct,
                               "missing": len(indexes) - len(available)}
        for i in available:
            if not equivalent(field, getattr(matched[i], field), labeled[i]["expected_" + field]):
                errors["wrong_facts"].append({"project": labeled[i].get("project_id") or labeled[i]["url"], "field": field})
    stages = [i for i in positive if matched[i] and labeled[i].get("expected_stage")]
    top = [case for case in labeled if case.get("expected_top5")]
    overlap = sum(any(case_match(case, item) for item in official_items[:5]) for case in top)
    errors["bad_ranking"] = [case.get("project_id") or case["url"] for case in top
                              if not any(case_match(case, item) for item in official_items[:5])]
    unlabelled = [item.source_url for item in official_items if not any(case_match(case, item) for case in labeled)]
    counts = Counter(item.project_key for item in official_items)
    return {"labeled_cases": len(labeled), "verified_count": len(official_items), "matched_predictions": len(predictions),
            "precision": tp / len(predictions) if predictions else None,
            "recall": tp / len(positive) if positive else None,
            "stage_accuracy": sum(matched[i].stage == labeled[i]["expected_stage"] for i in stages) / len(stages) if stages else None,
            "stage_denominator": len(stages), "fact_accuracy": facts, "fact_completeness": completeness,
            "fact_denominators": denominators,
            "official_source_rate": sum(bool(item.official_source_url) for item in official_items) / len(official_items) if official_items else None,
            "duplicate_rate": sum(count - 1 for count in counts.values()) / len(official_items) if official_items else None,
            "top5_overlap": overlap / len(top) if top else None,
            "top20_sample_sufficient": len(official_items) >= 20,
            "preview_count": sum(is_unverified(item) for item in items),
            "fallback_count": sum(item.research_mode == "fallback" and not is_unverified(item) for item in items),
            "unlabeled_predictions": unlabelled, "errors": errors,
            "human_company_fit_score": None, "human_cut_in_quality": None}


def longitudinal_metrics(snapshots):
    seen, repeated, total = set(), [], 0
    delivery_events, delivered_signatures, repeated_deliveries = set(), set(), []
    for date, items in sorted(snapshots):
        for item in items:
            total += 1
            signature = (item.project_key, material_hash(item))
            if signature in seen:
                repeated.append({"date": date, "project_key": item.project_key})
            seen.add(signature)
            if item.last_reported_at:
                event = (signature, item.last_reported_at)
                if event not in delivery_events:
                    if signature in delivered_signatures:
                        repeated_deliveries.append({"date": date, "project_key": item.project_key})
                    delivery_events.add(event)
                    delivered_signatures.add(signature)
    return {"rows": total, "unchanged_reappearances": repeated,
            "unchanged_reappearance_rate": len(repeated) / total if total else None,
            "recorded_delivery_events": len(delivery_events), "repeated_deliveries": repeated_deliveries,
            "recorded_delivery_duplicate_rate": len(repeated_deliveries) / len(delivery_events) if delivery_events else None,
            "scope": "reappearance includes retained reading items; delivery events use persisted acknowledgement timestamps"}
