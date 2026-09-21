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
