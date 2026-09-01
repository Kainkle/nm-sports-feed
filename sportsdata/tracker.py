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
                    "away_id": "17", "away": "Man City", "away_score": 0,
                    // v3, first 6 finished games only, each piece only when it served:
                    "incidents":  [{"minute": 63, "type": "goal", "team_id": "44",
                                    "player": "E. Haaland", "detail": "Goal", "is_home": false}],
                    "statistics": [{"group": "Shots", "name": "Total shots",
                                    "home": "20", "away": "4"}],
                    "lineups": {"home_players": [{"name": "D. Raya", "num": 1, "pos": "G",
                                                  "rating": "7.0"}],
                                "away_players": [ same ]}}],
     "upcoming":  [ same shape, scores 0, status "notstarted", never any detail ],
     // v3, whole keys absent when this season serves nothing:
     "stats":     [{"category": "Top goals", "rows": [{"name": "C. Palmer", "team": "Chelsea",
                                                       "logo": "https://...", "value": "18"}]}],
     "playoffs":  {"rounds": [{"name": "Quarter-finals", "matches": [
                     {"home": "...", "home_id": "...", "home_logo": "...", "home_score": 2,
                      "away": "...", "away_id": "...", "away_logo": "...", "away_score": 1,
                      "winner": "home", "status": "ended"}]}]}}

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

## v3 — stats leaders, playoffs, per-game detail (measured 2026-08-31)

