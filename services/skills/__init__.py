"""
数据源 Skill 注册表

新增数据源只需：
  1. 在本目录下创建新文件（如 baidu_news.py）
  2. 继承 NewsSkill 基类，实现 name / search_queries / fetch 三个属性/方法
  3. 在下方 SKILLS 列表中实例化并注册
"""

from services.skills.toutiao import ToutiaoSkill
from services.skills.wechat import WeChatSkill
from services.skills.wechat_rss import WeChatRssSkill
from services.skills.policy import PolicySkill
from services.skills.bidding import BiddingSkill
from services.skills.industry_media import IndustryMediaSkill
from services.skills.expo_assoc import ExpoAssocSkill
from services.skills.local_projects import LocalProjectSkill
from services.skills.local_wechat import LocalWeChatProjectSkill
from services.skills.zhihu_discover import ZhihuDiscoverSkill
from services.skills.bilibili_discover import BilibiliDiscoverSkill
from config import USE_SOGOU_WECHAT_FALLBACK

# ===== 已注册的 Skill 列表 =====
# 按需添加新的 Skill 实例即可自动生效

SKILLS = [
    LocalProjectSkill(),
    ZhihuDiscoverSkill(),
    BilibiliDiscoverSkill(),
    ToutiaoSkill(),
    WeChatRssSkill(),
    PolicySkill(),
    BiddingSkill(),
    IndustryMediaSkill(),
    ExpoAssocSkill(),
]

# 搜狗通道仅作为可选兜底，默认关闭
if USE_SOGOU_WECHAT_FALLBACK:
    SKILLS.insert(0, LocalWeChatProjectSkill())
    SKILLS.insert(4, WeChatSkill())
