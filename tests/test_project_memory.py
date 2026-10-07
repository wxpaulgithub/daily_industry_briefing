import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from services.opportunity.models import OpportunityBatch, ProjectOpportunity, make_project_key
from services.opportunity.project_memory import apply_history, load_memory, mark_reported, needs_report
from services.opportunity.storage import load_snapshot, save_snapshot
from services.opportunity.ai_researcher import rank_opportunities
from unittest.mock import patch
from v2_helpers import NOW


class ProjectMemoryTests(unittest.TestCase):
    def sample(self):
        return ProjectOpportunity("样本公司自动化立库采购公告", "https://example.com/project", stage="PROCUREMENT", budget="380万元", priority="A", first_seen_at=100)

    def test_secondary_tender_and_award_share_identity(self):
        keys = [make_project_key(title) for title in ("样本公司自动化立库采购公告", "样本公司自动化立库二次招标", "样本公司自动化立库中标候选人")]
        self.assertEqual(len(set(keys)), 1)

    def test_known_project_survives_owner_enrichment_and_url_change(self):
        old = self.sample()
        new = replace(old, owner="样本制造有限公司", source_url="https://www.ccgp.gov.cn/project", project_key="", first_seen_at=200)
        apply_history([new], {old.project_key: old.to_dict()}, 300)
        self.assertEqual(new.project_key, old.project_key)
        self.assertEqual(new.first_seen_at, 100)
        self.assertFalse(new.is_new)

    def test_unchanged_project_is_not_sent_again_across_days(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = self.sample()
            save_snapshot([first], "2026-10-06", base)
            mark_reported([first], "2026-10-06", base, now_ts=1000)
            second = self.sample()
            save_snapshot([second], "2026-10-07", base)
            loaded = load_snapshot("2026-10-07", base)[0]
            self.assertFalse(needs_report(loaded))
            self.assertEqual(loaded.last_reported_at, 1000)

    def test_stage_and_budget_changes_are_reportable_until_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = replace(self.sample(), stage="EARLY_SIGNAL")
            save_snapshot([first], "2026-10-06", base)
            mark_reported([first], "2026-10-06", base)
            second = self.sample()
            apply_history([second], load_memory(base))
            self.assertTrue(second.is_updated)
            self.assertTrue(needs_report(second))
            save_snapshot([second], "2026-10-07", base)
            refreshed = self.sample()
            save_snapshot([refreshed], "2026-10-07", base)
            self.assertTrue(needs_report(refreshed))
            mark_reported([refreshed], "2026-10-07", base)
            changed = replace(refreshed, budget="500万元")
            apply_history([changed], load_memory(base))
            self.assertTrue(needs_report(changed))

    def test_award_observation_updates_memory_without_entering_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            old = replace(self.sample(), published_ts=100)
            save_snapshot([old], "2026-10-06", base)
            awarded = replace(self.sample(), stage="AWARD", published_ts=200)
            save_snapshot(OpportunityBatch([], observations=[awarded]), "2026-10-07", base)
            self.assertEqual(load_snapshot("2026-10-07", base), [])
            self.assertEqual(load_memory(base)[old.project_key]["stage"], "AWARD")
            stale = replace(self.sample(), published_ts=100)
            apply_history([stale], load_memory(base))
            self.assertEqual(stale.stage, "AWARD")

    def test_new_items_precede_reported_items_in_durable_page_order(self):
        with tempfile.TemporaryDirectory() as directory:
            old = replace(self.sample(), title="高分旧项目", project_key="", final_score=95, research_mode="ai", published_ts=NOW.timestamp(), last_reported_at=1)
            from services.opportunity.project_memory import material_hash
            old.last_digest_hash = material_hash(old)
            new = replace(self.sample(), title="新发现项目", project_key="", final_score=75, research_mode="ai", published_ts=NOW.timestamp(), relevance_score=80)
            old.relevance_score = 100
            with patch("services.opportunity.ai_researcher.local_now", return_value=NOW):
                ranked = rank_opportunities([old, new], {}, 10)
            self.assertEqual(ranked[0].title, "新发现项目")
            save_snapshot(ranked, "2026-10-07", Path(directory))
            self.assertEqual(load_snapshot("2026-10-07", Path(directory))[0].title, "新发现项目")
