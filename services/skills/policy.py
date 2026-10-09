"""
政策与标准类信源 Skill（RSS）
"""
from services.skills.rss_generic import RSSKeywordSkill


class PolicySkill(RSSKeywordSkill):
    skill_name = "政策标准"
    source_name_fallback = "政策标准"
    max_per_feed = 8

    feed_sources = [
        {"label": "中国政府网政务动态", "publisher": "中国政府网政务动态", "kind": "official", "url": "https://www.gov.cn/yaowen/liebiao/"},
        {"label": "工信部要闻", "publisher": "工信部要闻", "kind": "official", "url": "https://www.miit.gov.cn/xwdt/gxdt/index.html"},
        {"label": "发改委要闻", "publisher": "发改委要闻", "kind": "official", "url": "https://www.ndrc.gov.cn/xwdt/"},
    ]

    include_keywords = [
        "智能制造",
        "工业互联网",
        "数字化转型",
        "制造业",
        "物流",
        "仓储",
        "机器人",
        "自动化",
        "新型工业化",
    ]

    exclude_keywords = [
        "文旅",
        "体育",
        "教育考试",
        "娱乐",
    ]
