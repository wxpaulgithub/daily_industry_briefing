import unittest

from services.opportunity.enrichment import extract_budget, extract_deadline, extract_location, extract_owner


class OpportunityEnrichmentTest(unittest.TestCase):
    def test_extract_fields(self):
        text = "江苏无锡采购人：星辰制造有限公司，预算金额：1280万元；投标截止：2026-10-12"
        self.assertEqual(extract_location(text), ("江苏", "无锡"))
        self.assertEqual(extract_budget(text), "1280万元")
        self.assertEqual(extract_deadline(text), "2026-10-12")
        self.assertEqual(extract_owner(text), "星辰制造有限公司")


if __name__ == "__main__":
    unittest.main()
