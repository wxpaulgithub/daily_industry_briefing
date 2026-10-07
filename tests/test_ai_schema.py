import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from services.opportunity.ai_client import create_llm_provider
from services.opportunity.providers.base import ProviderError
from services.opportunity.runtime import UsageTracker
from services.opportunity.schemas import OpportunityResearchResult, SearchQueries, strict_schema
from services.opportunity.settings import OpportunitySettings
from v2_helpers import research_result


class AISchemaTests(unittest.TestCase):
    def test_unknown_stage_and_extra_facts_are_rejected(self):
        data = research_result().model_dump()
        for change in ({"stage": "ACTIVE"}, {"guessed_budget": "1亿元"}, {"warehouse_relevance": 101}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                OpportunityResearchResult.model_validate({**data, **change})

    def test_schema_is_strict_for_nested_evidence(self):
        schema = strict_schema(OpportunityResearchResult)
        for node in [schema, schema["$defs"]["EvidenceItem"]]:
            self.assertFalse(node["additionalProperties"])
            self.assertEqual(set(node["required"]), set(node["properties"]))

    def test_openai_payload_and_usage(self):
        requests = []
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "completed", "output": [
                {"type": "web_search_call"}, {"type": "message", "content": [{"type": "output_text", "text": '{"queries":["立库采购"]}'}]}],
                "usage": {"input_tokens": 100, "output_tokens": 20}})
        async def run():
            settings = OpportunitySettings(openai_api_key="fake-secret", openai_model="configured-model")
            usage = UsageTracker(settings)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                provider = create_llm_provider(settings, usage, client=client)
                result = await provider.generate(SearchQueries, "instructions", "query", web_search=True, search_budget=2)
            self.assertEqual(result.queries, ["立库采购"])
            self.assertEqual(usage.search_calls, 1)
            self.assertEqual(usage.input_tokens, 100)
        asyncio.run(run())
        self.assertEqual(requests[0]["model"], "configured-model")
        self.assertTrue(requests[0]["text"]["format"]["strict"])
        self.assertEqual(requests[0]["tools"], [{"type": "web_search"}])
        self.assertEqual(requests[0]["max_tool_calls"], 2)
        self.assertNotIn("fake-secret", json.dumps(requests))

    def test_glm_retries_invalid_json_without_native_search(self):
        bodies = []
        def respond(request):
            bodies.append(json.loads(request.content))
            content = '{"queries":[1]}' if len(bodies) == 1 else '{"queries":["仓储改造"]}'
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        async def run():
            settings = OpportunitySettings(provider="glm", glm_api_key="fake-glm", glm_model="configured-glm")
            usage = UsageTracker(settings)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                provider = create_llm_provider(settings, usage, client=client)
                with patch("services.opportunity.providers.base.asyncio.sleep", return_value=None):
                    result = await provider.discover("instructions", "query")
                self.assertFalse(provider.capabilities.native_web_search)
                self.assertEqual(result.queries, ["仓储改造"])
            self.assertEqual(usage.input_tokens, 20)
        asyncio.run(run())
        self.assertEqual(len(bodies), 2)
        self.assertNotIn("tools", bodies[0])

    def test_error_response_does_not_expose_secrets(self):
        async def run():
            settings = OpportunitySettings(openai_api_key="fake-secret", openai_model="model")
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401, text="fake-secret"))) as client:
                provider = create_llm_provider(settings, UsageTracker(settings), client=client)
                with self.assertRaisesRegex(ProviderError, "provider_http_401") as caught:
                    await provider.discover("instructions", "query")
                self.assertNotIn("fake-secret", str(caught.exception))
        asyncio.run(run())
