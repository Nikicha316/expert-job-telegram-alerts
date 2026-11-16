"""Read visible expert job cards from Micro1's official opportunities page.

This module does NOT call the Micro1 API directly and does not try to bypass
site access restrictions. The official webpage itself loads its job cards with
JavaScript. If Micro1 denies that request, a browser cannot scrape missing cards.
"""

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from models import Job
from sources import SourceError

LOG = logging.getLogger(__name__)
OFFICIAL_URL = "https://www.micro1.ai/experts/opportunities"
UUID = re.compile(r"^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$", re.I)
PAY = re.compile(r"(?:\$|€|£)\s*[\d,.]+\s*(?:[-–]\s*(?:\$|€|£)?\s*[\d,.]+)?\s*(?:/\s*(?:h|hr|hour|yr|year)|per\s+(?:hour|year))?", re.I)
LOC = re.compile(r"(?:location|work location|eligible locations?)\s*:\s*([^\n]{2,120})", re.I)


def _job_id(href):
    """Take IDs only from Micro1's official job-posting domains."""
    if not href or not isinstance(href, str):
        return None
    url = urlparse(href)
    if url.scheme != "https" or (url.hostname or "").lower() not in ("jobs.micro1.ai", "req.micro1.ai"):
        return None
    segments = url.path.strip("/").split("/")
    if len(segments) != 2 or segments[0] != "post" or not UUID.fullmatch(segments[1]):
        return None
    return segments[1].lower()


def parse_micro1_cards(html):
    """Parse cards matching the official page's actual .jobs_item template."""
    soup = BeautifulSoup(html, "html.parser")
    board = soup.select_one(".jobs_list")
    if not board:
        raise SourceError("Micro1 browser page has no .jobs_list section; layout changed")
    text = board.get_text("\n", strip=True)
    if "Unable to load jobs" in text:
        raise SourceError("Micro1 website rendered 'Unable to load jobs'; no cards available")

    parsed = {}
    cards = board.select("a.jobs_item[href]")
    for card in cards:
        ident = _job_id(card.get("href"))
        title_tag = card.select_one(".jobs_name")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
        if not ident or not title:
            continue
        # The page's expert filter normally excludes full-time internal roles.
        tag_text = " ".join(x.get_text(" ", strip=True) for x in card.select(".jobs_tag"))
        if "core team" in tag_text.lower():
            continue
        pay_tag = card.select_one(".job_salary-bold") or card.select_one(".jobs_salary")
        pay_text = pay_tag.get_text(" ", strip=True) if pay_tag else ""
        pay_match = PAY.search(pay_text)
        pay = pay_match.group(0).strip() if pay_match else None
        # This page's current cards do not display a location. Do not guess.
        loc_tag = card.select_one(".jobs_location, [data-job-location], [data-location]")
        location = None
        if loc_tag:
            location = (loc_tag.get("data-job-location") or loc_tag.get("data-location") or
                        loc_tag.get_text(" ", strip=True)).strip() or None
        if not location:
            loc_match = LOC.search(card.get_text("\n", strip=True))
            if loc_match:
                location = loc_match.group(1).strip()
        parsed[ident] = Job(source="micro1", job_id=ident, title=title,
                            url=f"https://jobs.micro1.ai/post/{ident}", location=location, pay=pay)
    if not cards:
        raise SourceError("Micro1 rendered zero job cards; no baseline/history changes")
    if not parsed:
        raise SourceError(f"Micro1 rendered {len(cards)} cards but no valid official job links")
    return list(parsed.values())


def _visible_count(page):
    return page.locator(".jobs_list a.jobs_item").count()


