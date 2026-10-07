"""Business-facing provider interface. Credentials never enter prompts or logs."""
import asyncio
from dataclasses import dataclass
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..schemas import OpportunityResearchResult, SearchQueries

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ProviderCapabilities:
    native_web_search: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    reasoning_control: bool = False


class ProviderError(RuntimeError):
    """Only stable reason codes, never raw HTTP bodies/URLs/credentials."""


class LLMProvider:
    name = "base"
    capabilities = ProviderCapabilities()

    def __init__(self, settings, usage, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.usage = usage
        self.client = client

    async def generate(self, schema: type[T], instructions: str, prompt: str, *, web_search: bool = False,
                       search_budget: int = 3) -> T:
        for attempt in range(2):
            try:
                allowance = self.usage.reserve(search_budget if web_search else 0)
                if web_search and not allowance:
                    raise ProviderError("search_call_budget_exhausted")
                text = await self._request(schema, instructions, prompt, allowance)
                return schema.model_validate_json(text)
            except (ValidationError, ValueError):
                reason = "invalid_structured_output"
                prompt += "\n上次输出未通过 Schema 校验。请严格匹配所有字段、类型和枚举，未知事实留空。"
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                reason = f"provider_http_{code}"
                if code not in {408, 429, 500, 502, 503, 504}:
                    raise ProviderError(reason) from None
            except httpx.HTTPError:
                reason = "provider_network_error"
            except RuntimeError as exc:
                raise ProviderError(str(exc)) from None
            if attempt == 0:
                await asyncio.sleep(0.5)
        raise ProviderError(reason)

    async def discover(self, instructions: str, prompt: str) -> SearchQueries:
        return await self.generate(SearchQueries, instructions, prompt)

    async def research(self, instructions: str, prompt: str) -> OpportunityResearchResult:
        return await self.generate(OpportunityResearchResult, instructions, prompt)

    async def _post(self, url: str, key: str, payload: dict) -> dict:
        if self.client is not None:
            response = await self.client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
        else:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
                response = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
        response.raise_for_status()
        return response.json()

    async def _request(self, schema, instructions, prompt, search_budget):
        raise NotImplementedError
