"""Read public expert/contract job listings, without logging in or bypassing gates.

Mercor: Next.js dehydrated listing index + optional public sitemap.
micro1: The same public, read-only JSON feed used by the official
        https://www.micro1.ai/experts/opportunities page. No third parties.

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
MICRO1_PUBLIC_FEED = "https://prod-api.micro1.ai/api/v1/job/portal"
MICRO1_PAGE_SIZE = 100
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
