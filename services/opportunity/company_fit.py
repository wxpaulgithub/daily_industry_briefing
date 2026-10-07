"""Explainable Company Fit scoring using the configurable company profile."""
from .facts import budget_amount
from .runtime import read_json


def load_company_profile(config_dir):
    return read_json(config_dir / "company_profile.json")


def company_fit(item, profile: dict, verified_text: str = "") -> float:
    text = f"{item.title} {item.owner} {' '.join(item.technical_scope)} {verified_text}".upper()
    score = 10.0
    strengths = profile.get("strengths", [])
    core = ("堆垛机", "输送", "WMS", "WCS", "非标")
    if any(term in text and any(term in strength.upper() for strength in strengths) for term in core):
        score += 30
    if any(term.upper() in text for term in profile.get("preferred_customer_types", [])) or any(
        term in text for term in ("制造", "汽车零部件", "生产企业", "机械厂", "电子厂", "成品仓", "生产物流")
    ):
        score += 20
    amount = item.budget_amount if item.budget_amount is not None else budget_amount(item.budget)
    ideal_min = profile.get("ideal_project_budget_min", 500000)
    ideal_max = profile.get("ideal_project_budget_max", 5000000)
    accepted_max = profile.get("acceptable_project_budget_max", 10000000)
    if amount is not None:
        if ideal_min <= amount <= ideal_max:
            score += 15
        elif ideal_max < amount <= accepted_max:
            score += 10
        elif amount > accepted_max:
            score -= 20
    if item.project_type in {"RETROFIT", "MAINTENANCE"} or any(term in text for term in ("改造", "维保", "大修", "非标", "中小型")):
        score += 15
    if any(region in f"{item.province} {item.city}" for region in profile.get("preferred_regions", [])):
        score += 10
    for terms, penalty in (
        (("机场", "行李系统", "港口", "EPC"), 25),
        (("教学", "实训", "科研"), 30),
        (("SAAS", "纯软件"), 20),
        (("已投产", "正式投产"), 30),
    ):
        if any(term in text for term in terms):
            score -= penalty
    if item.stage == "AWARD":
        score -= 25
    return round(max(0, min(100, score)), 1)
