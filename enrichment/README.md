# LLM Enrichment & Risk Flagging

This package uses an LLM to turn 1,000 crawled Tiki listings into structured fields and to flag listings a marketplace review team would check. It also measures how far the LLM output can be trusted. The crawler in `src/` is not changed; this package only reads its output. Full plan: [docs/llm_enrichment_spec.md](../docs/llm_enrichment_spec.md).

**Summary (v1):** 1,000 products were enriched with `claude-haiku-5-5` at **$0.50 per 1,000 products**. Every sampled ID ended up in the results (0 validation rejects). 203 products got at least one flag: 81 flags from the LLM and 129 from deterministic rules. Against 200 evaluation labels (made by Gemini, with 50 random rows checked by a person), category agreement is **73%** and brand agreement is **92.5%**. Most category disagreements come from one gap in the v1 prompt, described in the [error analysis](../docs/error_analysis.md).

## How to run

Python 3.10+ is needed for the `anthropic` SDK (the unit tests run on 3.9 without it). Put `LLM_API_KEY` in `.env` (see [.env.example](../.env.example)).

```bash
python -m enrichment.sample                  # 1. 1,000-product sample -> data/sample_products.jsonl
python -m enrichment.rules                   # 2. rule checks          -> data/results/rule_flags.jsonl
python -m enrichment.enrich --limit 20       # 3. cheap test run       -> data/results/test/
python -m enrichment.enrich                  #    full run             -> data/results/enriched_v1.jsonl
python -m enrichment.evaluate --make-template   # 4. empty labels CSV for 200 products
python -m enrichment.evaluate --labels data/labels/eval_labels_gemini.csv \
    --label-source "gemini labels; 50 random rows checked by a human, 0 corrected"
```

Each step reads the previous step's file, so any step can be run again on its own. `python -m enrichment.enrich --help` lists the options (`--max-cost`, `--workers`, `--restart`, …). Unit tests: `python -m unittest discover -s tests`.

## Pipeline

| Step | What it does | Output |
| --- | --- | --- |
| Sample | All 32 known edge cases (22 price 0, 9 no images, 1 empty `url_key`) plus 4–5 random products from each of the 200 batch files, seed 42. Descriptions cut to 2,000 characters (215 were cut). | `data/sample_products.jsonl` |
| Rules | `price_zero`, `no_images`, `empty_url_key`, `short_description`, `duplicate_name`; `price_outlier` (1.5 × IQR per LLM category, categories with ≥ 20 products) after enrichment | `data/results/rule_flags.jsonl` |
| Enrich | `enrich_v1`: category from a fixed list of 19, brand or null, up to 5 attributes, language, confidence | in `enriched_v1.jsonl` |
| Flag | `flag_v1`: `name_description_mismatch`, `counterfeit_or_brand_risk`, `spam_or_low_quality`, each with severity and a quoted evidence sentence | in `enriched_v1.jsonl` |
| Validate | Every answer is checked against a pydantic schema; an invalid answer is retried once with the error, then saved to `rejects_v1.jsonl`. Completeness check: every sampled ID appears exactly once in results or rejects. | `run_summary_v1.json` |
| Evaluate | Scores the output against labels: accuracy and macro F1, brand match, precision and recall per flag, calibration | `eval_metrics_v1.json`, `eval_errors_v1.jsonl` |

## Results

### Run (from `data/results/run_summary_v1.json`)

| Metric | Value |
| --- | --- |
| Products enriched / rejected | 1,000 / 0 |
| Completeness check | passed (0 missing, 0 duplicates) |
| API calls | 2,001 (one call was retried; no answer was rejected) |
| Tokens in / out | 3,716,983 / 259,973 |
| Cost | $0.50 for 1,000 products (limit $2.00) |
| Mean latency | 2.6 s per product (two calls), 4 workers, about 11 minutes in total |
| Rerun with the cache | 0 API calls, $0.00 |

### Flags found

| Source | Flag | Products |
| --- | --- | --- |
| LLM | `spam_or_low_quality` | 58 |
| LLM | `counterfeit_or_brand_risk` | 13 |
| LLM | `name_description_mismatch` | 10 |
| Rule | `price_outlier` | 97 |
| Rule | `price_zero` / `no_images` / `empty_url_key` | 22 / 9 / 1 |
| Rule | `short_description` / `duplicate_name` | 0 / 0 |

Real examples (evidence written by the model; English glosses added in brackets):

