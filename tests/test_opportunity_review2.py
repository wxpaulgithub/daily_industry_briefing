import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
import io
import json
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from services.generator import render_html
from services.opportunity.ai_client import create_llm_provider
from services.opportunity.ai_researcher import prefilter, research_candidate, research_opportunities
from services.opportunity.evaluation import quality_metrics, validate_dataset, longitudinal_metrics
from services.opportunity.facts import content_signature
from services.opportunity.models import OpportunityBatch, OpportunityCandidate, is_unverified
from services.opportunity.pipeline import run_opportunity_pipeline
from services.opportunity.project_memory import apply_history, material_hash, needs_report, notice_version, split_reusable
from services.opportunity.publisher import publish_today
from services.opportunity.runtime import UsageTracker, read_json, update_status
from services.opportunity.schemas import SearchQueries
from services.opportunity.settings import OpportunitySettings
from services.opportunity.storage import save_snapshot
from services.opportunity.verifier import SourceDocument, SourceVerifier
from v2_helpers import NOW, URL, TITLE, TEXT, candidate, documents, research_result, settings_in


def sample():
    item = SourceVerifier({}).verify(candidate(), research_result(), documents())
    item.priority = "A"
    return item


def pdf_bytes(text="Warehouse procurement budget 3800000 deadline 2026-10-12. " * 3, blank=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if not blank:
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 30 700 Td (" + text + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class P0RegressionTests(unittest.TestCase):
    def test_rejected_project_cannot_return_from_same_day_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            save_snapshot([sample()], "2026-10-07", settings.output_dir)
            provider = SimpleNamespace(name="openai", usage=UsageTracker(settings), research=AsyncMock(return_value=research_result(is_real_project=False)))
            search = SimpleNamespace(discover=AsyncMock(return_value=[]), find_sources=AsyncMock(return_value=[]))
            verifier = SourceVerifier({})
            verifier.fetch = AsyncMock(return_value=documents()[0])
            async def run_actual(**kwargs):
                return await research_opportunities({}, settings, candidates=[candidate()], provider=provider, search=search, verifier=verifier)
            with patch("services.opportunity.pipeline.fetch_opportunities", new=run_actual), patch("services.opportunity.pipeline.local_now", return_value=NOW), patch("services.opportunity.ai_researcher.local_now", return_value=NOW):
                result = asyncio.run(run_opportunity_pipeline(settings, publish=False))
            status = read_json(settings.runtime_dir / "opportunity_ai_status.json")
            self.assertEqual(result, [])
            self.assertEqual((status["mode"], status["stage"], status["fallback_reason"]), ("ai", "DONE", ""))
            self.assertFalse(status["cached_fallback"])

    def test_preview_stays_blocked_after_cache_failure_and_timeout(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as directory:
                settings = settings_in(directory, webhook_url="https://example.com/mock")
                item = sample()
                item.research_mode = "preview"
                item.verification_status = "preview"
                item.official_source_url = ""
                save_snapshot([item], "2026-10-07", settings.output_dir)
                update_status(settings, "RANKING", mode="fallback")
                fetch = AsyncMock(side_effect=asyncio.TimeoutError) if timeout else AsyncMock(return_value=OpportunityBatch([]))
                send = AsyncMock(return_value={"ok": True})
                with patch("services.opportunity.pipeline.fetch_opportunities", new=fetch), patch("services.opportunity.pipeline.local_now", return_value=NOW), patch("services.opportunity.ai_researcher.local_now", return_value=NOW), patch("services.opportunity.publisher.local_now", return_value=NOW), patch("services.opportunity.publisher._send", new=send):
                    result = asyncio.run(run_opportunity_pipeline(settings, publish=False))
                    outcome = asyncio.run(publish_today(settings, date_str="2026-10-07"))
                self.assertEqual(outcome["status"], "no_new_opportunities")
                self.assertTrue(is_unverified(result[0]))
                self.assertEqual(result[0].official_source_url, "")
                self.assertEqual(result[0].priority, "WATCH")
                send.assert_not_awaited()

    def test_legacy_excerpt_risk_cannot_be_laundered_by_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, webhook_url="https://example.com/mock")
            item = sample()
            item.verification_status = ""
            item.research_mode = "fallback"
            item.risk_flags = ["search_excerpt_only"]
            save_snapshot([item], "2026-10-07", settings.output_dir)
            with patch("services.opportunity.publisher._send", new=AsyncMock()) as send:
                self.assertEqual(asyncio.run(publish_today(settings, date_str="2026-10-07"))["count"], 0)
                send.assert_not_awaited()

    def test_no_candidates_is_successful_empty_ai_run(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            provider = SimpleNamespace(name="openai", usage=UsageTracker(settings))
            search = SimpleNamespace(discover=AsyncMock(return_value=[]))
            result = asyncio.run(research_opportunities({}, settings, candidates=[], provider=provider, search=search))
            status = read_json(settings.runtime_dir / "opportunity_ai_status.json")
            self.assertEqual(result, [])
            self.assertEqual((status["mode"],status["fallback_reason"]),("ai",""))

    def test_model_summary_is_not_search_evidence(self):
        source = candidate()
        source.discovery_method = "ai_search"
        source.content = ""
        verifier = SourceVerifier({})
        verifier.fetch = AsyncMock(return_value=None)
        provider = SimpleNamespace(research=AsyncMock())
        search = SimpleNamespace(find_sources=AsyncMock(return_value=[]))
        result = asyncio.run(research_candidate(source, provider, search, verifier, {}, []))
        self.assertIsNone(result)
        provider.research.assert_not_awaited()


class BudgetAndSourcesTests(unittest.TestCase):
    def test_discovery_cannot_consume_verification_allowance(self):
        tracker = UsageTracker(OpportunitySettings(total_search_budget=24))
        self.assertEqual(tracker.reserve(20, phase="discovery"), 8)
        with self.assertRaisesRegex(RuntimeError, "search_call_budget_exhausted"):
            tracker.reserve(1, phase="discovery")
        self.assertEqual(tracker.reserve(12, phase="verification"), 12)

    def test_success_releases_unused_reservation_failure_keeps_it(self):
        tracker = UsageTracker(OpportunitySettings(total_search_budget=24))
        allowance = tracker.reserve(6)
        tracker.settle_search("discovery", allowance, 1)
        self.assertEqual(tracker.remaining("discovery"), 7)
        allowance = tracker.reserve(7)
        tracker.settle_search("discovery", allowance)
        self.assertEqual(tracker.remaining("discovery"), 0)
        self.assertEqual(tracker.search_summary()["search_phases"]["discovery"]["unknown_requests"], 1)

    def test_shared_hard_limit_is_enforced(self):
        tracker = UsageTracker(OpportunitySettings(total_search_budget=3))
        tracker.reserve(2)
        self.assertEqual(tracker.reserve(5, phase="verification"), 1)
        with self.assertRaises(RuntimeError):
            tracker.reserve(1)

    def test_legacy_limit_and_new_total_precedence(self):
        with patch.dict("os.environ", {"OPENAI_OPPORTUNITY_MAX_TOOL_CALLS":"3"}, clear=True):
            self.assertEqual(OpportunitySettings.from_env().total_search_budget, 3)
            with patch.dict("os.environ", {"OPPORTUNITY_TOTAL_SEARCH_BUDGET":"24"}):
                self.assertEqual(OpportunitySettings.from_env().total_search_budget,24)

    def test_concurrent_openai_sources_remain_per_response(self):
        async def respond(request):
            payload = json.loads(request.content)
            tag = payload["input"]
            await asyncio.sleep(0)
            return httpx.Response(200, json={"status":"completed", "output":[
                {"type":"web_search_call", "action":{"type":"search", "sources":[{"type":"url", "url":f"https://example.com/{tag}"}]}},
                {"type":"message", "content":[{"type":"output_text", "text":'{"queries":["test"]}'}]}]})
        async def run():
            settings = OpportunitySettings(openai_api_key="fake",openai_model="fake",total_search_budget=24)
            usage = UsageTracker(settings)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                provider = create_llm_provider(settings,usage,client=client)
                results = await asyncio.gather(*(provider.generate_result(SearchQueries,"test",tag,web_search=True,search_budget=3) for tag in ("a","b")))
            self.assertEqual([row.sources[0].url for row in results],["https://example.com/a","https://example.com/b"])
            self.assertTrue(all(not row.sources[0].snippet for row in results))
            self.assertEqual(usage.search_summary()["discovery_search_calls"],2)
            self.assertEqual(usage.reserved_search_calls,2)
        asyncio.run(run())

    def test_actual_tool_source_reaches_original_fetch(self):
        source = candidate()
        source.url = "https://example.com/repost"
        source.discovery_method = "ai_search"
        source.search_sources = [{"url":URL,"origin":"openai_web_search"}]
        verifier = SourceVerifier({})
        verifier.fetch = AsyncMock(side_effect=lambda url:documents()[0] if url == URL else None)
        provider = SimpleNamespace(name="openai",research=AsyncMock(return_value=research_result()))
        search = SimpleNamespace(find_sources=AsyncMock(return_value=[]))
        result = asyncio.run(research_candidate(source,provider,search,verifier,{},[]))
        self.assertEqual(result.official_source_url,URL)
        self.assertIn(URL,[call.args[0] for call in verifier.fetch.await_args_list])


class MemoryAndRecallTests(unittest.TestCase):
    def test_unchanged_verified_original_is_reused_without_research(self):
        item = sample()
        item.source_content_hash = content_signature(TEXT)
        item.last_researched_at = NOW.timestamp()-3600
        source = candidate()
        source.verified_url = URL
        source.content_hash = item.source_content_hash
        pending,reused = split_reusable([source],{item.project_key:item.to_dict()},NOW.timestamp())
        self.assertEqual(pending,[])
        self.assertEqual(len(reused),1)
        self.assertTrue(needs_report(reused[0]))  # Not yet delivered stays reportable.

    def test_unknown_changed_or_expired_original_is_not_skipped(self):
        item = sample()
        item.source_content_hash = content_signature(TEXT)
        item.last_researched_at = NOW.timestamp()-3600
        source = candidate()
        source.verified_url = URL
        for signature, age, mode in (("",3600,"ai"),("new-version",3600,"ai"),(item.source_content_hash,90000,"ai"),(item.source_content_hash,3600,"preview")):
            with self.subTest(signature=signature,age=age,mode=mode):
                source.content_hash = signature
                item.last_researched_at = NOW.timestamp()-age
                item.research_mode = mode
                pending,reused = split_reusable([source],{item.project_key:item.to_dict()},NOW.timestamp())
                self.assertEqual(len(pending),1)
                self.assertEqual(reused,[])

    def test_retender_and_clarification_are_material_updates(self):
        old = sample()
        old.last_reported_at = 100
        old.last_digest_hash = material_hash(old)
        for title in (TITLE.replace("采购公告","二次招标"),TITLE.replace("采购公告","澄清公告")):
            item = replace(old,title=title,project_key="",source_url=URL+"/new")
            item.notice_version = notice_version(title,item.published,item.source_url)
            apply_history([item],{old.project_key:old.to_dict()})
            self.assertTrue(item.is_updated)
            self.assertTrue(needs_report(item))

    def test_discovery_receives_recent_history_before_research(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory)
            item = sample()
            item.last_seen_at = NOW.timestamp()
            save_snapshot([item],"2026-10-07",settings.output_dir)
            provider = SimpleNamespace(name="openai",usage=UsageTracker(settings))
            search = SimpleNamespace(discover=AsyncMock(return_value=[]))
            with patch("services.opportunity.ai_researcher.local_now",return_value=NOW):
                asyncio.run(research_opportunities({},settings,candidates=[],provider=provider,search=search))
            prompt = json.loads(search.discover.await_args.args[0])
            self.assertEqual(prompt["recent_projects"][0]["title"],TITLE)

    def test_negated_services_are_not_hard_excluded(self):
        self.assertEqual(len(prefilter([OpportunityCandidate("堆垛机设备采购公告","https://example.com/a",content="设备采购，不含运输服务。") ])),1)
        self.assertEqual(prefilter([OpportunityCandidate("物流运输服务采购公告","https://example.com/a")]),[])


class PDFEvidenceTests(unittest.TestCase):
    def test_real_text_pdf_is_extracted_by_isolated_worker(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={"content-type":"application/pdf"},content=pdf_bytes()))) as client:
                doc = await SourceVerifier({},client,check_dns=False).fetch("https://example.com/file.pdf")
            self.assertEqual(doc.retrieval_method,"pdf")
            self.assertIn("Warehouse procurement",doc.text)
            self.assertEqual(doc.pages[0]["page"],1)
        asyncio.run(run())

    def test_blank_pdf_records_text_unavailable(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={"content-type":"application/pdf"},content=pdf_bytes(blank=True)))) as client:
                verifier = SourceVerifier({},client,check_dns=False)
                self.assertIsNone(await verifier.fetch("https://example.com/scan.pdf"))
                self.assertIn("pdf_text_unavailable",verifier.failures["https://example.com/scan.pdf"])
        asyncio.run(run())

    def test_quote_page_is_preserved_and_fabricated_quote_is_rejected(self):
        docs = documents()
        docs[0].retrieval_method="pdf"
        docs[0].pages=[{"page":4,"text":TEXT}]
        result = SourceVerifier({}).verify(candidate(),research_result(),docs)
        self.assertTrue(all(row["page"]==4 for row in result.evidence))
        research=research_result()
        for proof in research.evidence:
            if proof.field=="budget":
                proof.quote="预算金额：999万元"
        self.assertEqual(SourceVerifier({}).verify(candidate(),research,docs).budget,"")

    def test_html_direct_attachment_is_discovered(self):
        async def run():
            html="<html><p>"+TEXT+"</p><a href='/annex.pdf'>技术规格</a><a href='http://127.0.0.1/private.pdf'>invalid</a></html>"
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={"content-type":"text/html;charset=utf-8"},text=html))) as client:
                doc=await SourceVerifier({},client,check_dns=False).fetch("https://example.com/notice")
            self.assertEqual(doc.attachment_urls,["https://example.com/annex.pdf"])
        asyncio.run(run())

    def test_unrelated_page_cannot_supply_another_projects_budget(self):
        research=research_result(budget_text="999万元")
        other_url="https://example.com/other"
        for proof in research.evidence:
            if proof.field=="budget":
                proof.value="999万元";proof.quote="预算金额：999万元";proof.source_url=other_url
        docs=documents()+[SourceDocument(other_url,"另一个项目。预算金额：999万元。")]
        result=SourceVerifier({}).verify(candidate(),research,docs)
        self.assertEqual(result.budget,"")

    def test_attachment_can_support_parent_projects_budget(self):
        research=research_result()
        url="https://example.com/annex.pdf"
        for proof in research.evidence:
            if proof.field=="budget":
                proof.source_url=url
        doc=SourceDocument(url,"预算金额：380万元",retrieval_method="pdf",pages=[{"page":2,"text":"预算金额：380万元"}],parent_url=URL)
        result=SourceVerifier({}).verify(candidate(),research,documents()+[doc])
        self.assertEqual(result.budget,"380万元")
        self.assertEqual(next(row for row in result.evidence if row["field"]=="budget")["page"],2)


