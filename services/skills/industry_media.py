"""
行业媒体类信源 Skill（RSS + HTML 列表页）
"""
from services.skills.rss_generic import RSSKeywordSkill


class IndustryMediaSkill(RSSKeywordSkill):
    skill_name = "行业媒体"
    source_name_fallback = "行业媒体"
    max_per_feed = 10

    feed_sources = [
        # -- 通用科技/财经（RSS） --
        {"label": "36氪资讯", "url": "https://36kr.com/feed"},
        # -- 工业自动化垂直门户（HTML 列表页） --
        {"label": "中国传动网-行业资讯", "url": "https://www.chuandong.com/news/list4.html"},
        {"label": "中国传动网-企业动态", "url": "https://www.chuandong.com/news/list8.html"},
        {"label": "智能制造网-行业动态", "url": "https://www.gkzhan.com/news/t15/list.html"},
        {"label": "智能制造网-市场分析", "url": "https://www.gkzhan.com/news/t14/list.html"},
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
        "堆垛机",
        "输送线",
        "立体库",
        "立库",
        "仓储",
        "物流",
        "自动化",
        "伺服",
        "PLC",
        "传感器",
        "运动控制",
    ]

    exclude_keywords = [
        "明星",
        "娱乐",
        "影视",
        "房产",
        "游戏评测",
        "招聘",
        "求职",
        "培训课程",
        "展会预告",
    ]

