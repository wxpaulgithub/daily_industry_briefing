"""Business-facing provider interface. Credentials never enter prompts or logs."""
import asyncio
import logging
from dataclasses import dataclass
from typing import TypeVar, Generic, get_args
from ..models import SearchSource

import httpx
from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorType

from ..schemas import OpportunityResearchResult, SearchQueries

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)
_VALIDATION_ERROR_TYPES = frozenset(get_args(ErrorType))
_SAFE_PROVIDER_VALUE_ERRORS = frozenset({"empty_response", "incomplete_response"})


def _validation_diagnostics(schema: type[BaseModel], exc: ValidationError) -> list[dict[str, str]]:
    """Only schema-owned field names and built-in error codes may leave validation."""
    definition = schema.model_json_schema()
    diagnostics = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False)[:20]:
        node = definition
        path = []
        for part in error["loc"]:
            if "$ref" in node:
                node = definition.get("$defs", {}).get(node["$ref"].rsplit("/", 1)[-1], {})
            if isinstance(part, str) and part in node.get("properties", {}):
                path.append(part)
                node = node["properties"][part]
            elif isinstance(part, int) and node.get("type") == "array":
                path.append(str(part))
                node = node.get("items", {})
            else:
                # extra_forbidden locations can themselves contain response secrets.
                path.append("[unknown_field]")
                break
        code = error["type"]
        diagnostics.append({"path": ".".join(path) or "$",
                            "type": code if code in _VALIDATION_ERROR_TYPES else "value_error"})
    return diagnostics


def _retry_feedback(schema: type[BaseModel], errors: list[dict[str, str]]) -> str:
    details = "\n".join(f"{index}. {error['path']}: {error['type']}" for index, error in enumerate(errors, 1))
    evidence_rule = ("没有证据时使用 evidence=[]，提供的每条 Evidence 必须完整。"
                     if issubclass(schema, OpportunityResearchResult) else "")
    return ("\n上次返回的 JSON 有以下结构错误（字段路径: 错误类型）：\n" + details
            + "\n请只修正 JSON 结构和字段类型，不要增加任何来源材料中不存在的新事实。"
              "未知事实继续使用空字符串、空数组或 UNKNOWN；必填字段仍须明确返回。"
            + evidence_rule + "返回完整 JSON，不要解释。")


@dataclass
class RawProviderResult:
    text: str
    sources: list[SearchSource]
    search_calls: int = 0


@dataclass
class ProviderResult(Generic[T]):
    data: T
    sources: list[SearchSource]
    search_calls: int = 0


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

    async def generate(self, schema: type[T], instructions: str, prompt: str, **kwargs) -> T:
        return (await self.generate_result(schema, instructions, prompt, **kwargs)).data

    async def generate_result(self, schema: type[T], instructions: str, prompt: str, *, web_search: bool = False,
                              search_budget: int = 3, search_phase: str = "discovery") -> ProviderResult[T]:
        structured_failure = False
        for attempt in range(2):
            allowance = 0
            settled = False
            try:
                allowance = self.usage.reserve(search_budget if web_search else 0, phase=search_phase)
                if web_search and not allowance:
                    raise ProviderError("search_call_budget_exhausted")
                response = await self._request(schema, instructions, prompt, allowance)
                if isinstance(response, str):
                    response = RawProviderResult(response, [])
                self.usage.settle_search(search_phase, allowance, response.search_calls)
                settled = True
                data = schema.model_validate_json(response.text)
                if structured_failure:
                    logger.info("[OpportunityAI] structured output recovered provider=%s schema=%s attempt=%d",
                                self.name, schema.__name__, attempt + 1)
                return ProviderResult(data, response.sources, response.search_calls)
            except ValidationError as exc:
                reason = "invalid_structured_output"
                structured_failure = True
                errors = _validation_diagnostics(schema, exc)
                logger.warning("[OpportunityAI] invalid structured output provider=%s schema=%s attempt=%d errors=%s",
                               self.name, schema.__name__, attempt + 1, errors)
                if attempt == 0:
                    prompt += _retry_feedback(schema, errors)
            except ValueError as exc:
                # Only fixed provider codes leave this block; arbitrary exception
                # messages can contain model content or other sensitive values.
                reason = "invalid_structured_output"
                structured_failure = True
                error_type = exc.args[0] if exc.args and exc.args[0] in _SAFE_PROVIDER_VALUE_ERRORS else "value_error"
                errors = [{"path": "$", "type": error_type}]
                logger.warning("[OpportunityAI] invalid provider output provider=%s schema=%s attempt=%d errors=%s",
                               self.name, schema.__name__, attempt + 1, errors)
                if attempt == 0:
                    prompt += _retry_feedback(schema, errors)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                reason = f"provider_http_{code}"
                if code not in {408, 429, 500, 502, 503, 504}:
                    raise ProviderError(reason) from None
            except httpx.HTTPError:
                reason = "provider_network_error"
            except RuntimeError as exc:
                raise ProviderError(str(exc)) from None
            finally:
                if allowance and not settled:
                    self.usage.settle_search(search_phase, allowance)
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
