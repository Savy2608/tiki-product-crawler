# Tiki LLM Enrichment & Anomaly Flagging – Build Spec

Nov 18, 2026 · Vy Ngo

## How to use this spec

Build one milestone at a time and review each before moving on.

- Everything in the repo (code, comments, console output, file names, README) stays in English.
- Do not invent results. Every number in the README must come from a real run.
- Keep the existing crawler untouched. The new work lives in a new `enrichment/` package.

## Goal

Use an LLM to enrich and risk-flag Tiki product listings already crawled, and measure how well it works. The project applies GenAI to data generation, enrichment and processing.

The project should show three things a risk and controls team values:

1. **Enrichment:** turn messy Vietnamese name and description text into structured, schema-validated fields.
2. **Risk flagging:** detect suspicious listings (abuse, brand risk, data-quality failures) by combining deterministic rules with LLM judgment.
3. **Controls on the LLM itself:** an evaluation against hand labels, output validation, reproducibility and cost tracking. The LLM is treated as a component to be audited, not trusted.

## Scope

Ship the MVP first, then add the stretch items.

| Item | MVP | Stretch |
| --- | --- | --- |
| Sample size | 1,000 products | 5,000 products |
| Rule-based checks | Yes | Yes |
| LLM enrichment (category, brand, attributes) | Yes | Yes |
| LLM risk flags | Yes, 3 flag types | All 5 flag types |
| Hand-labeled eval set | 200 products | 300 products |
| Prompt comparison | One prompt, versioned | Two prompt versions compared on the eval set |
| Athena or SQL analysis of results | No | Load enriched output to S3 and query with Athena |

Out of scope: re-crawling Tiki, fine-tuning any model, building a UI.

## Input data and repo layout

Input is the crawler's existing output: `output/products_XXXX.json`, 200 files, 124,299 products, 314 MB, not in Git. Each product has exactly six fields: `id`, `name`, `url_key`, `price` (VND), `description` (HTML already removed), `images` (list). Text is mostly Vietnamese. Known quirks from the crawl: 22 products with `price: 0`, 9 with no images, 1 with an empty `url_key`.

New files to add:

```text
enrichment/
├── __init__.py
├── config.py          # settings from .env / CLI, same pattern as src/config.py
├── sample.py          # stratified sample -> data/sample_products.jsonl
├── rules.py           # deterministic checks, no LLM
├── llm_client.py      # provider wrapper: retries, timeouts, token + cost logging
├── prompts/
│   ├── enrich_v1.md   # versioned prompt files, never edited in place
│   └── flag_v1.md
├── schemas.py         # pydantic models for every LLM output
├── enrich.py          # runs enrichment + flagging, writes results, resumable
├── evaluate.py        # compares output to hand labels, writes metrics
└── report.py          # builds summary tables for the README
data/
├── sample_products.jsonl
├── labels/eval_labels.csv      # hand labels (committed)
└── results/                    # outputs (small, committed for the MVP sample)
tests/enrichment/               # unit tests, no network, LLM mocked
docs/llm_enrichment_spec.md     # this spec
```

Add `enrichment/README.md` and a new section in the main README (see README requirements).

## Pipeline steps

Five steps, each runnable on its own: `python -m enrichment.<step>`. Every step reads the previous step's file and writes its own, so any step can be rerun without redoing the others.

1. **Sample** (`sample.py`)
   - Draw a reproducible sample with a fixed seed (default 42), spread evenly across the 200 batch files.
   - Always include the known edge cases: all 22 `price: 0` products, the 9 with no images, the 1 with an empty `url_key`.
   - Truncate `description` to a configurable length (default 2,000 characters) to cap token cost. Record the original length.
   - Write `data/sample_products.jsonl` plus a small manifest (seed, size, source files, timestamp).
