"""博查搜索工具的离线回归测试；不调用外部 API。"""

import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
search_module = importlib.import_module("agent.tools.web_search")


class WebSearchTests(unittest.TestCase):
    def setUp(self):
        key_patch = patch.object(search_module, "BOCHA_API_KEY", "test-bocha-key")
        key_patch.start()
        self.addCleanup(key_patch.stop)
        post_patch = patch.object(search_module.requests, "post")
        self.post = post_patch.start()
        self.addCleanup(post_patch.stop)
        self.response = self.post.return_value
        self.response.json.return_value = {"code": 200, "data": {}}

    def search(self, **kwargs):
        return search_module.web_search.invoke({"query": "采购行情", **kwargs})

    def test_existing_query_only_calls_and_tool_schema(self):
        self.search(query="  采购行情  ")
        self.post.assert_called_once_with(
            "https://api.bocha.cn/v1/web-search",
            headers={
                "Authorization": "Bearer test-bocha-key",
                "Content-Type": "application/json",
            },
            json={"query": "采购行情", "summary": True, "freshness": "noLimit", "count": 5},
            timeout=30,
        )
        self.assertEqual(search_module.web_search.name, "web_search")
        schema = search_module.web_search.args_schema.model_json_schema()
        self.assertEqual(schema["required"], ["query"])
        self.assertEqual(schema["properties"]["count"]["default"], 5)
        self.assertNotIn("BOCHA_API_KEY", schema["properties"])

    def test_filters_and_count_reach_api(self):
        self.search(
            freshness="2026-10-01..2026-10-29", count=50,
            include="example.com,example.org", exclude="excluded.example",
        )
        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["freshness"], "2026-10-01..2026-10-29")
        self.assertEqual(payload["count"], 50)
        self.assertEqual(payload["include"], "example.com,example.org")
        self.assertEqual(payload["exclude"], "excluded.example")

    def test_results_keep_sources_and_respect_count(self):
        self.response.json.return_value = {
            "code": "200",
            "data": {"webPages": {"value": [
                {"name": "供应商报价", "summary": "报价摘要", "snippet": "备用摘要",
                 "url": "https://example.com/prices", "siteName": "供应商网站",
                 "datePublished": "2026-10-29"},
                {"name": "超出数量限制"},
            ]}},
        }
        output = self.search(count=1)
        for value in ("供应商报价", "报价摘要", "https://example.com/prices", "供应商网站", "2026-10-29"):
            self.assertIn(value, output)
        self.assertNotIn("备用摘要", output)
        self.assertNotIn("超出数量限制", output)

    def test_missing_page_fields_and_non_object_entries(self):
        self.response.json.return_value = {
            "code": 200,
            "data": {"webPages": {"value": [None, "invalid", {"snippet": "备用简介"}]}},
        }
        output = self.search()
        self.assertIn("备用简介", output)
        self.assertIn("无标题", output)
        self.assertIn("无链接", output)

    def test_invalid_inputs_do_not_request(self):
        for arguments in ({"query": " \n"}, {"count": 0}, {"count": 51}):
            with self.subTest(arguments=arguments):
                self.assertTrue(self.search(**arguments))
        self.post.assert_not_called()

    def test_missing_key_does_not_request(self):
        with patch.object(search_module, "BOCHA_API_KEY", None):
            self.assertIn("未配置 BOCHA_API_KEY", self.search())
        self.post.assert_not_called()

    def test_empty_results(self):
        self.assertIn("未找到相关网页", self.search())

    def test_malformed_responses_return_error(self):
        for payload in (
            [], {"code": 200, "data": "invalid"},
            {"code": 200, "data": {"webPages": "invalid"}},
            {"code": 200, "data": {"webPages": {"value": "invalid"}}},
        ):
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                self.assertIn("格式异常", self.search())
        self.response.json.side_effect = requests.exceptions.JSONDecodeError("invalid", "x", 0)
        self.assertIn("无法解析的 JSON", self.search())

    def test_transport_and_service_errors_redact_key(self):
        errors = (
            requests.Timeout("timeout test-bocha-key"),
            requests.ConnectionError("connection test-bocha-key"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.post.side_effect = error
                output = self.search()
                self.assertIn("请求失败", output)
                self.assertIn("[REDACTED]", output)
                self.assertNotIn("test-bocha-key", output)
        self.post.side_effect = None
        self.response.raise_for_status.side_effect = requests.HTTPError("401 test-bocha-key")
        self.assertNotIn("test-bocha-key", self.search())
        self.response.raise_for_status.side_effect = None
        self.response.json.return_value = {"code": 401, "msg": "invalid test-bocha-key"}
        output = self.search()
        self.assertIn("401", output)
        self.assertIn("[REDACTED]", output)
        self.assertNotIn("test-bocha-key", output)


if __name__ == "__main__":
    unittest.main()
