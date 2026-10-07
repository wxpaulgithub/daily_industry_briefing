import time


TRUSTED_SOURCE_HINTS = ("政府采购", "公共资源", "招标投标公共服务", "官网")


def source_score(source_name: str, url: str) -> float:
    text = f"{source_name} {url}".lower()
    if any(x.lower() in text for x in TRUSTED_SOURCE_HINTS) or any(x in text for x in (".gov.cn", "ggzy", "ccgp")):
        return 100.0
    if "招投标" in text or "采购" in text:
        return 75.0
    return 50.0


def urgency_score(stage: str, published_ts: float, deadline: str = "") -> float:
    by_stage = {"PROCUREMENT": 100, "EARLY_SIGNAL": 75, "AWARD": 35, "UNKNOWN": 25, "CLOSED": 0}
    score = float(by_stage.get(stage, 25))
    if published_ts:
        age_days = max((time.time() - published_ts) / 86400, 0)
        score = max(score - min(age_days * 2, 30), 0)
    if deadline:
        score = min(score + 5, 100)
    return round(score, 1)


def final_score(relevance_value: float, urgency_value: float, source_value: float) -> float:
    return round(relevance_value * 0.5 + urgency_value * 0.3 + source_value * 0.2, 1)


def freshness_score(published_ts: float, stage: str, now_ts: float | None = None) -> float:
    if not published_ts:
        return 30.0
    age = max(((now_ts or time.time()) - published_ts) / 86400, 0)
    for days, score in ((1, 100), (3, 90), (7, 75), (15, 50)):
        if age <= days:
            return float(score)
    return 35.0 if stage == "EARLY_SIGNAL" and age <= 90 else 10.0


def score_v2(item, profile: dict, verified_text: str = "", now_ts: float | None = None):
    from .company_fit import company_fit
    from .facts import budget_amount
    item.budget_amount = budget_amount(item.budget)
    item.company_fit_score = company_fit(item, profile, verified_text)
    item.freshness_score = freshness_score(item.published_ts, item.stage, now_ts)
    item.urgency_score = float({"PROCUREMENT": 100, "EARLY_SIGNAL": 85, "AWARD": 25, "CLOSED": 0, "UNKNOWN": 30}[item.stage])
    item.final_score = round(item.relevance_score * .30 + item.urgency_score * .20
                             + item.freshness_score * .15 + item.source_score * .10
                             + item.company_fit_score * .25, 1)
    item.priority = "A" if item.final_score >= 80 else "B" if item.final_score >= 65 else "WATCH" if item.final_score >= 55 else "DROP"
    return item
