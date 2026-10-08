import httpx

from config import REQUEST_TIMEOUT, USER_AGENT
from .base import SearchProvider
from ..sources.search import OpportunitySearchSource
from ..ai_prompt import DISCOVERY_INSTRUCTIONS


class ExistingSearchProvider(SearchProvider):
    def __init__(self, provider, queries, usage=None):
        self.provider = provider
        self.queries = queries
        self.usage = usage

    async def discover(self, prompt):
        queries = list(self.queries)
        if self.provider:
            try:
                expanded = await self.provider.discover(DISCOVERY_INSTRUCTIONS,
                    prompt + "\n生成最多8条高召回搜索词，覆盖生产物流、包装后端、老库升级和官方项目公告。")
                queries = expanded.queries[:8] + queries
            except Exception:
                pass
        return await self._fetch(list(dict.fromkeys(queries))[:10])

    async def find_sources(self, candidate):
        return await self._fetch([candidate.title + " 官方 招标 采购"], phase="verification")

    async def _fetch(self, queries, phase="discovery"):
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            return await OpportunitySearchSource(queries, self.usage, phase).fetch(client)