Player leaderboards: `/top-players` is 404 platform-wide; `/top-players/overall` is the live
spelling. It serves only for seasons with played games behind them (eng.1 26/26-style August
football: yes; NFL/NBA/NHL 26/27 with zero games and MLB mid-season: no — the absence is the
source's, recorded, not retried). The league `/statistics` route answers 200 everywhere but is
a paginated player roster with NO stat values in the body — not a leaderboard, deliberately
unused.

Playoffs: `/cups`, `/draw` and their `/total` and season-less variants all 404, on current
seasons AND on completed bracket seasons (UCL 24/25, NHL 25/26) — the route family died with
the 2026-08-31 migration. The bracket is therefore derived from what IS on the wire: knockout
games carry `roundInfo.name` ("Wild Card Round", "Super Bowl", "Round of 16" …) while
regular-season games carry a round number and NO name. Grouping the season's recent events by
that name reproduces SofaScore's own bracket. Cups/draw stay probed first (one GET each) so the
day the source reanimates them, the authoritative route wins again.

Per-game detail: `/event/{id}/statistics` serves for every sport that played, including UFC
(per-round fighting stats); `/incidents` and `/lineups` serve for football-shaped sports and
404 for UFC/baseball-incidents. Lineups' `coach` node is null on this surface (measured
football + rugby) — the coach keys are carried for the day it populates, absent until then.
Incident `injuryTime` rows are skipped on purpose: they are a display artifact of the source's
UI (a length, no player, no score) and would render as junk timeline rows.
"""

from __future__ import annotations

import json
import re
import pathlib
import threading
import time
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
# Team crests follow the logos.py doctrine: SofaScore's image host 403s the app's plain
# HTTP client (wire-measured — only TLS-impersonated clients pass), so the tracker never
# emits the source URL. The bytes are mirrored into this repo's raw CDN (img/t/{id},
# WRITE IF MISSING — a crest is identity, not data) and the mirrored URL is what the JSON
# carries. A fetch that fails yields "" and the app's monogram fallback: honest, and it
# heals on the next home-machine seed because the file is still missing.
_TEAM_IMG_SRC = "https://api.sofascore.app/api/v1/team/{id}/image"
_TEAM_CDN = "https://raw.githubusercontent.com/Kainkle/nm-sports-feed/main/img/t"
_TEAM_IMG_DIR = pathlib.Path(__file__).resolve().parent.parent / "img" / "t"
_team_img_failed: set[str] = set()


def _logo(team_id) -> str:
    """The team-crest URL the app can actually load, or "". Mirror-on-demand, cached on
    disk; a failed fetch is remembered for this process so one bad id costs one request."""
    tid = str(team_id)
    if tid in _team_img_failed:
        return ""
    for ext in ("png", "webp"):
        if (_TEAM_IMG_DIR / f"{tid}.{ext}").is_file():
            return f"{_TEAM_CDN}/{tid}.{ext}"
    try:
        r = cffi.get(_TEAM_IMG_SRC.format(id=tid), impersonate="chrome", timeout=20)
        data = r.content or b""
        # Same gate as logos.py: the .app host answers a bad id with 200 + ~0 bytes —
        # without the size floor, write-if-missing would enshrine an empty file forever.
        if r.status_code == 200 and len(data) >= 100:
            ext = "webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "png"
            _TEAM_IMG_DIR.mkdir(parents=True, exist_ok=True)
            (_TEAM_IMG_DIR / f"{tid}.{ext}").write_bytes(data)
            return f"{_TEAM_CDN}/{tid}.{ext}"
    except Exception:
        pass
    _team_img_failed.add(tid)
    return ""

# Caps. Standings 20 because a TV column stops being readable past that and the app's own drill-in
# paginates anyway; recent 15 / upcoming 10 because they feed carousels, not archives.
STANDINGS_CAP = 20
RECENT_CAP = 15
UPCOMING_CAP = 10
FORM_DEPTH = 5
# v3 caps. Detail rides on the first 6 finished games only — the tracker feeds a drill-in, not an
# archive, and 6 games x 3 routes is already 18 requests per league per build. Stats 4 x 5 is the
# screen's grid. Lineups 12 covers a starting eleven plus a spare; playoff caps bound the worst
# case (an NBA-style 4x7 post-season) without truncating any real round.
DETAIL_CAP = 6
STAT_CATEGORIES = 4
STAT_ROWS = 5
LINEUP_CAP = 12
PLAYOFF_ROUND_CAP = 8
PLAYOFF_MATCH_CAP = 16
PLAYOFF_PAGES = 3
INCIDENT_CAP = 40
# Seconds to wait before the single retry of a refused request. See [_get].
RETRY_PAUSE_S = 3.0


# ── THE PROBE ─────────────────────────────────────────────────────────────────────────────────
#
# SofaScore answers this repo's CI and refuses the development machine (403 to both api hosts, with a
# full browser header set — measured). So the source's actual response shapes cannot be read where the
# code is written, and two questions were about to be answered by guessing:
#
#   1. What are the score fields on an incident actually CALLED? `home_score`/`away_score` came back
#      absent on every one of 153 events after a fix that assumed `homeScore`/`awayScore`. Either the
#      names are wrong or the fields are not served. A second guess is not an answer.
#   2. Which surface carries play-by-play for the sports where `/incidents` is empty — MLB, NBA, NFL,
#      NHL all return nothing, and the user can see the plays on the source's own site.
#
# So this build MEASURES both and writes what it saw to `feed/_probe.json`, which the workflow already
# commits. The next run publishes the answer; the parser is then written against an observed shape
# rather than a remembered one. Nothing here changes what the feed serves — it only records.
PROBE: dict = {"http": {}, "incident_shape": None, "pbp_candidates": {}, "stats": {}}


def _tally(code) -> None:
    """Every response this build saw, by status.

    Without it a collector that returns nothing is indistinguishable from a source that carries
    nothing -- and those need opposite fixes. The build of 2026-09-01T03:22 produced `0 fresh + 15
    carried` with no error line and no exception: every league silently returned None. That reads as
    "the source has no data" and almost certainly means "the source refused this runner", but the
    feed had no way to say which, so the previous run's conclusions were drawn from ten-hour-old
    carried tables that predate the fix being tested.
    """
    k = str(code)
    with _PROBE_LOCK:
        PROBE["http"][k] = PROBE["http"].get(k, 0) + 1
_PROBE_LOCK = threading.Lock()

# Ranked by how likely each is to be the surface the source's own site reads for a play list. Every
# one of them is a GUESS, which is the entire point: the build reports which guesses answered.
_PBP_CANDIDATES = ("incidents", "comments", "innings", "play-by-play", "graph", "highlights", "odds/all")


def _get_status(url: str) -> tuple[int, dict | None]:
    """[_get], but it reports the status code. The probe needs to distinguish 'this surface does not
    exist' from 'this surface exists and served nothing', which [_get] flattens into None."""
    if cffi is None:
        return (0, None)
    try:
        r = cffi.get(url, impersonate="chrome", timeout=25)
    except Exception:
        _tally("exception")
        return (0, None)
    _tally(r.status_code)
    if r.status_code != 200 or not r.content:
        return (r.status_code, None)
    try:
        return (200, r.json())
    except Exception:
        return (200, None)


