"""Step 6: evaluate the LLM output against hand labels.

    python -m enrichment.evaluate --make-template   # writes data/labels/eval_labels.csv
    (a person fills the label columns by hand, see docs/labeling_guide.md)
    python -m enrichment.evaluate                   # writes data/results/eval_metrics_v1.json

The 200 products are drawn in three strata so that rare flags can be measured:
every edge case, a share of the products the LLM flagged, and a random share of
the rest. The stratum of each product is kept in a separate manifest, not in the
CSV, so the labeler cannot see what the model said. Population estimates weight
each labeled row by (stratum size / labeled rows in the stratum).
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import unicodedata
from collections import Counter

from enrichment.config import CATEGORIES, EnrichConfig
from enrichment.io import read_jsonl, write_json, write_jsonl
from enrichment.schemas import LLM_FLAGS

VERSION = "v1"
LABEL_COLUMNS = ["true_category", "true_brand"] + LLM_FLAGS + ["notes"]
CSV_COLUMNS = ["id", "name", "price_vnd", "tiki_url"] + LABEL_COLUMNS + ["description"]
NO_BRAND = "none"
HIGH_CONFIDENCE = 0.9


# ---------------------------------------------------------------- template

def draw_eval_sample(products, results, size, flagged_share, seed):
    """{id: stratum} for `size` products: all edge cases, then LLM-flagged, then the rest."""
    rng = random.Random(seed)
    llm_flagged = {r["id"] for r in results if any(f["source"] == "llm" for f in r["flags"])}
    strata = {"edge_case": [], "llm_flagged": [], "other": []}
    for p in products:
        if p["edge_cases"]:
            strata["edge_case"].append(p["id"])
        elif p["id"] in llm_flagged:
            strata["llm_flagged"].append(p["id"])
        else:
            strata["other"].append(p["id"])

    n_edge = len(strata["edge_case"])
    if size < n_edge:
        raise ValueError(f"eval size {size} is smaller than the {n_edge} edge cases")
    n_flagged = min(len(strata["llm_flagged"]), flagged_share)
    n_other = size - n_edge - n_flagged
    if n_other > len(strata["other"]):
        raise ValueError("not enough products for the eval sample")
    chosen = {pid: "edge_case" for pid in strata["edge_case"]}
    chosen.update({pid: "llm_flagged" for pid in rng.sample(strata["llm_flagged"], n_flagged)})
    chosen.update({pid: "other" for pid in rng.sample(strata["other"], n_other)})
    sizes = {name: len(ids) for name, ids in strata.items()}
    return chosen, sizes


def make_template(config, size, flagged_share, seed, force):
    labels_path = os.path.join(config.data_dir, "labels", "eval_labels.csv")
    manifest_path = os.path.join(config.data_dir, "labels", "eval_manifest.json")
    if os.path.exists(labels_path) and not force:
        raise SystemExit(f"{labels_path} already exists. It may hold hand labels, so it is not "
                         "overwritten; use --force to replace it.")
    products = read_jsonl(config.sample_path)
    results = read_jsonl(os.path.join(config.results_dir, f"enriched_{VERSION}.jsonl"))
    chosen, population = draw_eval_sample(products, results, size, flagged_share, seed)

    rows = [p for p in products if p["id"] in chosen]
    random.Random(seed).shuffle(rows)  # mix the strata so flagged rows are not grouped
    os.makedirs(os.path.dirname(labels_path), exist_ok=True)
    with open(labels_path, "w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel reads UTF-8
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for p in rows:
            writer.writerow({"id": p["id"], "name": p["name"], "price_vnd": p["price"],
                             "tiki_url": f"https://tiki.vn/{p['url_key']}.html" if p["url_key"] else "",
                             "description": p["description"]})
    labeled = Counter(chosen.values())
    write_json(manifest_path, {
        "seed": seed, "size": len(rows),
        "strata": {name: {"population": population[name], "labeled": labeled.get(name, 0)}
                   for name in population},
        "stratum_by_id": {str(pid): stratum for pid, stratum in chosen.items()},
    })
    print(f"Wrote {len(rows)} rows to {labels_path} and the strata to {manifest_path}")
    for name in population:
        print(f"  {name:<12} {labeled.get(name, 0):>4} labeled of {population[name]}")


# ---------------------------------------------------------------- labels

def normalize_brand(value):
    value = unicodedata.normalize("NFC", (value or "").strip())
    value = re.sub(r"\s+", " ", value).casefold()
    return None if value in ("", NO_BRAND, "null") else value


def read_labels(path):
    """Read and check the hand-labeled CSV. Returns (labels by id, list of problems)."""
    labels, problems = {}, []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            pid = int(row["id"])
            category = (row.get("true_category") or "").strip()
            brand = (row.get("true_brand") or "").strip()
            if category not in CATEGORIES:
                problems.append(f"line {line_no} (id {pid}): true_category {category!r} is not "
                                "one of the fixed categories")
            if not brand:
                problems.append(f"line {line_no} (id {pid}): true_brand is empty "
                                f"(write {NO_BRAND!r} when the listing names no brand)")
            flags = {}
            for name in LLM_FLAGS:
                value = (row.get(name) or "").strip()
                if value not in ("0", "1"):
                    problems.append(f"line {line_no} (id {pid}): {name} must be 0 or 1, "
                                    f"got {value!r}")
                flags[name] = value == "1"
            labels[pid] = {"category": category, "brand": normalize_brand(brand),
                           "brand_raw": brand, "flags": flags,
                           "notes": (row.get("notes") or "").strip()}
    return labels, problems


# ---------------------------------------------------------------- metrics

def safe_div(a, b):
    return round(a / b, 4) if b else None


def macro_f1(pairs):
    """Macro F1 over the classes that appear in the labels or the predictions."""
    classes = sorted({t for t, _ in pairs} | {p for _, p in pairs})
    scores = []
    for c in classes:
        tp = sum(1 for t, p in pairs if t == c and p == c)
        fp = sum(1 for t, p in pairs if t != c and p == c)
        fn = sum(1 for t, p in pairs if t == c and p != c)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(sum(scores) / len(scores), 4) if scores else None


def flag_metrics(rows, name):
    """Raw counts on the labeled rows plus weighted population estimates."""
    tp = fp = fn = tn = 0
    w_tp = w_fp = w_fn = 0.0
    for r in rows:
        truth, pred, w = r["label"]["flags"][name], name in r["pred_flags"], r["weight"]
        if truth and pred:
            tp += 1
            w_tp += w
        elif pred:
            fp += 1
            w_fp += w
        elif truth:
            fn += 1
            w_fn += w
        else:
            tn += 1
    return {"true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "true_negatives": tn,
            "precision": safe_div(tp, tp + fp), "recall": safe_div(tp, tp + fn),
            "estimated_precision": safe_div(w_tp, w_tp + w_fp),
            "estimated_recall": safe_div(w_tp, w_tp + w_fn)}


def score(labels, results, manifest, run_summary):
    by_id = {r["id"]: r for r in results}
    strata = manifest["strata"]
    weight = {name: s["population"] / s["labeled"] for name, s in strata.items() if s["labeled"]}

    rows, missing = [], []
    for pid, label in labels.items():
        result = by_id.get(pid)
        if result is None:
            missing.append(pid)  # rejected or not enriched: counted, never silently dropped
            continue
        stratum = manifest["stratum_by_id"][str(pid)]
        rows.append({"id": pid, "label": label, "stratum": stratum, "weight": weight[stratum],
                     "pred": result["enrichment"],
                     "pred_flags": {f["flag"] for f in result["flags"] if f["source"] == "llm"},
                     "flags": result["flags"]})

    pairs = [(r["label"]["category"], r["pred"]["category"]) for r in rows]
    correct = [r for r in rows if r["label"]["category"] == r["pred"]["category"]]
    total_weight = sum(r["weight"] for r in rows)
    confusions = Counter(f"{t} -> {p}" for t, p in pairs if t != p)

    brand_pairs = [(r["label"]["brand"], normalize_brand(r["pred"]["brand"])) for r in rows]
    has_brand = [(t, p) for t, p in brand_pairs if t is not None]
    predicted_brand = [(t, p) for t, p in brand_pairs if p is not None]

    confident = [r for r in rows if r["pred"]["confidence"] >= HIGH_CONFIDENCE]
    confident_wrong = [r for r in confident if r["label"]["category"] != r["pred"]["category"]]

    metrics = {
        "labeled_rows": len(labels),
        "scored_rows": len(rows),
        "not_scored_rejected_or_missing": len(missing),
        "strata": strata,
        "category": {
            "accuracy": safe_div(len(correct), len(rows)),
            "estimated_accuracy": safe_div(sum(r["weight"] for r in correct), total_weight),
            "macro_f1": macro_f1(pairs),
            "top_confusions": dict(confusions.most_common(10)),
        },
        "brand": {
            "exact_match_rate": safe_div(sum(1 for t, p in brand_pairs if t == p), len(rows)),
            "rows_with_true_brand": len(has_brand),
            "null_when_brand_exists_rate": safe_div(sum(1 for _, p in has_brand if p is None),
                                                    len(has_brand)),
            "rows_with_predicted_brand": len(predicted_brand),
            "wrong_or_invented_brand_rate": safe_div(
                sum(1 for t, p in predicted_brand if t != p), len(predicted_brand)),
        },
        "flags": {name: flag_metrics(rows, name) for name in LLM_FLAGS},
        "calibration": {
            "high_confidence_threshold": HIGH_CONFIDENCE,
            "high_confidence_rows": len(confident),
            "high_confidence_wrong_share": safe_div(len(confident_wrong), len(confident)),
        },
        "llm_run": {key: run_summary.get(key) for key in (
            "model", "prompts", "products", "rejects", "cost_per_1000_products_usd",
            "mean_latency_ms_per_product")},
    }
    metrics["llm_run"]["reject_rate"] = safe_div(run_summary.get("rejects", 0),
                                                 run_summary.get("products", 0))
    return metrics, rows


def error_rows(rows):
    """Every disagreement between the model and the labels, for docs/error_analysis.md."""
    errors = []
    for r in rows:
        label, pred = r["label"], r["pred"]
        kinds = []
        if label["category"] != pred["category"]:
            kinds.append(f"category: labeled {label['category']}, model {pred['category']} "
                         f"(confidence {pred['confidence']})")
        if label["brand"] != normalize_brand(pred["brand"]):
            kinds.append(f"brand: labeled {label['brand_raw']!r}, model {pred['brand']!r}")
        for name in LLM_FLAGS:
            truth, flagged = label["flags"][name], name in r["pred_flags"]
            if truth != flagged:
                kinds.append(f"{name}: {'missed' if truth else 'false positive'}")
        if kinds:
            errors.append({"id": r["id"], "stratum": r["stratum"], "errors": kinds,
                           "notes": label["notes"], "model_flags": r["flags"]})
    return errors


def run_scoring(config):
    labels_path = os.path.join(config.data_dir, "labels", "eval_labels.csv")
    manifest_path = os.path.join(config.data_dir, "labels", "eval_manifest.json")
    if not os.path.exists(labels_path):
        raise SystemExit(f"{labels_path} not found: run with --make-template first")
    labels, problems = read_labels(labels_path)
    if problems:
        print(f"The labels file is not complete: {len(problems)} problem(s). First ones:")
        for problem in problems[:15]:
            print("  " + problem)
        return 1

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    if set(labels) != {int(pid) for pid in manifest["stratum_by_id"]}:
        raise SystemExit("The IDs in the labels file do not match eval_manifest.json")
    results = read_jsonl(os.path.join(config.results_dir, f"enriched_{VERSION}.jsonl"))
    with open(os.path.join(config.results_dir, f"run_summary_{VERSION}.json"), encoding="utf-8") as f:
        run_summary = json.load(f)

    metrics, rows = score(labels, results, manifest, run_summary)
    write_json(os.path.join(config.results_dir, f"eval_metrics_{VERSION}.json"), metrics)
    errors = error_rows(rows)
    write_jsonl(os.path.join(config.results_dir, f"eval_errors_{VERSION}.jsonl"), errors)
    print(json.dumps({k: metrics[k] for k in ("scored_rows", "category", "brand", "calibration")},
                     indent=2, ensure_ascii=False))
    for name, m in metrics["flags"].items():
        print(f"{name:<28} precision {m['precision']} recall {m['recall']} "
              f"(TP {m['true_positives']}, FP {m['false_positives']}, FN {m['false_negatives']})")
    print(f"Wrote eval_metrics_{VERSION}.json and {len(errors)} disagreements to "
          f"eval_errors_{VERSION}.jsonl in {config.results_dir}/")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Evaluate the LLM output against hand labels.")
    p.add_argument("--data-dir", default="data", help="data folder (default: %(default)s)")
    p.add_argument("--make-template", action="store_true",
                   help="write the empty labels CSV for 200 products")
    p.add_argument("--size", type=int, default=200, help="products to label (default: %(default)s)")
    p.add_argument("--flagged", type=int, default=50,
                   help="LLM-flagged products in the eval sample (default: %(default)s)")
    p.add_argument("--seed", type=int, default=42, help="random seed (default: %(default)s)")
    p.add_argument("--force", action="store_true", help="overwrite an existing labels file")
    args = p.parse_args(argv)
    config = EnrichConfig(data_dir=args.data_dir)
    if args.make_template:
        make_template(config, args.size, args.flagged, args.seed, args.force)
        return 0
    return run_scoring(config)


if __name__ == "__main__":
    sys.exit(main())
