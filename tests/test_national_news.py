import asyncio
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

import httpx

from services.fetcher import Article, filter_relevant, deduplicate, _select_by_scope
from services.national_news import assess, annotate, reject_reason, quality_score, select_national_articles, deduplicate_national, write_diagnostics
from services.news_metadata import primary_metadata, parse_date
from services.skills.rss_generic import RSSKeywordSkill
from services.skills.national_sources import OrganizerNewsSkill
from services.generator import render_html

NOW = datetime(2026, 10, 9, 12, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


def article(title="制造工厂WMS仓储管理系统交付", host="example.com", index=1, **kwargs):
    return Article(title, f"https://{host}/news/{index}", published_ts=kwargs.pop("published_ts", NOW - 86400), **kwargs)


class NationalSelectionTests(unittest.TestCase):
    def test_news_can_be_award_or_case_without_open_sales_window(self):
        for title in ("WMS仓储管理系统中标结果公布", "智能仓储立体库项目已投产", "CeMAT物流展观众报名开启", "智能物流装备新标准发布"):
            self.assertFalse(assess(article(title))[2])
        self.assertEqual(assess(article("2.7亿元气象科技大楼施工总承包中标"))[2], "unrelated_topic")
        self.assertTrue(assess(article("某大桥工程施工招标"))[2])

    def test_humanoid_logistics_application_is_not_blanket_excluded(self):
        title = "人形机器人进入工厂物流场景搬运测试"
        self.assertEqual(len(filter_relevant([article(title)])), 1)
        unrelated = article("人形机器人娱乐节目", region_scope="wechat", skill_name="公众号RSS")
        self.assertEqual(filter_relevant([unrelated]), [])

    def test_relevance_outweighs_image_and_generic_vendor_words(self):
        direct = article("WCS控制系统与堆垛机升级案例")
        broad = article("工业机器人市场年度趋势", image_url="https://example.com/image.jpg", source_name="兰剑", summary="市场发展" * 40)
        self.assertGreater(quality_score(direct, NOW), quality_score(broad, NOW))

    def test_publisher_limit_shared_by_agv_and_forklift_and_subdomains(self):
        pool = [article(f"AGV项目案例编号{i}", "www.chinaagv.com" if i % 2 else "chinaforklift.com", i) for i in range(12)]
        pool += [article(f"WMS系统产品编号{i}", "other.example", i) for i in range(12)]
        selected = select_national_articles(pool, 24, now=NOW, per_source_limit=3)
        self.assertLessEqual(sum(a.source_group == "中叉网/AGV网" for a in selected), 3)
        self.assertTrue(any(a.source_domain == "other.example" for a in selected))

    def test_stale_and_unknown_publication_are_bounded(self):
        old = article(published_ts=NOW - 17 * 86400)
        historic = article("2023年仓储物流展通知", published_ts=0)
        self.assertEqual(reject_reason(old, NOW), "stale_publication")
        self.assertEqual(reject_reason(historic, NOW), "historical_title_without_date")
        pool = [article(f"WMS系统技术解析{i}", f"source{i}.example", i, published_ts=0) for i in range(12)]
        selected = select_national_articles(pool, 24, now=NOW, unknown_date_limit=2)
        self.assertEqual(len(selected), 2)
        self.assertFalse(select_national_articles(pool, 24, now=NOW, unknown_date_limit=0))

    def test_no_forced_bidding_quota(self):
        pool = [article("WMS软件新产品发布", "first.example"), article("市政大楼施工总包招标", "second.example")]
        selected = select_national_articles(pool, 24, now=NOW)
        self.assertEqual([a.title for a in selected], [pool[0].title])

    def test_dedup_prefers_primary_preserves_numbered_versions(self):
        title = "某制造企业智能仓储系统项目交付"
        copy = article(title, "toutiao.com", source_kind="aggregator")
        original = article(title, "official.example", 99, source_kind="vendor")
        versions = [article(f"WMS系统采购项目第{i}包", "official.example", i) for i in (1, 2)]
        selected = deduplicate_national([copy, original] + versions)
        self.assertIn(original, selected)
        self.assertNotIn(copy, selected)
        self.assertEqual(len(selected), 3)

    def test_syndicated_award_amounts_merge_but_distinct_projects_survive(self):
        first = article("科捷智能：中标约7500.00万元某智能物流自动化项目", "news1.example")
        second = article("科捷智能最新公告：中标约7500万元波兰智能物流自动化项目", "news2.example")
        third = article("科捷智能：关于自愿披露项目中标的公告", "news3.example", summary="波兰项目金额约7,500万元")
        another = article("科捷智能：中标约7500万元德国智能物流自动化项目", "news4.example")
        self.assertEqual(len(deduplicate_national([first, second, third])), 1)
        self.assertEqual(len(deduplicate_national([second, another])), 2)
        one = article("科捷智能：中标约7500万元智能物流项目", "one.example", summary="项目编号：WMS-001")
        two = article("科捷智能：中标约7500万元另一智能物流项目", "two.example", summary="项目编号：WMS-002")
        self.assertEqual(len(deduplicate_national([one, two])), 2)

    def test_url_tracking_duplicates_and_title_anagram_are_not_conflated(self):
        first = article("智能仓储系统采购第一包")
        copy = article("不同标题的同一公告")
        copy.url = first.url + "?utm_source=portal"
        other = article("第一包系统采购智能仓储", "other.example")
        self.assertEqual(len(deduplicate_national([first, copy, other])), 2)

    def test_metadata_render_and_json_roundtrip_backward_compatible(self):
        a = annotate(article("WMS仓储系统新版本发布", "chuandong.com", source_name="行业媒体", published="2026-10-08"))
        html = render_html([a], scope="national")
        self.assertIn("中国传动网", html)
        self.assertIn("chuandong.com", html)
        self.assertIn("技术产品", html)
        for scope in ("wechat", "discover", "local"):
            self.assertNotIn("技术产品", render_html([a], scope=scope))
        self.assertEqual(Article(**a.__dict__).source_domain, "chuandong.com")
        self.assertEqual(Article(title="旧文件", url="https://example.com").news_category, "")

    def test_source_diagnostics_include_failed_and_empty_fetches(self):
        from types import SimpleNamespace
        a = article()
        skill = SimpleNamespace(name="测试来源", source_diagnostics=[{"url":"https://blocked.example", "publisher":"官方来源", "status":"http_403", "returned":0}])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "report.json"
            write_diagnostics([a], [a], [a], [a], [skill], [("测试来源", [a], "live", 1)], p)
            report = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(report["fetches"][0]["status"], "http_403")
            self.assertEqual(report["stage_counts"]["selected"], 1)
            self.assertEqual(report["sources"][0]["domain"], "example.com")


class SourceParsingTests(unittest.TestCase):
    def setUp(self):
        self.skill = RSSKeywordSkill()
        self.skill.check_dns = False
        self.skill.feed_sources = [{"label":"企业动态列表", "publisher":"仓储企业官网", "url":"https://vendor.example/news/", "kind":"vendor"}]

    def test_listing_title_date_summary_do_not_collapse_together(self):
        page = '<ul><li><a href="/news/1.html" class="box-a"><p class="title">WMS智能仓储项目正式交付</p><p class="intro">系统集成ERP，支撑生产物流</p><span class="time">2026-10-08</span></a></li></ul>'
        entries = self.skill._parse_html_as_entries("https://vendor.example/news/", page)
        a = self.skill._entries_to_articles(entries, 10, "https://vendor.example/news/", {})[0]
        self.assertEqual(a.title, "WMS智能仓储项目正式交付")
        self.assertEqual(a.published, "2026-10-08")
        self.assertEqual(a.source_name, "仓储企业官网")
        self.assertEqual(a.source_kind, "vendor")
        self.assertIn("ERP", a.summary)

    def test_footer_and_summary_event_date_do_not_fake_publication(self):
        page = '<li><a href="/news/1">WMS系统正式发布</a><p class="intro">将在2026-11-03展示</p></li><footer>2026-10-08</footer>'
        entry = self.skill._parse_html_as_entries("https://vendor.example/news/", page)[0]
        self.assertEqual(entry["published_ts"], 0)

    def test_split_company_date_and_bare_date_card(self):
        page = '<li><a href="/news/1"><div class="c3l"><h3>10-08</h3><p>2026</p></div><h5>WMS智能仓储系统技术介绍</h5></a></li><li><div class="title"><a href="/news/2" title="AGV自动搬运系统新品发布">AGV自动搬运系统新品发布</a></div><p>2026-10-07 12:00</p></li>'
        entries = self.skill._parse_html_as_entries("https://vendor.example/news/", page)
        self.assertEqual(entries[0]["published"], "2026-10-08")
        self.assertEqual(entries[1]["published"], "2026-10-07")

    def test_foreign_link_does_not_inherit_official_publisher_claim(self):
        rows = [{"title":"WMS技术解析", "link":"https://media.example/story"}]
        a = self.skill._entries_to_articles(rows, 10, "https://vendor.example/news/", {})[0]
        self.assertEqual(a.source_name, "media.example")
        self.assertFalse(a.source_kind)

    def test_updated_metadata_is_not_publication_and_conflicts_are_unknown(self):
        self.assertEqual(primary_metadata('<meta itemprop="dateModified" content="2026-10-08"><h1>仓储系统</h1>', "https://example.com")["published_ts"], 0)
        text = '<meta property="article:published_time" content="2026-10-08"><meta itemprop="datePublished" content="2026-10-07">'
        self.assertEqual(primary_metadata(text, "https://example.com")["published_ts"], 0)
        self.assertEqual(parse_date("2026-02-30"), (0, ""))

    def test_http_403_is_reported_without_parsing_error_page(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="WMS报错页"))) as client:
                rows = await self.skill.fetch_all(client)
            self.assertEqual(rows, [])
            self.assertEqual(self.skill.source_diagnostics[0]["status"], "http_403")
        asyncio.run(run())

    def test_detail_enrichment_preserves_title_and_reads_publication(self):
        def respond(request):
            if request.url.path == "/news/":
                return httpx.Response(200,text='<li><a href="/news/1">WMS仓储系统新品发布</a></li>')
            return httpx.Response(200,text='<h1>WMS仓储系统新品发布</h1><meta property="article:published_time" content="2026-10-08"><article><p>仓储软件升级库存与拣选管理。</p></article>')
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                rows = await self.skill.fetch_all(client)
            self.assertEqual(rows[0].published, "2026-10-08")
            self.assertEqual(self.skill.source_diagnostics[0]["enriched"], 1)
        asyncio.run(run())

    def test_private_links_and_private_redirects_are_not_fetched(self):
        from services.news_metadata import read_public_text, SourceReadError
        async def run():
            calls = []
            def respond(request):
                calls.append(str(request.url))
                return httpx.Response(302, headers={"location":"http://127.0.0.1/api/status"})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
                with self.assertRaisesRegex(SourceReadError, "source_not_public"):
                    await read_public_text(client, "http://127.0.0.1/api/status", check_dns=False)
                self.assertEqual(calls, [])
                with self.assertRaisesRegex(SourceReadError, "source_not_public"):
                    await read_public_text(client, "https://public.example/news", check_dns=False)
                self.assertEqual(len(calls), 1)
        asyncio.run(run())

    def test_official_organizer_maps_only_real_api_records(self):
        skill = OrganizerNewsSkill()
        skill.check_dns = False
        data={"code":"001","data":{"zslist":[{"id":"123","wzmc":"LET物流装备展正式发布新主题","addtime":"2026-10-08 09:00:00","wznr":"/img/1.jpg"}]}}
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as client:
                rows = await skill.fetch_all(client)
            self.assertEqual(rows[0].source_kind, "organizer")
            self.assertEqual(rows[0].url, "https://www.chinalet.cn/home/news_x.html?id=123")
            self.assertEqual(rows[0].published, "2026-10-08")
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
