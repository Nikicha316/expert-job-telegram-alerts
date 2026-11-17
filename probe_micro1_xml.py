"""One-shot, no-login check of Micro1's public XML/RSS discovery files.

Diagnostic only: never posts Telegram messages or alters a bot's history.
This does not attempt the job portal API or bypass an HTTP restriction.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

URLS = (
    "https://www.micro1.ai/robots.txt",
    "https://www.micro1.ai/sitemap.xml",
    "https://jobs.micro1.ai/robots.txt",
    "https://jobs.micro1.ai/sitemap.xml",
)
USER_AGENT = "Micro1-XML-Discovery-Diagnostic/1.0"
POST_PATTERN = re.compile(r"^/post/[0-9a-f-]{36}(?:/)?$", flags=re.I)


@dataclass(frozen=True)
class XmlResults:
    total: int
    micro1_job_urls: list[str]
    other_sample: list[str]
    child_sitemaps: list[str]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def official_job_url(value: str) -> bool:
    try:
        url = urlsplit(value.strip())
    except ValueError:
        return False
    return (url.scheme == "https"
            and url.hostname == "jobs.micro1.ai"
            and bool(POST_PATTERN.fullmatch(url.path)))


def parse_xml(xml_bytes: bytes) -> XmlResults:
    """Read URL sets, sitemap indexes, Atom and RSS; do not parse HTML."""
    root = ET.fromstring(xml_bytes)
    nodes = []
    for node in root.iter():
        if _local_name(node.tag) in ("loc", "link"):
            href = node.attrib.get("href", "")
            if not href and node.text:
                href = node.text.strip()
            if href.startswith(("https://", "http://")):
                nodes.append(href)
    urls = list(dict.fromkeys(nodes))
    jobs = [url for url in urls if official_job_url(url)]
    children = [url for url in urls
                if _local_name(root.tag) == "sitemapindex"
                and urlsplit(url).hostname in ("www.micro1.ai", "jobs.micro1.ai")
                and urlsplit(url).path.endswith(".xml")]
    return XmlResults(len(urls), jobs, [url for url in urls if url not in jobs][:8], children)


def read_public_file(url: str) -> tuple[int, str, bytes]:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml, text/xml, application/rss+xml, text/plain"})
    with urlopen(req, timeout=18) as resp:
        body = resp.read(5_000_001)
        if len(body) > 5_000_000:
            raise ValueError("File exceeds diagnostic 5 MB limit")
        return resp.status, resp.headers.get("Content-Type", ""), body


def main() -> int:
    found = set()
    pending = list(URLS)
    checked = set()
    while pending and len(checked) < 8:
        url = pending.pop(0)
        if url in checked:
            continue
        checked.add(url)
        print(f"\nChecking {url}", flush=True)
        try:
            status, content_type, data = read_public_file(url)
            print(f"  HTTP {status}, Content-Type: {content_type}, bytes: {len(data)}")
            if url.endswith("robots.txt"):
                for line in data.decode("utf-8", errors="replace").splitlines():
                    if line.lower().startswith("sitemap:"):
                        print("  " + line.strip())
                continue
            try:
                results = parse_xml(data)
            except ET.ParseError as exc:
                print(f"  Not parseable as XML: {exc}")
                continue
            found.update(results.micro1_job_urls)
            for child in results.child_sitemaps:
                if child not in checked and child not in pending and len(checked) + len(pending) < 8:
                    pending.append(child)
            print(f"  URLs in XML: {results.total}")
            print(f"  Direct official Micro1 job URLs: {len(results.micro1_job_urls)}")
            if results.child_sitemaps:
                print(f"  Nested official sitemaps: {len(results.child_sitemaps)} (up to 4 followed)")
            for sample in results.micro1_job_urls[:5]:
                print(f"    JOB: {sample}")
            for sample in results.other_sample[:3]:
                print(f"    OTHER: {sample}")
        except HTTPError as exc:
            print(f"  HTTP {exc.code}; this endpoint is unavailable to this runner")
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"  Connection/file error: {type(exc).__name__}: {exc}")
    print(f"\nRESULT: {len(found)} unique official job-posting URLs discovered from XML/RSS files")
    if found:
        print("POSSIBLE SOURCE: validate freshness and lastmod before implementing a monitor.")
    else:
        print("NO VIABLE XML/RSS SOURCE CONFIRMED from this runner.")
    return 0  # Diagnostic result, not a production alert source.


if __name__ == "__main__":
    sys.exit(main())
