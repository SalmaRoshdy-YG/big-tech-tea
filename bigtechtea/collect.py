"""Turn configured sources into Paper objects."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

import time

from .feeds import FetchError, http_get, parse_feed
from .models import Paper

ARXIV_API = "http://export.arxiv.org/api/query"


@dataclass
class SourceResult:
    slug: str
    papers: list[Paper]
    error: str | None = None


def _apply_filters(entries: list[dict], cfg: dict) -> list[dict]:
    """Optional per-source narrowing, e.g. keep only /research/ URLs."""
    url_must_match = cfg.get("url_must_match")
    title_must_not_match = cfg.get("title_must_not_match")
    out = []
    for entry in entries:
        if url_must_match and not re.search(url_must_match, entry["url"], re.I):
            continue
        if title_must_not_match and re.search(title_must_not_match, entry["title"], re.I):
            continue
        out.append(entry)
    return out


def _build_arxiv_url(cfg: dict) -> str:
    params = {
        "search_query": cfg["query"],
        "start": "0",
        "max_results": str(cfg.get("max_results", 40)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    return f"{ARXIV_API}?{urllib.parse.urlencode(params)}"


def collect_source(cfg: dict, timeout: int = 30) -> SourceResult:
    slug = cfg["slug"]
    org = cfg.get("org", slug)
    kind = cfg.get("kind", "feed")

    if not cfg.get("enabled", True):
        return SourceResult(slug, [], error=None)

    url = _build_arxiv_url(cfg) if kind == "arxiv" else cfg["url"]

    try:
        entries = parse_feed(http_get(url, timeout=timeout))
    except FetchError as exc:
        return SourceResult(slug, [], error=str(exc))

    entries = _apply_filters(entries, cfg)[: int(cfg.get("max_items", 60))]

    papers = [
        Paper(
            source=slug,
            org=org,
            title=e["title"],
            url=e["url"],
            summary=e["summary"],
            published=e["published"],
            authors=e["authors"],
            tags=list(dict.fromkeys(e["tags"] + cfg.get("extra_tags", []))),
            content=cfg.get("content", "paper" if kind == "arxiv" else "blog"),
        )
        for e in entries
    ]
    return SourceResult(slug, papers)


def collect_all(sources: list[dict], only: list[str] | None = None,
                timeout: int = 30) -> tuple[list[Paper], list[SourceResult]]:
    results: list[SourceResult] = []
    previous_kind = None
    for cfg in sources:
        if only and cfg["slug"] not in only:
            continue
        # arXiv's API terms ask for a few seconds between requests.
        if cfg.get("kind") == "arxiv" and previous_kind == "arxiv":
            time.sleep(3)
        previous_kind = cfg.get("kind")
        results.append(collect_source(cfg, timeout=timeout))

    seen: set[str] = set()
    papers: list[Paper] = []
    for result in results:
        for paper in result.papers:
            if paper.uid in seen:
                continue
            seen.add(paper.uid)
            papers.append(paper)

    papers.sort(key=_sort_key, reverse=True)
    return papers, results


def _sort_key(paper: Paper) -> datetime:
    """Undated entries sort last rather than raising on None comparison."""
    if paper.published is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if paper.published.tzinfo is None:
        return paper.published.replace(tzinfo=timezone.utc)
    return paper.published
