"""Tests for enrichment.evaluate: eval sample, label checks and metric math."""
import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from enrichment.evaluate import (CSV_COLUMNS, draw_eval_sample, flag_metrics,  # noqa: E402
                                 macro_f1, normalize_brand, read_labels, score)


def sample_product(pid, edge=False):
    return {"id": pid, "edge_cases": ["price_zero"] if edge else []}


def result(pid, category="toys_games", brand=None, confidence=0.95, flags=()):
    return {"id": pid,
            "enrichment": {"category": category, "brand": brand, "key_attributes": [],
                           "language": "vi", "confidence": confidence},
            "flags": [{"flag": f, "source": "llm", "severity": "low", "evidence": "x"}
                      for f in flags]}


def label(category="toys_games", brand=None, **flags):
    names = ["name_description_mismatch", "counterfeit_or_brand_risk", "spam_or_low_quality"]
    return {"category": category, "brand": brand, "brand_raw": brand or "none",
            "flags": {n: flags.get(n, False) for n in names}, "notes": ""}


class EvalSampleTest(unittest.TestCase):
    def setUp(self):
        self.products = [sample_product(i, edge=i < 3) for i in range(100)]
        self.results = [result(i, flags=["spam_or_low_quality"] if 10 <= i < 20 else [])
                        for i in range(100)]

    def test_strata_sizes_and_reproducible(self):
        chosen, population = draw_eval_sample(self.products, self.results, 30, 5, seed=42)
        again, _ = draw_eval_sample(self.products, self.results, 30, 5, seed=42)
        self.assertEqual(chosen, again)
        self.assertEqual(population, {"edge_case": 3, "llm_flagged": 10, "other": 87})
        counts = {s: list(chosen.values()).count(s) for s in population}
        self.assertEqual(counts, {"edge_case": 3, "llm_flagged": 5, "other": 22})

    def test_template_columns_hide_the_model_answers(self):
        for column in CSV_COLUMNS:
            self.assertNotIn("pred", column)
            self.assertNotIn("model", column)


class ReadLabelsTest(unittest.TestCase):
    def write(self, rows):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig",
                                          newline="")
        writer = csv.DictWriter(tmp, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        tmp.close()
        self.addCleanup(os.remove, tmp.name)
        return tmp.name

    def test_complete_row(self):
        path = self.write([{"id": 1, "true_category": "books", "true_brand": "none",
                            "name_description_mismatch": "0", "counterfeit_or_brand_risk": "1",
                            "spam_or_low_quality": "0"}])
        labels, problems = read_labels(path)
        self.assertEqual(problems, [])
        self.assertIsNone(labels[1]["brand"])
        self.assertTrue(labels[1]["flags"]["counterfeit_or_brand_risk"])

    def test_empty_or_invalid_cells_are_reported(self):
        path = self.write([{"id": 1, "true_category": "gadgets", "true_brand": "",
                            "name_description_mismatch": "yes"}])
        _, problems = read_labels(path)
        self.assertEqual(len(problems), 5)  # category, brand, three flag cells
        self.assertTrue(all(p.startswith("line 2") for p in problems))

    def test_normalize_brand(self):
        self.assertEqual(normalize_brand("  Thiên   Long "), "thiên long")
        self.assertIsNone(normalize_brand("None"))
        self.assertIsNone(normalize_brand(""))


class MetricTest(unittest.TestCase):
    def test_macro_f1(self):
        # a: P 1/1, R 1/2 -> F1 0.667; b: P 1/2, R 1/1 -> F1 0.667
        self.assertEqual(macro_f1([("a", "a"), ("a", "b"), ("b", "b")]), 0.6667)

    def test_flag_metrics_raw_and_weighted(self):
        rows = [{"label": label(spam_or_low_quality=True), "pred_flags": {"spam_or_low_quality"},
                 "weight": 1.0},                                  # TP
                {"label": label(), "pred_flags": {"spam_or_low_quality"}, "weight": 1.0},  # FP
                {"label": label(spam_or_low_quality=True), "pred_flags": set(), "weight": 3.0},  # FN
                {"label": label(), "pred_flags": set(), "weight": 3.0}]   # TN
        m = flag_metrics(rows, "spam_or_low_quality")
        self.assertEqual((m["true_positives"], m["false_positives"], m["false_negatives"]), (1, 1, 1))
        self.assertEqual((m["precision"], m["recall"]), (0.5, 0.5))
        self.assertEqual(m["estimated_recall"], 0.25)  # 1 / (1 + 3)

    def test_score_end_to_end(self):
        labels = {1: label("books", None), 2: label("toys_games", "lego"),
                  3: label("toys_games", None, counterfeit_or_brand_risk=True)}
        results = [result(1, "books", None, 0.95), result(2, "books", "LEGO", 0.92),
                   result(3, "toys_games", "Lego", 0.5, flags=["counterfeit_or_brand_risk"])]
        manifest = {"strata": {"other": {"population": 30, "labeled": 3}},
                    "stratum_by_id": {"1": "other", "2": "other", "3": "other"}}
        metrics, _ = score(labels, results, manifest, {"products": 1000, "rejects": 0})
        self.assertEqual(metrics["category"]["accuracy"], 0.6667)
        self.assertEqual(metrics["brand"]["exact_match_rate"], 0.6667)
        self.assertEqual(metrics["brand"]["wrong_or_invented_brand_rate"], 0.5)
        self.assertEqual(metrics["calibration"]["high_confidence_rows"], 2)
        self.assertEqual(metrics["calibration"]["high_confidence_wrong_share"], 0.5)
        self.assertEqual(metrics["flags"]["counterfeit_or_brand_risk"]["precision"], 1.0)
        self.assertEqual(metrics["llm_run"]["reject_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
