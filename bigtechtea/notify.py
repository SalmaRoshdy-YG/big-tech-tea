"""Publish matches to ntfy.

`ntfy_topic` here is the notification channel the reader created in the ntfy
app. It has nothing to do with the research topics they follow.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .models import Paper
from .summarize import Brief

DEFAULT_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
MAX_BODY_CHARS = 3800  # ntfy caps message size; stay well under it


@dataclass
class Notification:
    ntfy_topic: str
    server: str
    title: str
    body: str
    tags: list[str]
    priority: int = 3
    click: str | None = None


def _auth_header(sub: dict) -> dict:
    """The token is read from the environment, never from the subscription file."""
    env_name = sub.get("token_env")
    token = os.environ.get(env_name) if env_name else None
    return {"Authorization": f"Bearer {token}"} if token else {}


def build_notifications(
    sub: dict,
    hits: list[tuple[Paper, list[str], Brief]],
) -> list[Notification]:
    server = sub.get("server", DEFAULT_SERVER).rstrip("/")
    ntfy_topic = sub["ntfy_topic"]
    priority = int(sub.get("priority", 3))

    if sub.get("digest") and len(hits) > 1:
        blocks = []
        for paper, _reasons, brief in hits:
            headline = brief.problem or brief.approach or ""
            blocks.append(
                f"{paper.org}: {paper.title}\n"
                + (f"{headline[:160]}\n" if headline else "")
                + paper.url
            )
        body = "\n\n".join(blocks)
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "\n\n(truncated - see the rest on the site)"
        return [
            Notification(
                ntfy_topic=ntfy_topic,
                server=server,
                title=f"{len(hits)} new papers - {sub['name']}",
                body=body,
                tags=["books"],
                priority=priority,
            )
        ]

    notifications = []
    for paper, reasons, brief in hits:
        body = brief.as_bullets()
        body += f"\n\nmatched: {', '.join(reasons[:4])}"
        notifications.append(
            Notification(
                ntfy_topic=ntfy_topic,
                server=server,
                title=f"[{paper.org}] {paper.title}"[:250],
                body=body[:MAX_BODY_CHARS],
                tags=["page_facing_up"],
                priority=priority,
                click=paper.url,
            )
        )
    return notifications


def send(notification: Notification, sub: dict, *, dry_run: bool = False,
         retries: int = 3, timeout: int = 20) -> bool:
    if dry_run:
        shown = "<from env>" if sub.get("_from_env") else notification.ntfy_topic
        print(f"    [dry-run] -> {notification.server}/{shown}")
        print(f"      {notification.title}")
        for line in notification.body.splitlines():
            print(f"      {line}")
        return True

    payload = {
        "topic": notification.ntfy_topic,
        "title": notification.title,
        "message": notification.body,
        "tags": notification.tags,
        "priority": notification.priority,
    }
    if notification.click:
        payload["click"] = notification.click

    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", **_auth_header(sub)}
    request = urllib.request.Request(notification.server, data=data, headers=headers)

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):        # not worth retrying
                print(f"    ! ntfy rejected {notification.ntfy_topic}: HTTP {exc.code}")
                return False
            print(f"    ! ntfy HTTP {exc.code} (attempt {attempt}/{retries})")
        except Exception as exc:                    # noqa: BLE001
            print(f"    ! ntfy {type(exc).__name__}: {exc} (attempt {attempt}/{retries})")
        time.sleep(2 ** attempt)
    return False
