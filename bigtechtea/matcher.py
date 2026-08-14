"""Decide whether a paper matches a subscription's interests.

Rule semantics (all optional, all case-insensitive):
    any_of      list[str]  -> at least one term must appear    (default: match all)
    all_of      list[str]  -> every term must appear
    none_of     list[str]  -> no term may appear
    regex       str        -> Python regex over title+summary+tags
    content     str        -> "paper" (arXiv only), "blog", or "any" (default)
    sources     list[str]  -> restrict to these source slugs
    title_only  bool       -> match against the title alone

Terms containing only word characters are matched on word boundaries, so
"rag" does not fire on "storage" or "paragraph".
"""

from __future__ import annotations

import re
from functools import lru_cache

from .models import Paper


@lru_cache(maxsize=2048)
def _term_pattern(term: str) -> re.Pattern:
    term = term.strip().lower()
    if re.fullmatch(r"[\w\s\-]+", term):
        return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
    return re.compile(re.escape(term))


def _hit(text: str, term: str) -> bool:
    return bool(_term_pattern(term).search(text))


def matches(paper: Paper, rules: dict) -> tuple[bool, list[str]]:
    """Return (matched, list of terms that fired) for one paper/subscription."""
    sources = rules.get("sources")
    if sources and paper.source not in sources:
        return False, []

    wanted = rules.get("content")
    if wanted and wanted != "any" and paper.content != wanted:
        return False, []

    text = paper.title.lower() if rules.get("title_only") else paper.haystack

    none_of = rules.get("none_of") or []
    if any(_hit(text, term) for term in none_of):
        return False, []

    reasons: list[str] = []

    all_of = rules.get("all_of") or []
    for term in all_of:
        if not _hit(text, term):
            return False, []
        reasons.append(term)

    any_of = rules.get("any_of") or []
    if any_of:
        fired = [term for term in any_of if _hit(text, term)]
        if not fired:
            return False, []
        reasons.extend(fired)

    pattern = rules.get("regex")
    if pattern:
        if not re.search(pattern, text, re.I):
            return False, []
        reasons.append(f"/{pattern}/")

    if not (all_of or any_of or pattern):
        reasons.append("all papers")

    return True, reasons
