"""Minimal RSS 2.0 / Atom 1.0 fetching and parsing, standard library only.

Deliberately no feedparser dependency: this runs unattended in CI in a public
repo, and one less third-party package is one less thing to audit.
"""

from __future__ import annotations

import gzip
import html
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

USER_AGENT = (
    "big-tech-tea/0.1 (+https://github.com/SalmaRoshdy-YG/big-tech-tea) "
    "polite feed reader"
)

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class FetchError(RuntimeError):
    pass


def http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} for {url}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as one type
        raise FetchError(f"{type(exc).__name__}: {exc} for {url}") from exc


def clean_text(value: str | None, limit: int = 600) -> str:
    if not value:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", value))
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"
    return text


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    candidate = value.replace("Z", "+00:00")
    for attempt in (candidate, candidate[:19], candidate[:10]):
        try:
            dt = datetime.fromisoformat(attempt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _text(node, *paths: str) -> str:
    for path in paths:
        found = node.find(path, NS)
        if found is not None:
            if found.text and found.text.strip():
                return found.text.strip()
            # Atom <link href="..."/> and <content type="html"> carry no .text
            href = found.get("href")
            if href:
                return href.strip()
            inner = "".join(found.itertext()).strip()
            if inner:
                return inner
    return ""


def _atom_link(entry) -> str:
    alternates = [
        link for link in entry.findall("atom:link", NS)
        if link.get("rel", "alternate") == "alternate" and link.get("href")
    ]
    if alternates:
        return alternates[0].get("href", "").strip()
    any_link = entry.find("atom:link", NS)
    return (any_link.get("href", "").strip() if any_link is not None else "")


def parse_feed(payload: bytes) -> list[dict]:
    """Return a list of raw entry dicts from RSS or Atom bytes."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FetchError(f"malformed XML: {exc}") from exc

    entries: list[dict] = []

    # --- RSS 2.0 -------------------------------------------------------
    for item in root.findall(".//item"):
        entries.append(
            {
                "title": clean_text(_text(item, "title"), 300),
                "url": _text(item, "link", "guid"),
                "summary": clean_text(
                    _text(item, "description", "content:encoded")
                ),
                "published": parse_date(
                    _text(item, "pubDate", "dc:date")
                ),
                "authors": [
                    clean_text(a.text, 120)
                    for a in item.findall("dc:creator", NS) + item.findall("author")
                    if a.text
                ],
                "tags": [
                    clean_text(c.text, 60)
                    for c in item.findall("category")
                    if c is not None and c.text
                ],
            }
        )

    # --- Atom 1.0 ------------------------------------------------------
    for entry in root.findall(".//atom:entry", NS):
        entries.append(
            {
                "title": clean_text(_text(entry, "atom:title"), 300),
                "url": _atom_link(entry) or _text(entry, "atom:id"),
                "summary": clean_text(
                    _text(entry, "atom:summary", "atom:content")
                ),
                "published": parse_date(
                    _text(entry, "atom:published", "atom:updated")
                ),
                "authors": [
                    clean_text(n.text, 120)
                    for n in entry.findall("atom:author/atom:name", NS)
                    if n.text
                ],
                "tags": [
                    (c.get("term") or "").strip()
                    for c in entry.findall("atom:category", NS)
                    if c.get("term")
                ],
            }
        )

    return [e for e in entries if e["title"] and e["url"]]
