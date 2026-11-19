"""Tests for enrichment.sample: reproducible, edge cases included, spread evenly."""
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from enrichment.sample import draw_sample, edge_case_reasons  # noqa: E402


def product(pid, price=1000, images=("https://img/a.jpg",), url_key="p", description="Nice"):
    return {"id": pid, "name": f"Product {pid}", "url_key": url_key, "price": price,
            "description": description, "images": list(images)}


def make_batches():
    """4 files of 10 products; 3 edge cases hidden in different files."""
    batches = []
    for f in range(4):
        batches.append((f"products_{f + 1:04d}.json",
                        [product(f * 100 + i) for i in range(10)]))
    batches[0][1][3]["price"] = 0
    batches[1][1][5]["images"] = []
    batches[3][1][9]["url_key"] = ""
    return batches


class SampleTest(unittest.TestCase):
    def test_edge_case_reasons(self):
        self.assertEqual(edge_case_reasons(product(1)), [])
        self.assertEqual(edge_case_reasons(product(1, price=0, images=(), url_key="")),
                         ["price_zero", "no_images", "empty_url_key"])

    def test_same_seed_gives_same_sample(self):
        a = draw_sample(make_batches(), 15, seed=42, max_chars=100)
        b = draw_sample(make_batches(), 15, seed=42, max_chars=100)
        c = draw_sample(make_batches(), 15, seed=7, max_chars=100)
        self.assertEqual([r["id"] for r in a], [r["id"] for r in b])
        self.assertNotEqual([r["id"] for r in a], [r["id"] for r in c])

    def test_size_unique_ids_and_all_edge_cases(self):
        rows = draw_sample(make_batches(), 15, seed=42, max_chars=100)
        ids = [r["id"] for r in rows]
        self.assertEqual(len(ids), 15)
        self.assertEqual(len(set(ids)), 15)
        self.assertTrue({3, 105, 309} <= set(ids))
        self.assertEqual(sum(1 for r in rows if r["edge_cases"]), 3)

    def test_random_part_is_spread_evenly(self):
        rows = draw_sample(make_batches(), 15, seed=42, max_chars=100)
        random_per_file = Counter(r["source_file"] for r in rows if not r["edge_cases"])
        self.assertEqual(sorted(random_per_file.values()), [3, 3, 3, 3])  # 12 / 4 files

    def test_description_is_cut_and_original_length_kept(self):
        batches = make_batches()
        batches[0][1][0]["description"] = "x" * 250
        rows = draw_sample(batches, 40, seed=42, max_chars=100)  # whole input
        row = next(r for r in rows if r["id"] == 0)
        self.assertEqual((len(row["description"]), row["description_original_length"]), (100, 250))

    def test_too_small_sample_or_input_is_an_error(self):
        with self.assertRaises(ValueError):
            draw_sample(make_batches(), 2, seed=42, max_chars=100)   # fewer than the edge cases
        with self.assertRaises(ValueError):
            draw_sample(make_batches(), 41, seed=42, max_chars=100)  # more than the input


if __name__ == "__main__":
    unittest.main()
