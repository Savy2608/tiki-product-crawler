"""End-to-end tests for enrichment.enrich with the fake provider."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from enrichment import enrich  # noqa: E402
from enrichment.config import EnrichConfig  # noqa: E402
from enrichment.io import read_jsonl, write_jsonl  # noqa: E402
from enrichment.llm_client import CostTracker, LLMClient  # noqa: E402
from tests.test_enrichment.fake_llm import FakeProvider  # noqa: E402


def product(pid, name, price=100_000):
    return {"id": pid, "name": name, "url_key": "p", "price": price,
            "description": "Mô tả sản phẩm", "description_original_length": 14,
            "images": ["https://img/a.jpg"]}


class EnrichRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = EnrichConfig(data_dir=self.tmp.name, workers=2, max_cost_usd=1.0)
        self.products = [product(i, f"Sách {i}") for i in range(25)]
        self.products[1]["name"] = "Áo FAKE"
        self.products[2]["name"] = "BAD listing"
        self.products[3]["price"] = 9_000_000  # far above the other books
        write_jsonl(self.config.rule_flags_path, [
            {"id": 0, "flags": [{"flag": "price_zero", "source": "rule", "severity": "medium",
                                 "evidence": "price is 0 VND"}]}])
        self.provider = FakeProvider()

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, spent=0.0):
        tracker = CostTracker(self.config.max_cost_usd, already_spent=spent)
        return LLMClient(self.provider, "fake-model", (1.0, 1.0), self.config.cache_dir, tracker)

    def run_all(self, client=None):
        prompts = enrich.load_prompts()
        with mock.patch.object(enrich, "log"):
            done, stop = enrich.run(self.config, self.products, client or self.client(), prompts)
            summary = None
            if not stop:
                rules = {r["id"]: r["flags"] for r in read_jsonl(self.config.rule_flags_path)}
                summary = enrich.finalize(self.config, self.products, done, rules, prompts)
        return done, stop, summary

    def test_full_run_merges_flags_and_passes_completeness(self):
        _, stop, summary = self.run_all()
        self.assertIsNone(stop)
        self.assertEqual((summary["results"], summary["rejects"]), (24, 1))
        self.assertTrue(summary["completeness"]["ok"])
        results = {r["id"]: r for r in read_jsonl(os.path.join(self.tmp.name, "results",
                                                               "enriched_v1.jsonl"))}
        self.assertEqual([f["flag"] for f in results[0]["flags"]], ["price_zero"])
        self.assertEqual([(f["flag"], f["source"]) for f in results[1]["flags"]],
                         [("counterfeit_or_brand_risk", "llm")])
        self.assertEqual([f["flag"] for f in results[3]["flags"]], ["price_outlier"])
        self.assertEqual(results[4]["model"], "fake-model")
        self.assertEqual(results[4]["prompt_versions"], {"enrich": "enrich_v1", "flag": "flag_v1"})
        rejects = read_jsonl(os.path.join(self.tmp.name, "results", "rejects_v1.jsonl"))
        self.assertEqual([(r["id"], r["step"]) for r in rejects], [(2, "enrich")])
        self.assertTrue(rejects[0]["raw"])

    def test_rerun_with_cache_costs_nothing(self):
        self.run_all()
        calls = len(self.provider.calls)
        self.config.restart = True
        _, _, summary = self.run_all()
        self.assertEqual(len(self.provider.calls) - calls, 2)  # only the rejected BAD product
        self.assertEqual(summary["products_from_cache"], 24)
        self.assertEqual(summary["api_cost_usd_this_run"], round(2 * 0.0011, 6))
        self.assertEqual(summary["cost_usd"], round(24 * 0.0022 + 2 * 0.0011, 6))

    def test_cost_limit_stops_and_next_run_continues(self):
        self.config.max_cost_usd = 0.01  # about 5 products at 0.0022 USD each
        done, stop, _ = self.run_all()
        self.assertIn("limit", stop)
        self.assertLess(len(done), 25)
        self.config.max_cost_usd = 1.0
        done, stop, summary = self.run_all()
        self.assertIsNone(stop)
        self.assertEqual(len(done), 25)
        self.assertTrue(summary["completeness"]["ok"])

    def test_temporary_api_error_leaves_the_product_for_the_next_run(self):
        self.products[5]["name"] = "BOOM"
        done, stop, _ = self.run_all()
        self.assertIsNone(stop)
        self.assertEqual(len(done), 24)
        self.assertNotIn(5, done)

    def test_completeness_detects_missing_and_duplicates(self):
        rows = [{"id": 1}, {"id": 1}]
        check = enrich.completeness([{"id": 1}, {"id": 2}], rows, [])
        self.assertEqual((check["ok"], check["missing"], check["duplicates"]), (False, 1, 1))


if __name__ == "__main__":
    unittest.main()
