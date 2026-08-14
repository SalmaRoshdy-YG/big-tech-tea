"""Load and validate sources.yaml and subscriptions/*.yml.

Vocabulary note: `ntfy_topic` is the notification channel string a person
creates and pastes in - it is what their phone listens to. The paper topics
they care about live under `interests`. The two are unrelated, and conflating
them is the easiest way to accidentally publish a channel you meant to keep
quiet.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

TOPIC_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")
WEAK_TOPICS = {
    "papers", "research", "arxiv", "ai-papers", "big-tech-tea", "bigtechtea", "tea",
    "notifications", "alerts", "test", "testing", "mytopic", "my-topic",
}
RULE_KEYS = {"any_of", "all_of", "none_of", "regex", "sources", "title_only",
             "content"}
SUB_KEYS = RULE_KEYS | {
    "name", "ntfy_topic", "ntfy_topic_env", "topic", "interests", "server", "token_env",
    "priority", "digest", "lookback_days", "max_per_run", "enabled",
}


class ConfigError(ValueError):
    pass


def load_sources(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = data.get("sources") or []
    slugs = set()
    for src in sources:
        for required in ("slug", "org"):
            if not src.get(required):
                raise ConfigError(f"source missing '{required}': {src!r}")
        if src["slug"] in slugs:
            raise ConfigError(f"duplicate source slug: {src['slug']}")
        slugs.add(src["slug"])
        kind = src.get("kind", "feed")
        if kind == "arxiv" and not src.get("query"):
            raise ConfigError(f"arxiv source '{src['slug']}' needs a 'query'")
        if kind == "feed" and not src.get("url"):
            raise ConfigError(f"feed source '{src['slug']}' needs a 'url'")
    return sources


def _check_ntfy_topic(sub: dict, origin: str) -> str | None:
    """Resolve the delivery channel.

    `ntfy_topic_env` names an environment variable (a GitHub Actions secret in
    CI) holding the channel, so a public repository never contains it. If the
    variable isn't set - on a contributor's fork, or during PR validation where
    secrets are unavailable - the subscription is left unresolved and skipped
    at send time rather than failing the whole run.
    """
    env_name = sub.pop("ntfy_topic_env", None)
    if env_name:
        if sub.get("ntfy_topic") or sub.get("topic"):
            raise ConfigError(
                f"{origin}: set either 'ntfy_topic' or 'ntfy_topic_env', not both"
            )
        resolved = (os.environ.get(env_name) or "").strip()
        if not resolved:
            sub["_unresolved"] = env_name
            return None
        sub["_from_env"] = True
        topic = resolved
    else:
        topic = (sub.get("ntfy_topic") or sub.get("topic") or "").strip()
    if not topic:
        raise ConfigError(
            f"{origin}: give 'ntfy_topic' (the channel string from the ntfy app) "
            "or 'ntfy_topic_env' (the name of an env var holding it)"
        )
    if topic.lower().startswith("change-me") or topic.lower() in WEAK_TOPICS:
        raise ConfigError(
            f"{origin}: '{topic}' is guessable, so strangers can read and spam this "
            "channel. Generate one with `openssl rand -hex 12`."
        )
    if not TOPIC_RE.match(topic):
        raise ConfigError(
            f"{origin}: 'ntfy_topic' must be 8-64 characters of letters, digits, "
            "'-' or '_'."
        )
    return topic


def _validate_sub(sub: dict, origin: str, known_slugs: set[str]) -> dict:
    unknown = set(sub) - SUB_KEYS
    if unknown:
        raise ConfigError(f"{origin}: unknown key(s): {', '.join(sorted(unknown))}")
    if not sub.get("name"):
        raise ConfigError(f"{origin}: 'name' is required")

    sub["ntfy_topic"] = _check_ntfy_topic(sub, origin)
    sub.pop("topic", None)

    # Interests may be nested (preferred) or flat (tolerated).
    interests = sub.pop("interests", None) or {
        key: sub.pop(key) for key in list(sub) if key in RULE_KEYS
    }
    unknown_rules = set(interests) - RULE_KEYS
    if unknown_rules:
        raise ConfigError(
            f"{origin}: unknown interest rule(s): {', '.join(sorted(unknown_rules))}"
        )
    for key in ("any_of", "all_of", "none_of", "sources"):
        value = interests.get(key)
        if value is not None and not isinstance(value, list):
            raise ConfigError(
                f"{origin}: '{key}' must be a list, got {type(value).__name__}"
            )
    for slug in interests.get("sources") or []:
        if slug not in known_slugs:
            raise ConfigError(f"{origin}: unknown source slug '{slug}'")
    content = interests.get("content")
    if content and content not in ("paper", "blog", "any"):
        raise ConfigError(
            f"{origin}: 'content' must be paper, blog or any (got {content!r})"
        )
    if interests.get("regex"):
        try:
            re.compile(interests["regex"])
        except re.error as exc:
            raise ConfigError(f"{origin}: bad regex: {exc}") from exc
    if not any(interests.get(k) for k in ("any_of", "all_of", "regex")):
        raise ConfigError(
            f"{origin}: give at least one of any_of / all_of / regex under "
            "'interests', otherwise every paper from every lab is a match"
        )

    sub["interests"] = interests
    sub.setdefault("lookback_days", 7)
    sub.setdefault("max_per_run", 15)
    sub.setdefault("enabled", True)
    sub["_origin"] = origin
    sub["_id"] = f"{Path(origin).stem}:{sub['name']}"
    return sub


def load_subscriptions(directory: Path, known_slugs: set[str]) -> list[dict]:
    subs: list[dict] = []
    files = sorted(list(directory.glob("*.yml")) + list(directory.glob("*.yaml")))
    for path in files:
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = data.get("subscriptions") if isinstance(data, dict) else data
        if raw is None and isinstance(data, dict):
            raw = [data]
        for entry in raw or []:
            subs.append(_validate_sub(dict(entry), path.name, known_slugs))
    return [s for s in subs if s.get("enabled", True)]