def _shrink(v, depth: int = 0):
    """A value small enough to read in a diff. Keys are the answer here, not values."""
    if depth > 3:
        return "..."
    if isinstance(v, dict):
        return {k: _shrink(x, depth + 1) for k, x in list(v.items())[:14]}
    if isinstance(v, list):
        return [_shrink(x, depth + 1) for x in v[:2]]
    if isinstance(v, str):
        return v[:60]
    return v


def _probe_incident_shape(ev_id: int) -> None:
    """One football event's RAW incident objects. Answers question 1 outright: whatever the score
    fields are called, they are in here under their real names."""
    with _PROBE_LOCK:
        if PROBE["incident_shape"] is not None:
            return
        PROBE["incident_shape"] = {"event_id": ev_id, "pending": True}
    code, data = _get_status(f"{BASE}/event/{ev_id}/incidents")
    rows = (data or {}).get("incidents") or []
    # Prefer a SCORING incident: a substitution would not carry a score under any name.
    goals = [r for r in rows if (r.get("incidentType") or "") == "goal"][:2]
    with _PROBE_LOCK:
        PROBE["incident_shape"] = {
            "event_id": ev_id,
            "status": code,
            "row_count": len(rows),
            "top_level_keys": sorted((data or {}).keys()),
            "goal_rows_raw": [_shrink(g) for g in goals],
            "any_row_raw": _shrink(rows[0]) if rows else None,
        }


def _probe_pbp(ev_id: int, league_id: str) -> None:
    """For a league whose `/incidents` served nothing: what DOES answer for this event, and what
    shape does it have. One event per league, so the probe costs fifteen extra requests a build."""
    with _PROBE_LOCK:
        if league_id in PROBE["pbp_candidates"]:
            return
        PROBE["pbp_candidates"][league_id] = {"pending": True}
    ev_code, ev = _get_status(f"{BASE}/event/{ev_id}")
    sport = ((((ev or {}).get("event") or {}).get("tournament") or {}).get("category") or {}).get("sport") or {}
    found = {}
    for path in _PBP_CANDIDATES:
        code, data = _get_status(f"{BASE}/event/{ev_id}/{path}")
        entry = {"status": code}
        if data:
            entry["keys"] = sorted(data.keys())
            # The first non-empty list under any key, shrunk — that is where a play list lives.
            for k, v in data.items():
                if isinstance(v, list) and v:
                    entry["sample_key"] = k
                    entry["sample"] = _shrink(v[0])
                    break
        found[path] = entry
    with _PROBE_LOCK:
        PROBE["pbp_candidates"][league_id] = {
            "event_id": ev_id,
            "event_status": ev_code,
            "sport": sport.get("slug") or sport.get("name"),
            "surfaces": found,
        }


def _get(url: str) -> dict | None:
    """One SofaScore GET. Returns None on anything but 200-with-JSON — callers treat that as
    'this piece of the league is absent', which is exactly what a 404 means on this API."""
    if cffi is None:
        raise RuntimeError("curl_cffi is required for the tracker; pip install curl_cffi")
    for attempt in range(2):
        try:
            r = cffi.get(url, impersonate="chrome", timeout=25)
        except Exception:
            _tally("exception")
            return None
        _tally(r.status_code)
        # ONE retry on a refusal, and only on a refusal. 403 and 429 from this source are a
        # throttle on the caller, not a statement about the URL -- a 404 means the piece genuinely
        # is not there and retrying it is just load. The pause is deliberate and short: this runs
        # across eight threads and fifteen leagues, and a long backoff on every refusal would push
        # the build past the cron interval, which is its own outage.
        if r.status_code in (403, 429) and attempt == 0:
            time.sleep(RETRY_PAUSE_S)
            continue
        break
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
            "logo": _logo(tid),
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


# --- v3: stats leaders, playoffs, per-game detail ---

def _safe(fn, *args):
    """Absence doctrine for the v3 mappers: a malformed 200 body degrades to 'not carried',
    exactly like a 404 does. A detail outage must never bubble into the feed build."""
    try:
        return fn(*args)
    except Exception:
        return None