class EvaluationAndNewsTests(unittest.TestCase):
    def case(self):
        return {"title":TITLE,"url":URL+"?utm_source=test","expected_relevant":True,"expected_budget":"380万元","human_reason":"synthetic unit test only"}

    def test_url_normalization_and_fact_completeness(self):
        case=self.case()
        metrics=quality_metrics([case],[sample()])
        self.assertEqual(metrics["recall"],1)
        self.assertEqual(metrics["fact_completeness"]["budget"],1)
        metrics=quality_metrics([case],[])
        self.assertEqual(metrics["recall"],0)
        self.assertEqual(metrics["fact_completeness"]["budget"],0)
        self.assertEqual(metrics["fact_denominators"]["budget"]["missing"],1)

    def test_preview_is_not_counted_as_verified_true_positive(self):
        item=sample();item.verification_status="preview"
        metrics=quality_metrics([self.case()],[item])
        self.assertEqual(metrics["recall"],0)
        self.assertEqual(metrics["preview_count"],1)

    def test_budget_units_are_normalized(self):
        case=self.case();case["expected_budget"]="3800000元"
        self.assertEqual(quality_metrics([case],[sample()])["fact_accuracy"]["budget"],1)

    def test_synthetic_or_unfrozen_dataset_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_dataset({"dataset_type":"synthetic_regression","cases":[]})
        with self.assertRaisesRegex(ValueError,"frozen"):
            validate_dataset({"dataset_type":"real_benchmark","evaluation_date":"2026-10-07","cases":[self.case()]},require_frozen=True)

    def test_longitudinal_material_update_is_not_unchanged_duplicate(self):
        old=sample();new=replace(old,budget="500万元")
        report=longitudinal_metrics([("2026-10-06",[old]),("2026-10-07",[old,new])])
        self.assertEqual(len(report["unchanged_reappearances"]),1)

    def test_news_scopes_keep_shared_magazine_and_no_opportunity_facts(self):
        from services.fetcher import Article
        article=Article(title="普通资讯测试",url="https://example.com/news",summary="资讯摘要",source_name="测试来源")
        for scope in ("national","local","wechat","discover"):
            with self.subTest(scope=scope):
                html=render_html([article],scope=scope)
                self.assertIn("智能仓储每日简讯",html)
                self.assertIn("普通资讯测试",html)
                self.assertNotIn("AI 初步判断",html)


