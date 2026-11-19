"""Step 2: deterministic data-quality checks (no LLM).

    python -m enrichment.rules

Reads data/sample_products.jsonl and writes data/results/rule_flags.jsonl,
one row per product with its list of rule flags. price_outlier needs the
LLM-assigned category, so it is applied later by the enrichment step.
"""
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict

from enrichment.config import parse_args
from enrichment.io import read_jsonl, write_jsonl

RULE_FLAGS = ["price_zero", "no_images", "empty_url_key", "short_description",
              "duplicate_name", "price_outlier"]
MIN_CATEGORY_SIZE = 20  # price_outlier only for categories with at least this many products


def flag(name, severity, evidence):
    return {"flag": name, "source": "rule", "severity": severity, "evidence": evidence}


def normalize_name(name):
    """Unicode NFC, lower case, single spaces: 'Áo  Thun ' and 'áo thun' match."""
    name = unicodedata.normalize("NFC", name or "")
    return re.sub(r"\s+", " ", name).strip().casefold()


def check_product(product, short_chars):
    """Flags that depend on one product only."""
    flags = []
    if product.get("price") == 0:
        flags.append(flag("price_zero", "medium", "price is 0 VND"))
    if not product.get("images"):
        flags.append(flag("no_images", "medium", "images list is empty"))
    if not product.get("url_key"):
        flags.append(flag("empty_url_key", "high", "url_key is empty, so the product URL is broken"))
    length = product.get("description_original_length", len(product.get("description") or ""))
    if length < short_chars:
        flags.append(flag("short_description", "low",
                          f"description has {length} characters (minimum {short_chars})"))
    return flags


def duplicate_name_flags(products):
    """{id: flag} for products whose normalized name appears more than once."""
    groups = defaultdict(list)
    for product in products:
        key = normalize_name(product.get("name"))
        if key:
            groups[key].append(product["id"])
    flags = {}
    for ids in groups.values():
        if len(ids) < 2:
            continue
        for pid in ids:
            others = [str(other) for other in ids if other != pid]
            shown = ", ".join(others[:3]) + (", ..." if len(others) > 3 else "")
            flags[pid] = flag("duplicate_name", "low",
                              f"same name as {len(others)} other product(s): {shown}")
    return flags


def iqr_bounds(values):
    """(low, high) = (Q1 - 1.5 IQR, Q3 + 1.5 IQR), quartiles with linear interpolation."""
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    iqr = q3 - q1
    return q1 - 1.5 * iqr, q3 + 1.5 * iqr


def price_outlier_flags(products, categories, min_size=MIN_CATEGORY_SIZE):
    """{id: flag} for prices outside 1.5 x IQR of their category.

    categories: {id: category}. Products with price 0 are left out of the bounds
    (they already have price_zero), and small categories are skipped."""
    prices = defaultdict(list)
    for product in products:
        category = categories.get(product["id"])
        if category and product.get("price"):
            prices[category].append(product["price"])
    bounds = {c: iqr_bounds(v) for c, v in prices.items() if len(v) >= min_size}

    flags = {}
    for product in products:
        category, price = categories.get(product["id"]), product.get("price")
        if category not in bounds or not price:
            continue
        low, high = bounds[category]
        if price > high:
            flags[product["id"]] = flag("price_outlier", "low",
                                        f"{price:,} VND is above the {category} upper bound "
                                        f"{high:,.0f} VND")
        elif price < low:
            flags[product["id"]] = flag("price_outlier", "low",
                                        f"{price:,} VND is below the {category} lower bound "
                                        f"{low:,.0f} VND")
    return flags


def run_rules(products, short_chars):
    """One row per product, in input order: {"id": ..., "flags": [...]}."""
    duplicates = duplicate_name_flags(products)
    rows = []
    for product in products:
        flags = check_product(product, short_chars)
        if product["id"] in duplicates:
            flags.append(duplicates[product["id"]])
        rows.append({"id": product["id"], "flags": flags})
    return rows


def main(argv=None):
    config = parse_args(argv, description="Run the deterministic rule checks on the sample.")
    products = read_jsonl(config.sample_path)
    rows = run_rules(products, config.short_description_chars)
    write_jsonl(config.rule_flags_path, rows)

    counts = Counter(f["flag"] for row in rows for f in row["flags"])
    flagged = sum(1 for row in rows if row["flags"])
    print(f"Checked {len(rows)} products: {flagged} have at least one rule flag.")
    for name in RULE_FLAGS[:-1]:
        print(f"  {name:<18} {counts.get(name, 0)}")
    print("  price_outlier      (applied after LLM enrichment)")
    print(f"Wrote {config.rule_flags_path}")


if __name__ == "__main__":
    sys.exit(main())
