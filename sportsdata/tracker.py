"""
League Tracker — standings, recent results and fixtures per league, for the app's drill-in pages.

## What the app reads

A `trackers` array beside `events`/`stories` in events.json. One entry per league that has data
today, never a half-entry and never a build failure:

    {"league_id": "eng.1", "season": "25/26",
     "standings": [{"pos": 1, "team_id": "44", "team": "Arsenal", "logo": "https://...",
                    "played": 3, "win": 3, "draw": 0, "loss": 0, "points": 9,
                    "form": ["W", "W", "W"]}],
     "recent":    [{"event_id": "...", "start_utc": "...", "status": "ended",
                    "home_id": "44", "home": "Arsenal", "home_score": 2,
                    "away_id": "17", "away": "Man City", "away_score": 0}],
     "upcoming":  [ same shape, scores 0, status "notstarted" ]}

## Why SofaScore

The fixture adapters already lean on it for the sports ESPN does not carry, and it is the only
keyless source that serves a league table, a result list and a fixture list from one id space —
which is what lets `team_id` in a standings row equal `home_id` in a result without a second
crosswalk. Its terms posture is recorded in `adapters.py` (SofaScoreAdapter): used on the project
owner's explicit instruction.

## ROUTE MIGRATION, measured 2026-08-31 — read before touching a URL here

SofaScore deleted `/sport/{slug}/scheduled-events` platform-wide (see `adapters.py`), and the
standings route moved with it. Both spellings the internet still documents —
`/unique-tournament/{id}/standings/season/{sid}` and `/unique-tournament/{id}/season/{sid}/standings`
— return **404 today**. The live route is:

    /unique-tournament/{id}/season/{sid}/standings/total

Event routes keep the documented shape: `.../events/last/{page}` and `.../events/next/{page}`.
A 404 there does not mean the route died — it is how an empty set answers (measured: NFL 26/27,
a season with no played games, 404s on `last` while 25/26 serves 30).

## Season selection

`/seasons` lists the NEWEST season first, whether or not it has started (NFL 26/27 sat above the
complete 25/26 on 2026-08-31). Index 0 is therefore not "current". The rule here is: the newest
season that shows ANY signal — standings rows, finished events, or upcoming events — is the
season the tracker reports. One entry never mixes seasons: an all-zero table with real upcoming
fixtures is what a league genuinely looks like in its kick-off week, and splicing last season's
results under this season's header would be the half-honest version of that.

UFC has no seasons on this source — its "seasons" are individual numbered cards — so its entry
carries `season: ""` and reads the season-less event routes directly.

## What is deliberately absent

F1, MotoGP, NASCAR and IndyCar: SofaScore's motorsport surface is EMPTY through every route
reachable on 2026-08-31 — zero events on category scheduled-events across an eight-week daily
sweep, zero unique-tournaments per category, `/category/{id}/seasons` 404. The crests still
serve (that part feeds LeagueBook), but there is nothing to track. WWE, AEW and boxing are not
carried as tournaments at all. Those seven ids are absent from [CROSSWALK] on purpose; they will
be added the day a route serves for them, not before.

## Form is computed, not fetched

SofaScore's standings rows carry no form field (measured keys: position, team, matches, wins,
draws, losses, scoresFor, scoresAgainst, points, promotion, descriptions — no form), and the
per-team form routes that once served it are 404. So form is derived from the same `recent`
list this module already fetched: newest-first, up to [FORM_DEPTH] results per team, from
`winnerCode` (1 home win, 2 away win, anything else a draw). Early season a team may have fewer
than five — the array is simply shorter, which is the true state.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

try:
    from curl_cffi import requests as cffi
except ImportError:  # pragma: no cover - same requirement SofaScoreAdapter carries
    cffi = None

# league_id -> SofaScore unique-tournament id. LeagueBook order, so the app can render the array
# as-is. Absences are documented in the module docstring.
CROSSWALK: dict[str, int] = {
    "nfl": 9464,
    "ncaaf": 32199,       # "NCAA Division FBS" — has fixtures, has NO standings on this source
    "nba": 132,
    "wnba": 486,
    "mlb": 11205,
    "nhl": 234,
    "eng.1": 17,
    "esp.1": 8,
    "ita.1": 23,
    "ger.1": 35,
    "uefa.champions": 7,
    "usa.1": 242,
    "ufc": 19906,         # season-less: cards, not a season
    "rugby-league": 294,  # NRL — the competition the feed actually carries under this id
    "afl": 656,
}

BASE = "https://api.sofascore.com/api/v1"
# The fixtures' badge host, reused so a standings row's crest and a fixture's crest come from the
# same place and Coil's memory cache sees one URL per team.
LOGO = "https://api.sofascore.app/api/v1/team/{id}/image"

# Caps. Standings 20 because a TV column stops being readable past that and the app's own drill-in
# paginates anyway; recent 15 / upcoming 10 because they feed carousels, not archives.
STANDINGS_CAP = 20
RECENT_CAP = 15
UPCOMING_CAP = 10
FORM_DEPTH = 5


def _get(url: str) -> dict | None:
    """One SofaScore GET. Returns None on anything but 200-with-JSON — callers treat that as
    'this piece of the league is absent', which is exactly what a 404 means on this API."""
    if cffi is None:
        raise RuntimeError("curl_cffi is required for the tracker; pip install curl_cffi")
    try:
        r = cffi.get(url, impersonate="chrome", timeout=25)
    except Exception:
        return None
    if r.status_code != 200 or not r.content:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _iso(ts) -> str:
    """SofaScore epoch seconds -> the feed's canonical `...Z` ISO string."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return ""


