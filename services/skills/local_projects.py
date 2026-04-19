"""
本地项目与政策 Skill（基于头条搜索）
"""
import logging

import httpx

from config import LOCAL_REGION_KEYWORDS
from services.fetcher import Article, NewsSkill, clean_title, normalize_summary

logger = logging.getLogger(__name__)


class LocalProjectSkill(NewsSkill):
    """本地项目/政策动态数据源"""

    @property
    def name(self) -> str:
        return "本地项目"

    @property
    def region_scope(self) -> str:
        return "local"

    @property
    def search_queries(self) -> list[dict]:
        region = " ".join(LOCAL_REGION_KEYWORDS)
        return [
            {"keyword": f"{region} 引进项目 最新项目 重大项目 招商引资", "label": "本地项目"},
            {"keyword": f"{region} 新建厂房 扩建 技改项目 项目开工 项目投产", "label": "本地制造项目"},
            {"keyword": f"{region} 产业园 物流园 智能仓储 物流中心 自动化立库", "label": "本地仓储建设"},
            {"keyword": f"{region} 鼓励政策 支持政策 产业政策 制造业政策", "label": "本地政策"},
        ]

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        articles: list[Article] = []
        try:
            resp = await client.get(
                "https://www.toutiao.com/api/search/content/",
                params={
                    "keyword": keyword,
                    "pd": "information",
                    "source": "input",
                    "dvpf": "pc",
                    "aid": "4916",
                    "page_num": "0",
                    "count": str(count),
                },
                headers={"Referer": "https://www.toutiao.com/"},
            )
            data = resp.json()

            items = data.get("data") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = clean_title(item.get("title", "").strip())
                url = item.get("share_url", "") or item.get("source_url", "")
                if not title or not url:
                    continue

                article = Article(
                    title=title,
                    url=url,
                    summary=normalize_summary(item.get("abstract", "")),
                    source_name=item.get("media_name", ""),
                    image_url=item.get("large_image_url", "") or item.get("image_url", ""),
                    published=(item.get("datetime", "") or "")[:16],
                    published_ts=float(item.get("publish_time", 0)),
                    region_scope=self.region_scope,
                )
                articles.append(article)
        except Exception as e:
            logger.warning(f"[本地项目] 搜索失败 [{keyword}]: {e}")

        return articles