2. **Rule checks** (`rules.py`, no LLM)
   - `price_zero`: price is 0.
   - `no_images`: images list is empty.
   - `empty_url_key`: url_key is empty.
   - `short_description`: description under a configurable length (default 50 characters).
   - `duplicate_name`: exact same normalized name as another product in the sample.
   - After enrichment, `price_outlier`: price outside 1.5 × IQR for its LLM-assigned category (only for categories with at least 20 products).
   - Output: one row per product with a list of rule flags.
3. **LLM enrichment** (`enrich.py`, prompt `enrich_v1.md`)
   - Input: name and truncated description. Output: `category` from a fixed list, `brand` (or null), `key_attributes` (up to 5 name/value pairs), `language`, `confidence` (0–1).
   - The fixed category list (about 15–20 top-level Tiki-style categories plus `other`) lives in `config.py`, so outputs are comparable and measurable.
   - Instruct the model to return null instead of guessing when the text does not say.
4. **LLM risk flagging** (`enrich.py`, prompt `flag_v1.md`)
   - MVP flag types: `name_description_mismatch`, `counterfeit_or_brand_risk` (for example replica, fake, or knock-off wording), `spam_or_low_quality`.
   - Stretch flag types: `price_implausible` (the LLM judges price versus product, VND), `restricted_item` (medicines, weapons, and other categories a marketplace would review).
   - Each flag returns `flag`, `severity` (low, medium, high), and a one-sentence `evidence` quoting the text that triggered it.
5. **Validate and combine**
   - Parse every LLM response into its pydantic schema. A response that fails validation is retried once with the error message, then saved to a `rejects` file with the reason. Never silently dropped.
   - Merge rule flags and LLM flags into one record per product, keeping the source of each flag (`rule` or `llm`).
   - Completeness check, same as the crawler: every sampled ID appears exactly once in results or rejects. Fail the run loudly if not.

Model and call settings: provider-agnostic wrapper; default to a small, low-cost model (for example a Claude Haiku-class model through the Anthropic API), set by name in `.env`. Temperature 0, JSON output mode where the provider supports it, request timeout, and retries with backoff. Store the model name and prompt version on every output row.

## Output schemas

One JSON line per product in `data/results/enriched_v1.jsonl`. Example (values illustrative):

```json
{
  "id": 203261704,
  "model": "<model name from .env>",
  "prompt_versions": {"enrich": "enrich_v1", "flag": "flag_v1"},
  "enrichment": {
    "category": "books",
    "brand": null,
    "key_attributes": [{"name": "author", "value": "Mo Willems"}],
    "language": "en",
    "confidence": 0.92
  },
  "flags": [
    {"flag": "price_outlier", "source": "rule", "severity": "low",
     "evidence": "399,999 VND vs category IQR upper bound 310,000 VND"}
  ],
  "usage": {"input_tokens": 512, "output_tokens": 96, "cost_usd": 0.00009,
            "latency_ms": 840, "cached": false}
}
```

Other outputs:

| File | Content |
| --- | --- |
| `data/results/rejects_v1.jsonl` | IDs whose LLM output failed validation twice, with the raw response and the error |
| `data/results/run_summary_v1.json` | Counts, completeness check result, total tokens, total cost, run time |
| `data/results/eval_metrics_v1.json` | Metrics from the evaluation step |
| `data/llm_cache/` | Cache keyed by hash of (model, prompt version, input text). Not committed |

## Evaluation

The evaluation is the part an audit team cares about most: it proves the LLM output can be trusted, and where it cannot.

**Hand labels must be made by a human, not by Claude Code or any LLM.** Otherwise the evaluation measures the model against itself. Claude Code only builds the template and the scoring.

1. `python -m enrichment.evaluate --make-template` draws 200 products from the sample (seed fixed, edge cases included) and writes `data/labels/eval_labels.csv` with empty columns: `true_category`, `true_brand`, one 0/1 column per flag type, plus a `notes` column.
2. Vy fills the CSV by hand. Target time is about 2–3 hours for 200 rows.
3. `python -m enrichment.evaluate` scores the results against the labels and writes `eval_metrics_v1.json`.

Metrics to report:

| Metric | Applies to |
| --- | --- |
| Accuracy and macro F1 | Category |
| Exact-match rate, and rate of null when a brand exists | Brand |
| Precision, recall, count of true positives | Each flag type |
| Share of rows where model confidence is high but the answer is wrong | Category (calibration check) |
| Reject rate (validation failures) | All LLM calls |
| Cost per 1,000 products (USD), mean latency | All LLM calls |

Also write `docs/error_analysis.md`: 10–20 real wrong answers, grouped by cause (Vietnamese slang, missing text, ambiguous category, and so on), each with the fix tried or proposed. This mirrors the existing `docs/bug_report.md` style.

## Engineering requirements

Match the quality bar the crawler already sets.

- **Configuration:** defaults in `enrichment/config.py`, overridable by `.env` and CLI flags (CLI wins over env, env wins over default). Add the new keys to `.env.example`: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_MAX_INPUT_CHARS`, `LLM_MAX_COST_USD`, `ENRICH_SAMPLE_SIZE`, `ENRICH_SEED`.
- **Secrets:** API key only from `.env` or the environment. Never printed, logged, or committed.
- **Cost guard:** estimate cost before the run and print it. Stop the run when spend reaches `LLM_MAX_COST_USD` (default 2 USD) and save progress.
- **Cache:** identical (model, prompt version, input) never calls the API twice. Rerunning with the cache costs 0 USD.
- **Resumable:** Ctrl+C then rerun continues from the next unfinished product, same pattern as the crawler.
- **Prompts are versioned files.** A change means a new file (`enrich_v2.md`), never an edit of v1, so results stay reproducible.
- **Tests** in `tests/enrichment/`, no network, the LLM client mocked: schema validation (good, malformed, wrong enum), each rule check, cache hit and miss, cost guard stop, completeness check, metric math on a tiny hand-made label set.
- **CI:** add the new tests to the existing GitHub Actions workflow (Python 3.9 and 3.12).
- **Dependencies:** add only what is needed (the anthropic Python SDK, `pydantic`) to `requirements.txt`.

## README requirements

Add a section "LLM Enrichment & Risk Flagging" to the main README and a fuller `enrichment/README.md`. Same plain, factual style as the existing README.

- **One-paragraph summary** with the headline numbers: products enriched, category accuracy, flags found, cost per 1,000 products.
- **How to run** each step, including a cheap test run (`--limit 20`).
- **Results tables** filled from `run_summary_v1.json` and `eval_metrics_v1.json`: metrics per field and per flag type; flags found by type and source (rule vs LLM); 3–5 real flagged examples with their evidence line.
- **Design decisions:** why a fixed category list, why rules plus LLM, why temperature 0, why versioned prompts, how cost is capped.
- **Known limitations, stated plainly:** sample size, single labeler, Vietnamese text, model drift, snapshot data from Oct–Nov 2026.
- Link to `docs/error_analysis.md` and to this spec.

## Milestones and acceptance checklist

MVP target is about 3–5 days, including 2–3 hours of hand labeling. Review and commit after each milestone.

**M1: Sample and rules**

- [ ] `sample.py` writes 1,000 products with all known edge cases included
- [ ] `rules.py` flags run and tests pass

**M2: LLM enrichment and flagging**

- [ ] Test run on 20 products works end to end, cost printed
- [ ] Full 1,000-product run finishes under the cost cap
- [ ] Completeness check passes: every ID in results or rejects exactly once
- [ ] Rerun with cache costs 0 USD

**M3: Evaluation**

- [ ] Template for 200 products generated
- [ ] 200 rows labeled by hand
- [ ] Metrics file written, `docs/error_analysis.md` written from real errors

**M4: Publish**

- [ ] README section with real numbers, CI green
- [ ] Pushed to GitHub, repo description and topics updated (add `llm`, `genai`, `data-quality`)

**Stretch**

- [ ] 5,000-product run, all 5 flag types
- [ ] Compare `enrich_v1` vs `enrich_v2` on the eval set
- [ ] Load results to S3 and query with Athena
