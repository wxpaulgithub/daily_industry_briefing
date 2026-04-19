"""
知乎浏览器发现 Skill（Playwright）

策略：
1. 优先用 Playwright 模拟浏览器抓取搜索页结果
2. 若浏览器链路失败，自动回退到现有 ZhihuDiscoverSkill
"""
import logging
import re
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import httpx

from config import USE_PLAYWRIGHT_FOR_ZHIHU
from services.fetcher import Article, clean_title, normalize_summary
from services.skills.browser_discover_base import BrowserDiscoverSkillBase
from services.skills.zhihu_discover import ZhihuDiscoverSkill

logger = logging.getLogger(__name__)
_ZHIHU_FALLBACK_IMAGE = "/static/discover-zhihu.svg"


class ZhihuBrowserSkill(BrowserDiscoverSkillBase):
    """Playwright 版知乎发现源"""

    def __init__(self) -> None:
        self._use_playwright = USE_PLAYWRIGHT_FOR_ZHIHU
        self._http_fallback = ZhihuDiscoverSkill()

    @property
    def name(self) -> str:
        # 保持名称一致，沿用现有过滤与展示规则
        return "知乎发现"

    @property
    def search_queries(self) -> list[dict]:
        return [
            {"keyword": "智能仓储 立体库 堆垛机", "label": "仓储"},
            {"keyword": "WMS WCS AGV AMR", "label": "系统"},
            {"keyword": "物流自动化 工厂改造", "label": "改造"},
            {"keyword": "仓储项目 招标 中标", "label": "项目"},
        ]

    @staticmethod
    def _normalize_zhihu_url(url: str) -> str:
        u = (url or "").strip()
        if not u:
            return ""
        if u.startswith("/"):
            u = "https://www.zhihu.com" + u
        parsed = urlparse(u)
        if "link.zhihu.com" in parsed.netloc:
            target = (parse_qs(parsed.query).get("target") or [""])[0]
            target = unquote(target).strip()
            if target:
                u = target
                parsed = urlparse(u)
        if "zhihu.com" not in parsed.netloc:
            return ""

        # API 链接强制网页化，避免点开 JSON
        m_q = re.search(r"/api/v4/questions/(\d+)", u)
        if m_q:
            return f"https://www.zhihu.com/question/{m_q.group(1)}"
        m_a = re.search(r"/api/v4/articles/(\d+)", u)
        if m_a:
            return f"https://zhuanlan.zhihu.com/p/{m_a.group(1)}"
        m_ans = re.search(r"/api/v4/answers/(\d+)", u)
        if m_ans:
            return f"https://www.zhihu.com/answer/{m_ans.group(1)}"

        if any(seg in u for seg in ("/question/", "zhuanlan.zhihu.com/p/", "zhihu.com/p/", "zhihu.com/answer/")):
            return u
        return ""

    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        if not self._use_playwright:
            logger.info(f"[{self.name}] 已关闭 Playwright 主链路，使用登录态 HTTP + Bing 兜底 [{keyword}]")
            return await self._http_fallback.fetch(client, keyword, count=count)

        search_url = f"https://www.zhihu.com/search?type=content&q={quote_plus(keyword)}"
        extract_script = """
() => {
  const pickText = (el, selector) => {
    const node = el.querySelector(selector);
    return node ? (node.textContent || '').trim() : '';
  };

  // 先尝试卡片选择器；如果结构变化，再退化到页面内所有知乎内容链接
  let cards = Array.from(document.querySelectorAll('[class*="SearchResult"], [class*="SearchItem"], section'));
  if (!cards.length) cards = [document.body];
  const out = [];
  const seen = new Set();

  for (const card of cards) {
    const anchors = Array.from(card.querySelectorAll('h2 a, a[href*="/question/"], a[href*="zhuanlan.zhihu.com/p/"], a[href*="zhihu.com/question/"]'));
    for (const a of anchors) {
      const href = (a.getAttribute('href') || '').trim();
      const title = (a.textContent || '').trim();
      if (!href || !title) continue;

      let url = href;
      if (url.startsWith('/')) url = 'https://www.zhihu.com' + url;
      if (!/zhihu\\.com\\/(question|zhuanlan)/.test(url)) continue;
      if (seen.has(url)) continue;
      seen.add(url);

      const summary =
        pickText(card, '.RichText') ||
        pickText(card, '[class*="SearchItem"] p') ||
        pickText(card, 'p');
      const imageEl = card.querySelector('img');
      const image =
        (imageEl && (imageEl.getAttribute('src') || imageEl.getAttribute('data-original') || imageEl.getAttribute('data-actualsrc'))) || '';
      const author =
        pickText(card, '[class*="AuthorInfo"]') ||
        pickText(card, '[class*="UserLink"]') ||
        '';

      out.push({ title, url, summary, image, author });
    }
  }
  return out;
}
"""
        browser_items = await self._extract_with_playwright(
            search_url=search_url,
            extract_script=extract_script,
            timeout_ms=28000,
            settle_ms=1800,
            scroll_rounds=2,
        )
        if not browser_items:
            loose_script = """
() => {
  const out = [];
  const seen = new Set();
  const anchors = Array.from(document.querySelectorAll('a[href]'));
  for (const a of anchors) {
    let href = (a.getAttribute('href') || '').trim();
    if (!href) continue;
    if (href.startsWith('/')) href = 'https://www.zhihu.com' + href;
    if (!/zhihu\\.com\\/(question\\/|p\\/|zhuanlan\\/p\\/|link\\?target=)/.test(href)) continue;
    const t =
      (a.getAttribute('title') || '').trim() ||
      (a.getAttribute('aria-label') || '').trim() ||
      (a.textContent || '').trim();
    if (!t || t.length < 4) continue;
    if (seen.has(href)) continue;
    seen.add(href);
    out.push({ title: t, url: href, summary: '', image: '', author: '' });
    if (out.length >= 30) break;
  }
  return out;
}
"""
            browser_items = await self._extract_with_playwright(
                search_url=search_url,
                extract_script=loose_script,
                timeout_ms=32000,
                settle_ms=2600,
                scroll_rounds=3,
            )

        articles: list[Article] = []
        for item in browser_items:
            title = clean_title(str(item.get("title", "")).strip())
            url = self._normalize_zhihu_url(str(item.get("url", "")).strip())
            if not title or not url:
                continue
            summary = normalize_summary(str(item.get("summary", "")).strip())
            image = str(item.get("image", "")).strip() or _ZHIHU_FALLBACK_IMAGE
            author = str(item.get("author", "")).strip()
            source = f"知乎/{author}".strip("/") if author else "知乎"
            articles.append(
                Article(
                    title=title,
                    url=url,
                    summary=summary,
                    source_name=source,
                    image_url=image,
                    published="",
                    published_ts=0.0,
                    skill_name=self.name,
                    region_scope=self.region_scope,
                )
            )
            if len(articles) >= count:
                break

        if articles:
            logger.info(f"[{self.name}] 浏览器抓取成功 [{keyword}] -> {len(articles)} 条")
            return articles[:count]

        # 浏览器链路无结果时，先尝试搜索引擎站内兜底，再回退 HTTP 解析链路
        bing_items = await self._fallback_via_bing(
            client=client,
            keyword=keyword,
            site_query="site:zhihu.com/question OR site:zhuanlan.zhihu.com/p OR site:zhihu.com/p",
            domain_markers=("zhihu.com/question", "zhuanlan.zhihu.com/p/", "zhihu.com/p/"),
            source_name="知乎",
            fallback_image=_ZHIHU_FALLBACK_IMAGE,
            count=count,
        )
        if bing_items:
            logger.info(f"[{self.name}] 浏览器无结果，Bing 兜底成功 [{keyword}] -> {len(bing_items)} 条")
            return bing_items[:count]

        logger.warning(f"[{self.name}] 浏览器与 Bing 均无结果，回退 HTTP 链路 [{keyword}]")
        return await self._http_fallback.fetch(client, keyword, count=count)
