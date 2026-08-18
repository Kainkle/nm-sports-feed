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
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import highlights, news, replays
from .adapters import ADAPTERS, SourceError
from .model import Event, Status

REPO = Path(__file__).resolve().parent.parent

# How far back the fixture window reaches, in days.
#
# ONE DAY WAS NOT ENOUGH, and the symptom was the "Latest events & highlights" row holding a single card.
# The highlight STORE had twenty matched clips; nineteen of them belonged to games that had already left
# the window, and an event that is not in the feed cannot carry its highlight into the app.
#
# Three days is chosen against how the matcher actually works rather than picked round: YouTube's per-channel
# RSS is a rolling fifteen-entry window, so a busy league's highlight is matched within hours of the game and
# then pushed out of reach. Holding the game itself for three days is what gives the store time to be useful,
# and it is also roughly how long a viewer still considers a result "latest".
#
# The cost is linear and concurrent: at 27 adapters this is 54 extra fetches per build, spread across the
# same worker pool. Fixtures are keyed by event_id, so overlapping windows de-duplicate for free.
BACKFILL_DAYS = 3


def collect(days: int) -> tuple[list[Event], list[dict]]:
    """
    Fetch every adapter over a date window.

    A failing source is recorded and skipped, never fatal. One league's outage must not empty the whole
    feed — a partial feed is a degraded product, an empty one is a broken app.
    """
    events: dict[str, Event] = {}
    problems: list[dict] = []
    today = datetime.now(timezone.utc).date()

    # CONCURRENT, because this is entirely network-bound and there are now dozens of sources.
    #
    # Sequentially the build took ~20s at three adapters and blew past ten minutes at twenty-six - long
    # enough that a job on a five-minute cron would start overlapping itself. Every fetch is an independent
    # HTTP call with no shared state, so the only thing serial execution was buying was latency.
    # THE WINDOW REACHES BACKWARDS, and that is not a detail.
    #
    # Highlights only exist AFTER a game ends, so a forward-only window (today onwards) can never hold a
    # finished game and its highlight at the same time: at 00:01 UTC the day rolled, yesterday's fixtures
    # left the feed, and six matched highlights went to zero without anything actually breaking.
    #
    # Starting a day earlier keeps completed games in the feed long enough to carry the highlight that
    # follows them, which is the entire point of the hero.
    jobs = [
        (ad, (today + timedelta(days=i)).isoformat())
        for ad in ADAPTERS
        for i in range(-BACKFILL_DAYS, days)
    ]
    counts: dict[str, int] = {ad.key: 0 for ad in ADAPTERS}

    def run(job):
        ad, d = job
        try:
            return ad, d, ad.fetch(d), None
        except SourceError as e:
            return ad, d, [], str(e)
        except Exception as e:  # a broken adapter must not take the whole feed down
            return ad, d, [], f"{type(e).__name__}: {e}"

    # Capped: this runs on a shared CI runner, and politeness to the sources matters more than shaving
    # the last few seconds.
    with ThreadPoolExecutor(max_workers=8) as pool:
        for ad, d, rows, err in pool.map(run, jobs):
            if err:
                problems.append({"source": ad.key, "date": d, "error": err})
                continue
            for ev in rows:
                # Keyed by event_id, so NHL's 7-day window overlapping successive calls de-duplicates
                # for free rather than publishing the same fixture several times.
                events[ev.event_id] = ev
                counts[ad.key] += 1

    for k, n in sorted(counts.items()):
        if n:
            print(f"  {k:<22} {n:>4} rows")

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

    # Highlights are matched after collection because matching needs the whole day's fixtures: a title
    # naming two teams is only unambiguous against the full set.
    today = datetime.now(timezone.utc).date().isoformat()
    matched = highlights.apply(events, today, out / "highlights.json")
    print(f"  highlights matched: {matched}")

    # Replays after highlights for the same reason: the match needs the whole fixture set, and a
    # source failure here must not be able to take the feed down (apply catches its own scrapes).
    replay_hits = replays.apply(events, today, out / "replays.json")
    print(f"  replays matched: {replay_hits}")

    # Editorial, fetched alongside the fixtures and published in the same file.
    #
    # One file rather than two: the box already polls this URL every ten seconds, and a second request on
    # its own schedule would double the traffic to save bytes that do not matter. Stories are small.
    stories = news.collect()
    print(f"  stories: {len(stories)} across "
          f"{len({s.competition_id for s in stories})} competitions")

    feed = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "events": [e.to_json() for e in events],
        "stories": [s.to_json() for s in stories],
    }
    (out / "events.json").write_text(json.dumps(feed, indent=2))

    cov = coverage(events, problems, a.days)
    # Recorded in the coverage report for the same reason fixtures are: "no stories" and "the news source
    # broke" look identical on screen, and only a count distinguishes them after the fact.
    cov["stories"] = {
        "total": len(stories),
        "by_competition": {
            cid: sum(1 for s in stories if s.competition_id == cid)
            for cid in sorted({s.competition_id for s in stories})
        },
    }
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
