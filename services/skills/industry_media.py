"""
行业媒体类信源 Skill（RSS）
"""
from services.skills.rss_generic import RSSKeywordSkill


class IndustryMediaSkill(RSSKeywordSkill):
    skill_name = "行业媒体"
    source_name_fallback = "行业媒体"
    max_per_feed = 10

    feed_sources = [
        {"label": "36氪资讯", "url": "https://36kr.com/feed"},
        {"label": "虎嗅24小时", "url": "https://www.huxiu.com/rss/0.xml"},
        {"label": "创业邦", "url": "https://www.cyzone.cn/rss.xml"},
    ]

    include_keywords = [
        "智能制造",
        "制造业",
        "工业自动化",
        "工业互联网",
        "仓储物流",
        "智慧物流",
        "机器人",
        "工业软件",
        "数字化工厂",
        "MES",
        "WMS",
        "WCS",
        "AGV",
    ]

    exclude_keywords = [
        "明星",
        "娱乐",
        "影视",
        "房产",
        "游戏评测",
    ]