def _status(ev: dict) -> str:
    """Source status, with the one remap the app's contract names: finished -> ended."""
    t = ((ev.get("status") or {}).get("type")) or ""
    return "ended" if t == "finished" else t


def _pick_season(ut: int) -> tuple[int | None, str]:
    """
    The newest season with any signal (see module docstring), as (season_id, year).

    A season qualifies by standings rows, finished events, or upcoming events — cheapest first is
    standings, but every candidate needs the standings call anyway when it wins, so the order is
    about short-circuiting dead seasons fast. Two rounds of requests per season in the worst case
    is acceptable because this runs at most a handful of times per league per build.
    """
    data = _get(f"{BASE}/unique-tournament/{ut}/seasons") or {}
    for s in data.get("seasons") or []:
        sid, year = s.get("id"), (s.get("year") or "")
        if not sid:
            continue
        st = _get(f"{BASE}/unique-tournament/{ut}/season/{sid}/standings/total") or {}
        for group in st.get("standings") or []:
            if group.get("rows"):
                return sid, year
        nxt = _get(f"{BASE}/unique-tournament/{ut}/season/{sid}/events/next/0") or {}
        if nxt.get("events"):
            return sid, year
        lst = _get(f"{BASE}/unique-tournament/{ut}/season/{sid}/events/last/0") or {}
        if lst.get("events"):
            return sid, year
    return None, ""


def _standings(ut: int, sid: int) -> list[dict]:
    """Up to [STANDINGS_CAP] rows of the table, contract-shaped. Empty list when the league has
    no table on this source (ncaaf) or the season has not drawn one yet."""
    st = _get(f"{BASE}/unique-tournament/{ut}/season/{sid}/standings/total") or {}
    rows: list[dict] = []
    # Several `type="total"` groups can coexist (measured on WNBA 2026: 7, 8 and 15 rows — a
    # conference split beside the combined table), and division-shaped leagues answer with many
    # more. The combined league table is always the WIDEST total group, so pick by row count,
    # not by first-seen; fall back to the widest group of any type when no total group exists.
    groups = st.get("standings") or []
    totals = [g for g in groups if g.get("type") == "total" and g.get("rows")]
    chosen = max(totals, key=lambda g: len(g.get("rows") or []), default=None)
    if chosen is None:
        chosen = max((g for g in groups if g.get("rows")), key=lambda g: len(g.get("rows") or []), default=None)
    for r in (chosen or {}).get("rows") or []:
        team = r.get("team") or {}
        tid = team.get("id")
        if not tid or not team.get("name"):
            continue
        rows.append({
            "pos": r.get("position") or 0,
            "team_id": str(tid),
            "team": team.get("name") or "",
            # SofaScore's 3-letter code (MCI, LAL). The app's standings chart is one pillar per
            # team, and a full name above a ~90dp column is an ellipsis; the code is what the
            # source itself shows in exactly that constraint. Absent-tolerant: the app derives
            # a fallback from the name when the key is missing (carried tables predating this
            # field have no code).
            "name_code": team.get("nameCode") or "",
            "logo": LOGO.format(id=tid),
            "played": r.get("matches") or 0,
            "win": r.get("wins") or 0,
            "draw": r.get("draws") or 0,
            "loss": r.get("losses") or 0,
            "points": r.get("points") or 0,
        })
    return rows[:STANDINGS_CAP]


