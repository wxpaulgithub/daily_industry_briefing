"""Shared discovery coverage; query labels measure searches, not verified demand."""
import asyncio
import json
import logging
import re
from datetime import timedelta
from ..runtime import local_now
from ..facts import normalize_url

logger = logging.getLogger(__name__)

CHANNELS = {
    "direct": ("直接采购", "中国 WMS WCS 智能仓储 立体库 堆垛机 AGV 设备采购 重新招标", 30),
    "intention": ("采购意向", "中国 智能仓储管理系统 输送设备 政府采购意向 企业采购计划 需求征集", 30),
    "retrofit": ("改造维保", "老立库 堆垛机 大修 维保 PLC WCS 电控 升级 企业采购公告", 30),
    "production": ("生产物流", "中国 制造企业 车间物流 厂内物流 成品物流 原料物流 物料系统 采购 技改", 30),
    "expansion": ("新建扩产", "制造企业 新工厂 生产基地 扩建 产能提升 自动化物流 建设", 90),
    "warehouse": ("仓库专项", "中国 成品库 原料库 备件库 器材库 粮库 智能化工程 自动化 采购", 30),
    "packaging": ("包装后端", "中国 包装后端 码垛 自动装车 输送 智能配送 设备采购 技改", 30),
    "early": ("早期信号", "制造企业 环评 技改备案 项目签约 建设规划 生产物流 自动化仓库", 90),
}

def dated_query(query, days=30):
    today = local_now().date()
    return f"{query} 发布时间 {(today-timedelta(days=days)).isoformat()} 至 {today.isoformat()} 优先原始公告，排除历史案例和纯行业资讯"

def merge_candidates(batches, limit=100):
    batches = [batch for batch in batches if isinstance(batch, list)]
    mixed = [batch[i] for i in range(max((len(x) for x in batches), default=0)) for batch in batches if i < len(batch)]
    by_url = {}
    for row in mixed:
        key = normalize_url(row.url)
        if not key:
            continue
        if key in by_url:
            by_url[key].discovery_channels = list(dict.fromkeys(by_url[key].discovery_channels + row.discovery_channels))
        else:
            by_url[key] = row
    return list(by_url.values())[:limit]

async def discover_coverage(search, prompt, planner):
    usage = getattr(search, "usage", None)
    cost = getattr(search, "query_cost", 1)
    report = {key: {"label": label, "requests": 0, "returned": 0, "status": "not_searched"}
              for key, (label, _, _) in CHANNELS.items()}
    search.coverage_report = report
    capacity = usage.remaining("discovery") // cost if usage else len(CHANNELS)
    planned = list(CHANNELS)[:capacity]
    for key in list(CHANNELS)[capacity:]:
        report[key]["status"] = "budget_exhausted"
    semaphore = asyncio.Semaphore(getattr(getattr(usage, "settings", None), "search_concurrency", 1))
    async def run(key, query):
        async with semaphore:
            return await run_request(key, query)
    async def run_request(key, query):
        report.setdefault(key, {"label": "AI扩展", "requests": 0, "returned": 0, "status": "not_searched"})
        report[key]["requests"] += 1
        try:
            rows = await search.search_query(query, phase="discovery")
            for row in rows:
                row.discovery_channels = list(dict.fromkeys(row.discovery_channels + [key]))
            report[key]["returned"] += len(rows)
            report[key]["status"] = "returned" if report[key]["returned"] else "empty"
            logger.info("[OpportunityDiscovery] channel=%s returned=%d status=%s", key, len(rows), report[key]["status"])
            return rows
        except Exception as exc:
            report[key]["status"] = "failed"
            response = getattr(exc, "response", None)
            report[key]["error"] = f"http_{response.status_code}" if response is not None else type(exc).__name__
            if response is not None:
                try:
                    code = str((response.json().get("error") or {}).get("code", ""))
                    if re.fullmatch(r"[A-Za-z0-9_-]{1,50}", code):
                        report[key]["provider_error_code"] = code
                except (ValueError, AttributeError):
                    pass
            logger.warning("[OpportunityDiscovery] channel=%s error=%s provider_code=%s", key, report[key]["error"], report[key].get("provider_error_code", ""))
            return []
    batches = await asyncio.gather(*(run(key, dated_query(
        ("" if CHANNELS[key][1].startswith("中国") else "中国 ") + CHANNELS[key][1], CHANNELS[key][2])) for key in planned))
    # Reserve one supplemental request for a missing channel. The other may
    # explore new wording seen in actual first-round results.
    remaining = min(2, usage.remaining("discovery") // cost) if usage else 2
    supplements = []
    gaps = [key for key in planned if not report[key]["returned"]]
    if remaining and gaps:
        key = gaps[0]
        supplements.append((key, dated_query(CHANNELS[key][1] + " 最新企业公告 项目需求", CHANNELS[key][2])))
    if remaining > len(supplements):
        rows = merge_candidates(batches)
        context = json.dumps({"first_round_results": [{"title": row.title, "summary": row.summary[:160]}
                             for row in rows[:24]], "coverage": report}, ensure_ascii=False)
        try:
            queries = await planner(prompt + "\n第一轮真实搜索结果：" + context)
        except Exception:
            queries = []
        seen = {query for _, query in supplements}
        for query in queries:
            query = str(query).strip()
            if query and query not in seen:
                supplements.append(("dynamic", dated_query(query)))
                seen.add(query)
            if len(supplements) >= remaining:
                break
    for key, query in supplements:
        batches.append(await run(key, query))
    return merge_candidates(batches)
