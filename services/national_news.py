"""Domestic industry news: relevance, provenance, diversity and diagnostics."""
from collections import Counter
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode
import json
import logging
import re
import time
from decimal import Decimal

logger = logging.getLogger(__name__)
CORE = ("智能仓储", "智慧仓储", "仓储", "仓库", "立体库", "立库", "堆垛机", "输送", "分拣", "拣选", "搬运", "穿梭车", "无人叉车", "货到人", "厂内物流", "生产物流", "物流自动化", "智慧物流", "智能物流", "物流机器人", "物流装备", "物流设备", "物流技术", "物流系统", "物流场景", "物流中心", "冷链", "wms", "wcs", "agv", "amr")
RELATED = ("工业自动化", "智能制造", "工业机器人", "工业软件", "工业互联网", "数字孪生", "运动控制", "伺服", "传感器", "plc", "mes", "工博会")
CONTEXT = ("物流", "供应链", "仓配", "仓储配送", "物料系统")
HARD_EXCLUDE = ("房价", "楼市", "明星", "综艺", "高考", "学区", "招聘", "求职", "培训课程", "股票涨停", "股票跌停", "违纪", "贪腐", "受贿")
UNRELATED = ("液冷", "数据中心", "气象科技大楼", "住宅", "商用车", "汽车维修", "切削", "机床", "手机芯片", "3d打印鞋")
BID = ("招标", "中标", "采购公告", "采购意向", "成交公告", "竞争性磋商", "询比采购")
EVENT = ("展会", "博览会", "展览会", "工博会", "物流展", "峰会", "论坛", "大会", "cemat", "let")
GROUPS = {"chinaagv.com": "中叉网/AGV网", "chinaforklift.com": "中叉网/AGV网"}
SOURCE_NAMES = {"toutiao.com": "今日头条", "chuandong.com": "中国传动网", "gkzhan.com": "智能制造网", "chinawuliu.com.cn": "中国物流与采购联合会", "chinaagv.com": "中国AGV网", "chinaforklift.com": "中国叉车网", "i56r.com": "掌链", "bzkj.cn": "北自科技官网", "blueswords.com": "兰剑智能官网", "chinalet.cn": "LET展会官网", "ndrc.gov.cn": "国家发展改革委", "miit.gov.cn": "工业和信息化部", "gov.cn": "中国政府网", "ccgp.gov.cn": "中国政府采购网", "ggzy.gov.cn": "全国公共资源交易平台", "cebpubservice.com": "中国招标投标公共服务平台"}


def is_national(article):
    return (article.region_scope or "national") not in ("local", "discover", "wechat")


def source_domain(url):
    host = (urlsplit(url).hostname or "").lower()
    for domain in (*SOURCE_NAMES, "xinhuanet.com", "xinhua.cn"):
        if host == domain or host.endswith("." + domain):
            return domain
    return host.removeprefix("www.").removeprefix("m.")


def normalize_url(url):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in ("spm", "from", "source")]
    return (parts.hostname or "").lower().removeprefix("www.") + parts.path.rstrip("/") + ("?" + urlencode(sorted(query)) if query else "")