# The categories a viewer expects first when the source carries them. Anything not listed arrives
# in the source's own order (each sport's most-viewed stat first), so non-football leagues pick
# sensibly without a per-sport table to maintain.
# Which leaderboard leads, per sport family. The source returns a dict whose key order is its own,
# and the first four are what the tab shows -- so without a preference a basketball league led with
# "assistTurnoverRatio" and a baseball one with "battingAtBats". The headline stat of each sport
# goes first; anything not named here still appears, just after these.
_STAT_PREFERENCE = (
    "goals", "assists", "rating",                                   # football
    "points", "rebounds", "steals", "blocks",                       # basketball
    "battingHomeRuns", "battingAvg", "battingHits", "pitchingWins",  # baseball
    "passingYards", "rushingYards", "receivingYards", "defensiveSacks",  # gridiron
)

_STAT_LABELS = {
    "rating": "Rating",
    "goals": "Top goals",
    "assists": "Top assists",
    "expectedGoals": "Expected goals",
    "expectedAssists": "Expected assists",
    "goalsAssistsSum": "Goals + assists",
    "topSpeed": "Top speed",
    "penaltyGoals": "Penalty goals",
    # The source's own camelCase is not a label. "BattingHomeRuns" over a leaderboard on a
    # television is a field name that escaped, and the fallback title-cases it into exactly that.
    "points": "Points", "rebounds": "Rebounds", "steals": "Steals", "blocks": "Blocks",
    "defensiveRebounds": "Defensive rebounds", "offensiveRebounds": "Offensive rebounds",
    "assistTurnoverRatio": "Assist / turnover", "doubleDoubles": "Double-doubles",
    "battingHomeRuns": "Home runs", "battingAvg": "Batting average", "battingHits": "Hits",
    "battingAtBats": "At bats", "battingOnBasePercentage": "On-base %", "pitchingWins": "Wins",
    "passingYards": "Passing yards", "rushingYards": "Rushing yards",
    "receivingYards": "Receiving yards", "defensiveSacks": "Sacks",
    "defensiveInterceptions": "Interceptions", "defensiveTotalTackles": "Tackles",
    "passingCompletionPercentage": "Completion %", "kickingFgMade": "Field goals",
    "saves": "Saves", "savePercentage": "Save %", "faceOffPercentage": "Face-off %",
    "blocked": "Blocked shots", "evenSavePercentage": "Even-strength save %",
}


