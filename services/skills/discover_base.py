"""
发现类信源通用基类

目标：
1. 统一 discover 范围
2. 提供站内检索（Bing）兜底能力
3. 提供搜索入口保底，避免页面无结果
"""
import re
import base64
from html import unescape
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import httpx

from services.fetcher import Article, NewsSkill, clean_title, normalize_summary


class DiscoverSearchSkillBase(NewsSkill):
    """发现类 Skill 的通用能力基类"""

    @property
    def region_scope(self) -> str:
        return "discover"

    @staticmethod
    def _strip_html_tags(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text or "")

    @staticmethod
    def _decode_bing_redirect(raw_url: str) -> str:
        """
        解析 Bing 包装跳转链接，尽量还原真实目标 URL。
        常见格式:
        - https://www.bing.com/ck/a?...&u=a1aHR0cHM6Ly93d3cuemhpaHUuY29tL...
        - https://www.bing.com/aclick?...&u=<url>
        """
        url = (raw_url or "").strip()
        if not url:
            return ""
        try:
            parsed = urlparse(url)
            if "bing.com" not in parsed.netloc:
                return url
            qs = parse_qs(parsed.query)
            u_val = (
                (qs.get("u") or [""])[0]
                or (qs.get("url") or [""])[0]
                or (qs.get("target") or [""])[0]
                or (qs.get("r") or [""])[0]
                or (qs.get("redir") or [""])[0]
            )
            if not u_val:
                return url
            # 直接就是 URL
            if u_val.startswith("http://") or u_val.startswith("https://"):
                return u_val
            # Bing 常见 a1 + base64url 编码
            if len(u_val) > 3 and u_val.startswith("a1"):
                b64 = u_val[2:]
                pad = "=" * (-len(b64) % 4)
                decoded = base64.urlsafe_b64decode((b64 + pad).encode("utf-8")).decode("utf-8", errors="ignore")
                if decoded.startswith("http://") or decoded.startswith("https://"):
                    return decoded
        except Exception:
            return url
        return url

    def _extract_bing_links(self, html: str, domain_markers: tuple[str, ...]) -> list[tuple[str, str, str]]:
        """
        解析 Bing 结果页，兼容多种结构：
        1) 经典 li.b_algo
        2) 全页兜底 a[href]（应对 Bing DOM 结构变动）
        返回: [(url, title, summary), ...]
        """
        pairs: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        # 路径1：经典结构化块
        blocks = re.findall(r'<li class="b_algo".*?</li>', html, re.DOTALL)
        for block in blocks:
            m = re.search(r'<h2><a href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not m:
                continue
            url = unquote(unescape(m.group(1))).strip()
            url = self._decode_bing_redirect(url)
            if not any(marker in url for marker in domain_markers):
                continue
            if url in seen:
                continue
            seen.add(url)
            title = clean_title(self._strip_html_tags(unescape(m.group(2))).strip())
            if not title:
                continue
            s = re.search(r"<p>(.*?)</p>", block, re.DOTALL)
            summary = normalize_summary(self._strip_html_tags(unescape(s.group(1))).strip()) if s else ""
            pairs.append((url, title, summary))

        # 路径2：全页链接兜底（Bing 改版时常见）
        if not pairs:
            for m in re.finditer(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
                url = unquote(unescape(m.group(1))).strip()
                url = self._decode_bing_redirect(url)
                if not url.startswith("http"):
                    continue
                if not any(marker in url for marker in domain_markers):
                    continue
                if url in seen:
                    continue
                title = clean_title(self._strip_html_tags(unescape(m.group(2))).strip())
                if not title or len(title) < 4:
                    continue
                seen.add(url)
                pairs.append((url, title, ""))
                if len(pairs) >= 50:
                    break

        # 路径3：从整页抓取被编码/转义的目标链接（解决 Bing 包装页导致的漏检）
        if not pairs:
            raw_url_hits: list[str] = []
            raw_url_hits += re.findall(r'https?://[^\s"\'<>]+', html)
            raw_url_hits += [unquote(x) for x in re.findall(r'https?%3A%2F%2F[0-9A-Za-z%._/\-]+', html)]
            seen_url: set[str] = set()
            for raw in raw_url_hits:
                u = self._decode_bing_redirect(unquote(unescape(raw)).strip())
                if not u.startswith("http"):
                    continue
                if not any(marker in u for marker in domain_markers):
                    continue
                if u in seen_url:
                    continue
                seen_url.add(u)
                # 标题退化为 URL 末段，避免空标题被过滤
                title = u.rstrip("/").split("/")[-1][:80] or "搜索结果"
                pairs.append((u, title, ""))
                if len(pairs) >= 50:
                    break

        return pairs

    async def _fallback_via_bing(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        site_query: str,
        domain_markers: tuple[str, ...],
        source_name: str,
        fallback_image: str,
        count: int,
    ) -> list[Article]:
        """使用 Bing 做站内检索兜底，尽量返回真实内容链接"""
        query = f"{site_query} {keyword}".strip()
        search_url = f"https://www.bing.com/search?q={quote_plus(query)}"
        try:
            resp = await client.get(
                search_url,
                headers={
                    "Referer": "https://www.bing.com/",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                },
                timeout=12.0,
            )
            html = resp.text
        except Exception:
            return []

        items: list[Article] = []
        for url, title, summary in self._extract_bing_links(html, domain_markers):
            items.append(
                Article(
                    title=title,
                    url=url,
                    summary=summary or f"来自搜索引擎的{source_name}相关结果。",
                    source_name=source_name,
                    image_url=fallback_image,
                    published="",
                    published_ts=0.0,
                    skill_name=self.name,
                    region_scope=self.region_scope,
                )
            )
            if len(items) >= count:
                break
        return items

    def _search_entry_fallback(
        self,
        keyword: str,
        search_url: str,
        source_name: str,
        fallback_image: str,
        summary: str,
    ) -> Article:
        """最终兜底：返回搜索入口，确保发现页不空"""
        return Article(
            title=f"{source_name}搜索：{keyword}",
            url=search_url,
            summary=summary,
            source_name=source_name,
            image_url=fallback_image,
            published="",
            published_ts=0.0,
            skill_name=self.name,
            region_scope=self.region_scope,
        )
