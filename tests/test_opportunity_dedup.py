import tempfile
import unittest
from pathlib import Path

from services.opportunity.models import ProjectOpportunity, make_project_key
from services.opportunity.storage import load_snapshot, save_snapshot


class OpportunityDedupTest(unittest.TestCase):
    def test_normalized_key_ignores_announcement_words(self):
        self.assertEqual(
            make_project_key("某公司智能仓储项目采购公告", "某公司", "无锡"),
            make_project_key("某公司智能仓储招标公告", "某公司", "无锡"),
        )

    def test_cross_day_first_seen_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = ProjectOpportunity(title="智能仓储系统采购公告", source_url="https://a.test", first_seen_at=100)
            save_snapshot([first], "2026-09-20", base)
            second = ProjectOpportunity(title="智能仓储系统中标公告", source_url="https://b.test", first_seen_at=200)
            save_snapshot([second], "2026-09-21", base)
            loaded = load_snapshot("2026-09-21", base)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].first_seen_at, 100)


if __name__ == "__main__":
    unittest.main()
