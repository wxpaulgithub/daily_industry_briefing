"""
招投标与中标公告类信源 Skill（混合信源）

1) 政府采购/公告列表页（RSS/HTML 解析）
2) 关键词搜索补充（今日头条搜索 API）
"""
import logging

import httpx

from services.fetcher import Article, clean_title, normalize_summary
from services.skills.rss_generic import RSSKeywordSkill

logger = logging.getLogger(__name__)


class BiddingSkill(RSSKeywordSkill):
    skill_name = "招投标"
    source_name_fallback = "招投标公告"
    max_per_feed = 12

    # 政府采购与公共资源（部分站点会限流，作为第一层来源）
    feed_sources = [
        {"label": "中国政府采购网采购信息", "url": "https://www.ccgp.gov.cn/cggg/zygg/index.htm"},
        {"label": "中国政府采购网地方采购", "url": "https://www.ccgp.gov.cn/cggg/dfgg/index.htm"},
        {"label": "全国公共资源交易平台", "url": "https://www.ggzy.gov.cn/deal/dealList.html"},
        {"label": "中国招标投标公共服务平台", "url": "https://bulletin.cebpubservice.com/"},  # 兜底 HTML 列表解析
    ]

    include_keywords = [
        "招标公告",
        "采购公告",
        "中标",
        "中标候选人",
        "成交",
        "竞争性谈判",
        "竞争性磋商",
        "公开招标",
        "单一来源",
        "EPC",
        "总包",
        "立体库",
        "智能仓储",
        "仓储",
        "物流",
        "自动化",
        "WMS",
        "WCS",
        "AGV",
        "堆垛机",
        "输送线",
        "集成",
        "中标公示",
        "招标",
    ]

    exclude_keywords = [
        "医疗耗材",
        "物业管理",
        "食材配送",
        "保洁",
    ]

    # 第二层：关键词搜索补充，提升可用性（当公告站点超时/限流时仍能有产出）
    search_queries_toutiao = [
        "智能仓储 招标 中标 公告",
        "立体库 堆垛机 输送线 招标 采购",
        "WMS WCS AGV 招标 中标",
        "物流自动化 集成项目 中标",
        "仓储设备 公开招标 中标候选人",
    ]

    async def _fetch_toutiao(self, client: httpx.AsyncClient, keyword: str, count: int = 12) -> list[Article]:
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
            data = resp.json() if resp.status_code == 200 else {}
            rows = (data or {}).get("data") or []
            if not isinstance(rows, list):
                rows = []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                title = clean_title(str(item.get("title", "")).strip())
                if not title:
                    continue
                url = str(item.get("share_url", "") or item.get("source_url", "")).strip()
                if not url:
                    continue
                summary = normalize_summary(str(item.get("abstract", "")).strip())
                text = f"{title} {summary}".lower()
                if self.exclude_keywords and any(kw.lower() in text for kw in self.exclude_keywords):
                    continue
                if self.include_keywords and not any(kw.lower() in text for kw in self.include_keywords):
                    continue
                articles.append(
                    Article(
                        title=title,
                        url=url,
                        summary=summary,
                        source_name=f"招投标搜索/{str(item.get('media_name', '')).strip()}".strip("/"),
                        image_url=str(item.get("large_image_url", "") or item.get("image_url", "")).strip(),
                        published=(str(item.get("datetime", "") or "")[:16]),
                        published_ts=float(item.get("publish_time", 0) or 0),
                        skill_name=self.name,
                        region_scope=self.region_scope,
                    )
                )
        except Exception as e:
            logger.warning(f"[{self.name}] 搜索补充失败 [{keyword}]: {e}")
        return articles

    async def fetch_all(self, client: httpx.AsyncClient) -> list[Article]:
        # 第一层：公告站点（RSS/HTML）
        rss_articles = await super().fetch_all(client)

        # 第二层：关键词搜索补充
        search_articles: list[Article] = []
        for kw in self.search_queries_toutiao:
            batch = await self._fetch_toutiao(client, kw, count=10)
            search_articles.extend(batch)

        merged = rss_articles + search_articles
        # 本 skill 内部去重（按 URL）
        unique: list[Article] = []
        seen: set[str] = set()
        for a in merged:
            if not a.url or a.url in seen:
                continue
            seen.add(a.url)
            a.skill_name = self.name
            a.region_scope = self.region_scope
            unique.append(a)

        logger.info(
            f"[{self.name}] 合并完成：公告源 {len(rss_articles)} 条，搜索补充 {len(search_articles)} 条，去重后 {len(unique)} 条"
        )
        return unique