def _periods(score: dict) -> list[int]:
    """The per-period points SofaScore carries (period1..N), in order — quarters, halves, innings
    and periods all live in the same numbered keys, so one reader serves every league. The app's
    match page draws the breakdown card from this; an unfinished or unstarted game has none."""
    out: list[int] = []
    for i in range(1, 10):
        v = score.get(f"period{i}")
        if v is not None:
            out.append(v)
    return out


def _events(ut: int, sid: int | None, which: str, cap: int) -> list[dict]:
    """
    One page of finished (`last`, newest first) or scheduled (`next`, soonest first) events,
    contract-shaped. `sid=None` reads the season-less route UFC needs.
    """
    base = (f"{BASE}/unique-tournament/{ut}/season/{sid}" if sid is not None
            else f"{BASE}/unique-tournament/{ut}")
    data = _get(f"{base}/events/{which}/0") or {}
    out: list[dict] = []
    for e in data.get("events") or []:
        home, away = e.get("homeTeam") or {}, e.get("awayTeam") or {}
        hid, aid = home.get("id"), away.get("id")
        if not hid or not aid or not home.get("name") or not away.get("name"):
            continue
        hs, as_ = e.get("homeScore") or {}, e.get("awayScore") or {}
        out.append({
            "event_id": f"sofa:{e.get('id')}",
            "start_utc": _iso(e.get("startTimestamp")),
            "status": _status(e),
            "home_id": str(hid),
            "home": home.get("name") or "",
            "home_score": hs.get("current") if hs.get("current") is not None else (hs.get("display") or 0),
            "home_periods": _periods(hs),
            "away_id": str(aid),
            "away": away.get("name") or "",
            "away_score": as_.get("current") if as_.get("current") is not None else (as_.get("display") or 0),
            "away_periods": _periods(as_),
        })
    out.sort(key=lambda x: x["start_utc"], reverse=(which == "last"))
    return out[:cap]


def _with_form(standings: list[dict], recent: list[dict]) -> list[dict]:
    """Attach each team's last [FORM_DEPTH] outcomes, newest first, derived from `recent`."""
    form: dict[str, list[str]] = {}
    for ev in recent:  # already newest-first
        for side, other in (("home_id", "away_id"), ("away_id", "home_id")):
            tid = ev[side]
            if len(form.get(tid) or []) >= FORM_DEPTH:
                continue
            mark = "D"
            if ev["home_score"] > ev["away_score"]:
                mark = "W" if side == "home_id" else "L"
            elif ev["home_score"] < ev["away_score"]:
                mark = "L" if side == "home_id" else "W"
            form.setdefault(tid, []).append(mark)
    for row in standings:
        row["form"] = form.get(row["team_id"]) or []
    return standings


def _league(league_id: str, ut: int) -> dict | None:
    """One tracker entry, or None when the league produced nothing. Every failure path inside
    degrades to a smaller entry or to None — a league's outage must not empty the array."""
    seasonless = league_id == "ufc"
    sid, year = (None, "") if seasonless else _pick_season(ut)
    if sid is None and not seasonless:
        return None

    standings = _standings(ut, sid) if sid is not None else []
    recent = _events(ut, sid, "last", RECENT_CAP)
    upcoming = _events(ut, sid, "next", UPCOMING_CAP)
    if not (standings or recent or upcoming):
        return None
    return {
        "league_id": league_id,
        "season": year,
        "standings": _with_form(standings, recent),
        "recent": recent,
        "upcoming": upcoming,
    }


def collect() -> list[dict]:
    """Every league that produced a tracker today. Wired into build.py by the orchestrator —
    this module deliberately does not touch the build chain itself."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(lambda kv: _league(kv[0], kv[1]), CROSSWALK.items()))
    return [e for e in entries if e]


def main() -> int:
    trackers = collect()
    print(json.dumps({"trackers": trackers}, indent=2))
    print(f"\n{len(trackers)} tracker(s)", file=__import__("sys").stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
