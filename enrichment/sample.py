"""Step 1: draw a reproducible sample of crawled products.

    python -m enrichment.sample [--sample-size 1000] [--seed 42]

The known edge cases (price 0, no images, empty url_key) are always included.
The rest of the sample is spread evenly across the batch files. Descriptions
are cut to --max-input-chars to cap the LLM cost; the original length is kept.
Writes data/sample_products.jsonl and data/sample_manifest.json.
"""
import glob
import os
import random
import sys
from datetime import datetime, timezone

from enrichment.config import parse_args
from enrichment.io import write_json, write_jsonl
from src.storage import load_json


def edge_case_reasons(product):
    """Known data-quality quirks from the crawl that must be in every sample."""
    reasons = []
    if product.get("price") == 0:
        reasons.append("price_zero")
    if not product.get("images"):
        reasons.append("no_images")
    if not product.get("url_key"):
        reasons.append("empty_url_key")
    return reasons


def load_batches(input_dir):
    """[(file_name, [products])] for every products_XXXX.json, in file order."""
    paths = sorted(glob.glob(os.path.join(input_dir, "products_*.json")))
    if not paths:
        raise SystemExit(f"No products_XXXX.json files found in {input_dir}/")
    return [(os.path.basename(path), load_json(path)) for path in paths]


def to_sample_row(product, source_file, reasons, max_chars):
    description = product.get("description") or ""
    return {
        "id": product["id"],
        "name": product.get("name") or "",
        "url_key": product.get("url_key") or "",
        "price": product.get("price"),
        "description": description[:max_chars],
        "description_original_length": len(description),
        "images": product.get("images") or [],
        "source_file": source_file,
        "edge_cases": reasons,
    }


def draw_sample(batches, size, seed, max_chars):
    """Pick `size` products: every edge case, then an even share of each batch file."""
    rng = random.Random(seed)
    edge, pools = [], []
    for file_name, products in batches:
        pool = []
        for position, product in enumerate(products):
            reasons = edge_case_reasons(product)
            if reasons:
                edge.append((file_name, position, product, reasons))
            else:
                pool.append((file_name, position, product, []))
        pools.append(pool)

    remaining = size - len(edge)
    if remaining < 0:
        raise ValueError(f"sample size {size} is smaller than the {len(edge)} edge cases")

    # Every file gets the same quota; the leftover is given to random files.
    quota = [remaining // len(pools)] * len(pools)
    for i in rng.sample(range(len(pools)), remaining % len(pools)):
        quota[i] += 1

    picked, leftover = [], []
    for pool, n in zip(pools, quota):
        chosen = set(rng.sample(range(len(pool)), min(n, len(pool))))
        picked += [item for i, item in enumerate(pool) if i in chosen]
        leftover += [item for i, item in enumerate(pool) if i not in chosen]
    shortfall = remaining - len(picked)
    if shortfall > len(leftover):
        raise ValueError(f"only {len(edge) + len(picked) + len(leftover)} products in the input")
    picked += rng.sample(leftover, shortfall)  # only when a file was too small

    chosen = sorted(edge + picked, key=lambda item: (item[0], item[1]))
    return [to_sample_row(product, file_name, reasons, max_chars)
            for file_name, _, product, reasons in chosen]


def main(argv=None):
    config = parse_args(argv, description="Draw a reproducible sample of crawled products.")
    batches = load_batches(config.input_dir)
    total = sum(len(products) for _, products in batches)
    rows = draw_sample(batches, config.sample_size, config.seed, config.max_input_chars)

    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)) or len(ids) != config.sample_size:
        raise SystemExit("Sample check failed: duplicate IDs or wrong size")  # never expected

    write_jsonl(config.sample_path, rows)
    edge_count = sum(1 for row in rows if row["edge_cases"])
    truncated = sum(1 for row in rows
                    if row["description_original_length"] > config.max_input_chars)
    write_json(config.manifest_path, {
        "seed": config.seed,
        "sample_size": len(rows),
        "edge_cases": edge_count,
        "truncated_descriptions": truncated,
        "max_input_chars": config.max_input_chars,
        "input_dir": config.input_dir,
        "input_files": len(batches),
        "input_products": total,
        "source_files": [file_name for file_name, _ in batches],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    print(f"Sampled {len(rows)} of {total} products from {len(batches)} files "
          f"(seed {config.seed}): {edge_count} edge cases, "
          f"{truncated} descriptions cut to {config.max_input_chars} characters.")
    print(f"Wrote {config.sample_path} and {config.manifest_path}")


if __name__ == "__main__":
    sys.exit(main())
