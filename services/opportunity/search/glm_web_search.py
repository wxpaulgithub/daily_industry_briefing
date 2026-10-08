"""GLM's dedicated Web-Search-Pro model, separate from the analysis model."""
import asyncio
import logging
import re

import httpx

from .base import SearchProvider
from ..facts import parse_date
from ..models import OpportunityCandidate, SearchSource
from dataclasses import asdict
from ..runtime import local_now

logger = logging.getLogger(__name__)


class GLMWebSearch(SearchProvider):
    """Search the public web with Web-Search-Pro, then pass source links downstream."""

    name = "glm_web_search"

    def __init__(self, settings, usage, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.usage = usage
        self.client = client

    async def discover(self, prompt: str) -> list[OpportunityCandidate]:
        queries = [
            "中国 智能仓储 自动化立体库 堆垛机 招标 采购公告 最近",
            "中国 制造企业 生产物流 智能仓库 设备采购 项目 招标 最近",
            "中国 仓储物流系统 WMS WCS AGV 输送分拣 项目采购 最近",
            "中国 立体库 改造 堆垛机 大修 维保 采购项目 最近",
            "中国 新工厂 扩建 成品库 包装线 后端物流 自动化 项目 最近",
            "智能仓储 项目 中标候选人 澄清 变更公告 官方公告 最近",
        ]
        # GLM proposes varied search angles; Web-Search-Pro performs the actual searches.
        from ..ai_prompt import DISCOVERY_INSTRUCTIONS
        try:
            planned = await self._plan_queries(DISCOVERY_INSTRUCTIONS, prompt)
            if planned:
                queries = planned[:6]
        except Exception as exc:
            logger.warning("[Opportunity/GLMWebSearch] query planning failed (%s); using topic queries", type(exc).__name__)

        batches = await asyncio.gather(*(self._search(query) for query in queries[:6]), return_exceptions=True)
        candidates = []
        for batch in batches:
            if isinstance(batch, Exception):
                continue
            candidates.extend(batch)
        return self._dedupe(candidates)[:50]

    async def find_sources(self, candidate: OpportunityCandidate) -> list[OpportunityCandidate]:
        query = f'"{candidate.title}" 官方 招标 采购 公告'
        if candidate.source_name:
            query += f" {candidate.source_name}"
        return self._dedupe(await self._search(query, phase="verification"))[:5]

    async def _plan_queries(self, instructions: str, prompt: str) -> list[str]:
        # Keep planning on the selected GLM model; searching is a separate API model.
        from ..ai_client import create_llm_provider
        provider = create_llm_provider(self.settings, self.usage, "glm")
        result = await provider.discover(
            instructions,
            prompt + "\n只输出6条互不重复、覆盖不同采购场景的中文联网搜索词，优先近30天，范围限中国大陆。",
        )
        return [str(query).strip() for query in result.queries if str(query).strip()]

    async def _search(self, query: str, *, phase="discovery") -> list[OpportunityCandidate]:
        allowance = self.usage.reserve(1, phase=phase)
        payload = {
            "model": "web-search-pro",
            "messages": [{"role": "user", "content": query}],
            "top_p": 0.7,
            "temperature": 0.1,
            "stream": False,
        }
        url = self.settings.glm_base_url.rstrip("/") + "/chat/completions"
        try:
            if self.client is not None:
                response = await self.client.post(url, headers={"Authorization": f"Bearer {self.settings.glm_api_key}"}, json=payload)
            else:
                async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
                    response = await client.post(url, headers={"Authorization": f"Bearer {self.settings.glm_api_key}"}, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            # Never include provider response bodies or request headers in logs.
            logger.warning("[Opportunity/GLMWebSearch] search request rejected (HTTP %s)", exc.response.status_code)
            self.usage.settle_search(phase, allowance)
            raise
        except httpx.HTTPError as exc:
            logger.warning("[Opportunity/GLMWebSearch] search request failed (%s)", type(exc).__name__)
            self.usage.settle_search(phase, allowance)
            raise

        except BaseException:
            self.usage.settle_search(phase, allowance)
            raise
        self.usage.settle_search(phase, allowance, 1)
        usage = data.get("usage") or {}
        tool_calls = self._tool_calls(data)
        search_calls = sum(bool(call.get("search_result")) for call in tool_calls)
        self.usage.record("glm_web_search", int(usage.get("prompt_tokens") or 0),
                          int(usage.get("completion_tokens") or 0), search_calls)
        rows = []
        for call in tool_calls:
            results = call.get("search_result") or []
            if isinstance(results, dict):
                results = results.get("results") or results.get("data") or []
            if not isinstance(results, list):
                continue
            for result in results:
                if not isinstance(result, dict):
                    continue
                title = str(result.get("title") or "").strip()
                link = str(result.get("link") or result.get("url") or "").strip()
                if not title or not link:
                    continue
                content = str(result.get("content") or result.get("snippet") or "").strip()
                media = str(result.get("media") or result.get("site_name") or "智谱联网搜索").strip()
                published = str(result.get("publish_time") or result.get("date") or "").strip()
                if not published:
                    date_match = re.search(
                        r"(?:发布时间|发布日期|发布于|发表于|published(?:\s+on)?)\s*[：:]?\s*"
                        r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?(?:[ T\s]+\d{1,2}[:：]\d{2}(?:[:：]\d{2})?)?)",
                        title + " " + content, re.IGNORECASE,
                    )
                    published = date_match.group(1) if date_match else ""
                published_date = parse_date(published)
                clean_title = re.sub(
                    r"\s*[（(]\s*(?:发布时间|发布日期)[：:]?\s*20\d{2}[^）)]*[）)]\s*$", "", title,
                ).strip()
                rows.append(OpportunityCandidate(
                    title=clean_title or title, url=link, summary=content[:2000],
                    source_name=f"GLM搜索/{media}",
                    published=published[:32],
                    published_ts=published_date.timestamp() if published_date else 0,
                    discovery_method="ai_search", snippet_origin="glm_web_search",
                    search_sources=[asdict(SearchSource(link, clean_title or title, content[:2000],
                                                       "glm_web_search", local_now().isoformat()))],
                ))
        return rows

    @staticmethod
    def _tool_calls(data: dict) -> list[dict]:
        choices = data.get("choices") or []
        if not choices:
            return []
        message = choices[0].get("message") or {}
        calls = message.get("tool_calls") or []
        # Some SDK/documentation response variants expose tool calls directly
        # as a list of search results rather than function-style call objects.
        return [call for call in calls if isinstance(call, dict)]

    @staticmethod
    def _dedupe(candidates: list[OpportunityCandidate]) -> list[OpportunityCandidate]:
        seen = set()
        rows = []
        for candidate in candidates:
            url = candidate.url.strip().rstrip("/")
            if not url or url in seen:
                continue
            seen.add(url)
            rows.append(candidate)
        return rows
