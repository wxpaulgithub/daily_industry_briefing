import json
import tempfile
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx

from services.opportunity.ai_researcher import prefilter, rank_opportunities, research_opportunities
from services.opportunity.evaluation import pipeline_metrics
from services.opportunity.freshness_guard import html_publication, stale_title_risk
from services.opportunity.models import OpportunityCandidate, ProjectOpportunity
from services.opportunity.runtime import UsageTracker, read_json, local_now
from services.opportunity.schemas import TriageBatch, TriageDecision
from services.opportunity.search.glm_web_search import GLMWebSearch
from services.opportunity.search.coverage import CHANNELS
from services.opportunity.settings import OpportunitySettings
from services.opportunity.source_resolver import SourceResolver
from services.opportunity.triage import triage_candidates
from services.opportunity.verifier import SourceDocument, SourceVerifier
from v2_helpers import settings_in

class FreshnessTests(unittest.TestCase):
    def test_publication_metadata_is_exact_and_modification_is_not_publication(self):
        self.assertEqual(html_publication('<script type="application/ld+json">{"dateModified":"2026-10-08"}</script>'), ('', ''))
        _, quote = html_publication('<meta property="article:published_time" content="2026-10-08"><title>Other</title>')
        self.assertEqual(quote, '<meta property="article:published_time" content="2026-10-08">')
        self.assertEqual(html_publication('<meta property="article:published_time" content="2026-10-08"><meta itemprop="datePublished" content="2024-01-01">'),('', ''))
        _, quote = html_publication('<script type="application/ld+json">{"datePublished":"2026-10-08"}</script>')
        self.assertEqual(quote, '"datePublished":"2026-10-08"')

    def test_title_year_is_a_clue_not_an_unconditional_delete(self):
        now=local_now()
        self.assertTrue(stale_title_risk(f'{now.year-2}年度仓储设备采购', now=now))
        self.assertFalse(stale_title_risk(f'{now.year-2}年度仓储设备采购', now.timestamp(), now=now))
        self.assertFalse(stale_title_risk(f'{now.year-2}年度仓储设备采购', deadline=(now+timedelta(days=3)).strftime('%Y-%m-%d'),now=now))
        self.assertFalse(stale_title_risk(f'{now.year-2}项目{now.year}年二次招标',now=now))

    def test_unknown_old_retender_can_be_researched_but_not_recommended(self):
        year=local_now().year-2
        row=OpportunityCandidate(f'{year}年度WMS重新招标','https://example.com/a')
        self.assertEqual(prefilter([row]),[row])
        item=ProjectOpportunity(row.title,row.url,research_mode='preview',stage='PROCUREMENT',relevance_score=95,final_score=64)
        self.assertEqual(rank_opportunities([item],{},10),[])
        self.assertIn('historical_title_year',item.risk_flags)

    def test_plain_old_title_is_excluded_before_paid_research(self):
        row=OpportunityCandidate(f'{local_now().year-2}年度仓储设备采购','https://example.com/old')
        self.assertEqual(prefilter([row]),[])

class DiscoveryAndTriageTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_channels_precede_dynamic_planning(self):
        settings=OpportunitySettings(total_search_budget=12,discovery_search_budget=12)
        usage=UsageTracker(settings)
        search=GLMWebSearch(settings,usage)
        queries=[]
        async def fetch(query,phase='discovery'):
            allowance=usage.reserve(1,phase=phase)
            usage.settle_search(phase,allowance,1)
            queries.append(query)
            return [OpportunityCandidate('制造企业设备采购','https://example.com/'+str(len(queries)))]
        async def planner(instructions,prompt):
            self.assertEqual(len(queries),8)
            self.assertIn('first_round_results',prompt)
            return ['新的物料配送采购表达']
        with patch.object(search,'_search',new=fetch),patch.object(search,'_plan_queries',new=planner):
            rows=await search.discover('{}')
        self.assertEqual(set(search.coverage_report)-{'dynamic'},set(CHANNELS))
        self.assertEqual(len(rows),9)
        self.assertTrue(all(search.coverage_report[key]['requests']==1 for key in CHANNELS))

    async def test_lower_budget_reports_unsearched_channels(self):
        settings=OpportunitySettings(total_search_budget=3,discovery_search_budget=3)
        search=GLMWebSearch(settings,UsageTracker(settings))
        async def fetch(query,phase='discovery'):
            a=search.usage.reserve(1,phase=phase);search.usage.settle_search(phase,a,1)
            return []
        with patch.object(search,'_search',new=fetch),patch.object(search,'_plan_queries',new=AsyncMock()) as planner:
            await search.discover('{}')
        self.assertEqual(sum(row['status']=='budget_exhausted' for row in search.coverage_report.values()),5)
        planner.assert_not_awaited()

    async def test_semantic_screen_recovers_nonwarehouse_wording(self):
        rows=[OpportunityCandidate('新建器材库物料自动配送系统','https://example.com/weak'),OpportunityCandidate('WMS企业版八大核心参数','https://example.com/promo')]
        provider=SimpleNamespace(generate=AsyncMock(return_value=TriageBatch(decisions=[
            TriageDecision(id=1,worth_research=True,score=91.,reason='具体生产物料需求'),
            TriageDecision(id=2,worth_research=False,score=5.,reason='产品宣传')])) )
        accepted,rejected=await triage_candidates(rows,provider,{})
        self.assertEqual(accepted,[rows[0]])
        self.assertEqual(rejected,[rows[1]])
        self.assertFalse(provider.generate.await_args.kwargs.get('web_search',False))

    async def test_duplicate_and_missing_triage_ids_are_not_silently_trusted(self):
        provider=SimpleNamespace(generate=AsyncMock(return_value=TriageBatch(decisions=[TriageDecision(id=1,worth_research=True,score=90.,reason='x')]*2)))
        with self.assertRaisesRegex(ValueError,'triage_incomplete_ids'):
            await triage_candidates([OpportunityCandidate('a','https://example.com/a'),OpportunityCandidate('b','https://example.com/b')],provider,{})

