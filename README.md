# Free Micro1 + Mercor job alerts on Telegram

Checks **public freelance/expert job opportunities** on Micro1 and Mercor, then sends a Telegram message when a *new listing ID* appears. Runs **on GitHub Actions every 15 minutes**, not on your computer.

The bot reports **title, company, location (when published), eligible countries/regions (when published), listed pay (when published), and a direct job link**. Unknown locations are explicitly marked **“Not listed — check the posting”**, never guessed.

## Cost and limitations

- **Free with a public GitHub repository** using standard `ubuntu-latest` runners. A private repo has usage limits and could incur charges.
- GitHub `schedule` is **best-effort**: scheduled runs can be late or occasionally dropped; the exact alert delay is not guaranteed.
- The official sites can redesign their pages or block automation. The Micro1 collector uses headless Chromium (Playwright) and may need updates. If a collector gets no credible listings it **fails**, retaining the existing seen history rather than mistakenly resetting it.
- This monitors Mercor's **public marketplace**, *not* corporate employment at `mercor.com/careers`. It monitors Micro1's **expert opportunities portal** and official public job portal, excluding explicitly full-time employee records when labeled. It does not guarantee every role is strictly AI-training work; the boards do not always make that distinction.
- First successful fetch of each site establishes a baseline **without messaging you about hundreds of old jobs**. Only jobs first observed on *later* checks produce alerts.
- This is a personal best-effort watcher, not an official API or affiliation. Respect each site's terms and do not increase the polling rate aggressively.

## Setup (only once)

### 1. Create your Telegram bot

