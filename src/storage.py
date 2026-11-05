"""Reading the input and writing the output safely.

Output layout (one batch = 1,000 input IDs):
  products_XXXX.json   saved products, written last: it marks the batch as finished
  errors_XXXX.json     failed IDs with the reason
  partial_XXXX.jsonl   one line per finished ID of the batch in progress
  summary.json         written by verify() when the whole crawl is done
"""
import csv
import json
import os
from collections import Counter

NOT_FOUND = "HTTP 404 - product not found"


def load_ids(path):
    """Read the product IDs from the input CSV (utf-8-sig removes the BOM)."""
    with open(path, encoding="utf-8-sig") as f:
        return [row["id"].strip() for row in csv.DictReader(f) if row["id"].strip()]


def save_json(path, data):
    """Write to a temp file first so a crash never leaves a half-written file."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class BatchFiles:
    """Paths and file operations for one batch."""

    def __init__(self, output_dir, number):
        self.number = number
        self.products = os.path.join(output_dir, f"products_{number:04d}.json")
        self.errors = os.path.join(output_dir, f"errors_{number:04d}.json")
        self.partial = os.path.join(output_dir, f"partial_{number:04d}.jsonl")

    def is_finished(self):
        return os.path.exists(self.products)

    def load_partial(self):
        """Results already saved for this unfinished batch: {pid: (product, error)}."""
        done = {}
        if os.path.exists(self.partial):
            with open(self.partial, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # last line may be cut off if the crawler was killed
                    done[row["pid"]] = (row["product"], row["error"])
        return done

    def open_partial(self):
        return open(self.partial, "a", encoding="utf-8")

    @staticmethod
    def append_partial(handle, pid, product, error):
        """Save one finished ID right away, so stopping mid-batch loses nothing."""
        handle.write(json.dumps({"pid": pid, "product": product, "error": error},
                                ensure_ascii=False) + "\n")
        handle.flush()

    def retryable_ids(self):
        """IDs of this finished batch that failed for a reason other than HTTP 404."""
        return [str(e["id"]) for e in load_json(self.errors) if e["reason"] != NOT_FOUND]

    def merge_retried(self, batch_ids, results):
        """Put the new results of retried IDs into the saved files, keeping input order.

        results: {pid: (product, error)}. Returns (products, errors) as saved."""
        products = load_json(self.products)
        errors = [e for e in load_json(self.errors) if str(e["id"]) not in results]
        for product, error in results.values():
            if product:
                products.append(product)
            else:
                errors.append(error)
        position = {pid: i for i, pid in enumerate(batch_ids)}
        products.sort(key=lambda p: position.get(str(p["id"]), len(position)))
        errors.sort(key=lambda e: position.get(str(e["id"]), len(position)))
        save_json(self.errors, errors)
        save_json(self.products, products)
        return products, errors

    def save(self, batch_ids, done):
        """Write errors, then products (the finished marker), then drop the partial file."""
        products = [done[pid][0] for pid in batch_ids if done[pid][0]]
        errors = [done[pid][1] for pid in batch_ids if not done[pid][0]]
        save_json(self.errors, errors)
        save_json(self.products, products)
        if os.path.exists(self.partial):
            os.remove(self.partial)
        return products, errors


def verify(output_dir, ids):
    """Check that every input ID appears exactly once in products or errors; write summary.json."""
    saved, failed = [], []
    reasons = Counter()
    for name in sorted(os.listdir(output_dir)):
        path = os.path.join(output_dir, name)
        if not name.endswith(".json"):
            continue
        if name.startswith("products_"):
            saved += [str(p["id"]) for p in load_json(path)]
        elif name.startswith("errors_"):
            for e in load_json(path):
                failed.append(str(e["id"]))
                reasons[e["reason"]] += 1

    seen = saved + failed
    summary = {
        "input_ids": len(ids),
        "saved_products": len(saved),
        "failed_ids": len(failed),
        "duplicate_ids": len(seen) - len(set(seen)),
        "missing_ids": len(set(ids) - set(seen)),
        "errors_by_reason": dict(reasons.most_common()),
    }
    save_json(os.path.join(output_dir, "summary.json"), summary)
    return summary


def reclean_descriptions(output_dir, clean):
    """Apply clean() again to every saved description (one-off fix for Bug 9).

    Returns (files_checked, descriptions_changed)."""
    files = changed = 0
    for name in sorted(os.listdir(output_dir)):
        if not (name.startswith("products_") and name.endswith(".json")):
            continue
        path = os.path.join(output_dir, name)
        products = load_json(path)
        files += 1
        file_changed = False
        for p in products:
            new = clean(p.get("description"))
            if new != (p.get("description") or ""):
                p["description"] = new
                changed += 1
                file_changed = True
        if file_changed:
            save_json(path, products)
    return files, changed
