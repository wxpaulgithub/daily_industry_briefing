import asyncio
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import app
from services.opportunity.settings import OpportunitySettings


class DeploymentTests(unittest.TestCase):
    def checker(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "check_opportunity_config.py"
        spec = importlib.util.spec_from_file_location("opportunity_deployment_check", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.check_settings

    def test_missing_ai_credentials_are_valid_rule_fallback(self):
        result = self.checker()(OpportunitySettings())
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "rules_fallback")

    def test_automatic_publishing_requires_its_own_webhook(self):
        result = self.checker()(OpportunitySettings(auto_publish=True))
        self.assertFalse(result["ok"])
        self.assertIn("auto_publish_requires_WECOM_OPPORTUNITY_WEBHOOK_URL", result["errors"])

    def test_preflight_never_emits_credentials(self):
        settings = OpportunitySettings(openai_api_key="fake-api-secret", openai_model="model", webhook_url="https://qyapi.weixin.qq.com/?key=fake-secret")
        text = json.dumps(self.checker()(settings))
        self.assertNotIn("fake-api-secret", text)
        self.assertNotIn("fake-secret", text)

    def test_web_startup_is_not_blocked_by_news_fetch_or_ai_research(self):
        async def run():
            blocker = asyncio.Event()
            async def fetch_news():
                await blocker.wait()
            news = AsyncMock(side_effect=fetch_news)
            ai = AsyncMock()
            scheduler = MagicMock()
            with patch("app.AsyncIOScheduler", return_value=scheduler), patch("app._do_fetch", news), patch("app._do_fetch_opportunities", ai), patch.dict("os.environ", {"FETCH_NEWS_ON_STARTUP": "true"}):
                async with app.lifespan(app.app):
                    await asyncio.sleep(0)
                    news.assert_awaited_once()
                    ai.assert_not_awaited()
                    response = await app.opportunities_status()
                    self.assertEqual(response.status_code, 200)
                scheduler.shutdown.assert_called_once_with(wait=False)
        asyncio.run(run())
