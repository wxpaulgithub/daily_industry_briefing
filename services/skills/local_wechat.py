"""
本地公众号项目动态 Skill
基于 WeChatSkill，增加本地关键词与公众号白名单过滤。
"""
import logging

import httpx

from services.fetcher import Article
from services.skills.wechat import WeChatSkill
from services.wechat_sources import (
    get_scope_queries,
    source_match_scope,
)

logger = logging.getLogger(__name__)


class LocalWeChatProjectSkill(WeChatSkill):
    """本地公众号项目动态（通过 wechat_sources 配置过滤，投放到 wechat 页 local 子类）"""

    @property
    def name(self) -> str:
        return "本地公众号"

    @property
    def region_scope(self) -> str:
        return "wechat"

    @property
    def source_filter_scope(self) -> str:
        return "local"

    @property
    def wechat_category(self) -> str:
        return "local"

    @property
    def search_queries(self) -> list[dict]:
        override = get_scope_queries("local")
        if override:
            return override
        region = "无锡 新吴区 锡山区 惠山区 滨湖区"
        return [
            {"keyword": f"{region} 引进项目 最新项目", "label": "本地项目动态"},
            {"keyword": f"{region} 招商引资 项目签约", "label": "本地招商"},
            {"keyword": f"{region} 新建厂房 扩建 开工投产", "label": "本地建设"},
            {"keyword": f"{region} 智能仓储 物流中心", "label": "本地仓储政策"},
        ]

    async def fetch_all(self, client: httpx.AsyncClient) -> list[Article]:
        articles = await super().fetch_all(client)
        filtered: list[Article] = []

        for a in articles:
            if source_match_scope(a.source_name, "local"):
                a.region_scope = self.region_scope
                a.skill_name = self.name
                a.wechat_category = self.wechat_category
                filtered.append(a)

        logger.info(f"[{self.name}] 白名单过滤后保留 {len(filtered)} 条")
        return filtered
