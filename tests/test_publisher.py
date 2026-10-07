import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx

from services.opportunity.models import ProjectOpportunity
from services.opportunity.publisher import publish_test, publish_today, retry_pending
from services.opportunity.runtime import read_json
from services.opportunity.storage import load_snapshot, save_snapshot
from v2_helpers import NOW, settings_in


class PublisherTests(unittest.TestCase):
    def sample(self):
        return ProjectOpportunity("制造企业旧立库采购公告", "https://www.ccgp.gov.cn/warehouse", stage="PROCUREMENT", priority="A", budget="380万元", deadline="2026-10-12", research_mode="ai", technical_scope=["堆垛机", "WMS"], entry_point="建议获取技术文件。")

    def test_send_uses_saved_data_and_success_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake-secret")
            sent = []
            def respond(request):
                self.assertTrue((settings.output_dir / "opportunities" / "2026-10-07.json").exists())
                sent.append(json.loads(request.content)["markdown"]["content"])
                return httpx.Response(200, json={"errcode": 0})
            save_snapshot([self.sample()], "2026-10-07", settings.output_dir)
            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    with self.assertLogs("httpx", level="INFO") as logs:
                        first = await publish_today(settings, date_str="2026-10-07", client=client)
                    self.assertNotIn("fake-secret", " ".join(logs.output))
                    second = await publish_today(settings, date_str="2026-10-07", client=client)
                self.assertEqual(first["status"], "success")
                self.assertEqual(second["status"], "no_new_opportunities")
            with patch("services.opportunity.publisher.local_now", return_value=NOW):
                asyncio.run(run())
            self.assertEqual(len(sent), 1)
            self.assertIn(load_snapshot("2026-10-07", settings.output_dir)[0].budget, sent[0])
            self.assertNotIn("fake-secret", json.dumps(read_json(settings.runtime_dir / "opportunity_publish_status.json")))

    def test_retries_are_bounded_and_do_not_mark_failed_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, webhook_url="https://qyapi.weixin.qq.com/fake", publish_attempts=2)
            save_snapshot([self.sample()], "2026-10-07", settings.output_dir)
            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"errcode": 93000}))) as client:
                    first = await publish_today(settings, date_str="2026-10-07", client=client)
                    second = await publish_today(settings, date_str="2026-10-07", client=client)
                    third = await publish_today(settings, date_str="2026-10-07", client=client)
                self.assertEqual(first["status"], "failed")
                self.assertEqual(second["attempts"], 2)
                self.assertEqual(third["status"], "retry_exhausted")
            with patch("services.opportunity.publisher.local_now", return_value=NOW):
                asyncio.run(run())
            self.assertEqual(load_snapshot("2026-10-07", settings.output_dir)[0].last_reported_at, 0)

    def test_retry_does_not_send_a_changed_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, webhook_url="https://qyapi.weixin.qq.com/fake")
            item = self.sample()
            save_snapshot([item], "2026-10-07", settings.output_dir)
            calls = []
            def respond(request):
                calls.append(request)
                return httpx.Response(200, json={"errcode": 93000})
            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    await publish_today(settings, date_str="2026-10-07", client=client)
                    save_snapshot([replace(item, budget="500万元")], "2026-10-07", settings.output_dir)
                    result = await publish_today(settings, date_str="2026-10-07", client=client, retry=True)
                    self.assertEqual(result["status"], "superseded")
            with patch("services.opportunity.publisher.local_now", return_value=NOW):
                asyncio.run(run())
            self.assertEqual(len(calls), 1)

    def test_test_message_contains_no_project_data(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, webhook_url="https://qyapi.weixin.qq.com/fake")
            sent = []
            def respond(request):
                sent.append(json.loads(request.content))
                return httpx.Response(200, json={"errcode": 0})
            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    await publish_test(settings, client)
            asyncio.run(run())
            self.assertEqual(sent[0]["markdown"]["content"], "智能仓储商机日报\n\n推送通道测试成功。")

    def test_acknowledged_delivery_does_not_repeat_after_memory_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, webhook_url="https://qyapi.weixin.qq.com/fake")
            save_snapshot([self.sample()], "2026-10-07", settings.output_dir)
            calls = []
            def respond(request):
                calls.append(request)
                return httpx.Response(200, json={"errcode": 0})
            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    with patch("services.opportunity.publisher.mark_reported", side_effect=OSError):
                        await publish_today(settings, date_str="2026-10-07", client=client)
                    await publish_today(settings, date_str="2026-10-07", client=client)
            with patch("services.opportunity.publisher.local_now", return_value=NOW):
                asyncio.run(run())
            self.assertEqual(len(calls), 1)

    def test_closed_and_expired_projects_are_not_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, webhook_url="https://qyapi.weixin.qq.com/fake")
            items = [replace(self.sample(), title="过期项目", deadline="2026-10-06"),
                     replace(self.sample(), title="已结束项目", stage="CLOSED")]
            save_snapshot(items, "2026-10-07", settings.output_dir)
            with patch("services.opportunity.publisher.local_now", return_value=NOW):
                result = asyncio.run(publish_today(settings, date_str="2026-10-07"))
            self.assertEqual(result["status"], "no_new_opportunities")

    def test_search_preview_is_never_auto_published(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, webhook_url="https://qyapi.weixin.qq.com/fake")
            item = self.sample()
            item.research_mode = "preview"
            save_snapshot([item], "2026-10-07", settings.output_dir)
            with patch("services.opportunity.publisher.local_now", return_value=NOW):
                result = asyncio.run(publish_today(settings, date_str="2026-10-07"))
            self.assertEqual(result["status"], "no_new_opportunities")
