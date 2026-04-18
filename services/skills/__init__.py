"""
数据源 Skill 注册表

新增数据源只需：
  1. 在本目录下创建新文件（如 baidu_news.py）
  2. 继承 NewsSkill 基类，实现 name / search_queries / fetch 三个属性/方法
  3. 在下方 SKILLS 列表中实例化并注册
"""

from services.skills.toutiao import ToutiaoSkill
from services.skills.wechat import WeChatSkill
from services.skills.policy import PolicySkill
from services.skills.bidding import BiddingSkill
from services.skills.industry_media import IndustryMediaSkill
from services.skills.expo_assoc import ExpoAssocSkill
from services.skills.local_projects import LocalProjectSkill
from services.skills.local_wechat import LocalWeChatProjectSkill

# ===== 已注册的 Skill 列表 =====
# 按需添加新的 Skill 实例即可自动生效

SKILLS = [
    LocalWeChatProjectSkill(),
    LocalProjectSkill(),
    ToutiaoSkill(),
    WeChatSkill(),
    PolicySkill(),
    BiddingSkill(),
    IndustryMediaSkill(),
    ExpoAssocSkill(),
]
