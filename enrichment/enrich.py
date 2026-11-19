"""Steps 3-5: LLM enrichment and risk flagging, validation, merge with rule flags.

    python -m enrichment.enrich --limit 20     # cheap test run -> data/results/test/
    python -m enrichment.enrich                # full sample    -> data/results/

For each sampled product two LLM calls are made (enrich_v1, flag_v1). Every
finished product is appended to partial_v1.jsonl at once, so Ctrl+C or the cost
limit loses nothing and the next run continues. When every product is done the
rule flags and price_outlier are merged and the final files are written:
enriched_v1.jsonl, rejects_v1.jsonl and run_summary_v1.json.
"""
import json
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from enrichment.config import CATEGORIES, parse_args
from enrichment.io import read_jsonl, write_json, write_jsonl
from enrichment.llm_client import (CostLimitReached, CostTracker, FatalLLMError, LLMClient,
                                   TransientLLMError, load_prompt, make_provider)
from enrichment.rules import price_outlier_flags
from enrichment.schemas import (ENRICH_JSON_SCHEMA, FLAG_JSON_SCHEMA, Enrichment,
                                FlagResult)

VERSION = "v1"
EST_OUTPUT_TOKENS_PER_CALL = 400  # answer plus thinking, used only for the estimate


def log(message):
    print(message, flush=True)


def load_prompts():
    return {"enrich": load_prompt("enrich_v1", categories=", ".join(f'"{c}"' for c in CATEGORIES)),
            "flag": load_prompt("flag_v1")}


def user_text(product):
    return json.dumps({"name": product["name"], "description": product["description"]},
                      ensure_ascii=False)


def add_usage(total, usage):
    for key in ("input_tokens", "output_tokens", "cost_usd", "api_cost_usd", "latency_ms"):
        total[key] = total.get(key, 0) + usage.get(key, 0)
    total["cached"] = total.get("cached", True) and usage.get("cached", False)
    total["llm_calls"] = total.get("llm_calls", 0) + (0 if usage.get("cached")
                                                      else usage.get("attempts", 0))


def process(product, client, prompts, model):
    """One product -> a result row or a reject row (never None)."""
    text = user_text(product)
    usage = {}
    base = {"id": product["id"], "model": model,
            "prompt_versions": {step: p.version for step, p in prompts.items()}}

    enriched = client.call(prompts["enrich"], text, Enrichment, ENRICH_JSON_SCHEMA)
    add_usage(usage, enriched.usage)
    if enriched.data is None:
        return dict(base, kind="reject", step="enrich", error=enriched.error,
                    raw=enriched.raw, usage=usage)

    flagged = client.call(prompts["flag"], text, FlagResult, FLAG_JSON_SCHEMA)
    add_usage(usage, flagged.usage)
    if flagged.data is None:
        return dict(base, kind="reject", step="flag", error=flagged.error,
                    raw=flagged.raw, usage=usage)

    llm_flags = [dict(f, source="llm") for f in flagged.data["flags"]]
    return dict(base, kind="result", enrichment=enriched.data, llm_flags=llm_flags, usage=usage)


def load_progress(path):
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # last line may be cut off if the run was killed
                done[row["id"]] = row
    return done


def estimate_cost(products, prompts, client):
    """Rough USD estimate: about 3 characters per token for Vietnamese text."""
    prompt_chars = sum(len(p.text) for p in prompts.values())
    input_tokens = sum((prompt_chars + 2 * len(user_text(p))) / 3 for p in products)
    output_tokens = len(products) * 2 * EST_OUTPUT_TOKENS_PER_CALL
    return client.cost(input_tokens, output_tokens)


def run(config, products, client, prompts):
    """Process every unfinished product. Returns (done, stop_reason or None)."""
    os.makedirs(config.run_dir, exist_ok=True)
    partial = os.path.join(config.run_dir, f"partial_{VERSION}.jsonl")
    if config.restart and os.path.exists(partial):
        os.remove(partial)
    done = load_progress(partial)
    todo = [p for p in products if p["id"] not in done]
    log(f"Products: {len(products)} | already done: {len(done)} | to do: {len(todo)} "
        f"| workers: {config.workers}")
    log(f"Estimated cost for the products to do (before cache hits): "
        f"{estimate_cost(todo, prompts, client):.4f} USD | limit {config.max_cost_usd:.2f} USD "
        f"| already spent: {client.tracker.spent:.4f} USD")

    stop, start, finished = None, time.time(), 0
    pool = ThreadPoolExecutor(max_workers=config.workers)
    pending = {}
    queue = iter(todo)
    try:
        with open(partial, "a", encoding="utf-8") as out:
            def submit_next():
                product = next(queue, None)
                if product is not None:
                    pending[pool.submit(process, product, client, prompts, client.model)] = product

            for _ in range(config.workers * 2):
                submit_next()
            while pending:
                ready, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in ready:
                    product = pending.pop(future)
                    try:
                        row = future.result()
                    except TransientLLMError as exc:
                        log(f"  {product['id']}: {exc} (left for the next run)")
                    except CostLimitReached as exc:
                        stop = str(exc)
                    else:
                        done[row["id"]] = row
                        out.write(json.dumps(row, ensure_ascii=False) + "\n")
                        out.flush()
                        finished += 1
                        if finished % 50 == 0:
                            log(f"  [{time.strftime('%H:%M:%S')}] {len(done)}/{len(products)} done "
                                f"| spent {client.tracker.spent:.4f} USD")
                    if stop is None:
                        submit_next()
    except FatalLLMError as exc:
        stop = f"fatal LLM error: {exc}"
    except KeyboardInterrupt:
        stop = "stopped with Ctrl+C"
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    log(f"Processed {finished} products in {time.time() - start:.0f} s")
    return done, stop