class WavesAndResolverTests(unittest.IsolatedAsyncioTestCase):
    async def run_waves(self,mode='preview',rejected=False):
        with tempfile.TemporaryDirectory() as directory:
            settings=settings_in(directory)
            rows=[OpportunityCandidate(f'制造企业{i}WMS设备采购','https://example.com/'+str(i)) for i in range(20)]
            async def triage(schema,instructions,prompt):
                data=json.loads(prompt)
                return TriageBatch(decisions=[TriageDecision(id=row['id'],worth_research=not rejected,score=90.,reason='研究候选') for row in data['candidates']])
            provider=SimpleNamespace(name='glm',usage=UsageTracker(settings),generate=triage)
            search=SimpleNamespace(discover=AsyncMock(return_value=[]))
            async def research(candidate,*args):
                return ProjectOpportunity(candidate.title,candidate.url,research_mode=mode,
                    verification_status='verified' if mode=='ai' else 'preview',stage='PROCUREMENT',published=local_now().strftime('%Y-%m-%d'),published_ts=local_now().timestamp(),
                    relevance_score=95,priority='A',final_score=95)
            with patch('services.opportunity.ai_researcher.research_candidate',new=AsyncMock(side_effect=research)) as call:
                items=await research_opportunities({},settings,candidates=rows,provider=provider,search=search)
            return call.await_count,read_json(settings.runtime_dir/'opportunity_ai_status.json'),read_json(settings.runtime_dir/'opportunity_research_diagnostics.json'),items

    async def test_preview_does_not_meet_target_and_extends_past_soft_limit(self):
        count,status,trace,_=await self.run_waves()
        self.assertEqual(count,18)
        self.assertEqual(status['research_wave'],3)
        self.assertEqual(status['verified_target_count'],0)
        self.assertEqual(trace['research_stop_reason'],'research_hard_limit')

    async def test_verified_target_stops_after_first_wave(self):
        count,status,trace,_=await self.run_waves('ai')
        self.assertEqual(count,6)
        self.assertEqual(status['research_wave'],1)
        self.assertEqual(trace['research_stop_reason'],'verified_target_met')

    async def test_triage_rejection_cannot_return_via_rules_fallback(self):
        count,_,trace,items=await self.run_waves(rejected=True)
        self.assertEqual(count,0)
        self.assertEqual(items,[])
        self.assertEqual(trace['drop_reason_counts']['triage_reject'],20)

    async def test_only_high_value_failed_original_gets_source_rescue(self):
        for score,expected in ((90,1),(20,0)):
            settings=OpportunitySettings()
            row=OpportunityCandidate('制造企业WMS采购项目','https://example.com/blocked',summary='采购人：样本制造有限公司。项目位于江苏无锡。',triage_score=score)
            original=SourceDocument('https://procurement.example.com/a',row.title+' 公开招标采购仓储管理系统。'*10)
            verifier=SourceVerifier({'trusted_domains':['procurement.example.com']})
            verifier.fetch=AsyncMock(side_effect=lambda url:original if url==original.url else None)
            verifier.fetch_attachments=AsyncMock(return_value=[])
            search=SimpleNamespace(find_sources=AsyncMock(return_value=[]),search_query=AsyncMock(return_value=[OpportunityCandidate(row.title,original.url)]))
            resolver=SourceResolver(search,verifier,settings,UsageTracker(settings))
            docs=await resolver.resolve(row)
            self.assertEqual(search.search_query.await_count,expected)
            self.assertEqual(any(doc.retrieval_method=='html' for doc in docs),bool(expected))

    async def test_html_publication_fragment_is_available_for_exact_evidence(self):
        html='<meta property="article:published_time" content="2026-10-08"><article>'+('WMS仓储管理系统招标采购说明。'*20)+'</article>'
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=html,headers={'content-type':'text/html'}))) as client:
            doc=await SourceVerifier({},client,check_dns=False).fetch('https://example.com/a')
        self.assertIn('<meta property="article:published_time" content="2026-10-08">',doc.text)

    async def test_explicit_verifier_rejection_cannot_return_as_rule_result(self):
        from v2_helpers import candidate, documents, research_result
        with tempfile.TemporaryDirectory() as directory:
            settings=settings_in(directory)
            provider=SimpleNamespace(name='glm',usage=UsageTracker(settings),generate=AsyncMock(return_value=TriageBatch(decisions=[TriageDecision(id=1,worth_research=True,score=90.,reason='具体采购')])),research=AsyncMock(return_value=research_result(evidence=[])))
            search=SimpleNamespace(discover=AsyncMock(return_value=[]),find_sources=AsyncMock(return_value=[]))
            verifier=SourceVerifier({});verifier.fetch=AsyncMock(return_value=documents()[0])
            items=await research_opportunities({},settings,candidates=[candidate()],provider=provider,search=search,verifier=verifier)
            self.assertEqual(items,[])
            trace=read_json(settings.runtime_dir/'opportunity_research_diagnostics.json')
            self.assertEqual(trace['drop_reason_counts']['verifier_reject'],1)

    def test_pipeline_report_distinguishes_search_miss_and_verifier_failure(self):
        cases=[{'title':'甲项目','url':'https://example.com/a','expected_relevant':True},{'title':'乙项目','url':'https://example.com/b','expected_relevant':True}]
        report=pipeline_metrics(cases,{'pipeline':[{'title':'乙项目','url':'https://example.com/b','pipeline_stage':'verifier','drop_reason':'source_unavailable'}]})
        self.assertEqual(report['miss_reasons'],{'search_miss':1,'source_unavailable':1})


class LiveFailureRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_failed_channels_are_not_reported_as_successful_search(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=settings_in(directory)
            search=SimpleNamespace(discover=AsyncMock(return_value=[]),coverage_report={key:{'status':'failed','error':'http_429'} for key in CHANNELS})
            provider=SimpleNamespace(name='glm',usage=UsageTracker(settings))
            items=await research_opportunities({},settings,candidates=[],provider=provider,search=search)
            status=read_json(settings.runtime_dir/'opportunity_ai_status.json')
            self.assertEqual(items,[])
            self.assertTrue(status['search_failed'])
            self.assertEqual(status['discovery_status'],'failed')
            self.assertEqual(status['fallback_reason'],'discovery_search_unavailable')

    async def test_glm_searches_are_serialized_without_extra_calls(self):
        import asyncio
        settings=OpportunitySettings(search_concurrency=1)
        active=0;peak=0
        async def respond(request):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            await asyncio.sleep(.01)
            active-=1
            return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[]}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            search=GLMWebSearch(settings,UsageTracker(settings),client)
            await asyncio.gather(*(search.search_query(str(i)) for i in range(3)))
        self.assertEqual(peak,1)
        self.assertEqual(search.usage.calls,3)
