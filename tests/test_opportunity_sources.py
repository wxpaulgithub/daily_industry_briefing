import unittest

import httpx

from services.opportunity.sources.bidding import OfficialBiddingSource


class OpportunitySourceTest(unittest.TestCase):
    def test_ccgp_mislabeled_encoding_is_recovered(self):
        html = '<a href="/item">智能仓储设备采购公告</a>'
        response = httpx.Response(200, content=html.encode("gb18030"))
        decoded = OfficialBiddingSource._decode_html(response, "https://www.ccgp.gov.cn/xxgg/")
        rows = OfficialBiddingSource._extract_links(decoded, "https://www.ccgp.gov.cn/xxgg/", "政府采购")
        self.assertEqual(rows[0].title, "智能仓储设备采购公告")
        self.assertEqual(rows[0].url, "https://www.ccgp.gov.cn/item")


if __name__ == "__main__":
    unittest.main()
