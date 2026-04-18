"""
展会与协会类信源 Skill（RSS）
"""
from services.skills.rss_generic import RSSKeywordSkill


class ExpoAssocSkill(RSSKeywordSkill):
    skill_name = "展会协会"
    source_name_fallback = "展会协会"
    max_per_feed = 10

    feed_sources = [
        {"label": "中国物流与采购联合会", "url": "http://www.chinawuliu.com.cn/rss.xml"},
        {"label": "中国机械工业联合会", "url": "http://www.cmif.org.cn/rss.xml"},
    ]

    include_keywords = [
        "展会",
        "论坛",
        "峰会",
        "博览会",
        "智能制造",
        "工业自动化",
        "仓储",
        "物流",
        "供应链",
        "数字化",
    ]

    exclude_keywords = [
        "招聘",
        "培训通知",
        "党建",
    ]

