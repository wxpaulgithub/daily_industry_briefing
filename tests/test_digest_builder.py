import unittest

from services.opportunity.models import ProjectOpportunity
from services.opportunity.publisher import build_wecom_digest


class DigestBuilderTests(unittest.TestCase):
    def test_large_digest_preserves_all_five_titles_and_footer(self):
        items = [ProjectOpportunity(f"项目{number}智能仓储采购" + "很长的名称" * 20, "https://example.com", budget="380万元", priority="A", stage="PROCUREMENT", technical_scope=["堆垛机"] * 50, entry_point="获取技术文件" * 100, research_mode="ai") for number in range(1, 7)]
        digest = build_wecom_digest(items, "2026-10-07", "https://mag.example.com")
        self.assertLessEqual(len(digest.encode()), 4096)
        for number in range(1, 6):
            self.assertIn(f"项目{number}", digest)
        self.assertNotIn("项目6", digest)
        self.assertIn("https://mag.example.com/?scope=opportunity", digest)

    def test_fallback_is_clearly_identified(self):
        item = ProjectOpportunity("样本立库采购", "https://example.com", research_mode="fallback")
        self.assertIn("规则筛选", build_wecom_digest([item], "2026-10-07", "https://example.com"))
