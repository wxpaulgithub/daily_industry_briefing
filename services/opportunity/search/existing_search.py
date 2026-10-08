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

    query_cost = 2

    async def discover(self, prompt):
        from .coverage import discover_coverage
        async def planner(context):
            if not self.provider:
                return []
            return (await self.provider.discover(DISCOVERY_INSTRUCTIONS, context + "\n根据实际结果和空缺通道生成最多2条补充搜索词。")).queries
        return await discover_coverage(self, prompt, planner)

    async def search_query(self, query, *, phase="discovery"):
        return await self._fetch([query], phase)

    async def find_sources(self, candidate):
        return await self._fetch([candidate.title + " 官方 招标 采购"], phase="verification")

    async def _fetch(self, queries, phase="discovery"):
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            return await OpportunitySearchSource(queries, self.usage, phase).fetch(client)
