"""Remember what has already been sent, so nobody gets the same paper twice.

State is stored per subscription rather than globally: a brand-new subscriber
gets the last `lookback_days` of matches on their first run, while existing
subscribers are unaffected.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

TTL_DAYS = 120


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"version": 1, "subs": {}}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and "subs" in loaded:
                    self.data = loaded
            except json.JSONDecodeError:
                print(f"! state file {path} is corrupt; starting fresh")

    def _bucket(self, sub_id: str) -> dict:
        return self.data["subs"].setdefault(sub_id, {})

    def was_notified(self, sub_id: str, uid: str) -> bool:
        return uid in self._bucket(sub_id)

    def mark(self, sub_id: str, uid: str) -> None:
        self._bucket(sub_id)[uid] = int(time.time())

    def is_new_subscription(self, sub_id: str) -> bool:
        return sub_id not in self.data["subs"]

    def prune(self, ttl_days: int = TTL_DAYS) -> int:
        cutoff = time.time() - ttl_days * 86400
        removed = 0
        for bucket in self.data["subs"].values():
            for uid in [u for u, ts in bucket.items() if ts < cutoff]:
                del bucket[uid]
                removed += 1
        return removed

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