def completeness(products, results, rejects):
    """Every sampled ID must appear exactly once in results or rejects."""
    expected = [p["id"] for p in products]
    seen = [r["id"] for r in results] + [r["id"] for r in rejects]
    counts = Counter(seen)
    missing = [pid for pid in expected if pid not in counts]
    duplicates = [pid for pid, n in counts.items() if n > 1]
    extra = [pid for pid in counts if pid not in set(expected)]
    return {"ok": not (missing or duplicates or extra), "expected": len(expected),
            "missing": len(missing), "duplicates": len(duplicates), "extra": len(extra)}


def finalize(config, products, done, rule_flags, prompts):
    """Merge rule flags and price_outlier, write the final files, return the summary."""
    order = {p["id"]: i for i, p in enumerate(products)}
    rows = sorted(done.values(), key=lambda r: order.get(r["id"], len(order)))
    results_in = [r for r in rows if r["kind"] == "result"]
    categories = {r["id"]: r["enrichment"]["category"] for r in results_in}
    outliers = price_outlier_flags(products, categories)

    results, rejects = [], []
    for row in rows:
        if row["kind"] == "reject":
            rejects.append({k: row[k] for k in ("id", "model", "prompt_versions", "step",
                                                "error", "raw", "usage")})
            continue
        flags = list(rule_flags.get(row["id"], [])) + row["llm_flags"]
        if row["id"] in outliers:
            flags.append(outliers[row["id"]])
        results.append({"id": row["id"], "model": row["model"],
                        "prompt_versions": row["prompt_versions"],
                        "enrichment": row["enrichment"], "flags": flags, "usage": row["usage"]})

    check = completeness(products, results, rejects)
    write_jsonl(os.path.join(config.run_dir, f"enriched_{VERSION}.jsonl"), results)
    write_jsonl(os.path.join(config.run_dir, f"rejects_{VERSION}.jsonl"), rejects)

    usages = [r["usage"] for r in rows]
    total_cost = sum(u.get("cost_usd", 0) for u in usages)
    flag_counts = Counter(f"{f['source']}:{f['flag']}" for r in results for f in r["flags"])
    summary = {
        "model": config.llm_model,
        "prompts": {step: {"version": p.version, "sha": p.sha} for step, p in prompts.items()},
        "products": len(products),
        "results": len(results),
        "rejects": len(rejects),
        "rejects_by_step": dict(Counter(r["step"] for r in rejects)),
        "completeness": check,
        "api_calls_this_run": sum(u.get("llm_calls", 0) for u in usages),
        "products_from_cache": sum(1 for u in usages if u.get("cached")),
        "api_cost_usd_this_run": round(sum(u.get("api_cost_usd", 0) for u in usages), 6),
        "input_tokens": sum(u.get("input_tokens", 0) for u in usages),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usages),
        "cost_usd": round(total_cost, 6),
        "cost_per_1000_products_usd": round(1000 * total_cost / len(usages), 4) if usages else None,
        "mean_latency_ms_per_product": round(statistics.mean(u.get("latency_ms", 0)
                                                             for u in usages)) if usages else None,
        "products_with_flags": sum(1 for r in results if r["flags"]),
        "flags_by_source_and_type": dict(sorted(flag_counts.items())),
        "categories": dict(Counter(categories.values()).most_common()),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_json(os.path.join(config.run_dir, f"run_summary_{VERSION}.json"), summary)
    return summary


def main(argv=None):
    config = parse_args(argv, description="Enrich and risk-flag the sampled products with an LLM.")
    products = read_jsonl(config.sample_path)
    if config.limit:
        products = products[:config.limit]
    if not os.path.exists(config.rule_flags_path):
        raise SystemExit(f"{config.rule_flags_path} not found: run python -m enrichment.rules first")
    rule_flags = {row["id"]: row["flags"] for row in read_jsonl(config.rule_flags_path)}

    prompts = load_prompts()
    partial = os.path.join(config.run_dir, f"partial_{VERSION}.jsonl")
    previous = 0.0 if config.restart else sum(
        r["usage"].get("api_cost_usd", 0) for r in load_progress(partial).values())
    tracker = CostTracker(config.max_cost_usd, already_spent=previous)
    client = LLMClient(make_provider(config), config.llm_model, config.prices(),
                       config.cache_dir, tracker)
    log(f"Model: {config.llm_model} | prompts: "
        + ", ".join(f"{p.version} ({p.sha})" for p in prompts.values())
        + f" | output: {config.run_dir}/")

    done, stop = run(config, products, client, prompts)
    if stop or len(done) < len(products):
        log(f"Run not complete: {len(done)}/{len(products)} products done"
            + (f" ({stop})" if stop else "") + ". Run the same command again to continue.")
        return 1

    summary = finalize(config, products, done, rule_flags, prompts)
    log(json.dumps({k: summary[k] for k in ("results", "rejects", "completeness",
                                             "api_calls_this_run", "products_from_cache",
                                             "api_cost_usd_this_run", "cost_usd",
                                             "cost_per_1000_products_usd",
                                             "products_with_flags",
                                             "flags_by_source_and_type")}, indent=2))
    if not summary["completeness"]["ok"]:
        log("COMPLETENESS CHECK FAILED: some sampled IDs are missing or duplicated.")
        return 1
    log(f"Completeness check passed. Wrote enriched_{VERSION}.jsonl, rejects_{VERSION}.jsonl "
        f"and run_summary_{VERSION}.json to {config.run_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
