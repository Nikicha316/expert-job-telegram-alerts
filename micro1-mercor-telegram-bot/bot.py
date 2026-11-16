"""Telegram opportunity monitor. Intended for a free GitHub Actions scheduler.

First successful run for EACH source establishes its baseline and sends no job
alerts. Later runs alert for new stable job IDs. State is committed by Actions.
"""

import argparse
import html
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from models import Job
from sources import SourceError, enrich_micro1_new_jobs, fetch_mercor, fetch_micro1

LOG = logging.getLogger("jobbot")
STATE_PATH = Path(os.environ.get("STATE_PATH", "data/seen_jobs.json"))
SOURCE_NAMES = ("micro1", "mercor")


class NotificationError(Exception):
    """Telegram rejected/failed to receive a message."""


def load_state(path=STATE_PATH):
    if not Path(path).exists():
        return {"version": 1, "sources": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("State JSON is unreadable: refusing to reset notification history") from exc
    if not isinstance(state, dict) or not isinstance(state.get("sources"), dict):
        raise RuntimeError("State JSON schema is invalid: refusing to reset notification history")
    if state.get("version") != 1:
        raise RuntimeError("Unsupported state version: refusing to reset notification history")
    return state


def save_state(state, path=STATE_PATH):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    # Atomic replace avoids a half-written state file if the job is interrupted.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                     prefix=".seen-", suffix=".tmp", delete=False) as tmp:
        tmp.write(payload)
        pending = Path(tmp.name)
    try:
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)


def format_message(job: Job):
    def esc(v):
        return html.escape(str(v), quote=True)
    fields = [
        f"🆕 <b>New {esc(job.source.capitalize())} opportunity</b>",
        f"<b>{esc(job.title)}</b>",
        f"📍 <b>Location:</b> {esc(job.location or 'Not listed — check the posting')}",
    ]
    if job.eligible_locations:
        fields.append(f"🌍 <b>Eligible locations:</b> {esc(job.eligible_locations)}")
    if job.pay:
        fields.append(f"💰 <b>Listed pay:</b> {esc(job.pay)}")
    fields.append(f'🔗 <a href="{esc(job.url)}">View job and apply</a>')
    return "\n".join(fields)


class TelegramNotifier:
    def __init__(self, token, chat_id, session=None):
        if not token or not chat_id:
            raise ValueError("Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in GitHub Actions secrets")
        self._token = token
        self._chat_id = str(chat_id)
        self._session = session or requests.Session()

    def send_text(self, message):
        # Don't log URL/exception details: the bot token is present in the URL.
        endpoint = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": message, "parse_mode": "HTML",
                   "link_preview_options": {"is_disabled": True}}
        for attempt in range(3):
            try:
                response = self._session.post(endpoint, json=payload, timeout=(10, 20))
            except requests.RequestException:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise NotificationError("Telegram connection failed (token hidden)") from None
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.status_code == 429 and attempt < 2:
                wait = min(int((body.get("parameters") or {}).get("retry_after", 4)), 25)
                time.sleep(max(1, wait))
                continue
            if response.status_code >= 500 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            if response.status_code != 200 or not body.get("ok"):
                raise NotificationError(f"Telegram rejected message (HTTP {response.status_code}); "
                                        "check bot token, chat ID, and whether you sent /start")
            return
        raise NotificationError("Telegram message was not accepted")

    def send_job(self, job):
        self.send_text(format_message(job))


def monitor_source(name, jobs, state, notifier, path=STATE_PATH, enrich=None):
    """Return (baseline_count, alerts_sent) and never mark failed sends as seen."""
    if not jobs:
        raise SourceError(f"{name} returned zero jobs; preserving previous state")
    seen_entry = state["sources"].get(name)
    unique = {job.job_id: job for job in jobs if job.source == name}
    if not unique:
        raise SourceError(f"{name} returned no valid job IDs")
    if not seen_entry or not seen_entry.get("initialized"):
        state["sources"][name] = {"initialized": True, "seen_ids": sorted(unique)}
        save_state(state, path)
        LOG.info("%s: baseline saved for %d current listings, no alert flood", name, len(unique))
        return len(unique), 0
    seen = set(seen_entry.get("seen_ids", []))
    pending = [job for ident, job in unique.items() if ident not in seen]
    # If enrichment is needed, it is only attempted for NEW listings.
    if enrich and pending:
        try:
            pending = enrich(pending)
        except Exception:
            LOG.warning("%s: optional detail enrichment failed; sending basic notifications", name)
    sent = 0
    for job in pending:
        notifier.send_job(job)  # raises on failure; ID is not recorded
        seen.add(job.job_id)
        seen_entry["seen_ids"] = sorted(seen)
        save_state(state, path)  # commit step also executes after partial failure
        LOG.info("%s: notified about %s", name, job.job_id)
        sent += 1
    LOG.info("%s: %d job(s), %d new notification(s)", name, len(unique), sent)
    return 0, sent


def main():
    parser = argparse.ArgumentParser(description="Monitor micro1 and Mercor for new public expert gigs")
    parser.add_argument("--test-telegram", action="store_true", help="Send a harmless test message")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        notifier = TelegramNotifier(os.environ.get("TELEGRAM_BOT_TOKEN"),
                                    os.environ.get("TELEGRAM_CHAT_ID"))
    except ValueError as exc:
        LOG.error("%s", exc)
        return 1
    if args.test_telegram:
        try:
            notifier.send_text("✅ <b>Job alert bot connected!</b>\n"
                               "Monitoring Micro1 and Mercor every 15 minutes when scheduled.\n"
                               "📍 Job locations will be shown when available.")
            LOG.info("Telegram test message sent")
            return 0
        except NotificationError as exc:
            LOG.error("%s", exc)
            return 1

    try:
        state = load_state(STATE_PATH)
    except RuntimeError as exc:
        LOG.error("%s", exc)
        return 1

    errors = []
    for name in SOURCE_NAMES:
        known = set(state["sources"].get(name, {}).get("seen_ids", []))
        initial = not state["sources"].get(name, {}).get("initialized")
        try:
            jobs = fetch_micro1() if name == "micro1" else fetch_mercor(known, initial)
            monitor_source(name, jobs, state, notifier,
                           enrich=enrich_micro1_new_jobs if name == "micro1" else None)
        except (SourceError, NotificationError) as exc:
            errors.append(f"{name}: {exc}")
            LOG.error("%s: %s", name, exc)
        except Exception as exc:
            errors.append(f"{name}: unexpected {type(exc).__name__}")
            LOG.exception("%s: unexpected collector error", name)

    # One small public heartbeat update per UTC ISO week, not 96 commits/day.
    # This also makes the repo active between job postings.
    week = datetime.now(timezone.utc).strftime("%G-W%V")
    if any(state["sources"].get(name, {}).get("initialized") for name in SOURCE_NAMES):
        if state.get("heartbeat_week") != week:
            state["heartbeat_week"] = week
            save_state(state, STATE_PATH)
    if errors:
        LOG.error("Monitor finished with %d source issue(s); prior history retained", len(errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
