import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from services.opportunity.ai_client import create_llm_provider
from services.opportunity.providers.base import ProviderError
from services.opportunity.runtime import UsageTracker
from services.opportunity.schemas import OpportunityResearchResult, SearchQueries, strict_schema
from services.opportunity.settings import OpportunitySettings
from v2_helpers import research_result


class AISchemaTests(unittest.TestCase):
    def minimal_research(self):
        return {"title": "某自动化立库项目", "is_real_project": True, "warehouse_relevance": 90,
                "stage": "PROCUREMENT", "project_type": "NEW_BUILD"}

    async def run_glm(self, responses, schema=OpportunityResearchResult):
        bodies = []
        def respond(request):
            bodies.append(json.loads(request.content))
            content = responses[len(bodies) - 1]
            data = ({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
                    if isinstance(content, str) else content)
            return httpx.Response(200, json=data)
        settings = OpportunitySettings(provider="glm", glm_api_key="fake-glm-secret", glm_model="configured-glm")
        usage = UsageTracker(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = create_llm_provider(settings, usage, client=client)
            with patch("services.opportunity.providers.base.asyncio.sleep", return_value=None):
                result = await provider.generate(schema, "instructions", "original-query")
        return result, bodies, usage

    def test_glm_missing_noncritical_fields_parses_on_first_attempt(self):
        result, bodies, usage = asyncio.run(self.run_glm([json.dumps(self.minimal_research())]))
        for field in ("owner", "province", "city", "published", "budget_text", "deadline",
                      "summary", "opportunity_reason", "entry_point"):
            self.assertEqual(getattr(result, field), "")
        for field in ("technical_scope", "source_urls", "evidence"):
            self.assertEqual(getattr(result, field), [])
        self.assertEqual(len(bodies), 1)
        self.assertEqual(usage.calls, 1)

    def test_all_core_fields_remain_required(self):
        core = {"title", "is_real_project", "warehouse_relevance", "stage", "project_type"}
        self.assertEqual({name for name, field in OpportunityResearchResult.model_fields.items() if field.is_required()}, core)
        for field in core:
            data = self.minimal_research()
            del data[field]
            with self.subTest(field=field), self.assertRaises(ValidationError) as caught:
                OpportunityResearchResult.model_validate_json(json.dumps(data))
            self.assertIn({"loc": (field,), "type": "missing"},
                          [{"loc": error["loc"], "type": error["type"]} for error in caught.exception.errors()])

    def test_partial_evidence_is_rejected(self):
        data = {**self.minimal_research(), "evidence": [{"field": "budget", "value": "380万元"}]}
        with self.assertRaises(ValidationError) as caught:
            OpportunityResearchResult.model_validate_json(json.dumps(data))
        self.assertEqual({error["loc"] for error in caught.exception.errors()},
                         {("evidence", 0, name) for name in ("source_title", "source_url", "quote")})

    def test_defaults_do_not_accept_wrong_types_or_null(self):
        for change in ({"city": None}, {"technical_scope": "WMS"}, {"source_urls": [1]},
                       {"evidence": None}, {"is_real_project": "true"}, {"warehouse_relevance": "90"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                OpportunityResearchResult.model_validate_json(json.dumps({**self.minimal_research(), **change}))

    def test_default_lists_are_independent(self):
        first = OpportunityResearchResult.model_validate(self.minimal_research())
        second = OpportunityResearchResult.model_validate(self.minimal_research())
        first.technical_scope.append("WMS")
        first.source_urls.append("https://example.com")
        first.evidence.extend(research_result().evidence)
        self.assertEqual(second.technical_scope, [])
        self.assertEqual(second.source_urls, [])
        self.assertEqual(second.evidence, [])

    def test_retry_gets_specific_field_errors_and_recovers(self):
        with self.assertLogs("services.opportunity.providers.base", level="INFO") as logs:
            result, bodies, usage = asyncio.run(self.run_glm([
                '{"stage":"TENDERING"}', json.dumps(self.minimal_research())]))
        feedback = bodies[1]["messages"][1]["content"]
        self.assertTrue(feedback.startswith("original-query\n"))
        self.assertIn("stage: literal_error", feedback)
        self.assertIn("title: missing", feedback)
        self.assertIn("不要增加任何来源材料中不存在的新事实", feedback)
        self.assertIn("evidence=[]", feedback)
        self.assertNotIn("TENDERING", feedback)
        self.assertEqual(result.stage, "PROCUREMENT")
        self.assertEqual(usage.calls, 2)
        self.assertIn("schema=OpportunityResearchResult attempt=1", logs.output[0])
        self.assertIn("literal_error", logs.output[0])
        self.assertIn("structured output recovered", logs.output[1])
        self.assertIn("attempt=2", logs.output[1])

    def test_feedback_and_logs_exclude_response_values_and_unknown_field_names(self):
        secret = "Authorization: Bearer fake-glm-secret\n完整网页正文-secret"
        data = {**self.minimal_research(), "title": {"raw": secret}, "stage": secret,
                "warehouse_relevance": secret, secret: secret,
                "evidence": [{"field": "budget", "value": secret, "quote": secret, secret: secret}]}
        raw = json.dumps(data, ensure_ascii=False)
        with self.assertLogs("services.opportunity.providers.base", level="INFO") as logs:
            _, bodies, _ = asyncio.run(self.run_glm([raw, json.dumps(self.minimal_research())]))
        feedback = bodies[1]["messages"][1]["content"]
        self.assertIn("evidence.0.source_title: missing", feedback)
        self.assertIn("evidence.0.source_url: missing", feedback)
        self.assertIn("warehouse_relevance: float_type", feedback)
        self.assertIn("[unknown_field]: extra_forbidden", feedback)
        for output in (feedback, "\n".join(logs.output)):
            for sensitive in (raw, secret, "fake-glm-secret", "Authorization", "完整网页正文-secret"):
                self.assertNotIn(sensitive, output)
        self.assertNotIn("original-query", "\n".join(logs.output))
        self.assertNotIn("instructions", "\n".join(logs.output))

    def test_invalid_json_feedback_excludes_parser_context(self):
        raw = 'Authorization fake-glm-secret 完整网页正文-secret {"stage":'
        with self.assertLogs("services.opportunity.providers.base", level="INFO") as logs:
            _, bodies, _ = asyncio.run(self.run_glm([raw, json.dumps(self.minimal_research())]))
        feedback = bodies[1]["messages"][1]["content"]
        self.assertIn("$: json_invalid", feedback)
        for output in (feedback, "\n".join(logs.output)):
            self.assertNotIn("fake-glm-secret", output)
            self.assertNotIn(raw, output)

    def test_incomplete_provider_response_has_safe_reason_feedback(self):
        raw = {"choices": [{"finish_reason": "length", "message": {"content": "fake-glm-secret"}}]}
        with self.assertLogs("services.opportunity.providers.base", level="INFO") as logs:
            _, bodies, _ = asyncio.run(self.run_glm([raw, json.dumps(self.minimal_research())]))
        feedback = bodies[1]["messages"][1]["content"]
        self.assertIn("$: incomplete_response", feedback)
        self.assertNotIn("fake-glm-secret", feedback + "\n".join(logs.output))
        self.assertIn("invalid provider output", logs.output[0])

    def test_unrecognized_value_error_is_not_exposed(self):
        secret = "Authorization fake-glm-secret complete model response"
        settings = OpportunitySettings(provider="glm", glm_api_key="fake-glm-secret", glm_model="configured-glm")
        usage = UsageTracker(settings)
        provider = create_llm_provider(settings, usage)
        provider._request = AsyncMock(side_effect=[ValueError(secret), json.dumps(self.minimal_research())])
        async def run():
            with patch("services.opportunity.providers.base.asyncio.sleep", return_value=None):
                return await provider.generate(OpportunityResearchResult, "secret instructions", "secret prompt")
        with self.assertLogs("services.opportunity.providers.base", level="INFO") as logs:
            result = asyncio.run(run())
        self.assertEqual(result.stage, "PROCUREMENT")
        output = "\n".join(logs.output)
        self.assertIn("'type': 'value_error'", output)
        for sensitive in (secret, "fake-glm-secret", "secret instructions", "secret prompt"):
            self.assertNotIn(sensitive, output)

    def test_failed_retry_logs_both_attempts_and_preserves_reason_code(self):
        with self.assertLogs("services.opportunity.providers.base", level="INFO") as logs:
            with self.assertRaisesRegex(ProviderError, "^invalid_structured_output$"):
                asyncio.run(self.run_glm(['{"stage":"TENDERING"}'] * 2))
        self.assertEqual(len(logs.output), 2)
        self.assertIn("attempt=1", logs.output[0])
        self.assertIn("attempt=2", logs.output[1])
        self.assertNotIn("structured output recovered", "\n".join(logs.output))

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
        feedback = bodies[1]["messages"][1]["content"]
        self.assertIn("queries.0: string_type", feedback)
        self.assertNotIn("evidence=[]", feedback)

    def test_error_response_does_not_expose_secrets(self):
        async def run():
            settings = OpportunitySettings(openai_api_key="fake-secret", openai_model="model")
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401, text="fake-secret"))) as client:
                provider = create_llm_provider(settings, UsageTracker(settings), client=client)
                with self.assertRaisesRegex(ProviderError, "provider_http_401") as caught:
                    await provider.discover("instructions", "query")
                self.assertNotIn("fake-secret", str(caught.exception))
        asyncio.run(run())
