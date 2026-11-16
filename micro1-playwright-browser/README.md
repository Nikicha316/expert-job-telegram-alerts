# Micro1 Playwright browser collector for your existing bot

This is a drop-in Playwright collector for your existing **Micro1 + Mercor Telegram** bot. It opens **https://www.micro1.ai/experts/opportunities** in Chromium and reads the *rendered* `.jobs_item` cards, rather than independently requesting Micro1's API, relying on search-engine indexing, or collecting from third-party job boards.

## Important: what is and is not working

- **Verified:** The collector uses the real job-card HTML classes found in the saved official Micro1 opportunities page: `a.jobs_item`, `.jobs_name`, `.job_salary-bold`, `.jobs_result-info`, and `.scroll-sentinel`.
- **Verified:** The included Playwright Chromium tests pass using a locally rendered Micro1-style job board, including infinite scrolling and location extraction. The original bot's existing offline tests also pass.
- **Verified from your GitHub diagnostic:** The official webpage returned HTTP 200, but rendered `Unable to load jobs. Please try again.` The jobs request was unavailable. **This Playwright version will report that failure rather than invent jobs.**
- **Live test in this environment:** Chromium navigation failed with `net::ERR_BLOCKED_BY_ADMINISTRATOR`. Therefore this is **not a confirmed working Micro1 cloud job feed**. Your GitHub runner still needs the official site to serve listings; Playwright can't recover cards that Micro1 refuses to provide.
- Micro1's Candidate Terms restrict automated scripts and scraping. Obtain permission for scheduled automated scraping, and don't use this code to circumvent site restrictions.

## Install on GitHub

1. Go to `https://github.com/Nikicha316/expert-job-telegram-alerts`.
2. Upload `micro1_playwright.py` to the repository root (next to `bot.py`).
3. Edit the existing `bot.py`. Replace this **one line**:

   ```python
   from sources import SourceError, enrich_micro1_new_jobs, fetch_mercor, fetch_micro1
   ```

   with these **two lines**:

   ```python
   from sources import SourceError, enrich_micro1_new_jobs, fetch_mercor
   from micro1_playwright import fetch_micro1
   ```

   Alternatively, replace the current `bot.py` with the bundled `bot.py` if it has not been customized.
4. Keep `sources.py`, `models.py`, `requirements.txt`, `.github/workflows/check-jobs.yml`, `data/seen_jobs.json`, and your Telegram Secrets unchanged. Your workflow already installs Playwright + Chromium and runs every five minutes.
5. On GitHub, go to Actions > Micro1 & Mercor Telegram job alerts > Run workflow > `monitor`.
6. Open the step **Monitor jobs or test Telegram**. A successful scan says `Micro1 Playwright: ... job cards from the official page` and on the first successful scan seeds the Micro1 history without flooding Telegram. Subsequent newly discovered jobs trigger the existing Telegram notifications.

## If Micro1 still doesn't work

If the logs say `Micro1's page reports 'Unable to load jobs'`, the official site did not serve listings to GitHub's browser. It is an **access restriction or upstream availability** problem, not a selector problem. Do not interpret zero cards as zero available jobs. Mercor will continue independently.

When the site does display listings, this collector checks that *all* pages of the infinite-scroll list were loaded (not just the first 18) before accepting a scan. It deduplicates using official posting UUIDs, strips tracking parameters, and skips obvious internal core-team cards.

**Location:** The current Micro1 opportunities card template does not visibly include location. This scraper displays a location only when the rendered card provides one. Your existing bot also attempts to enrich *new* jobs from their official detail pages; if that page is inaccessible, Telegram will accurately say `Not listed — check the posting`.

## Tests

Tests are in `tests/test_micro1_playwright.py`. The suite uses a real local Chromium browser and a synthetic HTML fixture reflecting the actual official page's job-card classes; it **does not** connect to Micro1 or create fake job alerts.

On a machine with Playwright and Chromium installed:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

For local testing with a system Chromium path, set `MICRO1_CHROMIUM_PATH=/path/to/chromium`. On GitHub, you do **not** need this setting because the workflow installs Playwright's Chromium.

## No API shortcut

The script never calls Micro1's API directly. **The official webpage itself calls its API to render job cards.** If Micro1 denies the page's request (HTTP 403), a normal Playwright browser cannot extract the missing listings. No rotating proxies, CAPTCHA handling, credential sharing, or browser-fingerprint bypass is included.
