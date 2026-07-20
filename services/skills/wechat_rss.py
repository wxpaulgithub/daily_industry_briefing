"""
微信公众号 RSS 采集 Skill（轻量版）

借鉴 we-mp-rss 思路：
1. 上游先把指定公众号转为 RSS
2. 本系统只消费 RSS 并做筛选聚合

特点：
- 配置驱动（config_data/wechat_sources.json）
- 轻量实现，不引入 we-mp-rss 的重功能
- 内置授权失效检测与告警（关键词 + 全员静默 + 陈旧度）
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import re
import time
from html import unescape

import feedparser
import httpx

from config import RSS_KEYWORD_HEALTHY_MIN_ARTICLES, RSS_SILENCE_THRESHOLD_HOURS
from services.fetcher import Article, NewsSkill, clean_title, normalize_summary
from services.notifier import check_keyword_in_content, send_alert, should_alert_persistent_signal
from services.wechat_sources import get_scope_rss_sources

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def _extract_published(entry: dict) -> tuple[float, str]:
    try:
        if getattr(entry, "published_parsed", None):
            ts = float(calendar.timegm(entry.published_parsed))
            return ts, str(entry.get("published", ""))[:19]
        if getattr(entry, "updated_parsed", None):
            ts = float(calendar.timegm(entry.updated_parsed))
            return ts, str(entry.get("updated", ""))[:19]
    except Exception:
        pass
    return 0.0, ""


def _extract_image(entry: dict, summary_html: str) -> str:
    try:
        media = entry.get("media_content", []) or []
        if media and isinstance(media, list) and isinstance(media[0], dict):
            url = str(media[0].get("url", "")).strip()
            if url:
                return url
        links = entry.get("links", []) or []
        for item in links:
            if not isinstance(item, dict):
                continue
            if item.get("rel") == "enclosure" and str(item.get("type", "")).startswith("image/"):
                url = str(item.get("href", "")).strip()
                if url:
                    return url
        m = re.search(r'<img[^>]+src="([^"]+)"', summary_html or "", re.IGNORECASE)
        if m:
            return m.group(1).strip()
    except Exception:
        return ""
    return ""


# 从微信文章页面提取封面图的正则模式（按可靠性排列）
_OG_IMAGE_RE = re.compile(r'property="og:image"\s+content="([^"]+)"')
_MSG_CDN_RE = re.compile(r'var\s+msg_cdn_url\s*=\s*"([^"]+)"')
# 并发限制：避免批量请求触发微信风控
_COVER_SEMAPHORE = asyncio.Semaphore(5)


async def _fetch_cover_image(client: httpx.AsyncClient, article_url: str) -> str:
    """访问微信文章页面，提取 og:image 作为封面图

    微信 RSS（we-mp-rss）不提供图片数据，但文章页面始终包含
    og:image meta 标签和 msg_cdn_url JS 变量，是最可靠的封面图来源。
    """
    if not article_url or "mp.weixin.qq.com" not in article_url:
        return ""
    async with _COVER_SEMAPHORE:
        try:
            resp = await client.get(
                article_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=12.0,
            )
            if resp.status_code != 200:
                return ""
            # 只读前 50KB 就够提取 meta 标签，避免解析整个 3MB 页面
            text = resp.text[:50000]
            m = _OG_IMAGE_RE.search(text)
            if m:
                return m.group(1).strip()
            m = _MSG_CDN_RE.search(text)
            if m:
                return m.group(1).strip()
        except Exception as e:
            logger.debug(f"[公众号RSS] 封面提取失败 {article_url[:60]}: {e}")
    return ""


class WeChatRssSkill(NewsSkill):
    def __init__(self, fetch_covers: bool = True):
        self.fetch_covers = fetch_covers
        # 本轮关键词命中详情：[{"keyword", "source", "title"}]，用于误报判断与告警文案
        self._auth_fail_hits: list[dict] = []

    @property
    def name(self) -> str:
        return "公众号RSS"

    @property
    def region_scope(self) -> str:
        return "wechat"

    @property
    def search_queries(self) -> list[dict]:
        # 该 Skill 不走关键词检索，保持接口兼容即可
        return []

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        return []

    async def _fetch_single_source(self, src: object, client: httpx.AsyncClient) -> list[Article]:
        articles: list[Article] = []
        feed_url = (getattr(src, "rss_url", "") or "").strip()
        if not feed_url:
            return articles
        try:
            resp = await client.get(
                feed_url,
                headers={
                    "Accept": "application/rss+xml, application/xml, text/xml, text/html",
                    "Referer": feed_url,
                },
                timeout=30.0,
            )
            feed = feedparser.parse(resp.text or "")
            entries = feed.entries or []
            if not entries:
                return articles

            scopes = getattr(src, "scopes", [])
            is_local_source = "local" in scopes
            wechat_category = "local" if is_local_source else "industry"
            article_scope = "local" if is_local_source else self.region_scope

            for entry in entries[:20]:
                title = clean_title(str(entry.get("title", "")).strip())
                url = str(entry.get("link", "")).strip()
                summary_html = str(entry.get("summary", "") or entry.get("description", "")).strip()
                summary = normalize_summary(_strip_html(unescape(summary_html)))
                if not title or not url:
                    continue

                # 授权失效关键词检测：RSS 内容中出现"扫码登录/登录过期"等占位语
                hit_kw = check_keyword_in_content(title, summary)
                if hit_kw:
                    self._auth_fail_hits.append(
                        {
                            "keyword": hit_kw,
                            "source": getattr(src, "name", ""),
                            "title": title[:40],
                        }
                    )
                    logger.warning(
                        f"[公众号RSS] 授权失效信号: 在 [{getattr(src, 'name', '')}] "
                        f"的文章中检测到关键词 [{hit_kw}]，标题: {title[:40]}"
                    )
                    continue  # 跳过占位提示项，不计入正常文章

                published_ts, published = _extract_published(entry)
                image_url = _extract_image(entry, summary_html)
                articles.append(
                    Article(
                        title=title,
                        url=url,
                        summary=summary,
                        source_name=getattr(src, "name", ""),
                        image_url=image_url,
                        published=published,
                        published_ts=published_ts,
                        skill_name=self.name,
                        region_scope=article_scope,
                        wechat_category=wechat_category,
                    )
                )

            # 批量提取缺少封面图的文章的 og:image
            no_image = [a for a in articles if not a.image_url] if self.fetch_covers else []
            if no_image:
                tasks = [
                    _fetch_cover_image(client, a.url)
                    for a in no_image
                ]
                cover_results = await asyncio.gather(*tasks)
                filled = 0
                for article, cover_url in zip(no_image, cover_results):
                    if cover_url:
                        article.image_url = cover_url
                        filled += 1
                if filled:
                    logger.info(
                        f"[公众号RSS] [{getattr(src, 'name', '')}] "
                        f"封面提取: {filled}/{len(no_image)} 篇成功"
                    )
        except Exception as e:
            logger.warning(f"[公众号RSS] 抓取失败 [{getattr(src, 'name', '')}] {feed_url}: {e}")
        return articles

    async def fetch_all(self, client: httpx.AsyncClient) -> list[Article]:
        import asyncio
        articles: list[Article] = []
        source_map: dict[str, object] = {}
        # 重置本轮关键词检测记录
        # 本轮关键词命中详情：[{"keyword", "source", "title"}]，用于误报判断与告警文案
        self._auth_fail_hits: list[dict] = []

        for scope in ("wechat", "local"):
            for src in get_scope_rss_sources(scope):
                key = f"{src.name}|{src.rss_url}"
                source_map[key] = src
        source_list = list(source_map.values())
        if not source_list:
            logger.info("[公众号RSS] 未配置 rss_url，跳过")
            return []

        local_source_count = sum(1 for src in source_list if "local" in getattr(src, "scopes", []))
        wechat_source_count = sum(1 for src in source_list if "wechat" in getattr(src, "scopes", []))
        logger.info(
            f"[公众号RSS] 来源诊断: total_sources={len(source_list)}, "
            f"local_sources={local_source_count}, wechat_sources={wechat_source_count}"
        )

        tasks = [self._fetch_single_source(src, client) for src in source_list]
        results = await asyncio.gather(*tasks)
        for batch in results:
            articles.extend(batch)

        local_article_count = sum(1 for a in articles if (a.region_scope or "").strip().lower() == "local")
        wechat_article_count = sum(1 for a in articles if (a.region_scope or "").strip().lower() == "wechat")
        logger.info(
            f"[公众号RSS] 文章诊断: total_articles={len(articles)}, "
            f"local_articles={local_article_count}, wechat_articles={wechat_article_count}"
        )
        logger.info(f"[公众号RSS] 采集完成，获取 {len(articles)} 条原始资讯")

        # ===== 授权失效检测与告警 =====
        await self._check_auth_status(client, articles, len(source_list))

        return articles

    async def _check_auth_status(
        self,
        client: httpx.AsyncClient,
        articles: list[Article],
        total_sources: int,
    ) -> None:
        """综合检测 RSS 授权状态，触发告警

        检测优先级：
        1. 关键词检测：RSS 内容中出现"扫码登录/登录过期"等占位语。命中后还需结合
           本轮正常文章数判断——文章数充足则视为单篇文章误命中（不告警），仅当
           feed 几乎无内容时才判定为真失效并告警。
        2. 全员静默检测：所有源在阈值时间内均无新文章
        3. 数据陈旧度检测：最新文章距今超过 80 小时
        """
        # 检测 1：关键词告警（结合本轮正常文章数判断，避免单篇文章误命中）
        if self._auth_fail_hits:
            # feed 健康（仍抓到足够多正常文章）→ 关键词命中视为误报，不发送告警
            if len(articles) >= RSS_KEYWORD_HEALTHY_MIN_ARTICLES:
                hits_preview = "; ".join(
                    f"[{h['source']}]《{h['title']}》({h['keyword']})"
                    for h in self._auth_fail_hits[:5]
                )
                logger.info(
                    f"[公众号RSS] 关键词命中但本轮已抓到 {len(articles)} 条正常文章"
                    f"(≥{RSS_KEYWORD_HEALTHY_MIN_ARTICLES})，判定为误报，不发送告警。"
                    f"命中详情: {hits_preview}"
                )
                # 不 return：误报时继续走陈旧度检测兜底（正常情况下不会触发）
            else:
                # feed 几乎无内容 + 关键词命中 → 高度疑似授权失效，发送告警（带命中详情）
                keywords = ", ".join(sorted({h["keyword"] for h in self._auth_fail_hits}))
                hits_detail = "; ".join(
                    f"[{h['source']}]《{h['title']}》" for h in self._auth_fail_hits[:5]
                )
                await send_alert(
                    client,
                    "keyword",
                    f"RSS 内容中检测到授权失效关键词 [{keywords}]，"
                    f"且本轮仅抓到 {len(articles)} 条正常文章，"
                    f"we-mp-rss 可能需要重新扫码授权。命中: {hits_detail}",
                )
                return  # 真失效，命中后不再检测其他条件

        # 单轮空结果容易受网络或上游临时波动影响，需持续超过阈值后再告警。
        if not articles and total_sources > 0:
            should_alert, elapsed_hours = should_alert_persistent_signal(
                "wechat_rss_empty",
                True,
                RSS_SILENCE_THRESHOLD_HOURS,
            )
            if should_alert:
                await send_alert(
                    client,
                    "silence",
                    f"所有 {total_sources} 个 RSS 源已连续 {elapsed_hours:.1f} 小时未抓取到任何文章，"
                    f"授权可能已失效。",
                )
            return
        should_alert_persistent_signal("wechat_rss_empty", False, RSS_SILENCE_THRESHOLD_HOURS)

        # 检测 3：数据陈旧度
        if articles:
            newest_ts = max(
                (a.published_ts for a in articles if a.published_ts > 0),
                default=0.0,
            )
            if newest_ts > 0:
                age_hours = (time.time() - newest_ts) / 3600
                threshold = RSS_SILENCE_THRESHOLD_HOURS
                if age_hours > threshold:
                    await send_alert(
                        client,
                        "staleness",
                        f"所有 RSS 源最新文章已距今 {age_hours:.1f} 小时"
                        f"（阈值 {threshold}h），授权可能已失效。",
                    )