def _humanise(key: str) -> str:
    """A source key as a label, for the ones [_STAT_LABELS] does not name.

    The old fallback upper-cased the first letter and stopped, so `passingTouchdowns` reached a
    television as "PassingTouchdowns". Splitting on the capitals is not a guess about the sport --
    it is just undoing camelCase, and it degrades to the same string for a single-word key."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).split()
    if not words:
        return key
    return " ".join([words[0].capitalize()] + [w.lower() for w in words[1:]])


def _display(v) -> str:
    """A stat value as its display string: ints bare, floats at 2dp, a lone trailing zero kept
    (7.0 is how the source shows a whole rating, not a formatting accident)."""
    if isinstance(v, str):
        return v
    if isinstance(v, float):
        return f"{v:.1f}" if not v % 1 else f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def _stats(ut: int, sid: int, names: dict[str, str] | None = None) -> list[dict]:
    """Up to [STAT_CATEGORIES] leaderboard categories x [STAT_ROWS] rows, or [] when the source
    carries no player stats for this season (a week-old 26/27 season serves nothing — that
    absence is the source's, and it is honest to carry no stats key at all)."""
    def _fetch(season_id: int) -> dict:
        # TWO SPELLINGS, and which one serves is decided by the sport, not by the season.
        #
        # Measured 2026-09-01 against the live API: `top-players/overall` is 404 for NBA, WNBA, NFL,
        # NHL and MLB on EVERY season back to 2022 — the walk-back could never have helped them,
        # because the route does not exist for those sports at all. `top-players/regularSeason`
        # answers 200 for all five with real leaderboards (nba: assists/blocks/…; nfl:
        # passingCompletionPercentage/defensiveSacks/…; mlb: battingAvg/battingHomeRuns/…).
        # Football keeps `overall`. Trying both costs one 404 on the sports that want the other.
        for suffix in ("top-players/overall", "top-players/regularSeason"):
            d = _get(f"{BASE}/unique-tournament/{ut}/season/{season_id}/{suffix}") or {}
            top = d.get("topPlayers") or {}
            if top:
                return top
        return {}

    top = _fetch(sid)
    tried: list[dict] = [{"season": sid, "answered": bool(top)}]
    if not top:
        # THE SEASON PICKER OPTIMISES FOR THE WRONG THING HERE.
        #
        # `_pick_season` takes the newest season carrying ANY signal, and an unstarted season with
        # nothing but upcoming fixtures qualifies. That is right for standings and fixtures -- it is
        # the season the tracker reports -- but player leaderboards only exist once games have been
        # PLAYED, so on WNBA/NBA/NFL the picked 26/27 season served nothing and the tab sat empty
        # while the previous season's leaderboards were sitting right there.
        #
        # So: walk back through the seasons and take the newest one that actually answers. Bounded
        # to a few, because a league with no stats in three seasons genuinely has none.
        seasons = (_get(f"{BASE}/unique-tournament/{ut}/seasons") or {}).get("seasons") or []
        ids = [x.get("id") for x in seasons if x.get("id")]
        try:
            start = ids.index(sid) + 1
        except ValueError:
            start = 0
        for prev in ids[start:start + 3]:
            top = _fetch(prev)
            tried.append({"season": prev, "answered": bool(top)})
            if top:
                break
        if not top:
            # Nothing in three seasons. Record the raw envelope so the reason is visible next build
            # rather than inferred: a 404 on the path is a different problem from a 200 with an
            # empty `topPlayers`, and the tab looks identical either way.
            code, raw = _get_status(f"{BASE}/unique-tournament/{ut}/season/{sid}/top-players/overall")
            with _PROBE_LOCK:
                PROBE["stats"][str(ut)] = {
                    "season": sid,
                    "tried": tried,
                    "overall_status": code,
                    "overall_keys": sorted((raw or {}).keys()),
                    "top_players_keys": sorted(((raw or {}).get("topPlayers") or {}).keys()),
                    "seasons_seen": ids[:6],
                }
    order = [k for k in _STAT_PREFERENCE if top.get(k)]
    order += [k for k in top if k not in order and top.get(k)]
    out: list[dict] = []
    for key in order[:STAT_CATEGORIES]:
        rows: list[dict] = []
        for r in top.get(key) or []:
            st, player = r.get("statistics") or {}, r.get("player") or {}
            team = r.get("team") or {}
            # TWO SHAPES for the player's club, and requiring the first one silently dropped an
            # entire sport. Football's rows carry a `team` node; MLB's carry `teamIds: [123]` and no
            # team at all, so `team.get("name")` was None on every row and the whole leaderboard was
            # discarded as malformed. The id resolves against the standings this league already
            # fetched — no extra request, and no row invented for a club we cannot name.
            team_id = str(team.get("id") or (r.get("teamIds") or [""])[0] or "")
            team_name = team.get("name") or (names or {}).get(team_id) or ""
            value = st.get(key)
            if value is None or not player.get("shortName") or not team_name:
                continue
            # Team crest, not a player headshot: it shares the standings' crest cache and the
            # row's secondary text is the team anyway.
            rows.append({
                "name": player.get("shortName"),
                "team": team_name,
                "logo": _logo(team_id),
                "value": _display(value),
            })
            if len(rows) >= STAT_ROWS:
                break
        if rows:
            out.append({"category": _STAT_LABELS.get(key) or _humanise(key), "rows": rows})
    return out


# incidentClass -> the detail line the match page shows. The raw enum ("regular") would leak the
# source's vocabulary onto a TV screen; unknown classes pass through as-is rather than guessed.
_GOAL_CLASSES = {
    "regular": "Goal", "penalty": "Penalty", "own": "Own goal",
    # Gridiron uses the same `goal` incidentType with its own classes. "fieldGoal" on a TV screen is
    # the source's enum leaking; measured on NCAAF 2026-09-01.
    "fieldGoal": "Field goal", "touchdown": "Touchdown", "safety": "Safety",
    "extraPoint": "Extra point", "twoPoint": "Two-point conversion",
}
_CARD_CLASSES = {"yellow": "Yellow card", "yellowRed": "Second yellow", "red": "Red card"}


def _incidents(ev_id: int, home_id: str, away_id: str) -> list[dict]:
    """Timeline rows for one finished game, newest-first as the source returns them. Empty for
    sports that carry no incidents (UFC, MLB) — the match page then hides the tab."""
    data = _get(f"{BASE}/event/{ev_id}/incidents") or {}
    out: list[dict] = []
    for i in data.get("incidents") or []:
        t = i.get("incidentType") or ""
        if t == "injuryTime":
            continue  # a display artifact of the source UI: a length, no player, no score
        row: dict = {"minute": i.get("time") or 0, "type": t}
        # A sport measured in innings or quarters has no minute. When the source names the period,
        # carry it: the app shows this string in the clock readout instead of a minute count.
        if i.get("periodName"):
            row["clock"] = i.get("periodName")
        # THE SCORE AT THIS MOMENT, straight from the source.
        #
        # The app was deriving a running score by counting goals and adding one each time, because
        # this was dropped here. That is right for football and WRONG for every sport where a score
        # moves by more than one: a basketball three-pointer read as "+1". SofaScore already carries
        # the pair on the incident, so the app never has to infer anything -- and the points earned
        # become a subtraction between consecutive rows rather than an assumption about the sport.
        if i.get("homeScore") is not None:
            row["home_score"] = i.get("homeScore")
        if i.get("awayScore") is not None:
            row["away_score"] = i.get("awayScore")
        if i.get("isHome") is not None:
            row["team_id"] = home_id if i.get("isHome") else away_id
            row["is_home"] = bool(i.get("isHome"))
        player = (i.get("player") or {}).get("shortName") or ""
        detail = ""
        if t == "goal":
            detail = _GOAL_CLASSES.get(i.get("incidentClass") or "", i.get("incidentClass") or "")
        elif t == "card":
            detail = _CARD_CLASSES.get(i.get("incidentClass") or "", i.get("incidentClass") or "")
        elif t == "substitution":
            player = (i.get("playerIn") or {}).get("shortName") or ""
            out_name = (i.get("playerOut") or {}).get("shortName") or ""
            detail = f"for {out_name}" if out_name else ""
        else:  # period and anything the source invents later: its own text is the best we have
            detail = i.get("text") or ""
        if player:
            row["player"] = player
        if detail:
            row["detail"] = detail
        out.append(row)
        if len(out) >= INCIDENT_CAP:
            break
    return out


# Comment rows that are chrome rather than play: the source's own scene-setting lines.
_COMMENT_SKIP = {"baseballInningHalfPitcher", "baseballInningHalf", "matchStarted", "periodStart"}


def _comments(ev_id: int, home_id: str, away_id: str) -> list[dict]:
    """Play-by-play from `/event/{id}/comments` — the surface for the sports `/incidents` refuses.

    Measured 2026-09-01: `/incidents` is 404 for baseball and UFC and serves for football-shaped
    sports; `/comments` serves 200 with 110 rows for an MLB game and 209 for an NCAAF one. It is
    where the source's own site reads its play list from, and nothing in this repo was asking for it.

    Two shapes come back and both are handled by asking each row what it carries rather than by
    branching on the sport:

      - baseball: `type: atBat`, a `player` node, `homeScore`/`awayScore` on the row, and
        `periodName: "1ST"` for the inning.
      - gridiron: `type` per play, no player node, the narration in `text`, `periodName: "4TH"`.

    ORDER IS NOT CONSISTENT between them — the MLB payload arrived oldest-first and the NCAAF one
    newest-first — so it is normalised here against the scores rather than assumed. The app wants
    newest-first, the same as `/incidents`.
    """
    data = _get(f"{BASE}/event/{ev_id}/comments") or {}
    rows = [c for c in (data.get("comments") or []) if (c.get("type") or "") not in _COMMENT_SKIP]
    if not rows:
        return []

    # NORMALISE TO OLDEST-FIRST, and do the whole walk in that direction.
    #
    # This is not tidiness. A scoring play is the row on which the score CHANGED, and walking
    # newest-first marks the row after it instead — you keep the ground-out that follows the home
    # run and drop the home run. The list is reversed at the end, because the app wants newest-first
    # exactly as `/incidents` serves it.
    #
    # Which end is which is asked, not assumed: the two payloads measured 2026-09-01 disagreed —
    # MLB arrived oldest-first, NCAAF newest-first. The end carrying the larger total is the newest.
    scored = [c for c in rows if c.get("homeScore") is not None and c.get("awayScore") is not None]
    if len(scored) >= 2:
        first = (scored[0].get("homeScore") or 0) + (scored[0].get("awayScore") or 0)
        last = (scored[-1].get("homeScore") or 0) + (scored[-1].get("awayScore") or 0)
        if last < first:
            rows = list(reversed(rows))

    def _row(c: dict) -> dict:
        r: dict = {"minute": 0, "type": "play", "detail": c.get("text") or ""}
        if c.get("periodName"):
            r["clock"] = c["periodName"]
        if c.get("isHome") is not None:
            r["team_id"] = home_id if c.get("isHome") else away_id
            r["is_home"] = bool(c.get("isHome"))
        name = (c.get("player") or {}).get("shortName") or ""
        if name:
            r["player"] = name
        if c.get("homeScore") is not None:
            r["home_score"] = c["homeScore"]
        if c.get("awayScore") is not None:
            r["away_score"] = c["awayScore"]
        return r

    # SCORING PLAYS AND PERIOD MARKS, not every pitch. Ninety-two at-bats is a spreadsheet, and a
    # timeline on a television has to be readable from a sofa. A play is kept when the source says
    # it scored, or — for the sports that carry no such flag — when the score moved on it.
    out: list[dict] = []
    seen_period = ""
    prev: tuple | None = None
    for c in rows:
        period = c.get("periodName") or ""
        if period and period != seen_period:
            seen_period = period
            out.append({"minute": 0, "type": "period", "detail": period, "clock": period})
        hs, as_ = c.get("homeScore"), c.get("awayScore")
        pair = (hs, as_) if hs is not None and as_ is not None else None
        moved = pair is not None and prev is not None and pair != prev
        if pair is not None:
            prev = pair
        if moved or c.get("isScoringPlay") or c.get("isGoal"):
            out.append(_row(c))
        if len(out) >= INCIDENT_CAP:
            break

    # A game whose plays carry no score at all — gridiron narration keeps it inside `text` — comes
    # back as nothing but period marks. Carrying the narration is better than carrying a skeleton.
    if sum(1 for r in out if r["type"] == "play") < 3:
        out = []
        seen_period = ""
        for c in rows[-INCIDENT_CAP:]:
            period = c.get("periodName") or ""
            if period and period != seen_period:
                seen_period = period
                out.append({"minute": 0, "type": "period", "detail": period, "clock": period})
            out.append(_row(c))

    out.reverse()
    return out


def _game_stats(ev_id: int) -> list[dict]:
    """The full-match comparison flattened to group/name/home/away rows. Only the 'ALL' period is
    taken — per-period splits double the bytes for a screen the design never breaks down."""
    data = _get(f"{BASE}/event/{ev_id}/statistics") or {}
    periods = data.get("statistics") or []
    whole = next((p for p in periods if p.get("period") == "ALL"), periods[0] if periods else None)
    out: list[dict] = []
    for g in (whole or {}).get("groups") or []:
        for it in g.get("statisticsItems") or []:
            if it.get("name") is None or it.get("home") is None or it.get("away") is None:
                continue
            out.append({"group": g.get("groupName") or "", "name": it.get("name"),
                        "home": it.get("home"), "away": it.get("away")})
    return out


def _lineups(ev_id: int) -> dict:
    """Both sides' starters, capped at [LINEUP_CAP]. Coach is carried when the source populates
    it (null on this surface today — measured in the module docstring), rating per player when
    the game has one. An empty lineup on both sides is not a lineup — returns {} so the key is
    omitted entirely."""
    data = _get(f"{BASE}/event/{ev_id}/lineups") or {}
    out: dict = {}
    for prefix, side in (("home", data.get("home") or {}), ("away", data.get("away") or {})):
        coach = side.get("coach") or {}
        cname = coach.get("shortName") or coach.get("name") or ""
        if cname:
            out[f"{prefix}_coach"] = cname
        players: list[dict] = []
        for p in side.get("players") or []:
            if p.get("substitute"):
                continue  # starters only: the design renders one XI column, not the bench
            pl, st = p.get("player") or {}, p.get("statistics") or {}
            if not pl.get("shortName"):
                continue
            row: dict = {"name": pl.get("shortName")}
            if p.get("shirtNumber") is not None:
                row["num"] = p.get("shirtNumber")
            if p.get("position"):
                row["pos"] = p.get("position")
            if st.get("rating") is not None:
                row["rating"] = _display(st.get("rating"))
            players.append(row)
            if len(players) >= LINEUP_CAP:
                break
        if players:
            out[f"{prefix}_players"] = players
    return out if out.get("home_players") or out.get("away_players") else {}


def _with_detail(recent: list[dict], league_id: str = "") -> list[dict]:
    """Attach incidents/statistics/lineups to the first [DETAIL_CAP] finished games — the games a
    viewer is most likely to open. Each piece attaches only when it actually served; older games
    and the whole `upcoming` list carry nothing by design."""
    attached = 0
    for ev in recent:
        if attached >= DETAIL_CAP:
            break
        if ev.get("status") != "ended":
            continue
        try:
            ev_id = int(str(ev.get("event_id") or "").rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        home_id, away_id = ev.get("home_id") or "", ev.get("away_id") or ""
        incs = _safe(_incidents, ev_id, home_id, away_id) or []
        if not incs:
            # `/incidents` 404s for baseball and UFC. `/comments` is where the source's own site
            # reads its play list, and it serves for both of the shapes this feed carries.
            incs = _safe(_comments, ev_id, home_id, away_id) or []
        if incs:
            ev["incidents"] = incs
        gstats = _safe(_game_stats, ev_id) or []
        if gstats:
            ev["statistics"] = gstats
        lineups = _safe(_lineups, ev_id) or {}
        if lineups:
            ev["lineups"] = lineups
        attached += 1
    return recent


def _playoff_match(e: dict) -> dict | None:
    """One bracket tie in the feed's shape, winner from the source's winnerCode with the score as
    the fallback (legs of two-legged ties end level: winner stays 'none' for them honestly)."""
    home, away = e.get("homeTeam") or {}, e.get("awayTeam") or {}
    hid, aid = home.get("id"), away.get("id")
    if not hid or not aid or not home.get("name") or not away.get("name"):
        return None
    hs = (e.get("homeScore") or {}).get("current") or 0
    as_ = (e.get("awayScore") or {}).get("current") or 0
    wc = e.get("winnerCode")
    winner = "none"
    if wc == 1 or (wc is None and hs > as_):
        winner = "home"
    elif wc == 2 or (wc is None and as_ > hs):
        winner = "away"
    return {
        "home": home.get("name"), "home_id": str(hid), "home_logo": _logo(hid), "home_score": hs,
        "away": away.get("name"), "away_id": str(aid), "away_logo": _logo(aid), "away_score": as_,
        "winner": winner, "status": _status(e),
    }


def _playoffs(ut: int, sid: int) -> dict | None:
    """The season's bracket derived from its events: knockout games are the only ones whose
    roundInfo carries a NAME ("Wild Card Round", "Round of 16"); regular-season games get a bare
    week number. Page 0 having no named round means the season has no bracket yet — one request,
    the common case for 30 of 35 weeks a year. Rounds order chronologically by earliest game."""
    base = f"{BASE}/unique-tournament/{ut}/season/{sid}"
    data = _get(f"{base}/events/last/0") or {}
    named0 = [e for e in data.get("events") or [] if ((e.get("roundInfo") or {}).get("name") or "")]
    if not named0:
        return None
    # (ts, row) so a two-legged tie's legs can be sorted into play order inside the round.
    by_round: dict[str, list[tuple[int, dict]]] = {}
    events = list(named0)
    for page in range(1, PLAYOFF_PAGES):  # page 0 already in hand
        nxt = _get(f"{base}/events/last/{page}") or {}
        evs = nxt.get("events") or []
        if not evs:
            break  # source ran out of history before the page cap
        events += [e for e in evs if ((e.get("roundInfo") or {}).get("name") or "")]
    for e in events:
        name = (e.get("roundInfo") or {}).get("name")
        m = _safe(_playoff_match, e)
        if name and m:
            by_round.setdefault(name, []).append((e.get("startTimestamp") or 0, m))
    if not by_round:
        return None
    rounds = [{"name": n,
               "matches": [m for _, m in sorted(by_round[n], key=lambda t: t[0])][:PLAYOFF_MATCH_CAP]}
              for n in sorted(by_round, key=lambda n: min(ts for ts, _ in by_round[n]))]
    return {"rounds": rounds[:PLAYOFF_ROUND_CAP]}


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
    entry = {
        "league_id": league_id,
        "season": year,
        "standings": _with_form(standings, recent),
        "recent": _with_detail(recent, league_id),
        "upcoming": upcoming,
    }
    # v3 keys attach only when the source actually served something for THIS season — absent
    # keys are how the app knows to show the quiet empty tab rather than fake numbers.
    if sid is not None:
        # The standings double as the team-name book for the leaderboards (see [_stats]).
        stats = _safe(_stats, ut, sid, {s["team_id"]: s["team"] for s in standings if s.get("team_id")}) or []
        if stats:
            entry["stats"] = stats
        playoffs = _safe(_playoffs, ut, sid)
        if playoffs:
            entry["playoffs"] = playoffs
    return entry


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
