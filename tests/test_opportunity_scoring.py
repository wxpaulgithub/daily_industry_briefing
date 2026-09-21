import unittest

from services.opportunity.scoring import final_score, source_score, urgency_score


class OpportunityScoringTest(unittest.TestCase):
    def test_procurement_is_more_urgent_than_award(self):
        self.assertGreater(urgency_score("PROCUREMENT", 0), urgency_score("AWARD", 0))

    def test_official_source_is_preferred(self):
        self.assertGreater(source_score("政府采购", "https://ccgp.gov.cn/a"), source_score("搜索发现", "https://example.com/a"))

    def test_final_score_weights_relevance_most(self):
        self.assertEqual(final_score(100, 50, 50), 75.0)


if __name__ == "__main__":
    unittest.main()
