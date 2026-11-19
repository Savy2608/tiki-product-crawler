"""Tests for enrichment.llm_client: cache, validation retry, refusals, cost guard."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from enrichment.llm_client import (CostLimitReached, CostTracker, LLMClient,  # noqa: E402
                                   Prompt, load_prompt)
from enrichment.schemas import ENRICH_JSON_SCHEMA, Enrichment  # noqa: E402
from tests.test_enrichment.fake_llm import FakeProvider  # noqa: E402

PROMPT = Prompt("enrich_test", "Extract the fields.")


def text(name):
    return json.dumps({"name": name, "description": "Mô tả"}, ensure_ascii=False)


class LLMClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider()
        self.tracker = CostTracker(limit_usd=1.0)
        # 1 USD per million tokens in and out: one call = 1100 tokens = 0.0011 USD
        self.client = LLMClient(self.provider, "fake-model", (1.0, 1.0), self.tmp.name, self.tracker)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name, prompt=PROMPT):
        return self.client.call(prompt, text(name), Enrichment, ENRICH_JSON_SCHEMA)

    def test_valid_answer_with_usage_and_cost(self):
        result = self.call("Sách")
        self.assertEqual(result.data["category"], "books")
        self.assertEqual((result.usage["input_tokens"], result.usage["output_tokens"]), (1000, 100))
        self.assertAlmostEqual(result.usage["cost_usd"], 0.0011)
        self.assertFalse(result.usage["cached"])

    def test_cache_hit_costs_nothing_and_skips_the_api(self):
        self.call("Sách")
        again = self.call("Sách")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertTrue(again.usage["cached"])
        self.assertEqual(again.usage["api_cost_usd"], 0.0)            # nothing spent now
        self.assertAlmostEqual(again.usage["cost_usd"], 0.0011)       # original cost kept
        self.assertAlmostEqual(self.tracker.spent, 0.0011)

    def test_cache_miss_for_other_input_or_prompt_version(self):
        self.call("Sách")
        self.call("Bút")
        self.call("Sách", prompt=Prompt("enrich_test_v2", "Extract the fields."))
        self.assertEqual(len(self.provider.calls), 3)

    def test_invalid_answer_is_retried_once_with_the_error(self):
        result = self.call("BADONCE")
        self.assertEqual(result.data["category"], "books")
        self.assertEqual(result.usage["attempts"], 2)

    def test_invalid_twice_is_rejected_with_raw_answer_and_not_cached(self):
        result = self.call("BAD")
        self.assertIsNone(result.data)
        self.assertIn("schema validation failed", result.error)
        self.assertEqual(result.raw, '{"category": "nonsense"}')
        self.call("BAD")
        self.assertEqual(len(self.provider.calls), 4)  # nothing cached for rejects

    def test_refusal_is_rejected(self):
        result = self.call("REFUSE")
        self.assertIsNone(result.data)
        self.assertIn("refused", result.error)

    def test_cost_guard_stops_before_the_next_call(self):
        self.tracker.limit = 0.002  # room for two calls
        self.call("A")
        self.call("B")
        with self.assertRaises(CostLimitReached):
            self.call("C")
        self.assertEqual(len(self.provider.calls), 2)

    def test_real_prompts_load_and_fill_categories(self):
        prompt = load_prompt("enrich_v1", categories='"books", "other"')
        self.assertIn('"books", "other"', prompt.text)
        self.assertNotIn("{categories}", prompt.text)
        self.assertEqual(len(prompt.sha), 12)
        self.assertIn('{"flags": []}', load_prompt("flag_v1").text)


if __name__ == "__main__":
    unittest.main()
