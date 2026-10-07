import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.opportunity.ai_researcher import prefilter, research_candidate, research_opportunities
from services.opportunity.models import OpportunityBatch, OpportunityCandidate
from services.opportunity.pipeline import run_opportunity_pipeline
from services.opportunity.providers.base import ProviderError
from services.opportunity.runtime import UsageTracker, read_json, update_status
from services.opportunity.storage import load_snapshot, save_snapshot
from services.opportunity.verifier import SourceVerifier
from v2_helpers import NOW, candidate, documents, research_result, settings_in


class AIFallbackTests(unittest.TestCase):
    def setup_flow(self, settings, *, result=None, error=None):
        provider = SimpleNamespace(name="openai", usage=UsageTracker(settings),
                                   research=AsyncMock(return_value=result or research_result(), side_effect=error))
        search = SimpleNamespace(discover=AsyncMock(return_value=[]), find_sources=AsyncMock(return_value=[]))
        verifier = SourceVerifier({})
        verifier.fetch = AsyncMock(return_value=documents()[0])
        return provider, search, verifier

    def test_missing_configuration_uses_rule_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            with patch("services.opportunity.ai_researcher.local_now", return_value=NOW):
                result = asyncio.run(research_opportunities({}, settings, candidates=[candidate()]))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].research_mode, "fallback")
            self.assertEqual(read_json(settings.runtime_dir / "opportunity_ai_status.json")["fallback_reason"], "provider_not_configured")

    def test_provider_failure_falls_back_without_losing_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            provider, search, verifier = self.setup_flow(settings, error=ProviderError("provider_http_503"))
            with patch("services.opportunity.ai_researcher.local_now", return_value=NOW):
                result = asyncio.run(research_opportunities({}, settings, candidates=[candidate()], provider=provider, search=search, verifier=verifier))
            self.assertEqual(result[0].research_mode, "fallback")

    def test_search_failure_still_researches_existing_page(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            provider, search, verifier = self.setup_flow(settings)
            search.discover.side_effect = ProviderError("search_failed")
            search.find_sources.side_effect = ProviderError("search_failed")
            with patch("services.opportunity.ai_researcher.local_now", return_value=NOW):
                result = asyncio.run(research_opportunities({}, settings, candidates=[candidate()], provider=provider, search=search, verifier=verifier))
            self.assertEqual(result[0].research_mode, "ai")
            self.assertEqual(result[0].budget, "380万元")

    def test_semantic_rejection_is_not_reintroduced_by_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            provider, search, verifier = self.setup_flow(settings, result=research_result(is_real_project=False))
            with patch("services.opportunity.ai_researcher.local_now", return_value=NOW):
                result = asyncio.run(research_opportunities({}, settings, candidates=[candidate()], provider=provider, search=search, verifier=verifier))
            self.assertEqual(result, [])
            self.assertEqual(read_json(settings.runtime_dir / "opportunity_ai_status.json")["mode"], "ai")

    def test_broad_early_signal_is_not_blocked_by_keyword_score(self):
        candidate = OpportunityCandidate("制造企业包装后端生产物流项目拟建", "https://example.com/project")
        self.assertEqual(len(prefilter([candidate])), 1)

    def test_large_pool_retains_broad_wording_for_research(self):
        strong = [OpportunityCandidate(f"企业{i}智能仓储堆垛机采购公告", f"https://example.com/{i}") for i in range(100)]
        broad = OpportunityCandidate("制造企业包装后端生产物流拟建项目", "https://example.com/broad")
        selected = prefilter(strong + [broad], limit=40)
        self.assertEqual(len(selected), 40)
        self.assertIn(broad, selected)

    def test_unverified_ai_discovery_does_not_become_a_rule_opportunity(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            provider, search, verifier = self.setup_flow(settings)
            invented = candidate()
            invented.content = ""
            invented.discovery_method = "ai_search"
            search.discover.return_value = [invented]
            verifier.fetch.return_value = None
            result = asyncio.run(research_opportunities({}, settings, candidates=[], provider=provider, search=search, verifier=verifier))
            self.assertEqual(result, [])

    def test_search_excerpt_reaches_ai_when_original_page_is_unavailable(self):
        source = candidate()
        source.discovery_method = "ai_search"
        source.summary = source.content
        verifier = SourceVerifier({})
        verifier.fetch = AsyncMock(return_value=None)
        provider = SimpleNamespace(name="glm", research=AsyncMock(return_value=research_result()))
        search = SimpleNamespace(find_sources=AsyncMock(return_value=[]))
        result = asyncio.run(research_candidate(source, provider, search, verifier, {}, []))
        self.assertIsNotNone(result)
        self.assertEqual(result.research_mode, "preview")
        self.assertEqual(result.official_source_url, "")
        self.assertEqual(provider.research.await_count, 1)

    def test_publish_failure_preserves_successful_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, auto_publish=True)
            item = SourceVerifier({}).verify(candidate(), research_result(), documents())
            with patch("services.opportunity.pipeline.fetch_opportunities", new=AsyncMock(return_value=[item])), patch("services.opportunity.pipeline.publish_today", new=AsyncMock(side_effect=OSError)), patch("services.opportunity.pipeline.local_now", return_value=NOW):
                result = asyncio.run(run_opportunity_pipeline(settings))
            self.assertEqual(len(result), 1)
            status = read_json(settings.runtime_dir / "opportunity_ai_status.json")
            self.assertEqual(status["stage"], "DONE")
            self.assertEqual(status["last_publish_status"], "failed")
            self.assertEqual(load_snapshot("2026-10-07", settings.output_dir)[0].budget, "380万元")

    def test_second_timeout_records_failure_instead_of_leaving_research_active(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            with patch("services.opportunity.pipeline.fetch_opportunities", new=AsyncMock(side_effect=asyncio.TimeoutError)):
                with self.assertRaises(asyncio.TimeoutError):
                    asyncio.run(run_opportunity_pipeline(settings))
            self.assertEqual(read_json(settings.runtime_dir / "opportunity_ai_status.json")["stage"], "FAILED")

    def test_total_outage_retains_existing_daily_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            item = SourceVerifier({}).verify(candidate(), research_result(), documents())
            save_snapshot([item], "2026-10-07", settings.output_dir)
            update_status(settings, "RANKING", mode="fallback")
            with patch("services.opportunity.pipeline.fetch_opportunities", new=AsyncMock(return_value=OpportunityBatch([]))), patch("services.opportunity.pipeline.local_now", return_value=NOW), patch("services.opportunity.ai_researcher.local_now", return_value=NOW):
                result = asyncio.run(run_opportunity_pipeline(settings))
            self.assertEqual(len(result), 1)
            self.assertIn("cached_snapshot_fallback", result[0].risk_flags)
