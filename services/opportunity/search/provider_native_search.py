from dataclasses import asdict

from .base import SearchProvider
from ..ai_prompt import DISCOVERY_INSTRUCTIONS
from ..facts import normalize_url
from ..models import OpportunityCandidate
from ..schemas import CandidateBatch


class NativeSearchProvider(SearchProvider):
    def __init__(self, provider):
        self.provider = provider

    async def discover(self, prompt):
        result = await self.provider.generate_result(CandidateBatch, DISCOVERY_INSTRUCTIONS,
            prompt + "\n最多30个候选。", web_search=True, search_budget=6, search_phase="discovery")
        candidates = self._candidates(result)
        if result.data.expanded_queries and len(candidates) < 20 and self.provider.usage.remaining("discovery"):
            try:
                extra = await self.provider.generate_result(CandidateBatch, DISCOVERY_INSTRUCTIONS,
                    prompt + "\n针对新表述扩展搜索：" + "；".join(result.data.expanded_queries[:4]),
                    web_search=True, search_budget=2, search_phase="discovery")
                candidates.extend(self._candidates(extra))
            except Exception:
                pass
        by_url = {normalize_url(row.url): row for row in candidates if normalize_url(row.url)}
        return list(by_url.values())[:30]

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
