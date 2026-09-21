import json
import unittest
from pathlib import Path

from services.opportunity.fetcher import build_opportunity
from services.opportunity.models import OpportunityCandidate
from services.opportunity.rules import classify_stage, classify_type, is_too_old


class OpportunityRulesTest(unittest.TestCase):
    def test_golden_cases(self):
        path = Path(__file__).parent / "fixtures" / "opportunity_cases.json"
        for case in json.loads(path.read_text(encoding="utf-8")):
            with self.subTest(case["title"]):
                candidate = OpportunityCandidate(title=case["title"], url="https://example.test/item")
                item = build_opportunity(candidate)
                self.assertEqual(item is not None, case["keep"])
                self.assertEqual(classify_stage(case["title"]), case["stage"])

    def test_project_type_can_be_mixed(self):
        self.assertEqual(classify_type("立体库堆垛机与WMS系统升级改造"), "MIXED")

    def test_editorial_buying_guide_is_not_an_opportunity(self):
        candidate = OpportunityCandidate(
            title="WMS供应商排行榜：采购前怎么选",
            url="https://example.test/guide",
        )
        self.assertIsNone(build_opportunity(candidate))

    def test_old_award_is_stale(self):
        now = 100 * 86400
        self.assertTrue(is_too_old("AWARD", 70 * 86400, now))
        self.assertFalse(is_too_old("EARLY_SIGNAL", 70 * 86400, now))


if __name__ == "__main__":
    unittest.main()
