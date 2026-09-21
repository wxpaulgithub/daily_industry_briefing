import asyncio
import json
import unittest
from unittest.mock import patch

import app
from services.opportunity.models import ProjectOpportunity


class OpportunityRoutesTest(unittest.TestCase):
    def test_scope_normalization(self):
        self.assertEqual(app._normalize_scope("opportunity"), "opportunity")
        self.assertEqual(app._normalize_scope("unknown"), "national")

    def test_today_api_shape(self):
        sample = ProjectOpportunity(title="立体库采购公告", source_url="https://example.test")
        with patch.object(app, "load_opportunity_snapshot", return_value=[sample]):
            response = asyncio.run(app.opportunities_today())
        body = json.loads(response.body)
        self.assertEqual(body["opportunities"][0]["project_key"], sample.project_key)

    def test_opportunity_page_uses_independent_template(self):
        sample = ProjectOpportunity(title="立体库采购公告", source_url="https://example.test")
        with patch.object(app, "load_opportunity_snapshot", return_value=[sample]):
            response = asyncio.run(app.index(scope="opportunity", wechat_kind="all"))
        html = response.body.decode("utf-8")
        self.assertIn("智能仓储每日简讯", html)
        self.assertIn("OPPORTUNITY LEADS", html)
        self.assertIn("scope=opportunity", html)


if __name__ == "__main__":
    unittest.main()
