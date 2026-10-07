from .base import SearchProvider
from ..ai_prompt import DISCOVERY_INSTRUCTIONS
from ..models import OpportunityCandidate
from ..schemas import CandidateBatch


class NativeSearchProvider(SearchProvider):
    def __init__(self, provider):
        self.provider = provider

    async def discover(self, prompt: str) -> list[OpportunityCandidate]:
        result = await self.provider.generate(CandidateBatch, DISCOVERY_INSTRUCTIONS,
                                               prompt + "\n最多30个候选。", web_search=True, search_budget=6)
        candidates = self._candidates(result)
        if result.expanded_queries and len(candidates) < 20:
            # Expand only while the run's search-call budget permits it.
            if self.provider.usage.reserved_search_calls < self.provider.settings.max_tool_calls:
                try:
                    extra = await self.provider.generate(CandidateBatch, DISCOVERY_INSTRUCTIONS,
                        prompt + "\n针对新表述扩展搜索：" + "；".join(result.expanded_queries[:4]),
                        web_search=True, search_budget=2)
                    candidates.extend(self._candidates(extra))
                except Exception:
                    pass  # Keep successful discovery results if expansion fails.
        return candidates[:30]

    async def find_sources(self, candidate: OpportunityCandidate) -> list[OpportunityCandidate]:
        result = await self.provider.generate(CandidateBatch, DISCOVERY_INSTRUCTIONS,
            f"只核对以下项目的官方原始来源和最新公告，不寻找其他项目：{candidate.title}\n发现URL：{candidate.url}\n最多3个来源，expanded_queries为空。",
            web_search=True, search_budget=1)
        return self._candidates(result)[:3]

    @staticmethod
    def _candidates(result):
        return [OpportunityCandidate(**item.model_dump(), discovery_method="ai_search")
                for item in result.candidates]
