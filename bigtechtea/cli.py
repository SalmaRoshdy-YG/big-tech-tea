"""big-tech-tea command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collect import collect_all
from .config import ConfigError, load_sources, load_subscriptions
from .matcher import matches
from .models import Paper
from .notify import build_notifications, send
from .state import State
from .summarize import Summarizer

ROOT = Path(__file__).resolve().parent.parent
CACHE_LIMIT = 4000


def _load(args):
    sources = load_sources(Path(args.sources).resolve())
    slugs = {s["slug"] for s in sources}
    subs = load_subscriptions(Path(args.subscriptions).resolve(), slugs)
    return sources, subs


def cmd_check(args) -> int:
    sources, subs = _load(args)
    print(f"{len(sources)} source(s), {len(subs)} active subscription(s)\n")
    _, results = collect_all(sources, only=args.only, timeout=args.timeout)
    failures = 0
    disabled = {s["slug"] for s in sources if not s.get("enabled", True)}
    for result in sorted(results, key=lambda r: r.slug):
        if result.slug in disabled:
            print(f"  off   {result.slug:<18} disabled in sources.yaml")
            continue
        if result.error:
            failures += 1
            print(f"  FAIL  {result.slug:<18} {result.error}")
        else:
            newest = result.papers[0].title[:52] if result.papers else "(no entries)"
            kind = "PAPER" if result.papers and result.papers[0].content == "paper" else "blog "
            print(f"  ok    {result.slug:<18} {kind} {len(result.papers):>3}  | {newest}")
    print(f"\n{failures} source(s) failing.")
    return 1 if failures else 0


def cmd_list(args) -> int:
    sources, subs = _load(args)
    for src in sources:
        flag = " " if src.get("enabled", True) else "-"
        kind = "PAPER" if src.get("content", "blog") == "paper" else "blog "
        print(f"{flag} {src['slug']:<18} {kind}  {src['org']}")
    print()
    for sub in subs:
        interests = sub["interests"]
        scope = ",".join(interests.get("sources") or ["all labs"])
        if sub.get("_unresolved"):
            print(f"  {sub['_id']:<34} channel from ${sub['_unresolved']} (not set here)")
            continue
        terms = ", ".join((interests.get("any_of") or interests.get("all_of") or [])[:4])
        print(f"  {sub['_id']:<34} scope={scope:<24} interests={terms}")
    return 0


def _select(papers: list[Paper], sub: dict, state: State, *, ignore_state: bool):
    lookback = float(sub.get("lookback_days", 7))
    hits = []
    for paper in papers:
        if paper.published and paper.age_days > lookback:
            continue
        if not ignore_state and state.was_notified(sub["_id"], paper.uid):
            continue
        matched, reasons = matches(paper, sub["interests"])
        if matched:
            hits.append((paper, reasons))
    cap = int(sub.get("max_per_run", 15))
    return hits[:cap], max(0, len(hits) - cap)


def _write_site_data(path: Path, entries: list[dict], sources: list[dict]) -> None:
    """Feed the GitHub Pages digest. Static JSON, no backend."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labs": [{"slug": s["slug"], "org": s["org"]}
                 for s in sources if s.get("enabled", True)],
        "papers": entries[:120],
    }
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def cmd_run(args) -> int:
    sources, subs = _load(args)
    state = State(Path(args.state).resolve())
    summarizer = Summarizer(Path(args.summaries).resolve(), mode=args.summarizer)

    papers, results = collect_all(sources, only=args.only, timeout=args.timeout)
    for result in results:
        if result.error:
            print(f"! source {result.slug}: {result.error}", file=sys.stderr)
    print(f"collected {len(papers)} entries from "
          f"{sum(1 for r in results if not r.error)} source(s); "
          f"summariser={summarizer.mode}")

    sent_total = 0
    briefs: dict[str, object] = {}

    for sub in subs:
        if sub.get("_unresolved"):
            print(f"  {sub['_id']}: skipped - environment variable "
                  f"{sub['_unresolved']} is not set")
            continue
        hits, overflow = _select(papers, sub, state, ignore_state=args.ignore_state)
        if not hits:
            print(f"  {sub['_id']}: no new matches")
            continue

        if args.seed:
            for paper, _ in hits:
                state.mark(sub["_id"], paper.uid)
            print(f"  {sub['_id']}: seeded {len(hits)} paper(s), nothing sent")
            continue

        print(f"  {sub['_id']}: {len(hits)} match(es)"
              + (f" (+{overflow} over max_per_run)" if overflow else ""))

        enriched = []
        for paper, reasons in hits:
            brief = briefs.get(paper.uid) or summarizer.brief_for(paper)
            briefs[paper.uid] = brief
            enriched.append((paper, reasons, brief))

        for notification in build_notifications(sub, enriched):
            if send(notification, sub, dry_run=args.dry_run):
                sent_total += 1
        for paper, reasons, _brief in enriched:
            if not args.dry_run:
                state.mark(sub["_id"], paper.uid)
            print(f"      - [{paper.org}] {paper.title[:66]}")

    if args.site and not args.dry_run:
        # Summarise the recent window for the public digest, not just matches.
        recent = [p for p in papers if not p.published or p.age_days <= args.site_days]
        entries = []
        for paper in recent[:120]:
            brief = briefs.get(paper.uid) or summarizer.brief_for(paper)
            briefs[paper.uid] = brief
            entry = paper.to_dict()
            entry["brief"] = brief.to_dict()
            entries.append(entry)
        _write_site_data(Path(args.site).resolve(), entries, sources)
        print(f"wrote {len(entries)} paper(s) to the site digest")

    removed = state.prune()
    if not args.dry_run:
        state.save()
        keep = {p.uid for p in papers} | set(summarizer.cache)
        summarizer.save(keep if len(summarizer.cache) > CACHE_LIMIT else None)
    print(f"\nsent {sent_total} notification(s); pruned {removed} stale state entries")
    return 0


