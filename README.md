# Tiki Product Crawler

[![Tests](https://github.com/Savy2608/tiki-product-crawler/actions/workflows/tests.yml/badge.svg)](https://github.com/Savy2608/tiki-product-crawler/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)

This project downloads product details for 200,000 Tiki product IDs, keeps 6 fields per product, and saves them as JSON files of 1,000 products each. IDs that fail are saved in separate files together with the reason.

> **Status:** complete. All 200,000 IDs were processed: 124,299 products saved, 75,701 IDs no longer exist on Tiki (HTTP 404). See [Results](#results).

## Contents

- [Requirements](#requirements)
- [How to run](#how-to-run)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Output](#output)
- [Results](#results)
- [Known limitations](#known-limitations)
- [Project structure](#project-structure)
- [Documentation](#documentation)

## Requirements

| Requirement | Status | Where |
| --- | --- | --- |
| Read 200,000 IDs from `products-0-200000.csv` (file has a BOM) | Done | `storage.load_ids()` |
| Call `https://api.tiki.vn/product-detail/api/v1/products/{id}` | Done | `api_client.TikiClient` |
| Keep `id`, `name`, `url_key`, `price`, `description`, `images` | Done | `parser.extract()` |
| Remove HTML from `description` | Done (incl. escaped HTML, [Bug 9](docs/bug_report.md#bug-9--escaped-html-left-in-descriptions)) | `parser.clean_description()` |
| One JSON file per 1,000 products | Done | `storage.BatchFiles` → `products_XXXX.json` |
| Save failed IDs with the reason | Done | `errors_XXXX.json` |
| Crawl all 200,000 IDs | Done (200 / 200 batches, 0 missing, 0 duplicate) | [Results](#results) |

## How to run

Requirements: Python 3.9+ and a libcurl that Tiki accepts. The recommended setup is the Python that comes with **Anaconda**, which already includes `pycurl` (see [`requirements.txt`](requirements.txt)). Tiki blocked `requests`, `curl_cffi` and the macOS system curl ([Bug 4](docs/bug_report.md#bug-4--two-curl-builds-behave-differently)).

```bash
git clone https://github.com/Savy2608/tiki-product-crawler.git
cd tiki-product-crawler
python main.py
```

On macOS, run `caffeinate -i python main.py` instead, so the Mac does not go to sleep during the long crawl.

```bash
python main.py --limit 50                 # test run on the first 50 IDs (output goes to output_test/)
python main.py --transport curl           # use the curl binary instead of pycurl
python main.py --retry-errors             # after the crawl: request again the IDs that failed with a non-404 error
python main.py --reclean                  # after the crawl: re-clean saved descriptions (Bug 9)
python main.py --help                     # all options
```

- Press **Ctrl+C** to stop. Running the script again continues from the first unfinished ID.
- Run only one copy at a time. Settings are read at startup, so restart the script after changing one.

Run the unit tests (they never contact Tiki):

```bash
python -m unittest discover -s tests -v
```

The 42 tests also run on GitHub Actions (Python 3.9 and 3.12) for every push.

## Configuration

Nothing is hard-coded. Every setting has a default that can be changed with an environment variable, a line in a `.env` file (copy [`.env.example`](.env.example)), or a command-line option. The command line wins over the environment, and the environment wins over the default.

| Option | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `--input` | `TIKI_INPUT_FILE` | `products-0-200000.csv` | CSV file with an `id` column |
| `--output` | `TIKI_OUTPUT_DIR` | `output` | Folder for the JSON files |
| `--batch-size` | `TIKI_BATCH_SIZE` | `1000` | IDs per output file |
| `--workers` | `TIKI_WORKERS` | `1` | Parallel requests |
| `--start-delay` / `--min-delay` / `--max-delay` | `TIKI_START_DELAY` … | `0.6` / `0.45` / `10` s | Pace between requests |
| `--block-pause` / `--max-block-pause` | `TIKI_BLOCK_PAUSE` … | `300` / `900` s | Pause after a block |
| `--retries` | `TIKI_MAX_NETWORK_RETRIES` | `5` | Attempts per ID on network errors |
| `--transport` | `TIKI_TRANSPORT` | `auto` | `pycurl`, `curl`, or `auto` (pycurl if installed) |
| `--curl-path` | `TIKI_CURL_PATH` | curl on `PATH` | curl binary for `--transport curl` |
| `--limit` | `TIKI_LIMIT` | none | Crawl only the first N IDs (test mode) |

If pycurl or curl cannot be found, the crawler stops at startup with a message that says what to install or which option to set.

## How it works

The code is split into small modules, each with one job:

| Step | What happens | Module |
| --- | --- | --- |
| 1. Input | Read the IDs and split them into batches of 1,000 | [`src/storage.py`](src/storage.py) |
| 2. Extract | Request each ID, pace requests, retry with backoff, pause when Tiki blocks | [`src/api_client.py`](src/api_client.py) |
| 3. Transform | Parse the JSON, keep the 6 fields, remove HTML from the description | [`src/parser.py`](src/parser.py) |
| 4. Load | Write `products_XXXX.json` and `errors_XXXX.json` safely, resume after a stop | [`src/storage.py`](src/storage.py) |
| 5. Verify | Check that every input ID appears exactly once and write `summary.json` | [`src/storage.py`](src/storage.py) |

[`main.py`](main.py) is the entry point that connects the modules, and [`src/config.py`](src/config.py) reads the settings.

**HTTP requests.** By default requests go through **pycurl** (libcurl inside Python). Each worker thread keeps one curl handle, so the TLS connection is reused between requests and no new process is started per request. The first version started a new `curl` process for every ID through `subprocess`; that is still available as a fallback with `--transport curl`.

**Handling Tiki's limits.** Tiki returns an HTML block page (HTTP 200) or HTTP 403/429 when requests come too fast. The crawler waits `delay` seconds before each request. After 50 successes in a row the delay gets 10% shorter; after a block it gets 50% longer and all workers pause 5 minutes (up to 15 minutes if blocks continue), then the same ID is retried. One worker is used, because a test with 2 workers was about 3 times slower due to block pauses ([Bug 6](docs/bug_report.md#bug-6--parallel-workers-slower-than-one-worker)).

**Errors.** HTTP 404 means the product no longer exists on Tiki; it is saved as `HTTP 404 - product not found`. Network errors are tried 5 times (waiting 5, 10, 15, 20 s), then saved with the reason. `--retry-errors` requests those IDs again later and merges the new results into the same batch files, in input order.

**Cleaning.** Descriptions are parsed with Python's `html.parser` twice: the first pass removes tags and decodes entities, the second removes tags that sellers wrote in escaped form (`&lt;span&gt;`, [Bug 9](docs/bug_report.md#bug-9--escaped-html-left-in-descriptions)). Plain text such as `<3` is kept.

**Stopping and resuming.** Each finished ID is appended to `output/partial_XXXX.jsonl` right away. Ctrl+C stops the worker threads with a shared `threading.Event`. On the next run, finished batches are skipped and the partial file is read back. Every JSON file is written to a `.tmp` file and then renamed, so a crash never leaves a half-written file.

## Output

Files are written to `output/`, which is not in Git because of its size (314 MB). Real examples are in [`sample/`](sample/).

| File | Content |
| --- | --- |
| `products_XXXX.json` | Products of batch `XXXX` in input order (batch `0001` = CSV rows 1–1,000) |
| `errors_XXXX.json` | Failed IDs of batch `XXXX` with the reason |
| `summary.json` | Written at the end: saved, failed, duplicate and missing IDs, errors by reason |

Example product from batch 0006 (description and image list shortened):

```json
{
  "id": 203261704,
  "name": "ELEPHANT AND PIGGIE - MO WILLEMS + audio",
  "url_key": "elephant-and-piggie-mo-willems-audio-p203261704",
  "price": 399999,
  "description": "ELEPHANT AND PIGGIE - MO WILLEMS + audio ...",
  "images": [
    "https://salt.tikicdn.com/ts/product/a8/3f/79/5188775287d7a69ef971ef2a852cec60.jpg",
    "https://salt.tikicdn.com/ts/product/1c/18/00/e3a1ead05ea4a237fb4219f202e12355.jpg"
  ]
}
```

Product names and descriptions are stored exactly as Tiki returns them, so most of them are in Vietnamese.

Example failed ID:

```json
{ "id": "139457787", "reason": "HTTP 404 - product not found" }
```

`id` is a number in product files (from the API) and a string in error files (from the CSV). `price` is in VND.

## Results

Final numbers from `output/summary.json`:

| Metric | Value |
| --- | --- |
| Input IDs | 200,000 |
| Saved products | **124,299** (62.1%) |
| Failed – HTTP 404 (product no longer exists) | 75,701 (37.9%) |
| Failed – other reasons | 0 |
| Missing / duplicate IDs | 0 / 0 |
| Output size | 200 product files + 200 error files, 314 MB |
| Run time | Oct 22 – Nov 1, about 11 days including block pauses and times the laptop was asleep |

The crawl ended with 61 network errors (curl could not resolve the host when the Wi-Fi dropped). `python main.py --retry-errors` requested them again with the pycurl transport: 37 were saved and the other 24 now return HTTP 404.

Data checks on the final output:

- Every input ID appears exactly once, in a product file or an error file.
- Every product has exactly the 6 fields, and `images` is always a list.
- No description contains HTML tags or entities after `python main.py --reclean` (157 descriptions were fixed, [Bug 9](docs/bug_report.md#bug-9--escaped-html-left-in-descriptions)).
- Kept as returned by the API: 22 products with `price: 0`, 9 without images, 1 with an empty `url_key`.

## Known limitations

- **Speed.** The rate limit sets the pace: about 1 request per 0.5–0.6 s with one worker, so a full crawl takes days. Batches 1–15 took about 43 minutes each with the first version. A faster crawl would need more IP addresses, not more threads ([Bug 6](docs/bug_report.md#bug-6--parallel-workers-slower-than-one-worker)).
- **The full crawl ran on the first version.** It was already running when the code was refactored, and the output format did not change. The refactored code was tested against the live API with `--retry-errors` (pycurl was accepted by Tiki), but the speed gain from connection reuse was not measured on a full batch.
- **Snapshot.** Prices and availability change; the data reflects Tiki between Oct 22 and Nov 5, 2026.

## Project structure

```text
├── main.py                  # entry point: connects the modules
├── src/
│   ├── config.py            # settings: defaults, environment / .env, command line
│   ├── api_client.py        # HTTP transport, throttle, retries and backoff
│   ├── parser.py            # JSON fields and HTML cleaning
│   └── storage.py           # input CSV, batch files, resume, verify
├── tests/                   # 42 unit tests (no network)
├── products-0-200000.csv    # input: 200,000 product IDs
├── requirements.txt         # pycurl (already included in Anaconda)
├── .env.example             # example settings
├── docs/
│   ├── bug_report.md        # 9 problems found and how they were handled
│   └── task_report.md       # goal, steps, code review changes and results
├── sample/                  # example API response, products and errors
├── experiments/             # my early test scripts (see experiments/README.md)
├── .github/workflows/       # runs the tests on every push
├── .gitignore               # keeps output/, .env, .venv/ and IDE files out of Git
└── README.md
```

## Documentation

- [Bug report](docs/bug_report.md) – the 9 problems I found while building and running the crawler: symptom, cause, how I found it, fix and lesson.
- [Task report](docs/task_report.md) – goal, approach, steps, changes after code review, results and next steps.
- [Experiments](experiments/README.md) – my first test scripts and how the first crawler differs from the final one.