def read_visible_board(page, timeout_ms=30000, max_pages=35):
    """Wait for JS and progressively scroll the official infinite-scroll job list."""
    try:
        page.wait_for_function("""() => {
          const board = document.querySelector('.jobs_list');
          return Boolean(board && (board.querySelector('a.jobs_item') ||
                  (board.textContent || '').includes('Unable to load jobs') ||
                  board.querySelector('.jobs_result-0-wrap')));
        }""", timeout=timeout_ms)
    except Exception:
        raise SourceError("Micro1 did not render job cards before timeout; no notifications sent") from None

    if "Unable to load jobs" in page.locator(".jobs_list").inner_text():
        raise SourceError("Micro1's page reports 'Unable to load jobs' (blocked or unavailable job feed)")
    if not _visible_count(page):
        raise SourceError("Micro1's page reports no roles; refusing to assume job board is empty")

    info = page.locator(".jobs_result-info")
    expected = None
    if info.count():
        match = re.search(r"([\d,]+)\s+roles?\s+found", info.first.inner_text(), re.I)
        if match:
            expected = int(match.group(1).replace(",", ""))
    if expected is None:
        raise SourceError("Micro1 job count is missing; cannot verify complete pagination")
    if expected == 0:
        raise SourceError("Micro1 claims zero jobs; preserving history")
    if expected > max_pages * 18:
        raise SourceError(f"Micro1 has {expected} jobs, more than the configured page cap")

    for _ in range(max_pages):
        before = _visible_count(page)
        if before >= expected:
            break
        sentinel = page.locator(".scroll-sentinel")
        if sentinel.count():
            try:
                sentinel.first.scroll_into_view_if_needed(timeout=6000)
            except Exception:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        try:
            page.wait_for_function("""oldCount => {
              const board = document.querySelector('.jobs_list');
              return !!board && board.querySelectorAll('a.jobs_item').length > oldCount;
            }""", arg=before, timeout=12000)
        except Exception:
            raise SourceError(
                f"Micro1 loaded {before}/{expected} cards; pagination stalled or was denied. "
                "Refusing incomplete scan") from None
    count = _visible_count(page)
    if count != expected:
        raise SourceError(f"Micro1 returned {count}/{expected} visible cards; refusing incomplete scan")
    return parse_micro1_cards(page.content())


def fetch_micro1():
    """Open the official webpage in normal headless Chromium and read rendered HTML."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SourceError("Playwright is missing; install requirements.txt") from exc

    error_codes = []
    failed_requests = []
    try:
        with sync_playwright() as pw:
            launch_kwargs = {"headless": True, "args": ["--disable-dev-shm-usage"]}
            if os.environ.get("MICRO1_CHROMIUM_PATH"):
                launch_kwargs["executable_path"] = os.environ["MICRO1_CHROMIUM_PATH"]
            browser = pw.chromium.launch(**launch_kwargs)
            try:
                context = browser.new_context(viewport={"width": 1400, "height": 950}, locale="en-US")
                page = context.new_page()

                def record_response(resp):
                    if urlparse(resp.url).hostname == "prod-api.micro1.ai" and resp.status >= 400:
                        error_codes.append(resp.status)

                def record_failure(req):
                    if urlparse(req.url).hostname == "prod-api.micro1.ai":
                        failed_requests.append("official job-feed request failed")

                page.on("response", record_response)
                page.on("requestfailed", record_failure)
                try:
                    response = page.goto(OFFICIAL_URL, wait_until="domcontentloaded", timeout=45000)
                    if response and response.status >= 400:
                        raise SourceError(f"Micro1 official page returned HTTP {response.status}")
                    jobs = read_visible_board(page)
                    LOG.info("Micro1 Playwright: %d job cards from the official page", len(jobs))
                    return jobs
                except SourceError as exc:
                    reason = f" ({'HTTP ' + str(error_codes[-1]) if error_codes else 'job feed inaccessible'})"
                    if failed_requests and not error_codes:
                        reason = " (official job-feed network request failed)"
                    # Store diagnostics only when explicitly requested.
                    if os.environ.get("MICRO1_DIAGNOSTIC_DIR"):
                        output = Path(os.environ["MICRO1_DIAGNOSTIC_DIR"])
                        output.mkdir(parents=True, exist_ok=True)
                        try:
                            page.screenshot(path=str(output / "micro1.png"), full_page=True, timeout=12000)
                            (output / "micro1-rendered.html").write_text(page.content(), encoding="utf-8")
                        except Exception:
                            LOG.warning("Could not write Micro1 screenshot")
                    raise SourceError(f"{exc}{reason}") from None
                finally:
                    context.close()
            finally:
                browser.close()
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError(f"Micro1 Playwright failed: {type(exc).__name__}: {str(exc).splitlines()[0][:180]}") from None
