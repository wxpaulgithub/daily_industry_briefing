import asyncio
import unittest

import httpx

from services.opportunity.facts import normalize_url
from services.opportunity.schemas import EvidenceItem, OpportunityResearchResult
from services.opportunity.verifier import SourceDocument, SourceVerifier, source_tier
from v2_helpers import URL, candidate, documents, research_result


class VerifierTests(unittest.TestCase):
    def test_valid_schema_without_evidence_does_not_verify_budget_or_project(self):
        research = OpportunityResearchResult.model_validate_json(
            '{"title":"样本制造企业自动化立体库改造采购公告","is_real_project":true,'
            '"warehouse_relevance":90,"stage":"PROCUREMENT","project_type":"RETROFIT",'
            '"budget_text":"380万元","evidence":[]}')
        self.assertEqual(research.budget_text, "380万元")
        self.assertIsNone(SourceVerifier({}).verify(candidate(), research, documents()))

    def test_title_evidence_does_not_verify_other_unsupported_facts(self):
        original = research_result()
        research = OpportunityResearchResult.model_validate_json(original.model_dump_json())
        research.evidence = [row for row in research.evidence if row.field == "title"]
        item = SourceVerifier({}).verify(candidate(), research, documents())
        self.assertIsNotNone(item)
        for field in ("owner", "province", "city", "published", "budget", "deadline"):
            self.assertEqual(getattr(item, field), "")
        self.assertEqual(item.stage, "UNKNOWN")
        self.assertIn("unsupported_budget", item.risk_flags)
        self.assertIn("stage_not_verified", item.risk_flags)

    def test_missing_or_invented_quote_does_not_create_budget(self):
        research = research_result(budget_text="999万元", summary="预算999万元，截止2026-02-31。")
        research.evidence = [row for row in research.evidence if row.field != "budget"] + [EvidenceItem(field="budget", value="999万元", source_title="公告", source_url=URL, quote="预算金额：999万元")]
        item = SourceVerifier({}).verify(candidate(), research, documents())
        self.assertEqual(item.budget, "")
        self.assertIn("unsupported_budget", item.risk_flags)
        self.assertNotIn("999万元", item.summary)
        self.assertNotIn("2026-02-31", item.summary)
        self.assertEqual(item.deadline, "2026-10-12 09:30")

    def test_supported_facts_and_official_url(self):
        item = SourceVerifier({}).verify(candidate(), research_result(), documents())
        self.assertEqual(item.budget, "380万元")
        self.assertEqual(item.official_source_url, URL)
        self.assertEqual(item.stage, "PROCUREMENT")

    def test_search_excerpt_is_labeled_preview_and_never_officially_verified(self):
        docs = documents()
        docs[0].retrieval_method = "search_excerpt"
        item = SourceVerifier({}).verify(candidate(), research_result(), docs)
        self.assertEqual(item.research_mode, "preview")
        self.assertEqual(item.source_tier, 3)
        self.assertEqual(item.source_name, "搜索摘要（原文未读取）")
        self.assertEqual(item.official_source_url, "")
        self.assertIn("search_excerpt_only", item.risk_flags)

    def test_html_parser_extracts_visible_text_when_trafilatura_is_empty(self):
        from services.opportunity.verifier import _VisibleText
        parser = _VisibleText()
        parser.feed("<html><head><title>Project page</title></head><body><nav>menu</nav><h1>Project</h1><p>" +
                    ("procurement details " * 10) + "</p><script>ignored secret</script></body></html>")
        text, title = parser.result()
        self.assertIn("procurement details", text)
        self.assertNotIn("ignored secret", text)
        self.assertEqual(title, "Project page")

    def test_award_quote_cannot_support_procurement(self):
        research = research_result()
        for proof in research.evidence:
            if proof.field == "stage":
                proof.quote = "中标公告"
        docs = documents()
        docs[0].text += " 中标公告"
        item = SourceVerifier({}).verify(candidate(), research, docs)
        self.assertEqual(item.stage, "UNKNOWN")

    def test_url_hostname_spoof_and_private_urls_are_rejected(self):
        self.assertEqual(source_tier("https://ccgp.gov.cn.evil.com/a", {}), 3)
        self.assertEqual(source_tier("https://corp.example.com/a", {"trusted_domains": ["example.com"]}), 1)
        for url in ("http://127.0.0.1/a", "http://localhost/a", "file:///etc/passwd", "http://10.0.0.1", "https://user:secret@example.com/a"):
            self.assertEqual(normalize_url(url), "")

    def test_redirect_to_private_page_is_not_fetched(self):
        requested = []
        def respond(request):
            requested.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                result = await SourceVerifier({}, client, check_dns=False).fetch("https://example.com/project")
                self.assertIsNone(result)
        asyncio.run(run())
        self.assertEqual(len(requested), 1)
