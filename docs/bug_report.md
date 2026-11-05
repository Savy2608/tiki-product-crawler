# Bug Report – Tiki Product Crawler

Problems found while building and running the crawler: what happened, why, how it was found, how it was fixed, and what was learned.

| # | Bug | Status |
| --- | --- | --- |
| 1 | BOM in the input CSV | Fixed |
| 2 | HTML bot-check page instead of JSON | Fixed |
| 3 | Rate limiting (IP blocked for several minutes) | Handled by adaptive throttle |
| 4 | Two curl builds behave differently | Fixed (Anaconda curl) |
| 5 | Settings changed while running have no effect | Documented (restart needed) |
| 6 | Parallel workers slower than one worker | Fixed (`MAX_WORKERS = 1`) |
| 7 | Ctrl+C did not stop the worker threads | Fixed |
| 8 | Ctrl+C lost the batch in progress | Fixed |
| 9 | Escaped HTML left in descriptions | Fixed (`src/parser.py`, saved files re-cleaned) |

---

## Bug 1 – BOM in the input CSV

- **Symptom:** Reading `row["id"]` from `products-0-200000.csv` failed, because the first column name was `"\ufeffid"` instead of `"id"`.
- **Cause:** The file starts with a UTF-8 BOM (byte order mark). With `encoding="utf-8"` the BOM becomes part of the first column name.
- **How it was found:** The column name looks like `id` when printed, but `repr()` shows the hidden character: `'\ufeffid'`.
- **Fix:** Open the file with `encoding="utf-8-sig"`, which removes the BOM.
- **Lesson:** When a column "does not exist" but looks correct, print `repr()` of the names. Files exported from Excel often start with a BOM.

## Bug 2 – HTML bot-check page instead of JSON

- **Symptom:** `json.loads` failed. The response had HTTP status 200 (sometimes 403), but the body was an HTML page of about 25,863 bytes instead of JSON.
- **Cause:** Tiki's bot detection serves a challenge page to clients it thinks are bots.
- **How it was found:** Saved the response body to `block_page.html` and inspected it; it is about 25 KB and starts with `<`.
- **Fix:** Treat a 200 response whose body starts with `<`, or status 403/429, as "blocked" (pause and retry), not as a product error. Bug 4 describes the client that gets JSON.
- **Lesson:** HTTP 200 does not always mean success. Check the body before parsing it.

## Bug 3 – Rate limiting

- **Symptom:** After about 10–15 quick requests, every request returned the block page for several minutes. This happened on home Wi-Fi and on 4G.
- **Cause:** Tiki limits the number of requests per IP address over time.
- **How it was found:** Test runs with fixed delays, counting how often the crawler was blocked. With 0.30 s between requests, a batch lost about 12 of its 28 minutes to block pauses; 0.45 s was more stable.
- **Fix:** An adaptive throttle shared by all workers:
  - start at 0.6 s per request; after 50 successes in a row multiply the delay by 0.9 (minimum 0.45 s);
  - when blocked, multiply the delay by 1.5 (maximum 10 s) and pause 5 minutes, doubling on consecutive blocks up to 15 minutes;
  - retry the blocked ID until it succeeds, so no ID is lost because of a block.
- **Lesson:** Measure the limit instead of guessing, and let the crawler slow down by itself instead of crashing or skipping IDs.

## Bug 4 – Two curl builds behave differently

- **Symptom:** The same URL returned JSON from one HTTP client and the HTML block page from another.
- **Cause:** Bot detection looks at how the client connects (TLS/HTTP fingerprint), not only at headers. `requests`, `curl_cffi` (even with `impersonate="chrome"`) and the macOS system curl `/usr/bin/curl` were blocked; the Anaconda build `/opt/anaconda3/bin/curl` was accepted.
- **How it was found:** Sent the same request with each client and compared the responses.
- **Fix:** Use the Anaconda libcurl with a `Mozilla/5.0` user agent. The first version called `/opt/anaconda3/bin/curl` through `subprocess` with a hard-coded path; after code review the crawler uses pycurl (the same libcurl inside Python, with connection reuse), and the curl binary is only a fallback found on `PATH` or given with `--curl-path`.
- **Lesson:** "It works in my terminal but not in Python" can mean two different programs are being used. Check the exact path of each tool (`which -a curl`).

