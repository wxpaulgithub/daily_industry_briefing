from dataclasses import replace
from datetime import datetime
from pathlib import Path

from services.opportunity.facts import SHANGHAI
from services.opportunity.models import OpportunityCandidate
from services.opportunity.schemas import OpportunityResearchResult
from services.opportunity.settings import OpportunitySettings
from services.opportunity.verifier import SourceDocument

NOW = datetime(2026, 10, 7, 8, tzinfo=SHANGHAI)
URL = "https://www.ccgp.gov.cn/project/warehouse"
TITLE = "样本制造企业自动化立体库改造采购公告"
TEXT = f"{TITLE}。采购人：样本制造有限公司。项目位于江苏无锡。预算金额：380万元。发布时间：2026-10-07。投标截止时间：2026-10-12 09:30。公开招标，包含堆垛机、输送系统、WMS、WCS。"


def settings_in(directory, **changes):
    root = Path(directory)
    return replace(OpportunitySettings(), output_dir=root / "output", runtime_dir=root / "runtime", **changes)


def candidate():
    return OpportunityCandidate(TITLE, URL, summary=TEXT, published="2026-10-07", published_ts=NOW.timestamp(), content=TEXT)


def research_result(**changes):
    facts = {
        "title": (TITLE, TITLE), "owner": ("样本制造有限公司", "采购人：样本制造有限公司"),
        "province": ("江苏", "项目位于江苏无锡"), "city": ("无锡", "项目位于江苏无锡"),
        "published": ("2026-10-07", "发布时间：2026-10-07"), "budget": ("380万元", "预算金额：380万元"),
        "deadline": ("2026-10-12 09:30", "投标截止时间：2026-10-12 09:30"),
        "stage": ("PROCUREMENT", "公开招标，包含堆垛机"),
    }
    data = dict(title=TITLE, is_real_project=True, warehouse_relevance=100., owner="样本制造有限公司",
                province="江苏", city="无锡", published="2026-10-07", budget_text="380万元",
                deadline="2026-10-12 09:30", stage="PROCUREMENT", project_type="RETROFIT",
                technical_scope=["堆垛机", "输送系统", "WMS", "WCS"], summary="制造企业旧立库改造。",
                opportunity_reason="采购期且技术范围匹配。", entry_point="建议获取招标技术文件。", source_urls=[URL],
                evidence=[dict(field=field, value=value, source_title=TITLE, source_url=URL, quote=quote)
                          for field, (value, quote) in facts.items()])
    data.update(changes)
    return OpportunityResearchResult.model_validate(data)


def documents():
    return [SourceDocument(URL, TEXT, TITLE)]
