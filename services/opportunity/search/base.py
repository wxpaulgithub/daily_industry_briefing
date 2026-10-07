from abc import ABC, abstractmethod

from ..models import OpportunityCandidate


class SearchProvider(ABC):
    @abstractmethod
    async def discover(self, prompt: str) -> list[OpportunityCandidate]:
        pass

    @abstractmethod
    async def find_sources(self, candidate: OpportunityCandidate) -> list[OpportunityCandidate]:
        pass
