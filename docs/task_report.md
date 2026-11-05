# Task Report – Tiki Product Crawler

- **Author:** Vy Ngo
- **Repository:** https://github.com/Savy2608/tiki-product-crawler
- **Status:** Complete – all 200,000 IDs processed (Oct 22 – Nov 1, 2026; retry and re-clean on Nov 5)

---

## 1. Goal

Download product details for 200,000 Tiki product IDs and store them as clean JSON.

- **Input:** `products-0-200000.csv` – 200,000 product IDs (UTF-8 with BOM).
- **API:** `https://api.tiki.vn/product-detail/api/v1/products/{id}`
- **Fields kept:** `id`, `name`, `url_key`, `price`, `description` (HTML removed), `images` (list of image URLs).
- **Output:** one JSON file per 1,000 products, plus a separate file of failed IDs with the reason for each failure.

## 2. Approach

| Decision | Why |
| --- | --- |
| Use Anaconda's libcurl (pycurl; the curl binary as fallback) | Tiki returned an HTML bot-check page to `requests`, `curl_cffi` and the macOS system curl. Only the Anaconda curl build received JSON. pycurl uses the same libcurl and reuses connections. |
| One worker (`--workers 1`) with an adaptive delay | Tiki blocks the IP for several minutes when requests come too fast. A 2-worker test was about 3 times slower because of block pauses. |
| Adaptive throttle | Starts at 0.6 s per request, speeds up (×0.9, minimum 0.45 s) after 50 successes in a row, slows down (×1.5) and pauses 5–15 min when blocked. |
| Batches of 1,000 IDs, one file each | Matches the required output format and lets the crawl resume after a stop. |
| Write to `.tmp`, then `os.replace` | A crash never leaves a half-written JSON file. |
| Skip finished batches, keep finished IDs in a partial file | A restart resumes from the first unfinished ID. |
| `verify()` writes `summary.json` | Checks that every input ID appears exactly once in products or errors. |

## 3. Steps

1. **Explore the API** with a few requests ([`experiments/step1_api_test.py`](../experiments/step1_api_test.py)); saved a full response ([`sample/api_response_138083218.json`](../sample/api_response_138083218.json)) to learn the field structure.
2. **Read the input CSV.** Found the BOM problem (column name `\ufeffid`) and fixed it with `encoding="utf-8-sig"`.
3. **Fetch and clean one product:** keep 6 fields, strip HTML tags and entities from `description`, collect `base_url` for each image (`large_url` if `base_url` is missing).
4. **Hit bot detection** (HTML page instead of JSON, checked with [`experiments/diagnose_block_page.py`](../experiments/diagnose_block_page.py)). Tested `requests`, `curl_cffi`, `/usr/bin/curl` and `/opt/anaconda3/bin/curl`; only the last one worked.
5. **Hit rate limiting.** Measured delays: 0.30 s was blocked often, 0.45 s was more stable. Built the adaptive throttle with shared pauses.
6. **Add batching, error files, resume and safe writes** (first version: [`experiments/step2_first_crawler.py`](../experiments/step2_first_crawler.py)).
7. **Fix Ctrl+C** so it stops all worker threads within seconds (`threading.Event` + `pool.shutdown(cancel_futures=True)`).
8. **Test parallel workers** (2 workers) and go back to 1 worker after the test.
9. **Clean up the project** (test scripts moved out of the project root, later added to `experiments/`), write `.gitignore` and `README.md`, push to GitHub.
10. **Run the full crawl** with `caffeinate -i python tiki_crawler.py` (the first, single-file version) to keep the Mac awake.
11. **Keep finished IDs on stop:** each result is appended to `output/partial_XXXX.jsonl`, so stopping mid-batch no longer loses work. Added sample data (`sample/`), the early test scripts (`experiments/`) and this documentation to the repository.
12. **Add automated tests:** 23 unit tests in `tests/` (no network: the HTTP call is faked) run by GitHub Actions on Python 3.9 and 3.12 for every push.

## 4. Changes after code review (Oct 22)

The first version was code-reviewed. Each point and what changed:

| Review point | Change |
| --- | --- |
| The curl path `/opt/anaconda3/bin/curl` was hard-coded, so the script crashed on machines without Anaconda. | The default transport is now pycurl. The curl binary is only a fallback, found on `PATH` or given with `--curl-path`. If neither is available, the crawler stops at startup with a clear message. |
| Starting a new `curl` process for each of the 200,000 requests wastes CPU and memory. | pycurl runs libcurl inside Python. Each worker thread keeps one curl handle, so the TLS connection is reused and no process is started per request. |
| Settings (`MAX_WORKERS`, `START_DELAY`, …) were constants in the code. | `src/config.py` reads every setting from command-line options (`argparse`), environment variables or a `.env` file (`.env.example` is included). |
| Everything was in one file, `tiki_crawler.py`. | Split into `src/api_client.py` (HTTP, retries, backoff), `src/parser.py` (JSON fields and HTML cleaning, Bug 9 fixed), `src/storage.py` (files, resume, verify) and `main.py` (entry point). The tests were split the same way (42 tests). |

The output format did not change, so the new version continues the crawl from the files written by the first version.

## 5. Results

The crawl ran from Oct 22 to Nov 1 (about 11 days including block pauses and times the laptop was asleep). It was started with the first version, and the output format did not change.

| Metric | Value |
| --- | --- |
| Input IDs | 200,000 (all unique) |
| Saved products | 124,299 (62.1%) |
| Failed – HTTP 404 | 75,701 (37.9%) |
| Failed – other reasons | 0 |
| Missing / duplicate IDs | 0 / 0 |

After the crawl:

1. **Retry.** 61 IDs had failed with a network error (curl could not resolve the host when the Wi-Fi dropped). `python main.py --retry-errors` requested them again: 37 were saved and 24 now return HTTP 404. This was also the first live test of the pycurl transport, and Tiki accepted it.
2. **Re-clean.** `python main.py --reclean` fixed 157 descriptions that still had escaped HTML ([Bug 9](bug_report.md#bug-9--escaped-html-left-in-descriptions)).
3. **Final checks.** Every input ID appears exactly once; every product has the 6 fields and `images` is a list; no description contains tags or entities. Kept as returned by the API: 22 products with `price: 0`, 9 without images, 1 with an empty `url_key`.

Speed: batches 1–15 took 22–144 minutes each, about 43 minutes on average.

## 6. Next steps

- Load the data into PostgreSQL and model it for analysis.
- Measure how much faster a full batch is with pycurl connection reuse than with one curl process per request.