1. Open Telegram, find **[@BotFather](https://t.me/BotFather)** (verified account).
2. Send `/newbot` and follow the instructions. Copy the **bot token**, keep it private.
3. Open your new bot in Telegram and send **`/start`** (bots cannot message you until you initiate the chat).
4. Obtain your **chat ID**: in a private/incognito browser tab, open `https://api.telegram.org/botYOUR_TOKEN/getUpdates`, replacing `YOUR_TOKEN` with your bot token. Look for `"chat":{"id":123456789,...}` in the JSON. The number is your chat ID. If `"result":[]`, send your bot another message and reload.
5. Close the private tab after retrieving the number. **Never share the full URL or your token**, or paste it into GitHub code. As an alternative, you can use a small local `getUpdates` script instead of placing the token in a browser URL.

### 2. Create a free public GitHub repository

1. Go to [github.com/new](https://github.com/new).
2. Name it, for example, `expert-job-telegram-alerts`.
3. Choose **Public** (important for unlimited free standard-runner use).
4. Create the repository. Unzip this download and put **all its contents, including `.github/workflows/check-jobs.yml`,** in the root of your new repository. Use **Add file → Upload files** (check nested folder paths are preserved) or push using git. The repository's root should contain `bot.py`, `sources.py`, `requirements.txt`, `data/` and `.github/`.

If your browser's upload doesn't retain folders, use Git once for setup:

```bash
cd micro1-mercor-telegram-bot
git init
git add .
git commit -m "Add free Telegram job monitor"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

**No local computer is needed after this setup.** You can do everything via the GitHub web UI if folder uploads are supported by your browser.

### 3. Store Telegram credentials as repository secrets

In your GitHub repository open **Settings → Secrets and variables → Actions → New repository secret**. Add exactly these two secrets:

| Secret name | Secret value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token supplied by BotFather |
| `TELEGRAM_CHAT_ID` | Chat ID from `getUpdates` |

Do **not** save either value in a Python file, commit, environment file, issue, or workflow YAML. GitHub Actions injects these at runtime.

### 4. Allow the workflow to save its job history

Go to **Settings → Actions → General → Workflow permissions** and choose **Read and write permissions**, then Save, *if the option is available*. The workflow itself only requests `contents: write` (permission to commit `data/seen_jobs.json` back to this repository).

Ensure GitHub Actions is enabled for your repository. On the **Actions** tab, find **Micro1 & Mercor Telegram job alerts**.

### 5. Send a test message, then initialize the bot

1. Open **Actions → Micro1 & Mercor Telegram job alerts → Run workflow**.
2. Choose **test** and click **Run workflow**. You should receive **“Job alert bot connected!”** in Telegram.
3. Run the same workflow again with **monitor**. This fetches the boards and saves a list of all currently seen job IDs. **No existing-job alert spam on first run.**
4. Check that the workflow succeeded and `data/seen_jobs.json` now has initialized `sources` entries for **both** `micro1` and `mercor`.
5. Thereafter GitHub runs the workflow automatically at **:07, :22, :37 and :52 UTC** each hour. Your machine can remain switched off.

If only one source succeeds initially, it establishes its own baseline; the failing source is retried later and initializes independently when it first works.

## Example notification

> 🆕 **New Micro1 opportunity**  
> **Python AI Evaluator**  
> 📍 **Location:** Remote — Europe  
> 🌍 **Eligible locations:** Europe  
> 💰 **Listed pay:** $40–$75 / hour  
> 🔗 **View job and apply**

This is an **illustrative** example, not a real posting. All fields are read from the listing; absent locations say "Not listed".

## How duplicate prevention works

`data/seen_jobs.json` is a *public* history of official posting IDs, **not credentials**. Each source has a distinct baseline and a permanent set of IDs. A Telegram message is sent for a new ID, then the ID is written to the JSON file. GitHub Actions commits it to the repository at the end of the run. If Telegram fails, that ID is **not** marked as seen and is retried next time.

The bot has **at-least-once notifications**: very rarely, if a message is delivered but the GitHub history commit subsequently fails, it could be sent again on the next run. History is kept when scraping fails. The workflow uses a concurrency group to avoid simultaneous writes, and writes a weekly heartbeat so the public repo doesn't remain inactive for long periods. GitHub may disable schedules after 60 days of inactivity; check Actions if alerts suddenly stop.

## Troubleshooting

- **Test message fails:** Open your bot and send `/start`; re-check the token and chat ID secrets. Don't put tokens in logs or screenshots.
- **Workflow doesn't appear:** Verify `.github/workflows/check-jobs.yml` exists on your repository's default branch and Actions is enabled.
- **"Permission denied" on `git push`:** Update **Settings → Actions → General → Workflow permissions** to read/write; check branch protection rules.
- **"Micro1 found only 0 official listings" or similar:** Site rendered no job links or its JSON format changed. The workflow deliberately fails rather than pretending there are no new jobs. Inspect the logs and update `sources.py`. `MICRO1_MIN_JOBS` defaults to 5 to catch partial page loads.
- **"Mercor embedded data has no listing records":** Mercor likely changed the embedded Next.js response. Update `parse_mercor_index` in `sources.py`.
- **Location says "Not listed":** That location was not found in the publicly fetched job details. Use the linked posting to confirm country eligibility before applying. For Micro1, up to 20 new listings per run get an extra detail check (`MICRO1_ENRICH_LIMIT`).
- **Some Mercor listings do not appear on Explore:** The bot also checks Mercor's public sitemap and fetches previously unseen sitemap-only details; if the sitemap fails, Explore monitoring continues and a warning appears.
- **Notifications stop after many weeks:** GitHub schedules sometimes stop when a public repository is inactive for 60 days or when there are account issues. The bot makes a weekly state heartbeat commit, but you should still check Actions occasionally.

## Test locally (optional)

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m unittest discover -s tests -v
```

To run the bot yourself once, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in your shell environment first, then run `python bot.py`. Never commit them. Running the bot locally is **optional**; GitHub Actions runs it in the cloud.

## Files

- `.github/workflows/check-jobs.yml` — 15-minute cron, secrets, state commits, manual tests.
- `bot.py` — Telegram messages and durable deduplication.
- `sources.py` — Micro1 + Mercor public listing collectors and location extraction.
- `models.py` — shared job record.
- `data/seen_jobs.json` — safe-to-commit history.
- `tests/` — offline behavior and parser tests.
- `requirements.txt` — Python dependencies.

Official references: [GitHub schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [GitHub Actions pricing](https://docs.github.com/en/billing/concepts/product-billing/github-actions), and [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage).
