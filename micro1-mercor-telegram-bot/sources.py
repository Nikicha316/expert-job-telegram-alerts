"""Read public expert/contract job listings, without logging in or bypassing gates.

Mercor: Next.js dehydrated listing index + optional public sitemap.
micro1: Playwright on the public expert opportunities portal (API responses,
        embedded JSON and visible official job links), then its job portal.

The sites can change their markup or data shape. A parser that sees zero jobs
raises SourceError instead of silently declaring the site empty.
"""

import json
import logging
import os
import re
from collections.abc import Iterator
from urllib.parse import quote, urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from models import Job

LOG = logging.getLogger(__name__)
HEADERS = {"User-Agent": "PersonalOpportunityMonitor/1.0 (public jobs only)",
           "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = (12, 25)
MICRO1_EXPERTS = "https://www.micro1.ai/experts/opportunities"
MICRO1_PORTAL = "https://jobs.micro1.ai/"
MERCOR_INDEX = "https://work.mercor.com/explore"
MERCOR_SITEMAP = "https://work.mercor.com/sitemap.xml"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
MERCOR_ID_RE = re.compile(r"list_[a-zA-Z0-9_-]+")
LOCATION_RE = re.compile(r"\b(?:location|work location|eligible location|geography)\s*[:\-]\s*([^\r\n]{2,170})", re.I)
PAY_RE = re.compile(r"\$\s*\d[\d,.]*(?:\s*[-\u2013\u2014]\s*\$?\s*\d[\d,.]*)?\s*(?:/\s*(?:hour|hr|task|year)|per\s+(?:hour|task|year))", re.I)


class SourceError(Exception):
    """A board could not be read confidently. Don't update seen state."""


def _clean(value, max_chars=200):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        # Some feeds describe a location as {name: "Europe"}, not a string.
        value = _first(value, "displayName", "name", "label", "city", "country", "text")
        if value is None:
            return None
    if isinstance(value, (list, tuple)):
        items = [_clean(x, 80) for x in value]
        value = ", ".join(x for x in items if x)
    value = re.sub(r"\s+", " ", str(value)).strip(" :-\t")
    if not value or value.lower() in ("none", "null", "n/a", "unknown", "not specified"):
        return None
    return value if len(value) <= max_chars else value[:max_chars - 1] + "…"


def _first(record, *keys):
    for key in keys:
        val = record.get(key)
        if val is not None and val != "" and val != []:
            return val
    return None


def _post_id(url):
    """Only official micro1 job URLs; do not take a UUID from other domains."""
    if not isinstance(url, str):
        return None
    parts = urlparse(url)
    if parts.netloc and parts.netloc.lower() != "jobs.micro1.ai":
        return None
    hit = re.search(r"/post/([a-f0-9-]{36})(?:/|$)", parts.path, re.I)
    if hit and UUID_RE.fullmatch(hit.group(1)):
        return hit.group(1).lower()
    return None


def _mercor_id(url):
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc.lower() != "work.mercor.com":
        return None
    match = re.search(r"/jobs/(list_[a-zA-Z0-9_-]+)", parsed.path)
    return match.group(1) if match else None


def _pay(minimum, maximum, unit=None, currency="$", label=None):
    """Never invent a rate; currency sign reflects the board's published UI."""
    if label and isinstance(label, str) and ("$" in label or "€" in label or "£" in label):
        return _clean(label, 120)
    if minimum is None and maximum is None:
        return None
    try:
        min_num = float(minimum) if minimum is not None else None
        max_num = float(maximum) if maximum is not None else None
    except (ValueError, TypeError):
        return None
    def fmt(n):
        return f"{n:,.0f}" if n == int(n) else f"{n:,.2f}".rstrip("0").rstrip(".")
    if min_num is not None and max_num is not None:
        val = f"{currency}{fmt(min_num)}" if min_num == max_num else f"{currency}{fmt(min_num)}–{currency}{fmt(max_num)}"
    elif min_num is not None:
        val = f"from {currency}{fmt(min_num)}"
    else:
        val = f"up to {currency}{fmt(max_num)}"
    period = _clean(unit, 30)
    if period:
        period = period.lower().replace("per-", "").replace("per ", "").replace("hourly", "hour").replace("yearly", "year")
        if period in ("hr", "hour", "task", "year", "month", "week"):
            val += f" / {period}"
        elif period in ("one-time", "once"):
            val += " one-time"
    return val


def location_from_text(text):
    """Only return an explicit location, or an explicit standalone Remote tag."""
    if not text:
        return None
    text = str(text)
    match = LOCATION_RE.search(text)
    if match:
        return _clean(match.group(1), 160)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for i, line in enumerate(lines[:-1]):
        if line.lower().rstrip(":") in ("location", "work location", "job location"):
            return _clean(lines[i + 1], 160)
    if re.search(r"(?:^|\n)\s*(?:remote|fully remote|remote - [^\n]+)\s*(?:\n|$)", text, re.I):
        return "Remote"
    return None


def _next_data(html):
    tag = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError as exc:
        raise SourceError("Mercor embedded JSON is invalid") from exc


def _find_mercor_listing_array(node, depth=0):
    if depth > 15:
        return None
    if isinstance(node, dict):
        val = node.get("listings")
        if isinstance(val, list) and val and isinstance(val[0], dict) and "listingId" in val[0]:
            return val
        for child in node.values():
            if isinstance(child, (dict, list)):
                found = _find_mercor_listing_array(child, depth + 1)
                if found is not None:
                    return found
    if isinstance(node, list):
        for child in node[:40]:
            if isinstance(child, (dict, list)):
                found = _find_mercor_listing_array(child, depth + 1)
                if found is not None:
                    return found
    return None


def _mercor_to_job(record):
    if not isinstance(record, dict):
        return None
    ident = _clean(record.get("listingId"), 100)
    title = _clean(record.get("title"), 180)
    status = str(record.get("status") or "active").lower()
    if not ident or not MERCOR_ID_RE.fullmatch(ident) or not title or status in ("closed", "inactive", "archived"):
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:95] or "opportunity"
    url = f"https://work.mercor.com/jobs/{quote(ident)}/{slug}"
    location = _clean(_first(record, "location", "workLocation"), 160)
    arrangement = _clean(record.get("workArrangement"), 80)
    if not location and arrangement and "remote" in arrangement.lower():
        location = "Remote"
    eligible = _clean(_first(record, "eligibleLocation", "eligibleResidenceLocation"), 250)
    return Job(
        source="mercor", job_id=ident, title=title, url=url, location=location,
        pay=_pay(record.get("rateMin"), record.get("rateMax"), record.get("payRateFrequency")),
        eligible_locations=eligible,
    )


