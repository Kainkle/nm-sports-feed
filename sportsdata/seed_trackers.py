"""Refresh the published tracker tables from a SofaScore-served egress.

    python -m sportsdata.seed_trackers

SofaScore serves GitHub runner egress nothing (see build.py's TRACKER_CARRY_HOURS note), so
CI builds can only carry tables forward. This script is the refresh half: run it on a machine
SofaScore actually serves (a home WAN — the collector impersonates Chrome and is wire-proven
from home), and it merges fresh tables into the published feed WITHOUT rebuilding fixtures —
one collector pass, not a full adapter sweep, so it is safe to run any time.

It writes feed/events.json in place; commit and push the result (the standard manual-rebuild
path). If the published feed has moved on (a CI build landed mid-run), re-run — the merge is
idempotent per league.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import tracker
from .build import _merge_trackers

REPO = Path(__file__).resolve().parent.parent
PUBLISHED = REPO / "feed" / "events.json"


def main() -> int:
    if not PUBLISHED.exists():
        print("no published feed at feed/events.json — run a full build first")
        return 1

    fresh = tracker.collect()
    if not fresh:
        print("collector returned nothing — this egress is not served either; nothing written")
        return 1

    feed = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    prev = feed.get("trackers") or []
    merged = _merge_trackers(fresh, prev)
    feed["trackers"] = merged
    PUBLISHED.write_text(json.dumps(feed, indent=2), encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    print(f"trackers: {len(fresh)} fresh + {len(merged) - len(fresh)} carried = {len(merged)} "
          f"(collected {now}) — written to feed/events.json; commit and push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
