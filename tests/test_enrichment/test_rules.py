"""Tests for enrichment.rules: each rule check and the IQR price outlier."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from enrichment.rules import (check_product, iqr_bounds, normalize_name,  # noqa: E402
                              price_outlier_flags, run_rules)


def product(pid, name="Sách hay", price=1000, images=("https://img/a.jpg",), url_key="p",
            description="Mô tả sản phẩm đủ dài để không bị gắn cờ mô tả ngắn, hơn năm mươi ký tự."):
    return {"id": pid, "name": name, "url_key": url_key, "price": price,
            "description": description, "description_original_length": len(description),
            "images": list(images)}


def names(flags):
    return [f["flag"] for f in flags]


class RuleTest(unittest.TestCase):
    def test_clean_product_has_no_flags(self):
        self.assertEqual(check_product(product(1), 50), [])

    def test_each_single_product_rule(self):
        self.assertEqual(names(check_product(product(1, price=0), 50)), ["price_zero"])
        self.assertEqual(names(check_product(product(1, images=()), 50)), ["no_images"])
        self.assertEqual(names(check_product(product(1, url_key=""), 50)), ["empty_url_key"])
        self.assertEqual(names(check_product(product(1, description="Ngắn"), 50)),
                         ["short_description"])

    def test_short_description_uses_the_original_length(self):
        row = product(1, description="x" * 10)
        row["description_original_length"] = 5000  # the text was cut, not short
        self.assertEqual(check_product(row, 50), [])

    def test_every_flag_has_source_severity_and_evidence(self):
        for f in check_product(product(1, price=0, images=(), url_key="", description=""), 50):
            self.assertEqual(f["source"], "rule")
            self.assertIn(f["severity"], ("low", "medium", "high"))
            self.assertTrue(f["evidence"])

    def test_normalize_name(self):
        self.assertEqual(normalize_name("  Áo   Thun\tNam "), "áo thun nam")
        # NFD (decomposed accents) and NFC must give the same key
        self.assertEqual(normalize_name("Áo"), normalize_name("Áo"))

    def test_duplicate_name(self):
        rows = run_rules([product(1, name="Bút Bi"), product(2, name="bút  bi"),
                          product(3, name="Thước kẻ")], 50)
        self.assertEqual([names(r["flags"]) for r in rows],
                         [["duplicate_name"], ["duplicate_name"], []])
        self.assertIn("2", rows[0]["flags"][0]["evidence"])

    def test_iqr_bounds(self):
        low, high = iqr_bounds([1, 2, 3, 4, 5, 6, 7, 8, 9])  # Q1 = 3, Q3 = 7, IQR = 4
        self.assertEqual((low, high), (-3.0, 13.0))

    def test_price_outlier_only_for_large_categories(self):
        products = [product(i, price=100_000) for i in range(20)]
        products += [product(20, price=5_000_000), product(21, price=0)]
        products += [product(30 + i, price=1 if i == 0 else 100_000) for i in range(5)]
        categories = {i: "books" for i in range(22)}
        categories.update({30 + i: "pets" for i in range(5)})  # only 5 products: skipped
        flags = price_outlier_flags(products, categories)
        self.assertEqual(list(flags), [20])
        self.assertIn("above the books upper bound", flags[20]["evidence"])


if __name__ == "__main__":
    unittest.main()
