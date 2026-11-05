"""Tiki product crawler (entry point).

Downloads product details for the IDs in the input CSV and saves them into
JSON files of 1,000 products each; failed IDs are saved with the reason, and
summary.json is written at the end. Run `python main.py --help` for options.

Press Ctrl+C to stop: pending requests are cancelled, and the next run resumes
from the first unfinished ID.
"""
import json
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from src import parser, storage
from src.api_client import Throttle, TikiClient, make_transport
from src.config import parse_args


def log(message):
    print(message, flush=True)


def split_batches(ids, size):
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def fetch_product(client, pid):
    """Fetch one ID and keep the 6 fields: (product, None) or (None, error)."""
    data, error = client.fetch(pid)
    return (parser.extract(data), None) if data is not None else (None, error)


def retry_errors(config, client, ids):
    """Request again the saved IDs that failed for a reason other than HTTP 404."""
    fixed = still_failed = 0
    for n, batch in enumerate(split_batches(ids, config.batch_size), start=1):
        files = storage.BatchFiles(config.output_dir, n)
        if not files.is_finished():
            continue
        retry = files.retryable_ids()
        if not retry:
            continue
        results = {pid: fetch_product(client, pid) for pid in retry}
        files.merge_retried(batch, results)
        ok = sum(1 for product, _ in results.values() if product)
        fixed += ok
        still_failed += len(retry) - ok
        log(f"Batch {n}: retried {len(retry)} IDs, {ok} saved, {len(retry) - ok} still failed")
    log(f"Retry done: {fixed} saved, {still_failed} still failed.")


def crawl(config, client, ids):
    """Crawl every unfinished batch. Returns True when all batches are finished."""
    batches = split_batches(ids, config.batch_size)
    throttle = client.throttle

    start = time.time()
    processed = 0
    pool = ThreadPoolExecutor(max_workers=config.workers)
    try:
        for n, batch in enumerate(batches, start=1):
            files = storage.BatchFiles(config.output_dir, n)
            if files.is_finished():
                continue  # finished in a previous run

            done = files.load_partial()
            todo = [pid for pid in batch if pid not in done]
            if done:
                log(f"  Batch {n}: resuming, {len(done)} IDs already done")

            with files.open_partial() as partial:
                results = pool.map(lambda pid: fetch_product(client, pid), todo)
                for pid, (product, error) in zip(todo, results):
                    done[pid] = (product, error)
                    files.append_partial(partial, pid, product, error)
                    processed += 1
                    if len(done) % 100 == 0:
                        rate = processed / (time.time() - start)
                        log(f"  [{time.strftime('%H:%M:%S')}] Batch {n}: {len(done)}/{len(batch)} "
                            f"| {rate:.2f} IDs/s | delay {throttle.delay:.2f}s")

            products, errors = files.save(batch, done)
            log(f"Batch {n}/{len(batches)} saved: {len(products)} ok, {len(errors)} errors "
                f"| {(time.time() - start) / 60:.1f} min elapsed")
    except KeyboardInterrupt:
        log("\nStopping: cancelling pending requests (finished IDs are kept)...")
        throttle.stop_event.set()
        pool.shutdown(wait=True, cancel_futures=True)
        log("Stopped. Run the script again to resume.")
        return False
    pool.shutdown()
    log(f"Done in {(time.time() - start) / 60:.1f} min")
    return True


def main(argv=None):
    config, mode = parse_args(argv)

    if mode == "reclean":
        files, changed = storage.reclean_descriptions(config.output_dir, parser.clean_description)
        log(f"Re-cleaned {config.output_dir}/: {changed} descriptions changed in {files} files.")
        return

    if config.limit and mode == "crawl":
        shutil.rmtree(config.output_dir, ignore_errors=True)  # fresh start for every test run
    os.makedirs(config.output_dir, exist_ok=True)

    ids = storage.load_ids(config.input_file)
    if config.limit:
        ids = ids[:config.limit]

    transport = make_transport(config)
    client = TikiClient(config, transport, Throttle(config, threading.Event(), log))
    log(f"Total IDs: {len(ids)} | Batches: {-(-len(ids) // config.batch_size)} "
        f"| Workers: {config.workers} | Transport: {transport.name} "
        f"| Delay: {config.start_delay}-{config.max_delay}s | Output: {config.output_dir}/")

    if mode == "retry":
        retry_errors(config, client, ids)
    elif not crawl(config, client, ids):
        return
    summary = storage.verify(config.output_dir, ids)
    log("Summary:")
    log(json.dumps(summary, indent=2))


if __name__ == "__main__":
    sys.exit(main())
