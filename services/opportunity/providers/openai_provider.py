"""OpenAI Responses REST API with strict JSON Schema and bounded web search."""
from .base import LLMProvider, ProviderCapabilities, ProviderError, RawProviderResult
from ..models import SearchSource
from ..runtime import local_now
from ..facts import normalize_url
from ..schemas import strict_schema


class OpenAIProvider(LLMProvider):
    name = "openai"
    capabilities = ProviderCapabilities(True, True, True, True)

    async def _request(self, schema, instructions, prompt, search_budget):
        payload = {
            "model": self.settings.openai_model, "instructions": instructions, "input": prompt,
            "store": False, "max_output_tokens": self.settings.max_output_tokens,
            "text": {"format": {"type": "json_schema", "name": schema.__name__, "strict": True,
                                "schema": strict_schema(schema)}},
        }
        if self.settings.reasoning:
            payload["reasoning"] = {"effort": self.settings.reasoning}
        if search_budget:
            payload.update(tools=[{"type": "web_search"}], tool_choice="required",
                           max_tool_calls=search_budget, include=["web_search_call.action.sources"])
        data = await self._post(self.settings.openai_base_url + "/responses", self.settings.openai_api_key, payload)
        usage = data.get("usage") or {}
        output = data.get("output") or []
        self.usage.record(self.name, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
                          sum(item.get("type") == "web_search_call" for item in output))
        if data.get("status") != "completed":
            raise ValueError("incomplete_response")
        parts = [part for item in output if item.get("type") == "message" for part in item.get("content", [])]
        if any(part.get("type") == "refusal" for part in parts):
            raise ProviderError("provider_refusal")
        sources = {}
        for item in output:
            if item.get("type") != "web_search_call":
                continue
            action = item.get("action") or {}
            rows = list(action.get("sources") or [])
            if action.get("url"):
                rows.append({"url": action["url"]})
            for row in rows:
                url = normalize_url(row.get("url", "")) if isinstance(row, dict) else ""
                if url:
                    sources[url] = SearchSource(url, str(row.get("title") or ""), origin="openai_web_search",
                                                retrieved_at=local_now().isoformat())
        return RawProviderResult("".join(part.get("text", "") for part in parts if part.get("type") == "output_text"),
                                 list(sources.values()), sum(item.get("type") == "web_search_call" for item in output))
