"""
展会与协会类信源 Skill（RSS + HTML 列表页）
"""
from services.skills.rss_generic import RSSKeywordSkill


class ExpoAssocSkill(RSSKeywordSkill):
    skill_name = "展会协会"
    source_name_fallback = "展会协会"
    max_per_feed = 10

    feed_sources = [
        # -- 展会预告与报道 --
        {"label": "传动网-展会预告", "publisher": "传动网", "kind": "association", "url": "https://www.chuandong.com/exhibition/list0.html"},
        {"label": "传动网-展会报道", "publisher": "传动网", "kind": "association", "url": "https://www.chuandong.com/news/list5.html"},
        # -- 行业协会动态 --
        {"label": "中国物流与采购联合会", "publisher": "中国物流与采购联合会", "kind": "association", "url": "http://www.chinawuliu.com.cn/zixun/"},
        {"label": "中国机械工业联合会", "publisher": "中国机械工业联合会", "kind": "association", "url": "http://www.cmif.org.cn/"},
    ]

    include_keywords = [
        # 仓储/物流核心
        "智能仓储",
        "仓储物流",
        "仓储",
        "物流自动化",
        "物流",
        "供应链",
        "搬运",
        "立体库",
        "堆垛机",
        "输送",
        "传输",
        "AGV",
        "AMR",
        "WMS",
        "WCS",
        # 工业自动化核心
        "智能制造",
        "工业自动化",
        "工业机器人",
        "机器人",
        "自动化",
        "工博会",
        "物流展",
        "智能装备",
        "工业装备",
        "装配",
        "动力传动",
        "PLC",
        "伺服",
        "传感器",
    ]

    exclude_keywords = [
        "招聘",
        "培训通知",
        "党建",
        # 能源/电力类
        "五金机电",
        "电线电缆",
        "核电",
        "核能",
        "水电站",
        "清洁能源",
        "光伏",
        "储能",
        # 车辆类
        "商用车",
        "专用车",
        "汽车展",
        # 建筑/地产/轻工
        "纺织",
        "房地产",
        "家装",
        # 过往年份（排除过旧的展会）
    ]

