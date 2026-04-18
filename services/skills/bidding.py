"""
招投标与中标公告类信源 Skill（RSS）
"""
from services.skills.rss_generic import RSSKeywordSkill


class BiddingSkill(RSSKeywordSkill):
    skill_name = "招投标"
    source_name_fallback = "招投标公告"
    max_per_feed = 12

    # 这里先放公开 RSS 源；后续可按你的可访问平台继续扩展
    feed_sources = [
        {"label": "中国政府采购网采购信息", "url": "http://www.ccgp.gov.cn/cggg/zygg/rss.xml"},
        {"label": "中国政府采购网地方采购", "url": "http://www.ccgp.gov.cn/cggg/dfgg/rss.xml"},
    ]

    include_keywords = [
        "立体库",
        "智能仓储",
        "仓储",
        "物流",
        "自动化",
        "WMS",
        "WCS",
        "AGV",
        "堆垛机",
        "输送线",
        "集成",
        "中标",
        "招标",
    ]

    exclude_keywords = [
        "医疗耗材",
        "物业管理",
        "食材配送",
        "保洁",
    ]

