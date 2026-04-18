"""
微信公众号搜索 Skill - 通过搜狗微信搜索获取公众号文章

搜狗微信搜索 (weixin.sogou.com) 可按关键词搜索公众号文章，
覆盖面广，是获取微信公众号内容最稳定的公开渠道。

注意：搜狗有频率限制，本 Skill 采用顺序请求 + 延时策略避免触发反爬。
"""

import asyncio
import datetime
import logging
import re
from html import unescape
from typing import Optional

import httpx

from services.fetcher import (
    Article,
    NewsSkill,
    clean_title,
    normalize_summary,
)

logger = logging.getLogger(__name__)

# 搜狗反爬页面特征词
_BLOCK_INDICATORS = ["antispider", "verify", "captcha", "验证", "频繁"]

# 搜狗域名前缀，用于补全相对链接
_SOGOU_BASE = "https://weixin.sogou.com"


class WeChatSkill(NewsSkill):
    """微信公众号搜索数据源（通过搜狗微信搜索）"""

    @property
    def name(self) -> str:
        return "微信公众号"

    @property
    def search_queries(self) -> list[dict]:
        return [
            {"keyword": "智能仓储 立体仓库 堆垛机", "label": "智能仓储"},
            {"keyword": "智能制造 工业自动化", "label": "智能制造"},
            {"keyword": "AGV 物流机器人 仓储机器人", "label": "AGV物流"},
            {"keyword": "WMS WCS MES 数字化工厂", "label": "数字化"},
            {"keyword": "中鼎 承亿 昆船 北自院 兰剑", "label": "厂商"},
            {"keyword": "工业自动化展 智能制造展 物流展", "label": "工业展览"},
        ]

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        """从搜狗微信搜索获取公众号文章"""
        articles = []
        try:
            resp = await client.get(
                "https://weixin.sogou.com/weixin",
                params={
                    "type": "2",  # type=2 搜索文章（非公众号）
                    "query": keyword,
                    "ie": "utf8",
                    "s_from": "input",
                    "_sug_": "n",
                    "_sug_type": "",
                },
                headers={
                    "Referer": "https://weixin.sogou.com/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )

            html = resp.text

            # 检测反爬拦截页面
            html_lower = html.lower()
            if any(kw in html_lower for kw in _BLOCK_INDICATORS):
                logger.warning(f"[微信公众号] 搜狗触发反爬限制，跳过关键词 [{keyword}]")
                return []

            articles = self._parse_html(html)

            # 解析搜狗跳转链接，获取真实的微信文章 URL
            if articles:
                await self._resolve_redirects(client, articles)

        except Exception as e:
            logger.warning(f"[微信公众号] 搜索失败 [{keyword}]: {e}")

        return articles[:count]

    async def _resolve_redirects(self, client: httpx.AsyncClient, articles: list[Article]) -> None:
        """解析搜狗跳转链接，提取真实的微信文章 URL

        搜狗跳转页用 JS 拼接真实 URL：
            var url = '';
            url += 'https://mp.';
            url += 'weixin.qq.c';
            url += 'om/s?...';
            window.location.replace(url)
        """
        async def _resolve_one(article: Article) -> None:
            if not article.url or "sogou.com/link" not in article.url:
                return
            try:
                resp = await client.get(
                    article.url,
                    headers={"Referer": "https://weixin.sogou.com/"},
                )
                # 提取 JS 中 url += '...' 的所有片段并拼接
                parts = re.findall(r"url\s*\+=\s*'([^']*)'", resp.text)
                if parts:
                    real_url = "".join(parts)
                    if real_url.startswith("http"):
                        article.url = real_url
            except Exception:
                pass

        await asyncio.gather(*[_resolve_one(a) for a in articles])

    async def fetch_all(self, client: httpx.AsyncClient) -> list[Article]:
        """顺序搜索所有关键词（搜狗有频率限制，不宜并发）"""
        all_articles: list[Article] = []

        for query in self.search_queries:
            try:
                batch = await self.fetch(client, query["keyword"])
                all_articles.extend(batch)
                logger.debug(f"[微信公众号] 关键词 [{query['label']}] 获取 {len(batch)} 条")
                # 每组关键词间隔 2 秒，避免触发搜狗频率限制
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[微信公众号] 关键词 [{query['keyword']}] 失败: {e}")
                continue

        logger.info(f"[{self.name}] 采集完成，获取 {len(all_articles)} 条原始资讯")
        return all_articles

    # ===== HTML 解析 =====

    def _parse_html(self, html: str) -> list[Article]:
        """解析搜狗微信搜索结果页 HTML

        搜狗结果结构:
          <ul class="news-list">
            <li id="sogou_vr_...">
              <div class="img-box"><a><img src="..."></a></div>
              <div class="txt-box">
                <h3><a href="/link?url=...">标题</a></h3>
                <p class="txt-info">摘要</p>
                <div class="s-p">
                  <span class="all-time-y2">公众号名称</span>
                  <span class="s2"><script>document.write(timeConvert('ts'))</script></span>
                </div>
              </div>
            </li>
          </ul>
        """
        articles = []

        # 精确匹配 news-list 内的 <li>（以 sogou_vr 开头的 id），避免误匹配 <link> 等
        blocks = re.findall(
            r'<li\s+id="sogou_vr[^"]*"[^>]*>(.*?)</li>',
            html,
            re.DOTALL,
        )
        if not blocks:
            return articles

        for block in blocks:
            try:
                article = self._parse_block(block)
                if article:
                    articles.append(article)
            except Exception:
                continue

        return articles

    @staticmethod
    def _clean_url(raw_url: str) -> str:
        """清理 URL：解码 HTML 实体 + 补全相对路径"""
        url = unescape(raw_url.strip())
        if url.startswith("/"):
            url = _SOGOU_BASE + url
        return url

    def _parse_block(self, block: str) -> Optional[Article]:
        """解析单条文章 HTML 块"""
        # --- 标题和链接 ---
        title_match = re.search(
            r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        if not title_match:
            return None

        url = self._clean_url(title_match.group(1))
        # 清理标题中的高亮标签 <!--red_beg-->...<!--red_end--> 和 <em>
        title_html = title_match.group(2)
        title_html = re.sub(r'<!--(?:red_beg|red_end)-->', '', title_html)
        title = clean_title(re.sub(r'<[^>]+>', '', title_html).strip())
        if not title or not url:
            return None

        # --- 摘要 ---
        summary = ""
        summary_match = re.search(
            r'<p[^>]*class="txt-info"[^>]*>(.*?)</p>',
            block, re.DOTALL,
        )
        if summary_match:
            raw = summary_match.group(1)
            raw = re.sub(r'<!--(?:red_beg|red_end)-->', '', raw)
            raw = re.sub(r'<[^>]+>', '', raw).strip()
            summary = normalize_summary(raw)

        # --- 来源（公众号名称）---
        # 搜狗结构: <span class="all-time-y2">公众号名称</span>
        source_name = ""
        src_match = re.search(
            r'<span[^>]*class="all-time-y2"[^>]*>(.*?)</span>',
            block, re.DOTALL,
        )
        if src_match:
            source_name = re.sub(r'<[^>]+>', '', src_match.group(1)).strip()

        # --- 发布时间 ---
        # 搜狗用 <script>document.write(timeConvert('unix_timestamp'))</script>
        published = ""
        published_ts = 0.0
        ts_match = re.search(
            r"timeConvert\('(\d+)'\)",
            block,
        )
        if ts_match:
            try:
                published_ts = float(ts_match.group(1))
                # 转为可读日期
                dt = datetime.datetime.fromtimestamp(published_ts)
                published = dt.strftime("%Y-%m-%d")
            except (ValueError, OSError):
                published_ts = 0.0

        # --- 缩略图 ---
        image_url = ""
        img_match = re.search(
            r'<div[^>]*class="img-box"[^>]*>.*?<img[^>]*src="([^"]+)"',
            block,
            re.DOTALL,
        )
        if img_match:
            raw_src = img_match.group(1).strip()
            # 解码 HTML 实体（&amp; → &），补全协议
            image_url = unescape(raw_src)
            if image_url.startswith("//"):
                image_url = "https:" + image_url

        return Article(
            title=title,
            url=url,
            summary=summary,
            source_name=source_name,
            image_url=image_url,
            published=published,
            published_ts=published_ts,
        )
