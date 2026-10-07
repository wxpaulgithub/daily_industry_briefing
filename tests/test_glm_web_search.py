import unittest
from types import SimpleNamespace

from services.opportunity.search.glm_web_search import GLMWebSearch
from services.opportunity.rules import is_too_old


class FakeUsage:
    def __init__(self):
        self.reserved = []
        self.recorded = []

    def reserve(self, amount):
        self.reserved.append(amount)
        return amount

    def record(self, *values):
        self.recorded.append(values)


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "usage": {"prompt_tokens": 12, "completion_tokens": 34},
            "choices": [{"message": {"tool_calls": [
                {"type": "web_search", "search_result": [{
                    "title": "某制造企业自动化立库采购公告（发布时间：2024-02-08 23:59:00）",
                    "link": "https://procurement.example.cn/notice/1",
                    "content": "采购堆垛机、输送系统及 WMS。",
                    "media": "企业采购平台",
                }]},
                {"type": "web_search", "search_result": []},
            ]}}],
        }


class FakeClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, *, headers, json):
        self.calls.append((url, headers, json))
        return FakeResponse()


class GLMWebSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_results_become_candidates_and_are_metered(self):
        settings = SimpleNamespace(
            glm_base_url="https://open.bigmodel.cn/api/paas/v4",
            glm_api_key="test-secret",
            request_timeout=5,
        )
        usage = FakeUsage()
        client = FakeClient()
        search = GLMWebSearch(settings, usage, client)

        candidates = await search._search("制造企业自动化立库招标")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "某制造企业自动化立库采购公告")
        self.assertEqual(candidates[0].url, "https://procurement.example.cn/notice/1")
        self.assertEqual(candidates[0].discovery_method, "ai_search")
        self.assertGreater(candidates[0].published_ts, 0)
        self.assertTrue(is_too_old("PROCUREMENT", candidates[0].published_ts))
        self.assertEqual(client.calls[0][0], "https://open.bigmodel.cn/api/paas/v4/chat/completions")
        self.assertEqual(client.calls[0][2]["model"], "web-search-pro")
        self.assertEqual(usage.reserved, [1])
        self.assertEqual(usage.recorded, [("glm_web_search", 12, 34, 1)])


if __name__ == "__main__":
    unittest.main()