- `name_description_mismatch`, high: *Vương Miện Nam* [men's crown]: "the description says 'Vương Miện Cô Dâu…' [bridal crown] and 'cho phái đẹp' [for women]".
- `counterfeit_or_brand_risk`, low: *Cân sức khỏe Iscale Iphone* [health scale]: the name uses Apple's "Iphone" for a scale Apple does not make.
- `spam_or_low_quality`, medium: *Túi tote BlackPink*: "the description is mostly repeated keyword phrases such as 'túi tote bts, túi tote bt21…'".
- `price_outlier` (rule): a children's electric car at 2,250,000 VND, above the `toys_games` upper bound of 666,500 VND.

### Evaluation (from `data/results/eval_metrics_v1.json`, 200 rows)

The labels were made by Gemini following [docs/labeling_guide.md](../docs/labeling_guide.md); 50 random rows were checked by a person and none were corrected ([label_review.json](../data/labels/label_review.json)). These numbers are therefore **agreement with a second model**, not accuracy against independent human labels.

| Field | Metric | Value |
| --- | --- | --- |
| Category | agreement / macro F1 | 73.0% / 0.52 |
| Brand | exact match / null when a brand exists | 92.5% / 7.8% |
| Calibration | rows with confidence ≥ 0.9 that disagree | 9.6% (12 of 125) |
| `spam_or_low_quality` | precision / recall | 0.47 / 1.00 |
| `counterfeit_or_brand_risk` | precision / recall | 0.33 / 1.00 |
| `name_description_mismatch` | precision / recall | 0.50 / 0.38 |

The eval sample has three strata: all 32 edge cases, 50 of the 79 LLM-flagged products, and 118 of the other 889. The metrics file also has population estimates weighted by stratum.

Main findings (details in the [error analysis](../docs/error_analysis.md)):

1. 31 of the 54 category disagreements are accessories (watches, belts, wallets, jewelry) filed under clothing. The v1 prompt lists the category names without the definitions the labels follow; fixing that alone would raise agreement to about 88%.
2. Both flag prompts find everything the labels flag (recall 1.0) but over-flag: any "inbox" or hashtag list counts as spam, and any brand name without proof of authorization counts as risk.
3. The raw-price outlier rule flags 10% of products; the same rule on log(price) flags 32 instead of 97.

## Design decisions

- **Fixed category list.** Free-text categories cannot be counted or compared; a fixed list of 19 (plus `other`) makes accuracy measurable and keeps v1 and v2 comparable.
- **Rules plus LLM.** Facts that a rule can check (price 0, no images) are never sent to the LLM: rules are free, exact and reproducible. The LLM is used only for judgments that need reading the text.
- **Structured outputs plus a second check.** The API is given a JSON schema, and every answer is validated again with pydantic, including limits the schema does not carry (confidence range, at most 5 attributes). Invalid answers are retried once and then rejected, never dropped.
- **Reproducibility without temperature 0.** The spec asked for temperature 0, but this model does not accept sampling parameters. Instead the model ID is fixed, each prompt is a versioned file with a hash, and every answer is cached by (model, prompt version, prompt hash, input). A rerun reads the cache and gives identical output.
- **Versioned prompts.** `enrich_v1.md` and `flag_v1.md` are never edited; a change means a new file, so old results stay reproducible.
- **Cost control.** The cost is estimated before the run, spend is tracked per call, and the run stops at `LLM_MAX_COST_USD` (default $2) with progress saved. The cache makes reruns free, and each row keeps the cost it had when it was produced.
- **Resumable.** Each finished product is appended to `partial_v1.jsonl` immediately, the same pattern as the crawler; Ctrl+C or the cost limit loses nothing.

## Known limitations

- **Labels from a second model.** The 200 labels come from Gemini, and the human check of 50 rows was done while seeing Gemini's answers. Agreement is not accuracy.
- **Small numbers of positive flags** (6–38 per type in the eval set), so precision and recall have wide uncertainty.
- **Sample size.** 1,000 of 124,299 products; 200 evaluated.
- **Vietnamese text.** Prompts are in English and the listings mostly in Vietnamese; slang and seller jargon ("hàng QC", "rep 1:1") are a known source of errors.
- **Model drift.** Results are tied to `claude-haiku-5-5` and prompt v1; another model or version needs a new run and a new evaluation.
- **Snapshot data.** Listings crawled from Tiki between Oct 22 and Nov 5, 2026.

## Files

```text
enrichment/
├── config.py        # settings (.env / CLI), fixed category list, prices
├── sample.py        # step 1
├── rules.py         # step 2 and price_outlier
├── llm_client.py    # provider wrapper: cache, validation retry, cost tracking
├── schemas.py       # JSON schemas + pydantic models
├── enrich.py        # steps 3-5
├── evaluate.py      # eval template and scoring
├── io.py            # JSON Lines helpers
└── prompts/         # enrich_v1.md, flag_v1.md
data/
├── sample_products.jsonl, sample_manifest.json
├── labels/          # eval template, Gemini labels, review record
└── results/         # rule flags, enriched output, rejects, run summary, metrics, errors
```
