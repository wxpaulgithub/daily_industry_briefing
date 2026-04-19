"""
今日头条搜索 Skill - 通过今日头条搜索 API 获取资讯
国内可用，无需翻墙，图片覆盖率高
"""
import logging

import httpx

from services.fetcher import (
    Article,
    NewsSkill,
    clean_title,
    normalize_summary,
)

logger = logging.getLogger(__name__)


class ToutiaoSkill(NewsSkill):
    """今日头条搜索数据源"""

    @property
    def name(self) -> str:
        return "今日头条"

    @property
    def search_queries(self) -> list[dict]:
        return [
            {"keyword": "智能制造 工业自动化 智能仓储 立库 堆垛机", "label": "智能制造"},
            {"keyword": "智能工厂 集成 数字化工厂 AGV", "label": "智能工厂"},
            {"keyword": "立库招标 投标 仓储设备 智能仓库 中标", "label": "行业信息"},
            {"keyword": "制造业 数字化转型 工业互联网", "label": "数字化"},
            {"keyword": "工业4.0 数字孪生 MES WMS WCS", "label": "工业技术"},
            {"keyword": "工业自动化展 智能制造展 物流展", "label": "工业展览"},
            {"keyword": "中鼎集成 昆船智能 北自科技 兰剑 今天国际 井松智能 音飞储存 德马科技 极智嘉 海柔创新 快仓", "label": "厂商"},
        ]

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        """从今日头条搜索 API 获取资讯"""
        articles = []
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
            data = resp.json() if resp.status_code == 200 else {}
            rows = (data or {}).get("data") or []
            if not isinstance(rows, list):
                rows = []

            for item in rows:
                if not isinstance(item, dict):
                    continue

                title = item.get("title", "").strip()
                if not title:
                    continue

                title = clean_title(title)
                url = item.get("share_url", "") or item.get("source_url", "")
                if not url:
                    continue

                articles.append(Article(
                    title=title,
                    url=url,
                    summary=normalize_summary(item.get("abstract", "")),
                    source_name=item.get("media_name", ""),
                    image_url=item.get("large_image_url", "") or item.get("image_url", ""),
                    published=(item.get("datetime", "") or "")[:16],
                    published_ts=float(item.get("publish_time", 0)),
                ))

        except Exception as e:
            logger.warning(f"[今日头条] 搜索失败 [{keyword}]: {e}")

        return articles