def contains(text, word):
    if word.isascii():
        return bool(re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", text))
    return word in text


def assess(article):
    text = (article.title + " " + article.summary).lower()
    if any(word in text for word in HARD_EXCLUDE):
        return "", 0.0, "irrelevant_content"
    core = [w for w in CORE if contains(text, w)]
    # AMR also names an automobile maintenance show; the acronym alone is ambiguous.
    if any(w in text for w in UNRELATED) and not any(w not in ("amr", "mes", "plc") for w in core):
        return "", 0.0, "unrelated_topic"
    related = any(contains(text, w) for w in RELATED)
    context = any(w in text for w in CONTEXT)
    if not core and not related and not context:
        return "", 0.0, "no_industry_context"
    relevance = min(.95, .72 + .04 * len(core)) if core else (.55 if context else .38)
    if any(contains(article.title.lower(), w) for w in EVENT):
        category = "展会活动"
    elif any(w in text for w in BID):
        if not core and not context:
            return "", 0.0, "bidding_without_logistics"
        category = "项目招采"
    elif article.skill_name == "政策标准" or any(w in text for w in ("标准", "政策", "实施方案", "指导意见")):
        category = "政策标准"
    elif core and any(w in text for w in ("交付", "项目", "投产", "改造", "案例", "落地")):
        category = "项目案例"
    elif article.source_kind == "vendor":
        category = "企业动态"
    elif not core and not context:
        category = "相关工业动态"
    elif core:
        category = "技术产品"
    else:
        category = "行业动态"
    return category, relevance, ""


def annotate(article):
    article.source_domain = source_domain(article.url)
    article.source_group = GROUPS.get(article.source_domain, article.source_group or article.source_domain)
    if article.source_name in ("", "行业媒体", "展会协会", "政策标准", "招投标公告"):
        article.source_name = SOURCE_NAMES.get(article.source_domain, article.source_domain)
    article.news_category, article.relevance_score, _ = assess(article)
    return article


def reject_reason(article, now=None, max_age_days=15):
    now = time.time() if now is None else now
    category, relevance, reason = assess(article)
    if reason:
        return reason
    if article.published_ts:
        if article.published_ts > now + 86400:
            return "future_publication"
        if now - article.published_ts > max_age_days * 86400:
            return "stale_publication"
    else:
        years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", article.title)]
        if years and max(years) < datetime.fromtimestamp(now).year:
            return "historical_title_without_date"
    return ""


def quality_score(article, now=None):
    now = time.time() if now is None else now
    annotate(article)
    score = article.relevance_score * .65
    if article.published_ts and article.published_ts <= now + 86400:
        age = max(0, (now - article.published_ts) / 86400)
        score += .20 if age <= 3 else (.12 if age <= 7 else (.06 if age <= 15 else -.25))
    else:
        score -= .10
    domain = article.source_domain
    if article.source_kind in ("official", "organizer") or domain.endswith(".gov.cn"):
        score += .12
    elif article.source_kind in ("vendor", "industry_media") or domain in SOURCE_NAMES and domain != "toutiao.com":
        score += .07
    if article.source_kind == "aggregator" and any(w in article.title for w in ("核心竞争力排名", "厂家深度盘点", "十大厂家")):
        score -= .20
    if len(article.summary) > 60:
        score += .03
    return round(score, 4)


def event_identity(article):
    """Identify syndicated award stories only with named company, amount and close publication."""
    title = article.title
    if "中标" not in title or not article.published_ts:
        return None
    subject = re.split(r"[:：]|中标|公告", title, maxsplit=1)[0].strip()
    subject = re.sub(r"最新$", "", subject)
    if not 2 <= len(subject) <= 18 or any(w in subject for w in ("某", "快讯", "据悉")):
        return None
    amount = re.search(r"(?<![0-9,.])([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(万元|亿元)", title)
    if not amount:
        amount = re.search(r"(?<![0-9,.])([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(万元|亿元)", article.summary)
    if not amount:
        return None
    value = Decimal(amount[1].replace(",", "").replace("，", "")) * (10000 if amount[2] == "亿元" else 1)
    regions = {w for w in ("波兰", "德国", "江苏", "广东", "上海", "北京", "辽宁", "河北", "唐山", "西安", "长春", "苏州", "广州", "深圳") if w in title}
    identifiers = re.findall(r"(?:招标编号|项目编号)[:：\s]*([A-Za-z0-9-]{4,})", title + " " + article.summary)
    return subject, value, regions, set(identifiers), article.published_ts, tuple(w for w in ("二次", "重新", "变更", "澄清", "取消") if w in title)


def same_event(first, second):
    if not first or not second or first[:2] != second[:2] or first[5] != second[5] or abs(first[4] - second[4]) > 3 * 86400:
        return False
    if first[2] and second[2] and first[2].isdisjoint(second[2]):
        return False
    if first[3] and second[3] and first[3].isdisjoint(second[3]):
        return False
    return True


def deduplicate_national(articles):
    # A primary release with the same headline outranks its aggregator copy.
    ordered = sorted(articles, key=lambda a: (a.source_kind in ("official", "vendor", "organizer"), source_domain(a.url) != "toutiao.com", bool(a.published_ts), quality_score(a)), reverse=True)
    result, seen_urls, titles, events = [], set(), [], []
    for article in ordered:
        url = normalize_url(article.url)
        title = re.sub(r"[\W_]+", "", article.title.lower())
        numbers = re.findall(r"\d+", title)
        version = tuple(w for w in ("二次", "重新", "变更", "澄清", "取消") if w in title)
        identity = event_identity(article)
        identifiers = set(re.findall(r"(?:招标编号|项目编号)[:：\s]*([A-Za-z0-9-]{4,})", article.title + " " + article.summary))
        duplicate = any(same_event(identity, previous) for previous in events) or url in seen_urls or any(
            len(title) >= 14 and numbers == old_numbers and version == old_version
            and not (identifiers and old_identifiers and identifiers.isdisjoint(old_identifiers))
            and not (identity and old_identity)
            and (title == old or SequenceMatcher(None, title, old).ratio() >= .90)
            for old, old_numbers, old_version, old_identifiers, old_identity in titles)
        if not duplicate:
            result.append(article)
            seen_urls.add(url)
            titles.append((title, numbers, version, identifiers, identity))
            events.append(identity)
    return result


def select_national_articles(articles, count, *, now=None, per_source_limit=6, unknown_date_limit=4, max_age_days=15):
    now = time.time() if now is None else now
    candidates = deduplicate_national([annotate(a) for a in articles if not reject_reason(a, now, max_age_days)])
    candidates.sort(key=lambda a: quality_score(a, now), reverse=True)
    selected, sources, categories, unknown = [], Counter(), Counter(), 0
    category_limit = max(1, (count + 2) // 3)
    for article in candidates:
        if len(selected) >= count:
            break
        if sources[article.source_group] >= per_source_limit or categories[article.news_category] >= category_limit:
            continue
        if not article.published_ts and unknown >= unknown_date_limit:
            continue
        selected.append(article)
        sources[article.source_group] += 1
        categories[article.news_category] += 1
        unknown += not bool(article.published_ts)
    # Relevance determines the feature and presentation order, not image availability.
    return selected


def write_diagnostics(raw, relevant, unique, selected, skills, results, path):
    raw = [annotate(a) for a in raw if is_national(a)]
    stages = {"raw": raw, "relevant": [a for a in relevant if is_national(a)], "deduplicated": [a for a in unique if is_national(a)], "selected": selected}
    domains = sorted({a.source_domain for a in raw})
    rows = [{"domain": d, **{stage: sum(a.source_domain == d for a in items) for stage, items in stages.items()}} for d in domains]
    modes = {name: mode for name, _, mode, _ in results}
    fetches = [{**report, "skill": skill.name, "execution_mode": modes.get(skill.name, "not_run"), "from_cache": modes.get(skill.name) != "live"} for skill in skills for report in getattr(skill, "source_diagnostics", [])]
    rejected = Counter(reason for a in raw if (reason := reject_reason(a)))
    from config import NATIONAL_SOURCE_MAX_COUNT, NATIONAL_UNKNOWN_DATE_MAX_COUNT, MAX_ARTICLES
    selected_urls = {normalize_url(a.url) for a in selected}
    groups = Counter(a.source_group for a in selected)
    categories = Counter(a.news_category for a in selected)
    selection_drops = Counter()
    for article in stages["deduplicated"]:
        if normalize_url(article.url) in selected_urls:
            continue
        reason = reject_reason(article)
        if not reason:
            if groups[article.source_group] >= NATIONAL_SOURCE_MAX_COUNT:
                reason = "publisher_limit"
            elif categories[article.news_category] >= (MAX_ARTICLES + 2) // 3:
                reason = "category_limit"
            elif not article.published_ts and sum(not a.published_ts for a in selected) >= NATIONAL_UNKNOWN_DATE_MAX_COUNT:
                reason = "unknown_date_limit"
            else:
                reason = "display_limit"
        selection_drops[reason] += 1
    report = {"generated_at": datetime.now().astimezone().isoformat(), "stage_counts": {stage: len(items) for stage, items in stages.items()}, "sources": rows, "fetches": fetches, "categories": dict(Counter(a.news_category for a in selected)), "drop_reasons": dict(rejected), "selection_drop_reasons": dict(selection_drops), "duplicates_removed": len(stages["relevant"]) - len(stages["deduplicated"]), "unknown_date_selected": sum(not a.published_ts for a in selected), "selected_source_groups": dict(Counter(a.source_group for a in selected))}
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        logger.warning("[国内资讯] 无法保存来源诊断")
    logger.info("[国内资讯] 来源分布=%s 分类=%s", dict(Counter(a.source_domain for a in selected)), report["categories"])