class FurtherIntegrationTests(unittest.TestCase):
    def test_existing_search_requests_are_bounded_and_do_not_count_as_llm(self):
        from services.opportunity.sources.search import OpportunitySearchSource
        async def run():
            settings=OpportunitySettings(discovery_search_budget=1,total_search_budget=1)
            usage=UsageTracker(settings)
            calls=[]
            def respond(request):
                calls.append(str(request.url))
                return httpx.Response(200,json={"data":[]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                await OpportunitySearchSource(["test"],usage).fetch(client)
            self.assertEqual(len(calls),1)
            self.assertEqual(usage.calls,0)
            self.assertEqual(usage.search_summary()["discovery_search_calls"],1)
        asyncio.run(run())

    def test_glm_empty_response_counts_as_actual_search_request(self):
        from services.opportunity.search.glm_web_search import GLMWebSearch
        async def run():
            settings=OpportunitySettings(glm_api_key="fake",total_search_budget=24)
            usage=UsageTracker(settings)
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"choices":[{"message":{"tool_calls":[]}}]}))) as client:
                rows=await GLMWebSearch(settings,usage,client)._search("test",phase="verification")
            self.assertEqual(rows,[])
            self.assertEqual(usage.search_summary()["verification_search_calls"],1)
            self.assertEqual(usage.search_phases["verification"]["requests"],1)
        asyncio.run(run())

    def test_pdf_worker_timeout_is_a_recorded_failure(self):
        import subprocess
        verifier=SourceVerifier({})
        with patch("services.opportunity.verifier.subprocess.run",side_effect=subprocess.TimeoutExpired("pdf",15)):
            self.assertIsNone(asyncio.run(verifier._pdf_document("https://example.com/timeout.pdf",b"pdf")))
        self.assertIn("pdf_parse_failed",verifier.failures["https://example.com/timeout.pdf"])

    def test_older_notice_cannot_revert_retender_version(self):
        old=sample();old.published_ts=NOW.timestamp()+100;old.notice_version="retender-version"
        stale=sample();stale.published_ts=NOW.timestamp();stale.last_reported_at=1
        old.last_digest_hash=material_hash(old);old.last_reported_at=1
        apply_history([stale],{old.project_key:old.to_dict()})
        self.assertEqual(stale.notice_version,"retender-version")
        self.assertFalse(needs_report(stale))

    def test_fixed_benchmark_candidates_are_isolated_and_never_publish(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("benchmark_review2",Path(__file__).resolve().parents[1]/"scripts/test_opportunity_ai_live.py")
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            settings=settings_in(directory,openai_api_key="fake",openai_model="fake",glm_api_key="fake",glm_model="fake")
            path=Path(directory)/"dataset.json"
            path.write_text(json.dumps({"dataset_type":"real_benchmark","evaluation_date":"2026-10-07","cases":[{"title":TITLE,"url":URL,"expected_relevant":True,"human_reason":"synthetic unit test only","documents":[asdict(documents()[0])]}]},ensure_ascii=False),encoding="utf-8")
            args=SimpleNamespace(candidates=path,prepare=False,mode="research",results=None,live=True,compare=True,provider=None,limit=3,output=Path(directory)/"report.json")
            titles=[]
            async def capture(config,current,**kwargs):
                titles.append(kwargs["candidates"][0].title)
                self.assertFalse(current.auto_publish)
                self.assertNotEqual(current.output_dir,settings.output_dir)
                kwargs["candidates"][0].title="mutated"
                self.assertEqual((await kwargs["verifier"].fetch(URL)).text,TEXT)
                self.assertIsNone(await kwargs["verifier"].fetch("https://example.com/not-frozen"))
                return []
            with patch.object(module.OpportunitySettings,"from_env",return_value=settings),patch.object(module,"create_llm_provider",return_value=SimpleNamespace()),patch.object(module,"research_opportunities",new=capture),patch("services.opportunity.publisher._send",new=AsyncMock()) as send:
                asyncio.run(module.run(args))
                send.assert_not_awaited()
            self.assertEqual(titles,[TITLE,TITLE])
            self.assertEqual(read_json(args.output)["evaluation_date"],"2026-10-07")

    def test_snapshot_reappearance_is_not_repeated_delivery(self):
        item=sample();item.last_reported_at=100
        report=longitudinal_metrics([("2026-10-06",[item]),("2026-10-07",[item])])
        self.assertEqual(report["unchanged_reappearance_rate"],.5)
        self.assertEqual(report["recorded_delivery_events"],1)
        self.assertEqual(report["recorded_delivery_duplicate_rate"],0)


class MemoryBeforePoolLimitTests(unittest.TestCase):
    def test_many_unchanged_projects_do_not_crowd_out_new_research(self):
        from services.opportunity.runtime import write_json
        with tempfile.TemporaryDirectory() as directory:
            settings=settings_in(directory,research_limit=1,candidate_limit=40)
            memory,pool={},[]
            for index in range(50):
                title=f"工厂{index}自动化立体库堆垛机改造采购公告"
                url=f"https://example.com/old-{index}"
                text=TEXT.replace(TITLE,title)
                source=OpportunityCandidate(title,url,summary=text,content=text,verified_url=url,content_hash=content_signature(text),published_ts=NOW.timestamp())
                item=replace(sample(),title=title,source_url=url,project_key="",source_content_hash=source.content_hash,last_researched_at=NOW.timestamp()-3600)
                memory[item.project_key]=item.to_dict();pool.append(source)
            pool.append(candidate())
            write_json(settings.output_dir/"opportunities"/"index.json",memory)
            provider=SimpleNamespace(name="openai",usage=UsageTracker(settings),research=AsyncMock(return_value=research_result()))
            search=SimpleNamespace(discover=AsyncMock(return_value=[]),find_sources=AsyncMock(return_value=[]))
            verifier=SourceVerifier({});verifier.fetch=AsyncMock(return_value=documents()[0])
            with patch("services.opportunity.ai_researcher.local_now",return_value=NOW):
                asyncio.run(research_opportunities({},settings,candidates=pool,provider=provider,search=search,verifier=verifier))
            provider.research.assert_awaited_once()
            self.assertEqual(json.loads(provider.research.await_args.args[1])["candidate"]["title"],TITLE)
            self.assertEqual(read_json(settings.runtime_dir/"opportunity_ai_status.json")["last_reused"],50)
