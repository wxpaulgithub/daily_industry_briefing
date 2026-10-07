import json
import unittest
from pathlib import Path

from services.opportunity.ai_researcher import prefilter
from services.opportunity.models import OpportunityCandidate
from services.opportunity.rules import classify_stage


class OpportunityGoldTests(unittest.TestCase):
    def test_curated_prefilter_and_stage_regression_cases(self):
        data = json.loads((Path(__file__).parent / 'fixtures' / 'opportunity_gold.json').read_text(encoding='utf-8'))
        self.assertEqual(data['dataset_type'], 'synthetic_regression')
        self.assertGreaterEqual(len(data['cases']), 80)
        for case in data['cases']:
            with self.subTest(case=case['id']):
                item = OpportunityCandidate(case['title'], case['url'], summary=case['summary'])
                self.assertEqual(bool(prefilter([item])), case['expected_prefilter'])
                self.assertEqual(classify_stage(case['title']), case['expected_stage'])
