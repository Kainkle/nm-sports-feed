"""
Run every adapter, write the feed the boxes read, and write a coverage report.

    python -m sportsdata.build [--days 3] [--out feed/]

## Why the coverage report exists

The open question is not "does a source respond" — that is already verified — but **which competitions
actually have fixtures once this runs for real**. That cannot be answered by research, only by running the
thing and looking. So every build emits `coverage.json` alongside the feed: per competition, how many
events, over what dates, how many carry an explicit status, how many have badge art.

That file is the instrument. It says what is thin before a customer finds out.

## The delivery model

Output is plain JSON written to a directory. In production this is committed to a public repo and the boxes
read it as a static file — the `nmm-ota` pattern, already proven in the field. **Boxes never call a sports
API directly**: free tiers survive one central poller and die instantly against a fleet.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .adapters import ADAPTERS, SourceError
from .model import Event, Status

REPO = Path(__file__).resolve().parent.parent


def collect(days: int) -> tuple[list[Event], list[dict]]:
    """
    Fetch every adapter over a date window.

    A failing source is recorded and skipped, never fatal. One league's outage must not empty the whole
    feed — a partial feed is a degraded product, an empty one is a broken app.
    """
    events: dict[str, Event] = {}
    problems: list[dict] = []
    today = datetime.now(timezone.utc).date()

    for ad in ADAPTERS:
        got = 0
        for i in range(days):
            d = (today + timedelta(days=i)).isoformat()
            try:
                for ev in ad.fetch(d):
                    # Keyed by event_id, so NHL's 7-day window overlapping successive calls de-duplicates
                    # for free rather than publishing the same fixture several times.
                    events[ev.event_id] = ev
                    got += 1
            except SourceError as e:
                problems.append({"source": ad.key, "date": d, "error": str(e)})
        print(f"  {ad.key:<6} {got:>4} rows over {days}d")

    return list(events.values()), problems


def coverage(events: list[Event], problems: list[dict], days: int) -> dict:
    by_comp: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_comp[e.competition_id].append(e)

    # EVERY adapter appears, including ones that returned nothing.
    #
    # The first run of this report silently omitted NHL because it produced zero events in August. That is
    # precisely the failure a coverage report exists to catch: "returned nothing" and "was never asked" look
    # identical once a row is missing, and a source that quietly stops is the one that hurts. An explicit
    # zero row is the signal.
    for ad in ADAPTERS:
        by_comp.setdefault(ad.competition_id, [])

    comps = []
    for cid, evs in sorted(by_comp.items()):
        if not evs:
            ad = next((a for a in ADAPTERS if a.competition_id == cid), None)
            comps.append({
                "competition": cid,
                "name": ad.competition_name if ad else cid,
                "sport": ad.sport if ad else "",
                "events": 0,
                "date_range": None,
                "explicit_status": 0,
                "status_coverage_pct": 0,
                "both_logos": 0,
                "logo_coverage_pct": 0,
                "live_now": 0,
                # Cannot be determined from here — a league in its off-season and a league whose API broke
                # both return zero. `problems` disambiguates: an entry there means the fetch itself failed.
                "note": "zero events — off-season, or the source stopped returning data. Check `problems`.",
            })
            continue
        dates = sorted({e.start_utc[:10] for e in evs if e.start_utc})
        explicit = sum(1 for e in evs if e.status is not Status.UNKNOWN)
        with_logo = sum(1 for e in evs if e.home.logo and e.away.logo)
        live = sum(1 for e in evs if e.status is Status.LIVE)
        comps.append({
            "competition": cid,
            "name": evs[0].competition_name,
            "sport": evs[0].sport,
            "events": len(evs),
            "date_range": [dates[0], dates[-1]] if dates else None,
            "explicit_status": explicit,
            "status_coverage_pct": round(100 * explicit / len(evs)) if evs else 0,
            "both_logos": with_logo,
            "logo_coverage_pct": round(100 * with_logo / len(evs)) if evs else 0,
            "live_now": live,
        })

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "total_events": len(events),
        "competitions": comps,
        # Empty is the good case. Anything here is a source that failed this run.
        "problems": problems,
        # Named so a reader is not left guessing why a sport they expected is absent.
        "known_absent": {
            "cricket": "no free source found; ESPN lists 0 leagues, Cricinfo 403s, TheSportsDB leagues empty",
            "snooker": "TheSportsDB has the league but no current fixtures",
            "boxing": "Matchroom only; Top Rank / PBC / Golden Boy uncovered",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--out", default=str(REPO / "sportsdata" / "feed"))
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"building {a.days}-day feed from {len(ADAPTERS)} adapters")
    events, problems = collect(a.days)
    events.sort(key=lambda e: (e.start_utc, e.competition_id))

    feed = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "events": [e.to_json() for e in events],
    }
    (out / "events.json").write_text(json.dumps(feed, indent=2))

    cov = coverage(events, problems, a.days)
    (out / "coverage.json").write_text(json.dumps(cov, indent=2))

    print(f"\n{'competition':<12} {'events':>7} {'status%':>8} {'logos%':>7} {'live':>5}  dates")
    for c in cov["competitions"]:
        rng = f"{c['date_range'][0]} .. {c['date_range'][1]}" if c["date_range"] else "-"
        print(f"{c['competition']:<12} {c['events']:>7} {c['status_coverage_pct']:>7}% "
              f"{c['logo_coverage_pct']:>6}% {c['live_now']:>5}  {rng}")
    if problems:
        print(f"\n{len(problems)} source problem(s):")
        for p in problems[:6]:
            print(f"  {p['source']} {p['date']}: {p['error']}")
    print(f"\nwrote {out/'events.json'} and {out/'coverage.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
