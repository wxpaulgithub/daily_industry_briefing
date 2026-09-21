"""Deterministic opportunity classification and relevance rules."""
import time

from .models import OpportunityCandidate


WAREHOUSE_TERMS = {
    "智能仓储": 35, "仓储自动化": 35, "自动化立体库": 35, "立体库": 28,
    "堆垛机": 25, "WMS": 24, "WCS": 24, "AGV": 18, "AMR": 18,
    "输送线": 16, "输送系统": 18, "分拣系统": 18, "物流自动化": 24,
    "仓储设备": 18, "物流中心": 12, "智能物流": 18,
}
INTENT_TERMS = {
    "招标公告": 18, "采购公告": 18, "公开招标": 18, "竞争性磋商": 14,
    "询价": 10, "采购": 10, "中标候选人": 8, "中标": 6,
    "扩建": 13, "新建": 13, "改造": 15, "升级": 10, "维保": 16, "维护": 12,
}
HARD_EXCLUDES = (
    "食材配送", "快递服务", "运输服务", "车辆采购", "物业服务", "医疗耗材",
    "仓库租赁", "装卸服务", "保洁服务", "物流运输", "冷链运输",
)
EDITORIAL_EXCLUDES = (
    "排行榜", "榜单", "盘点", "怎么选", "如何选择", "供应商对比", "厂家对比",
    "选型指南", "采购指南", "品牌推荐", "行业趋势", "深度解析", "一文看懂",
    "项目一览", "中标一览", "项目汇总", "中标汇总",
)


def candidate_text(candidate: OpportunityCandidate) -> str:
    return f"{candidate.title} {candidate.summary} {candidate.content}".strip()


def relevance(candidate: OpportunityCandidate) -> tuple[float, list[str], list[str]]:
    text = candidate_text(candidate)
    upper = text.upper()
    risks = [f"excluded:{term}" for term in HARD_EXCLUDES if term in text]
    risks.extend(f"editorial:{term}" for term in EDITORIAL_EXCLUDES if term in text)
    if risks:
        return 0.0, [], risks
    matched: list[str] = []
    score = 0
    for term, weight in {**WAREHOUSE_TERMS, **INTENT_TERMS}.items():
        if term.upper() in upper:
            score += weight
            matched.append(term)
    # A generic warehouse mention alone is too weak for the sales pipeline.
    if "仓储" in text and not any(x in upper for x in ("智能仓储", "仓储自动化", "立体库", "WMS", "WCS")):
        score += 6
        matched.append("仓储")
    has_domain = any(term.upper() in upper for term in WAREHOUSE_TERMS)
    has_intent = any(term in text for term in INTENT_TERMS)
    if has_domain and has_intent:
        score += 15
    elif not has_domain:
        risks.append("weak_warehouse_fit")
    return float(min(score, 100)), list(dict.fromkeys(matched)), risks


def classify_type(text: str) -> str:
    upper = text.upper()
    hits = []
    if any(x in text for x in ("新建", "建设", "物流中心", "新厂")):
        hits.append("NEW_BUILD")
    if any(x in text for x in ("改造", "升级", "技改", "扩建")):
        hits.append("RETROFIT")
    if any(x in text for x in ("维保", "维护", "维修", "保养")):
        hits.append("MAINTENANCE")
    if any(x in upper for x in ("WMS", "WCS", "软件", "信息系统")):
        hits.append("SOFTWARE")
    if any(x in upper for x in ("AGV", "AMR", "堆垛机", "输送线", "货架", "设备")):
        hits.append("EQUIPMENT")
    unique = list(dict.fromkeys(hits))
    if len(unique) > 1:
        return "MIXED"
    return unique[0] if unique else "UNKNOWN"


def classify_stage(text: str) -> str:
    if any(x in text for x in ("废标", "终止公告", "流标", "已结束")):
        return "CLOSED"
    if any(x in text for x in ("中标", "成交", "中标候选人")):
        return "AWARD"
    if any(x in text for x in ("招标公告", "采购公告", "公开招标", "竞争性磋商", "询价公告")):
        return "PROCUREMENT"
    if any(x in text for x in ("拟建", "规划", "立项", "环评", "签约", "扩产", "开工")):
        return "EARLY_SIGNAL"
    return "UNKNOWN"


def should_keep(score: float, stage: str) -> bool:
    return score >= 38 and stage in {"EARLY_SIGNAL", "PROCUREMENT", "AWARD"}


def is_too_old(stage: str, published_ts: float, now_ts: float | None = None) -> bool:
    if not published_ts:
        return False
    age_days = max(((now_ts or time.time()) - published_ts) / 86400, 0)
    limits = {"PROCUREMENT": 45, "EARLY_SIGNAL": 90, "AWARD": 21}
    return age_days > limits.get(stage, 30)
