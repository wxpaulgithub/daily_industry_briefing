import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import app
from services.opportunity.models import ProjectOpportunity
from services.opportunity.runtime import update_status
from v2_helpers import settings_in


class V2RouteTests(unittest.TestCase):
    def test_status_is_not_matched_as_a_date_and_hides_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_in(directory, openai_api_key="fake-api-secret", webhook_url="https://qyapi.weixin.qq.com/?key=fake-webhook-secret")
            update_status(settings, "DONE", last_count=2)
            async def run():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://localhost") as client:
                    response = await client.get("/api/opportunities/status")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["last_count"], 2)
                self.assertNotIn("fake-api-secret", response.text)
                self.assertNotIn("fake-webhook-secret", response.text)
            with patch("app.OpportunitySettings.from_env", return_value=settings):
                asyncio.run(run())

    def test_empty_page_is_read_only_and_keeps_magazine_shell(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://localhost") as client:
                response = await client.get("/?scope=opportunity")
            self.assertEqual(response.status_code, 200)
            self.assertIn("智能仓储每日简讯", response.text)
            self.assertIn("国内", response.text)
            self.assertIn("当日暂无", response.text)
        with patch("app.load_opportunity_snapshot", return_value=[]), patch("app._do_fetch_opportunities", new=AsyncMock()) as fetch:
            asyncio.run(run())
            fetch.assert_not_awaited()

    def test_archive_uses_opportunity_snapshot_and_requested_date(self):
        item = ProjectOpportunity("历史立库采购", "https://example.com")
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://localhost") as client:
                response = await client.get("/archive/2026-09-20?scope=opportunity")
            self.assertEqual(response.status_code, 200)
            self.assertIn("2026年09月20日", response.text)
            self.assertIn("历史立库采购", response.text)
        with patch("app.load_opportunity_snapshot", return_value=[item]), patch("app.load_articles_json") as news:
            asyncio.run(run())
            news.assert_not_called()
