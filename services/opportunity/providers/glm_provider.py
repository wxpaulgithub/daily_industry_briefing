"""GLM Chat Completions: JSON output plus local strict validation and retry."""
import json

from .base import LLMProvider, ProviderCapabilities, ProviderError
from ..schemas import strict_schema


class GLMProvider(LLMProvider):
    name = "glm"

    def __init__(self, settings, usage, client=None):
        super().__init__(settings, usage, client)
        # Standard GLM chat is analysis only. Web search uses GLMWebSearch's
        # dedicated web-search-pro model and is deliberately kept separate.
        self.capabilities = ProviderCapabilities(False, False, True, False)

    async def _request(self, schema, instructions, prompt, search_budget):
        payload = {
            "model": self.settings.glm_model,
            "messages": [
                {"role": "system", "content": instructions + "\n只返回 JSON，严格遵守 Schema：" + json.dumps(strict_schema(schema), ensure_ascii=False)},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": self.settings.max_output_tokens,
            "stream": False,
        }
        data = await self._post(self.settings.glm_base_url + "/chat/completions", self.settings.glm_api_key, payload)
        usage = data.get("usage") or {}
        self.usage.record(self.name, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("empty_response")
        choice = choices[0]
        if choice.get("finish_reason") not in {"stop", None}:
            raise ValueError("incomplete_response")
        text = (choice.get("message") or {}).get("content")
        if not isinstance(text, str):
            raise ProviderError("provider_empty_output")
        return text
