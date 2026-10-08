import unittest
from datetime import timedelta

from services.opportunity.ai_researcher import prefilter
from services.opportunity.models import OpportunityCandidate
from services.opportunity.runtime import local_now
from services.opportunity.search.glm_web_search import GLMWebSearch


class OpportunityQualityTests(unittest.TestCase):
    def test_each_search_angle_survives_candidate_cap(self):
        batches = [[OpportunityCandidate(f"topic{i}-{j}", f"https://example.com/{i}/{j}")
                    for j in range(50)] for i in range(6)]
        rows = GLMWebSearch._balanced_candidates(batches, 50)
        self.assertEqual(len(rows), 50)
        self.assertEqual({row.title.split("-")[0] for row in rows}, {f"topic{i}" for i in range(6)})
        self.assertLessEqual(max(sum(row.title.startswith(f"topic{i}-") for row in rows) for i in range(6)), 9)

    def test_old_tender_cannot_consume_a_research_slot(self):
        old = OpportunityCandidate("制造企业堆垛机采购公告", "https://example.com/old",
            published_ts=(local_now() - timedelta(days=240)).timestamp())
        self.assertEqual(prefilter([old]), [])

    def test_still_open_extended_tender_remains_a_candidate(self):
        row = OpportunityCandidate("堆垛机采购公告", "https://example.com/extended",
            published_ts=(local_now() - timedelta(days=60)).timestamp(),
            summary="投标截止时间：" + (local_now() + timedelta(days=10)).strftime("%Y-%m-%d"))
        self.assertEqual(prefilter([row]), [row])

    def test_official_original_has_priority_over_same_topic_repost(self):
        repost = OpportunityCandidate("制造企业堆垛机采购公告", "https://news.example.com/a", discovery_method="ai_search")
        original = OpportunityCandidate("制造企业堆垛机采购公告", "https://procurement.example.com/a")
        self.assertEqual(prefilter([repost, original], config={"trusted_domains": ["procurement.example.com"]})[0], original)

    def test_contract_award_word_cannot_close_a_current_tender(self):
        from services.opportunity.rules import candidate_stage
        row = OpportunityCandidate("WMS系统采购项目（重新招标）", "https://example.com/wms",
            content="本项目公开招标，投标人不得发生骗取中标的情况。")
        self.assertEqual(candidate_stage(row), "PROCUREMENT")

    def test_procurement_intention_is_an_early_signal(self):
        from services.opportunity.rules import candidate_stage
        row = OpportunityCandidate("智能仓储及动力传送带采购项目意向公开", "https://example.com/intention")
        self.assertEqual(candidate_stage(row), "EARLY_SIGNAL")


class SearchAndSourceQualityTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_cannot_replace_core_software_and_intention_searches(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        import json
        search = GLMWebSearch(SimpleNamespace(), None)
        with patch.object(search, "_plan_queries", new=AsyncMock(return_value=["重复主题"] * 6)), \
             patch.object(search, "_search", new=AsyncMock(return_value=[])) as requests:
            await search.discover(json.dumps({"company_profile": {"preferred_regions": ["江苏"]}}))
        queries = [call.args[0] for call in requests.await_args_list]
        self.assertGreaterEqual(len(queries), 8)
        self.assertLessEqual(len(queries), 10)
        self.assertTrue(any("中国 WMS WCS" in q for q in queries))
        self.assertTrue(any("采购意向" in q for q in queries))
        self.assertTrue(all(local_now().date().isoformat() in q for q in queries))

    async def test_wechat_captcha_200_is_not_original_evidence(self):
        import httpx
        from services.opportunity.verifier import SourceVerifier
        url = "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha"
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                httpx.Response(200, text="captcha" * 30, headers={"Content-Type": "text/html"}))) as client:
            verifier = SourceVerifier({}, client, check_dns=False)
            self.assertIsNone(await verifier.fetch(url))
            self.assertIn("source_access_challenge", verifier.failures[url])

    async def test_compressed_html_is_decoded_only_once(self):
        import gzip
        import httpx
        from services.opportunity.verifier import SourceVerifier
        html = '<html><body><article><h1>WMS系统采购招标公告</h1><p>' + ('采购仓储管理系统，报名及投标以原文公告为准。' * 12) + '</p></article></body></html>'
        raw = gzip.compress(html.encode('utf-8'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                httpx.Response(200, content=raw, headers={"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"}))) as client:
            verifier = SourceVerifier({}, client, check_dns=False)
            doc = await verifier.fetch("https://example.com/compressed-notice")
            self.assertIsNotNone(doc)
            self.assertIn("仓储管理系统", doc.text)
            self.assertEqual(doc.retrieval_method, "html")