## Bug 5 – Settings changed while running have no effect

- **Symptom:** After `MIN_DELAY` and `BLOCK_PAUSE` were changed in the first version (`tiki_crawler.py`), the running crawler kept the old behavior.
- **Cause:** Python reads the script once when it starts. Editing the file later does not change the running process.
- **How it was found:** The pause lengths and delays in the log did not change after the edit.
- **Fix:** Stop the crawler with Ctrl+C and start it again. Finished work is skipped on restart (see Bug 8). Settings are now command-line options or environment variables, so they no longer require editing the code.
- **Lesson:** Settings in code apply only to new runs. Print the settings in use at startup so you can check them; the crawler prints the workers, transport, delays and output folder.

## Bug 6 – Parallel workers slower than one worker

- **Symptom:** With `MAX_WORKERS = 2` (Oct 22, 07:12–07:36), only 300 IDs were finished in about 24 minutes, with 5 blocks, even after the delay rose to 1.2–1.8 s. Sequential crawling finishes 1,000 IDs in about 28 minutes, so 2 workers were about 3 times slower.
- **Cause:** Two workers reach the rate limit much sooner, and each block pauses all workers for at least 5 minutes, which cancels out the extra speed.
- **How it was found:** Compared the `IDs/s` progress lines and the number of "Blocked by Tiki" lines with the sequential run.
- **Fix:** Back to `MAX_WORKERS = 1`.
- **Lesson:** More threads do not help when the limit is on the server side. Measure real throughput including pauses.

## Bug 7 – Ctrl+C did not stop the worker threads

- **Symptom:** In the first version, Ctrl+C did not stop the crawler. The threads kept sending requests until the whole batch was finished.
- **Cause:** `with ThreadPoolExecutor(...)` waits for every submitted task when the block exits, and `KeyboardInterrupt` is raised only in the main thread, so the workers never saw it.
- **How it was found:** After Ctrl+C the terminal kept printing progress and requests continued.
- **Fix:**
  - a shared `threading.Event` (`stop_event`); workers sleep with `stop_event.wait(seconds)`, so they wake up and stop at once;
  - the pool is created without `with`; on `KeyboardInterrupt` the main thread calls `stop_event.set()` and `pool.shutdown(wait=True, cancel_futures=True)`;
  - the crawler prints `Stopped. Run the script again to resume.`
- **Lesson:** Threads do not receive Ctrl+C. Tell them to stop with a flag and cancel pending work explicitly.

## Bug 8 – Ctrl+C lost the batch in progress

- **Symptom:** Stopping in the middle of a batch threw away the IDs already downloaded in that batch (for example 300 of 1,000 IDs in batch 12 on Oct 22).
- **Cause:** Results were kept in memory and written only when the whole batch was finished.
- **How it was found:** After a restart, the progress counter of the batch started again from 0.
- **Fix:** Each finished ID is appended to `output/partial_XXXX.jsonl` right away. On restart these IDs are skipped, and the partial file is deleted once `products_XXXX.json` is saved. `products_XXXX.json` is written last, so it is the signal that a batch is complete.
- **Lesson:** For long jobs, save progress at the smallest useful step, not only at the end of a big unit of work.

## Bug 9 – Escaped HTML left in descriptions

- **Symptom:** 2 of the first 9,784 saved descriptions still contain tag-like text such as `<span;>` between words of the product text.
- **Cause:** Some sellers' descriptions contain *escaped* HTML such as `&lt;span;&gt;` (the stray semicolon is in the data). `clean_description()` removes tags with a regex first and calls `html.unescape` afterwards, so `&lt;span;&gt;` only turns into `<span;>` after the regex has already run.
- **How it was found:** A data quality check on the saved batches searched every description for text that looks like a tag or an entity.
- **Fix:** `src/parser.py` now cleans descriptions with Python's `html.parser` in two passes: the first removes tags and decodes entities, the second removes tags that only appeared after decoding. Plain text such as `<3` is kept. Files saved before the fix were cleaned again with `python main.py --reclean` after the crawl: 157 of 124,299 descriptions changed, and a final check found no tags or entities left.
- **Lesson:** Check the cleaned data, not only the code. The order of cleaning steps matters when the input can be encoded more than once.
