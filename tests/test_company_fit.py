import unittest
from dataclasses import replace

from services.opportunity.company_fit import company_fit, load_company_profile
from services.opportunity.facts import budget_amount, deadline_expired, parse_date
from services.opportunity.models import ProjectOpportunity
from services.opportunity.scoring import score_v2
from services.opportunity.settings import OpportunitySettings
from v2_helpers import NOW


class CompanyFitTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_company_profile(OpportunitySettings().config_dir)

    def test_manufacturing_retrofit_beats_airport_epc(self):
        good = ProjectOpportunity("制造企业堆垛机改造采购", "https://example.com", province="江苏", budget="380万元", project_type="RETROFIT", technical_scope=["堆垛机", "WMS"])
        poor = replace(good, title="机场行李系统EPC采购", province="", budget="2亿元", project_type="NEW_BUILD", technical_scope=[])
        self.assertGreater(company_fit(good, self.profile), company_fit(poor, self.profile) + 40)

    def test_profile_changes_budget_and_region_preferences(self):
        item = ProjectOpportunity("制造企业堆垛机采购", "https://example.com", province="江苏", budget="800万元")
        current = company_fit(item, self.profile)
        profile = {**self.profile, "acceptable_project_budget_max": 5000000, "preferred_regions": []}
        self.assertLess(company_fit(item, profile), current)

    def test_budget_conversion_is_deterministic(self):
        self.assertEqual(budget_amount("833.12万元"), 8331200)
        self.assertEqual(budget_amount("1.2亿元"), 120000000)
        self.assertIsNone(budget_amount("预算待核实"))

    def test_deadline_preserves_time_and_date_only_last_day(self):
        self.assertFalse(deadline_expired("2026-10-07 09:30", NOW))
        self.assertTrue(deadline_expired("2026-10-07 07:30", NOW))
        self.assertFalse(deadline_expired("2026-10-07", NOW))
        self.assertIsNone(parse_date("2026-02-31"))

    def test_v2_priority_and_unknown_date(self):
        item = ProjectOpportunity("制造企业堆垛机维保采购", "https://example.com", stage="PROCUREMENT", province="江苏", budget="380万元", project_type="MAINTENANCE", relevance_score=100, source_score=100, published_ts=NOW.timestamp())
        score_v2(item, self.profile, now_ts=NOW.timestamp())
        self.assertEqual(item.priority, "A")
        self.assertGreaterEqual(item.final_score, 90)
