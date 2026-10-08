from dataclasses import asdict

from .base import SearchProvider
from ..ai_prompt import DISCOVERY_INSTRUCTIONS
from ..facts import normalize_url
from ..models import OpportunityCandidate
from ..schemas import CandidateBatch


class NativeSearchProvider(SearchProvider):
    def __init__(self, provider):
        self.provider = provider

    @property
    def usage(self):
        return self.provider.usage

    async def discover(self, prompt):
        from .coverage import discover_coverage
        async def planner(context):
            return (await self.provider.discover(DISCOVERY_INSTRUCTIONS, context + "\n根据实际结果和空缺通道生成最多2条补充搜索词。")).queries
        return await discover_coverage(self, prompt, planner)

    async def search_query(self, query, *, phase="discovery"):
        result = await self.provider.generate_result(CandidateBatch, DISCOVERY_INSTRUCTIONS,
            query + "\n使用一次联网搜索，只列出搜索工具实际返回且符合方向的项目，最多12个，不虚构URL。", web_search=True, search_budget=1, search_phase=phase)
        return self._candidates(result)

    async def find_sources(self, candidate):
        result = await self.provider.generate_result(CandidateBatch, DISCOVERY_INSTRUCTIONS,
            f"只核对以下项目的官方原始来源和最新公告，不寻找其他项目：{candidate.title}\n发现URL：{candidate.url}\n最多3个来源。",
            web_search=True, search_budget=1, search_phase="verification")
        return self._candidates(result)

    @staticmethod
    def _candidates(result):
        sources = [asdict(source) for source in result.sources]
        rows = [OpportunityCandidate(**item.model_dump(), discovery_method="ai_search", search_sources=sources)
                for item in result.data.candidates]
        known = {normalize_url(row.url) for row in rows}
        for source in result.sources:
            if source.url not in known:
                rows.append(OpportunityCandidate(source.title or source.url, source.url,
                    discovery_method="ai_search", search_sources=[asdict(source)]))
        return rows