def parse_mercor_index(html):
    data = _next_data(html)
    if not data:
        raise SourceError("Mercor listing page has no __NEXT_DATA__; markup changed or access denied")
    records = _find_mercor_listing_array(data)
    if not records:
        raise SourceError("Mercor embedded data has no listing records; site format may have changed")
    jobs = [job for raw in records if (job := _mercor_to_job(raw))]
    if not jobs:
        raise SourceError("Mercor index supplied no active listings")
    return jobs


def _urls_from_sitemap_xml(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError("Mercor sitemap XML was invalid") from exc
    urls, children = set(), []
    for loc in root.iter():
        if not loc.tag.endswith("loc") or not loc.text:
            continue
        text = loc.text.strip()
        if _mercor_id(text):
            urls.add(text)
        elif "sitemap" in text.lower() and urlparse(text).netloc == "work.mercor.com":
            children.append(text)
    return urls, children


def _mercor_detail(html, url):
    data = _next_data(html)
    if not data:
        return None
    ident = _mercor_id(url)
    if not ident:
        return None
    def find_role(node, depth=0):
        if depth > 14:
            return None
        if isinstance(node, dict):
            if node.get("listingId") == ident and node.get("title"):
                return node
            for item in node.values():
                if isinstance(item, (dict, list)):
                    value = find_role(item, depth + 1)
                    if value:
                        return value
        elif isinstance(node, list):
            for item in node[:40]:
                value = find_role(item, depth + 1)
                if value:
                    return value
        return None
    record = find_role(data)
    if record:
        return _mercor_to_job(record)
    return None


def fetch_mercor(known_ids: set[str], initial: bool) -> list[Job]:
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        response = session.get(MERCOR_INDEX, timeout=TIMEOUT)
        response.raise_for_status()
        listings = parse_mercor_index(response.text)
    except (requests.RequestException, SourceError) as exc:
        raise SourceError(f"Mercor index unavailable: {type(exc).__name__}") from None

    jobs = {job.job_id: job for job in listings}
    # Mercor's sitemap has additional published contract roles not present in
    # its Explore index. New sitemap-only jobs are fetched individually.
    try:
        response = session.get(MERCOR_SITEMAP, timeout=TIMEOUT)
        response.raise_for_status()
        sitemap_urls, submaps = _urls_from_sitemap_xml(response.text)
        for submap in submaps[:5]:
            sub = session.get(submap, timeout=TIMEOUT)
            sub.raise_for_status()
            child_urls, _ = _urls_from_sitemap_xml(sub.text)
            sitemap_urls.update(child_urls)
        for url in sorted(sitemap_urls):
            ident = _mercor_id(url)
            if not ident or ident in jobs:
                continue
            if initial or ident in known_ids:
                # Seed the existing ID on the first run without fetching 100s
                # of detail pages. Known IDs don't need re-fetching either.
                jobs[ident] = Job("mercor", ident, "Existing Mercor opportunity", url)
                continue
            try:
                detail = session.get(url, timeout=TIMEOUT)
                detail.raise_for_status()
                job = _mercor_detail(detail.text, url)
                if job:
                    jobs[ident] = job
                else:
                    LOG.warning("Could not parse NEW Mercor sitemap-only listing: %s", url)
            except requests.RequestException:
                LOG.warning("Could not fetch NEW Mercor sitemap-only listing %s", url)
    except (requests.RequestException, SourceError) as exc:
        LOG.warning("Mercor sitemap unavailable; monitoring Explore index: %s", type(exc).__name__)

    LOG.info("Mercor: found %d active/indexed IDs", len(jobs))
    return list(jobs.values())


def _micro1_dict_to_job(record, origin_is_jobs=False):
    if not isinstance(record, dict):
        return None
    url = _first(record, "url", "jobUrl", "applyUrl", "jobLink", "link", "postingUrl", "href")
    ident = _post_id(url) if isinstance(url, str) else None
    if not ident:
        value = _first(record, "id", "jobId", "job_id", "postId", "uuid", "_id")
        if value is not None and UUID_RE.fullmatch(str(value)):
            ident = str(value).lower()
    if not ident:
        return None
    title = _clean(_first(record, "title", "jobTitle", "job_title", "roleTitle", "roleName", "name", "position"), 180)
    if not title or title.lower() in ("apply", "apply now", "learn more", "view job", "job"):
        return None
    job_type = str(_first(record, "employmentType", "jobType", "roleType", "employment_type") or "").lower()
    status = str(_first(record, "status", "jobStatus") or "").lower()
    if status in ("closed", "inactive", "archived", "filled", "unpublished", "draft"):
        return None
    if record.get("isActive") is False or record.get("active") is False:
        return None
    # Avoid obvious internal full-time employee roles; unknown types remain
    # included because expert gigs often omit a type in the summary feed.
    if ("full-time" in job_type or "full_time" in job_type or job_type == "fulltime") and not any(
        t in job_type for t in ("contract", "project", "freelance")
    ):
        return None
    loc = _clean(_first(record, "location", "jobLocation", "workLocation", "locationText", "locationName"), 160)
    if isinstance(record.get("remote"), bool) and record["remote"] and not loc:
        loc = "Remote"
    if not loc and isinstance(record.get("description"), str):
        loc = location_from_text(record["description"])
    eligible = _clean(_first(record, "eligibleCountries", "eligibleLocations", "countries", "allowedCountries"), 250)
    pay = _pay(
        _first(record, "minPay", "payMin", "minSalary", "salaryMin", "hourlyRateMin", "rateMin"),
        _first(record, "maxPay", "payMax", "maxSalary", "salaryMax", "hourlyRateMax", "rateMax"),
        _first(record, "payFrequency", "payPeriod", "salaryPeriod", "ratePeriod") or "hour",
        label=_first(record, "salaryText", "payText", "compensationText"),
    )
    return Job("micro1", ident, title, f"https://jobs.micro1.ai/post/{ident}", loc, pay, eligible)


def _micro1_from_json(node) -> Iterator[Job]:
    """Walk actual JSON responses, no assumptions about API endpoint names."""
    def walk(value, depth=0):
        if depth > 15:
            return
        if isinstance(value, dict):
            job = _micro1_dict_to_job(value)
            if job:
                yield job
            for part in value.values():
                if isinstance(part, (dict, list)):
                    yield from walk(part, depth + 1)
        elif isinstance(value, list):
            for part in value[:3000]:
                yield from walk(part, depth + 1)
    yield from walk(node)


def _card_data(link):
    """Pick the nearest small ancestor containing a heading and the link."""
    anchor_text = link.get_text(" ", strip=True)
    chosen = None
    for ancestor in link.parents:
        if getattr(ancestor, "name", None) not in ("div", "li", "article", "section"):
            continue
        text = ancestor.get_text("\n", strip=True)
        if len(text) > 1000 or len(ancestor.find_all("a", href=True)) > 5:
            continue
        heading = ancestor.find(["h2", "h3", "h4", "h5"])
        if heading:
            chosen = ancestor
            break
        if not chosen and 20 < len(text) < 400:
            chosen = ancestor
    if chosen is None:
        return anchor_text, anchor_text
    heading = chosen.find(["h2", "h3", "h4", "h5"])
    title = heading.get_text(" ", strip=True) if heading else anchor_text
    if title.lower() in ("apply", "apply now", "view details", "details", "learn more"):
        candidates = [x for x in chosen.get_text("\n", strip=True).splitlines()
                      if x.strip().lower() not in ("apply", "apply now", "view details", "details", "learn more")]
        title = candidates[0] if candidates else title
    return title, chosen.get_text("\n", strip=True)


def parse_micro1_html(html):
    """Parse visible official job links and embedded JSON from a loaded page."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, Job] = {}
    for a in soup.find_all("a", href=True):
        target = a.get("href", "")
        ident = _post_id(target)
        if not ident:
            continue
        title, text = _card_data(a)
        title = _clean(title, 180)
        if not title or title.lower() in ("apply", "apply now", "view details", "details"):
            continue
        m = PAY_RE.search(text)
        found[ident] = Job("micro1", ident, title, f"https://jobs.micro1.ai/post/{ident}",
                           location_from_text(text), _clean(m.group(0), 100) if m else None)
    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "")
        if script.get("id") != "__NEXT_DATA__" and script_type not in ("application/json", "application/ld+json"):
            continue
        if script.string and len(script.string) < 8_000_000:
            try:
                for job in _micro1_from_json(json.loads(script.string)):
                    old = found.get(job.job_id)
                    found[job.job_id] = _merge_jobs(old, job) if old else job
            except (ValueError, TypeError):
                pass
    return list(found.values())


def _merge_jobs(original, candidate):
    if not original:
        return candidate
    return Job(
        source=original.source, job_id=original.job_id,
        title=original.title if len(original.title) >= len(candidate.title) else candidate.title,
        url=original.url,
        location=original.location or candidate.location,
        pay=original.pay or candidate.pay,
        eligible_locations=original.eligible_locations or candidate.eligible_locations,
    )


def _render_micro1_page(page):
    # Handle both infinite scroll and explicit "Load more" pagination. The
    # page can also contain an iframe with the listing app.
    stable = 0
    previous = -1
    for _ in range(25):
        total = 0
        clicked = False
        for frame in list(page.frames):
            try:
                total += frame.locator('a[href*="/post/"]').count()
                buttons = frame.get_by_role(
                    "button", name=re.compile(r"^(load more|show more|see more|more jobs|view more)$", re.I)
                )
                if buttons.count() and buttons.first.is_visible() and buttons.first.is_enabled():
                    buttons.first.click(timeout=3000)
                    clicked = True
                frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                continue
        stable = stable + 1 if total == previous and not clicked else 0
        if stable >= 3:
            break
        previous = total
        page.wait_for_timeout(950)
    page.wait_for_timeout(600)


def fetch_micro1() -> list[Job]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SourceError("Playwright is missing: pip install -r requirements.txt") from exc
    found: dict[str, Job] = {}
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1366, "height": 768}, locale="en-US")
        try:
            for target in (MICRO1_EXPERTS, MICRO1_PORTAL):
                page = context.new_page()
                captured = []
                def on_response(response):
                    try:
                        ct = response.headers.get("content-type", "")
                        size = int(response.headers.get("content-length") or "0")
                        if "json" not in ct.lower() or size > 8_000_000:
                            return
                        obj = response.json()
                        captured.extend(_micro1_from_json(obj))
                    except Exception:
                        return
                page.on("response", on_response)
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(3000)
                    _render_micro1_page(page)
                    for frame in page.frames:
                        try:
                            jobs = parse_micro1_html(frame.content())
                            for job in jobs:
                                found[job.job_id] = _merge_jobs(found.get(job.job_id), job)
                        except Exception:
                            pass
                    for job in captured:
                        found[job.job_id] = _merge_jobs(found.get(job.job_id), job)
                    LOG.info("Micro1 %s: %d records from JSON, %d total distinct IDs",
                             urlparse(target).netloc, len(captured), len(found))
                except Exception as exc:
                    errors.append(f"{urlparse(target).netloc}: {type(exc).__name__}")
                    LOG.warning("Micro1 page did not load completely: %s", urlparse(target).netloc)
                finally:
                    page.close()
        finally:
            context.close()
            browser.close()
    jobs = list(found.values())
    if len(jobs) < int(os.environ.get("MICRO1_MIN_JOBS", "5")):
        raise SourceError(f"Micro1 found only {len(jobs)} official listings; refusing to reset baseline. "
                          f"Possible site change or blocked browser. {'; '.join(errors)}")
    LOG.info("Micro1: found %d official job IDs", len(jobs))
    return jobs


def enrich_micro1_new_jobs(jobs: list[Job]) -> list[Job]:
    """On *new* jobs only, retrieve missing location/pay from detail pages.

    Avoid hitting hundreds of detail pages on the first run. If the detail
    page is unavailable, leave the field blank rather than guessing.
    """
    missing = [job for job in jobs if not job.location or not job.pay]
    if not missing:
        return jobs
    limit = int(os.environ.get("MICRO1_ENRICH_LIMIT", "20"))
    session = requests.Session()
    session.headers.update(HEADERS)
    enriched = {}
    for job in missing[:limit]:
        try:
            response = session.get(job.url, timeout=TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            text = soup.get_text("\n", strip=True)
            location = location_from_text(text)
            match = PAY_RE.search(text[:5000])
            enriched[job.job_id] = job.updated(
                location=job.location or location,
                pay=job.pay or (_clean(match.group(0), 100) if match else None),
            )
        except requests.RequestException:
            LOG.info("Micro1 job detail is not accessible over HTTP: %s", job.job_id)
    # Try the same browser engine used for the listing for still-empty location.
    need_browser = [job for job in missing[:limit]
                    if not (enriched.get(job.job_id, job).location)]
    if need_browser:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
                context = browser.new_context()
                try:
                    page = context.new_page()
                    for job in need_browser[:10]:
                        try:
                            page.goto(job.url, wait_until="domcontentloaded", timeout=25000)
                            page.wait_for_timeout(600)
                            text = BeautifulSoup(page.content(), "html.parser").get_text("\n", strip=True)
                            previous = enriched.get(job.job_id, job)
                            match = PAY_RE.search(text[:5000])
                            enriched[job.job_id] = previous.updated(
                                location=previous.location or location_from_text(text),
                                pay=previous.pay or (_clean(match.group(0), 100) if match else None),
                            )
                        except Exception:
                            LOG.info("Could not enrich micro1 job %s", job.job_id)
                    page.close()
                finally:
                    context.close()
                    browser.close()
        except Exception:
            LOG.warning("Micro1 detail enrichment unavailable; job notifications still sent")
    return [enriched.get(job.job_id, job) for job in jobs]