def cmd_preview(args) -> int:
    args.dry_run, args.seed, args.ignore_state = True, False, True
    return cmd_run(args)


def cmd_digest(args) -> int:
    """Build the site digest only - no matching, no notifications."""
    args.dry_run, args.seed, args.ignore_state = False, False, True
    sources, _ = _load(args)
    summarizer = Summarizer(Path(args.summaries).resolve(), mode=args.summarizer)
    papers, _results = collect_all(sources, only=args.only, timeout=args.timeout)
    recent = [p for p in papers if not p.published or p.age_days <= args.site_days]
    entries = []
    for paper in recent[:120]:
        entry = paper.to_dict()
        entry["brief"] = summarizer.brief_for(paper).to_dict()
        entries.append(entry)
    _write_site_data(Path(args.site).resolve(), entries, sources)
    summarizer.save()
    print(f"wrote {len(entries)} paper(s) to {args.site}")
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sources", default=str(ROOT / "sources.yaml"))
    common.add_argument("--subscriptions", default=str(ROOT / "subscriptions"))
    common.add_argument("--state", default=str(ROOT / "state" / "seen.json"))
    common.add_argument("--summaries", default=str(ROOT / "state" / "summaries.json"))
    common.add_argument("--site", default=str(ROOT / "docs" / "data" / "latest.json"),
                        help="where to write the public digest (empty string to skip)")
    common.add_argument("--site-days", type=float, default=14.0)
    common.add_argument("--summarizer", choices=["heuristic", "llm"], default=None)
    common.add_argument("--only", nargs="*", help="limit to these source slugs")
    common.add_argument("--timeout", type=int, default=30)

    parser = argparse.ArgumentParser(prog="big-tech-tea")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", parents=[common], help="fetch, match, summarise, notify")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--seed", action="store_true",
                     help="mark current matches as sent without notifying")
    run.add_argument("--ignore-state", action="store_true")
    run.set_defaults(func=cmd_run)

    preview = sub.add_parser("preview", parents=[common],
                             help="dry run that ignores saved state")
    preview.set_defaults(func=cmd_preview)

    digest = sub.add_parser("digest", parents=[common],
                            help="rebuild the public digest only")
    digest.set_defaults(func=cmd_digest)

    check = sub.add_parser("check", parents=[common],
                           help="verify every source still parses")
    check.set_defaults(func=cmd_check)

    listing = sub.add_parser("list", parents=[common],
                             help="show sources and subscriptions")
    listing.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
