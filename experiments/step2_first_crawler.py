"""
Tiki product crawler

Downloads product details for 200k product IDs from the Tiki API and saves
them into JSON files of 1,000 products each. Failed IDs are saved together
with the reason for the failure. A summary.json report is written at the end.

Requests are sent through the system `curl` command: Tiki's bot protection
started serving an HTML block page to Python HTTP clients, while plain curl
keeps receiving the JSON API response.
"""
import csv
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

LIMIT = 50  # test mode: only crawl the first 50 IDs. Set to None to crawl all.

INPUT_FILE = "products-0-200000.csv"
OUTPUT_DIR = "output_test" if LIMIT else "output"
API_URL = "https://api.tiki.vn/product-detail/api/v1/products/{}"
BATCH_SIZE = 1000
MAX_WORKERS = 4
REQUEST_DELAY = 0.25  # seconds each worker waits before every request
MAX_RETRIES = 8
COOLDOWN = 60  # seconds all workers pause when Tiki starts blocking

_block_lock = threading.Lock()
_blocked_until = 0.0


def wait_if_blocked():
    """Sleep while the shared cooldown is active."""
    while True:
        remaining = _blocked_until - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 5))


def report_block():
    """Start a shared cooldown so every worker stops sending requests."""
    global _blocked_until
    with _block_lock:
        if time.time() >= _blocked_until:
            _blocked_until = time.time() + COOLDOWN
            print(f"  Blocked by Tiki, pausing all workers for {COOLDOWN}s...", flush=True)


def http_get(url):
    """Download a URL with the system curl command. Returns (status_code, body)."""
    result = subprocess.run(
        ["/opt/anaconda3/bin/curl", "-s", "--max-time", "15", "-A", "Mozilla/5.0",
         "-w", "\n%{http_code}", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl exited with code {result.returncode}")
    body, _, status = result.stdout.rpartition("\n")
    return int(status), body


def clean_description(raw):
    """Remove HTML tags and entities, collapse whitespace."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract(data):
    """Keep only the fields required by the project."""
    images = data.get("images") or []
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "url_key": data.get("url_key"),
        "price": data.get("price"),
        "description": clean_description(data.get("description")),
        "images": [img.get("base_url") or img.get("large_url") for img in images
                   if img.get("base_url") or img.get("large_url")],
    }


def fetch(pid):
    """Return (product, None) on success or (None, error) on failure."""
    last_error = "unknown error"
    for attempt in range(1, MAX_RETRIES + 1):
        wait_if_blocked()
        time.sleep(REQUEST_DELAY)
        try:
            status, body = http_get(API_URL.format(pid))
        except Exception as e:
            last_error = f"request failed: {e}"
            time.sleep(2 ** min(attempt, 5))
            continue

        if status == 404:
            return None, {"id": pid, "reason": "HTTP 404 - product not found"}
        if status == 200 and not body.lstrip().startswith("<"):
            try:
                return extract(json.loads(body)), None
            except ValueError:
                last_error = f"invalid JSON response: {body[:100]!r}"
        elif status == 200:
            last_error = "blocked: received an HTML page instead of JSON"
            report_block()
        elif status in (403, 429):
            last_error = f"HTTP {status} - rate limited"
            report_block()
        else:
            last_error = f"HTTP {status}"
            time.sleep(2 ** min(attempt, 5))
    return None, {"id": pid, "reason": last_error}


def load_ids():
    with open(INPUT_FILE, encoding="utf-8-sig") as f:
        return [row["id"].strip() for row in csv.DictReader(f) if row["id"].strip()]


def save_json(path, data):
    """Write to a temp file first so a crash never leaves a half-written file."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def verify(ids):
    """Check that every input ID appears exactly once in either products or errors."""
    saved, failed = [], []
    reasons = Counter()
    for name in sorted(os.listdir(OUTPUT_DIR)):
        path = os.path.join(OUTPUT_DIR, name)
        if not name.endswith(".json"):
            continue
        if name.startswith("products_"):
            with open(path, encoding="utf-8") as f:
                saved += [str(p["id"]) for p in json.load(f)]
        elif name.startswith("errors_"):
            with open(path, encoding="utf-8") as f:
                for e in json.load(f):
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
    save_json(os.path.join(OUTPUT_DIR, "summary.json"), summary)
    print("Summary:")
    print(json.dumps(summary, indent=2))


def main():
    if LIMIT:
        shutil.rmtree(OUTPUT_DIR, ignore_errors=True)  # fresh start for every test run
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    ids = load_ids()
    if LIMIT:
        ids = ids[:LIMIT]
    batches = [ids[i:i + BATCH_SIZE] for i in range(0, len(ids), BATCH_SIZE)]
    print(f"Total IDs: {len(ids)} | Batches: {len(batches)} | Output: {OUTPUT_DIR}/")

    start = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for n, batch in enumerate(batches, start=1):
            products_path = os.path.join(OUTPUT_DIR, f"products_{n:04d}.json")
            errors_path = os.path.join(OUTPUT_DIR, f"errors_{n:04d}.json")
            if os.path.exists(products_path):
                print(f"Batch {n}/{len(batches)} already done, skipping")
                continue

            products, errors = [], []
            for product, error in pool.map(fetch, batch):
                if product:
                    products.append(product)
                else:
                    errors.append(error)

            save_json(products_path, products)
            save_json(errors_path, errors)
            elapsed = time.time() - start
            print(f"Batch {n}/{len(batches)}: {len(products)} ok, {len(errors)} errors "
                  f"| {elapsed / 60:.1f} min elapsed", flush=True)

    print(f"Done in {(time.time() - start) / 60:.1f} min")
    verify(ids)


if __name__ == "__main__":
    main()