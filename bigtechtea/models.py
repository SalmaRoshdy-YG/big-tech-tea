"""Core data model."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _canonical_url(url: str) -> str:
    """Strip tracking params and trailing slashes so the same paper hashes alike."""
    url = url.strip()
    url = re.sub(r"[?&](utm_[^=]+|ref|source|fbclid|gclid)=[^&]*", "", url)
    url = re.sub(r"[?&]$", "", url)
    # arxiv abs/pdf and version suffixes point at the same paper
    url = re.sub(r"arxiv\.org/pdf/", "arxiv.org/abs/", url)
    url = re.sub(r"(arxiv\.org/abs/[^/?#]+?)v\d+", r"\1", url)
    return url.rstrip("/").lower()


@dataclass
class Paper:
    source: str                       # slug, e.g. "deepmind"
    org: str                          # display name, e.g. "Google DeepMind"
    title: str
    url: str
    summary: str = ""
    published: datetime | None = None
    authors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    content: str = "blog"             # "paper" (arXiv etc.) or "blog" (announcement)

    @property
    def uid(self) -> str:
        key = _canonical_url(self.url) or self.title.strip().lower()
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    @property
    def age_days(self) -> float:
        if not self.published:
            return 0.0
        now = datetime.now(timezone.utc)
        pub = self.published
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return max(0.0, (now - pub).total_seconds() / 86400.0)

    @property
    def haystack(self) -> str:
        """Lowercased text that keyword rules are matched against."""
        return " ".join([self.title, self.summary, " ".join(self.tags),
                         " ".join(self.authors), self.org]).lower()

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "source": self.source,
            "org": self.org,
            "content": self.content,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "published": self.published.isoformat() if self.published else None,
            "authors": self.authors,
            "tags": self.tags,
        }
