"""
本地公众号项目动态 Skill
基于 WeChatSkill，增加本地关键词与公众号白名单过滤。
"""
import logging

import httpx

from config import (
    LOCAL_INTENT_KEYWORDS,
    LOCAL_REGION_KEYWORDS,
    LOCAL_WECHAT_WHITELIST,
)
from services.fetcher import Article
from services.skills.wechat import WeChatSkill

logger = logging.getLogger(__name__)


class LocalWeChatProjectSkill(WeChatSkill):
    """本地公众号项目动态（白名单优先）"""

    @property
    def name(self) -> str:
        return "本地公众号"

    @property
    def region_scope(self) -> str:
        return "local"

    @property
    def search_queries(self) -> list[dict]:
        region = "无锡 新吴区 锡山区 惠山区 滨湖区"
        intent = " ".join(LOCAL_INTENT_KEYWORDS[:8])
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
            src = (a.source_name or "").strip().lower()
            if not src:
                continue
            if any(k.lower() in src for k in LOCAL_WECHAT_WHITELIST):
                a.region_scope = self.region_scope
                a.skill_name = self.name
                filtered.append(a)

        logger.info(f"[{self.name}] 白名单过滤后保留 {len(filtered)} 条")
        return filtered
