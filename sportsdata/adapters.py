"""
One adapter per source. Each fetches, normalises, and emits canonical [Event]s.

Both adapters here point at **official league APIs**, not at downstream aggregators. That is the core
decision recorded in `docs/SPORTS_AGGREGATOR_PLAN.md`: ESPN and similar ingest from the leagues and add
their own errors, which is why an earlier attempt found discrepancies. Going upstream costs one adapter per
league and is strictly more accurate.

Every endpoint below was verified by direct probe — see `docs/SPORTS_SOURCES_VERIFIED.md`.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .model import Event, Status, Team, utc

# There is no single correct request shape, which is why this is a per-adapter setting rather than a
# constant. Verified: MLB answers a bare request; NHL returns 403 without a User-Agent; ESPN refuses a
# curl-style UA and accepts no headers at all. Assuming "more headers is safer" is wrong in both directions.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class SourceError(RuntimeError):
    """Raised so `build.py` can record a source as failed without aborting every other source."""


def _get(url: str, headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} for {url}") from e
    except Exception as e:
        raise SourceError(f"{type(e).__name__} for {url}: {e}") from e


class Adapter:
    """Contract: `key`, `competition_name`, and `fetch(date)` returning canonical events."""

    key: str = ""
    sport: str = ""
    competition_id: str = ""
    competition_name: str = ""

    def fetch(self, date: str) -> list[Event]:  # date is YYYY-MM-DD
        raise NotImplementedError


class MlbAdapter(Adapter):
    """
    MLB — `statsapi.mlb.com`. Official, free, no key, answers a bare request.

    Status comes from `abstractGameState` with `detailedState` consulted for the exceptional cases. That
    split matters: `abstractGameState` only ever says Preview/Live/Final, so a postponed game reads as
    "Preview" there and would be published as a normal upcoming fixture. `detailedState` is the field that
    actually says "Postponed", and checking it is the difference between an accurate feed and one that
    invites viewers to a game that is not happening.
    """

    key = "mlb"
    sport = "baseball"
    competition_id = "mlb"
    competition_name = "MLB"

    URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
    TEAMS_URL = "https://statsapi.mlb.com/api/v1/teams?sportId=1"

    def __init__(self) -> None:
        self._teams: dict[int, dict] | None = None

    def _team_index(self) -> dict[int, dict]:
        """
        Club id -> full club record, fetched once per build.

        **This exists because the schedule payload is alias-poor.** It carries only the full club name — no
        abbreviation, no short name — which was measured: 0 of 41 events had an abbreviation and every one
        had a single alias. Aliases are the entire basis of matching a fixture to a provider's stream title,
        and a provider writing "BAL @ TB" cannot be matched against "Baltimore Orioles" alone.

        The teams endpoint carries `abbreviation`, `teamName`, `shortName` and `franchiseName`, so one extra
        request per build turns one alias into four or five. This is the ID crosswalk the plan calls the
        durable asset, in its smallest useful form.
        """
        if self._teams is None:
            try:
                data = _get(self.TEAMS_URL)
                self._teams = {t["id"]: t for t in data.get("teams", []) if t.get("id")}
            except SourceError:
                # Non-fatal: fall back to schedule-only names. A thinner alias set is degraded matching,
                # not a broken feed, and it must not take the whole source down.
                self._teams = {}
        return self._teams

    def fetch(self, date: str) -> list[Event]:
        data = _get(self.URL.format(date=date))
        idx = self._team_index()
        out: list[Event] = []
        for day in data.get("dates", []):
            for g in day.get("games", []):
                st = g.get("status", {}) or {}
                out.append(
                    Event(
                        event_id=f"mlb:{g.get('gamePk')}",
                        sport=self.sport,
                        competition_id=self.competition_id,
                        competition_name=self.competition_name,
                        start_utc=utc(g.get("gameDate", "")),
                        status=self._status(st),
                        home=self._team(g["teams"]["home"], idx),
                        away=self._team(g["teams"]["away"], idx),
                        source=self.key,
                        source_status=f"{st.get('abstractGameState','')}/{st.get('detailedState','')}",
                        venue=(g.get("venue") or {}).get("name", ""),
                        game_number=int(g.get("gameNumber") or 1),
                    )
                )
        return out

    @staticmethod
    def _status(st: dict) -> Status:
        detailed = (st.get("detailedState") or "").lower()
        # Exceptional states first — they hide behind an otherwise-normal abstract state.
        if "postpon" in detailed:
            return Status.POSTPONED
        if "cancel" in detailed:
            return Status.CANCELLED
        if "suspend" in detailed or "delay" in detailed:
            return Status.SUSPENDED
        abstract = (st.get("abstractGameState") or "").lower()
        if abstract == "live":
            return Status.LIVE
        if abstract == "final":
            return Status.FINAL
        if abstract == "preview":
            return Status.SCHEDULED
        return Status.UNKNOWN

    @staticmethod
    def _team(side: dict, idx: dict[int, dict] | None = None) -> Team:
        t = side.get("team", {}) or {}
        tid_raw = t.get("id")
        # Merge the richer club record over the schedule's thin one.
        if idx and tid_raw in idx:
            t = {**t, **idx[tid_raw]}
        name = t.get("name", "")
        # MLB gives no logo in the schedule payload. The club id is stable and the league publishes a
        # per-club SVG at a predictable path, so the URL is derived rather than left empty — the box needs
        # a badge to composite a matchup card and an empty field means a text-only fallback.
        tid = t.get("id")
        logo = f"https://www.mlbstatic.com/team-logos/{tid}.svg" if tid else None
        aliases = [a for a in {name, t.get("teamName") or "", t.get("shortName") or "",
                                t.get("abbreviation") or "", t.get("franchiseName") or "",
                                t.get("clubName") or ""} if a]
        return Team(
            id=f"mlb:{tid}",
            name=name,
            abbrev=t.get("abbreviation") or "",
            logo=logo,
            aliases=sorted(aliases),
        )


class NhlAdapter(Adapter):
    """
    NHL — `api-web.nhle.com`. Official, free, no key, but **403s without a User-Agent**.

    Returns a seven-day `gameWeek` from a single call, so a week of schedule costs one request. The payload
    also carries official light/dark SVG badge URLs, which removes the badge-sourcing problem for this
    league entirely.
    """

    key = "nhl"
    sport = "hockey"
    competition_id = "nhl"
    competition_name = "NHL"

    URL = "https://api-web.nhle.com/v1/schedule/{date}"

    def fetch(self, date: str) -> list[Event]:
        data = _get(self.URL.format(date=date), headers={"User-Agent": BROWSER_UA})
        out: list[Event] = []
        for day in data.get("gameWeek", []):
            for g in day.get("games", []):
                out.append(
                    Event(
                        event_id=f"nhl:{g.get('id')}",
                        sport=self.sport,
                        competition_id=self.competition_id,
                        competition_name=self.competition_name,
                        start_utc=utc(g.get("startTimeUTC", "")),
                        status=self._status(g),
                        home=self._team(g.get("homeTeam", {})),
                        away=self._team(g.get("awayTeam", {})),
                        source=self.key,
                        source_status=f"{g.get('gameState','')}/{g.get('gameScheduleState','')}",
                        venue=(g.get("venue") or {}).get("default", "") if isinstance(g.get("venue"), dict) else "",
                    )
                )
        return out

    @staticmethod
    def _status(g: dict) -> Status:
        # Schedule state overrides game state: a postponed game can still carry a forward-looking gameState.
        sched = (g.get("gameScheduleState") or "").upper()
        if sched == "PPD":
            return Status.POSTPONED
        if sched in ("CNCL", "CANCELLED"):
            return Status.CANCELLED
        if sched == "SUSP":
            return Status.SUSPENDED
        state = (g.get("gameState") or "").upper()
        # LIVE and CRIT are both in-progress; CRIT means late-and-close, not a different phase.
        if state in ("LIVE", "CRIT"):
            return Status.LIVE
        if state in ("OFF", "FINAL"):
            return Status.FINAL
        if state in ("FUT", "PRE"):
            return Status.SCHEDULED
        return Status.UNKNOWN

    @staticmethod
    def _team(t: dict) -> Team:
        place = (t.get("placeName") or {}).get("default", "") if isinstance(t.get("placeName"), dict) else ""
        common = (t.get("commonName") or {}).get("default", "") if isinstance(t.get("commonName"), dict) else ""
        full = " ".join(x for x in (place, common) if x).strip()
        abbrev = t.get("abbrev") or ""
        aliases = [a for a in {full, common, place, abbrev} if a]
        return Team(
            id=f"nhl:{t.get('id')}",
            name=full or common or abbrev,
            abbrev=abbrev,
            logo=t.get("logo"),
            logo_dark=t.get("darkLogo"),
            aliases=sorted(aliases),
        )


ADAPTERS: list[Adapter] = [MlbAdapter(), NhlAdapter()]


class EspnAdapter(Adapter):
    """
    ESPN's site API — **one adapter, many competitions.**

    ESPN exposes a single `{sport}/{league}` path space, so covering twelve of our categories is twelve
    instances of this class rather than twelve implementations. The league identifiers were enumerated from
    ESPN's own HATEOAS taxonomy rather than guessed — `rugby/scrum` 403s while `rugby/270557` works, and
    several competitions use numeric slugs. See `docs/SPORTS_SOURCES_VERIFIED.md`.

    **Send no headers.** Verified: a bare request succeeds and a curl-style `User-Agent` is refused. That is
    the opposite of NHL, which 403s without one — there is no universally safe request shape.

    Two things this source gives that the official league APIs do not: **official brand colours** on each
    team, and a live `detail` string ("Bottom 7th"). Both go straight into the card.
    """

    URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates={ymd}"

    def __init__(self, sport: str, league: str, competition_id: str, competition_name: str) -> None:
        self._sport = sport
        self._league = league
        self.key = f"espn:{sport}/{league}"
        self.sport = competition_id.split(":")[0] if ":" in competition_id else sport
        self.competition_id = competition_id
        self.competition_name = competition_name

    def fetch(self, date: str) -> list[Event]:
        ymd = date.replace("-", "")
        data = _get(self.URL.format(sport=self._sport, league=self._league, ymd=ymd))
        out: list[Event] = []
        for e in data.get("events", []) or []:
            comps = e.get("competitions") or []
            if not comps:
                continue
            c = comps[0]
            sides = {s.get("homeAway"): s for s in (c.get("competitors") or [])}
            home, away = sides.get("home"), sides.get("away")
            card = ""
            if not home or not away:
                # Individual sports split: golf/tennis draws have no two-sided shape at all
                # (skipped — they need their own presentation), but FIGHT CARDS and RACES are
                # athlete-listed events that simply omit `homeAway` (measured identically on both:
                # MMA 2026-08-19, racing 2026-08-19 — every competitor homeAway=None, team=None).
                # Synthesize the pair from listing order — for a card that is the two fighters, for
                # a race it is the first two of twenty drivers, placeholders and nothing more — and
                # keep the event name in `card`, because THAT is the identity: a card is named for
                # headliners the API may not list, and a race's drivers say nothing about which
                # race it is. Display and matching key off `card`, not the synthesized pair.
                athletes = [s for s in (c.get("competitors") or []) if s.get("athlete")]
                if self._sport not in ("mma", "racing") or len(athletes) < 2:
                    continue
                away, home = athletes[0], athletes[1]
                card = e.get("name", "") or ""
            stype = (e.get("status") or {}).get("type") or {}
            out.append(
                Event(
                    event_id=f"espn:{self._sport}:{e.get('id')}",
                    sport=self.sport,
                    competition_id=self.competition_id,
                    competition_name=self.competition_name,
                    start_utc=utc(e.get("date", "")),
                    status=self._status(stype),
                    home=self._team(home),
                    away=self._team(away),
                    source=self.key,
                    source_status=f"{stype.get('state','')}/{stype.get('description','')}",
                    venue=(c.get("venue") or {}).get("fullName", ""),
                    detail=stype.get("detail", "") or "",
                    card=card,
                )
            )
        return out

    @staticmethod
    def _status(stype: dict) -> Status:
        desc = f"{stype.get('description','')} {stype.get('name','')}".lower()
        if "postpon" in desc:
            return Status.POSTPONED
        if "cancel" in desc:
            return Status.CANCELLED
        if "suspend" in desc or "delay" in desc:
            return Status.SUSPENDED
        state = (stype.get("state") or "").lower()
        if state == "in":
            return Status.LIVE
        if state == "post":
            return Status.FINAL
        if state == "pre":
            return Status.SCHEDULED
        return Status.UNKNOWN

    @staticmethod
    def _team(side: dict) -> Team:
        t = side.get("team") or side.get("athlete") or {}
        name = t.get("displayName") or t.get("name") or ""
        aliases = [a for a in {name, t.get("name") or "", t.get("shortDisplayName") or "",
                               t.get("fullName") or "", t.get("location") or "",
                               t.get("abbreviation") or ""} if a]
        logo = t.get("logo")
        if not logo and isinstance(t.get("flag"), dict):
            logo = t["flag"].get("href")  # MMA athletes carry a country flag, not a crest
        return Team(
            id=f"espn:{t.get('id')}",
            name=name,
            abbrev=t.get("abbreviation") or "",
            logo=logo,
            aliases=sorted(aliases),
            color=(t.get("color") or None),
        )


class MotoGpAdapter(Adapter):
    """
    MotoGP — `api.motogp.pulselive.com`. Free, no key, bare request.

    Emitted as one event per race weekend rather than per session. Practice, qualifying, sprint and race are
    separate broadcasts, so per-session is the eventual right granularity — but the seasons endpoint models
    weekends, and inventing sessions we cannot verify would be fabricating schedule data.
    """

    key = "motogp"
    sport = "motorsport"
    competition_id = "motogp"
    competition_name = "MotoGP"

    URL = "https://api.motogp.pulselive.com/motogp/v1/events?seasonYear={year}"

    def fetch(self, date: str) -> list[Event]:
        year = date[:4]
        data = _get(self.URL.format(year=year))
        rows = data if isinstance(data, list) else data.get("events", [])
        out: list[Event] = []
        for e in rows:
            start = e.get("date_start") or e.get("dateStart") or ""
            if not start[:10] == date:
                continue
            name = e.get("name") or e.get("short_name") or "Race"
            circuit = ((e.get("circuit") or {}).get("name")) if isinstance(e.get("circuit"), dict) else ""
            out.append(
                Event(
                    event_id=f"motogp:{e.get('id')}",
                    sport=self.sport,
                    competition_id=self.competition_id,
                    competition_name=self.competition_name,
                    start_utc=utc(start),
                    # The seasons feed is a calendar, not a live feed. UNKNOWN is honest: it means the app
                    # shows a schedule entry and no LIVE badge, which is exactly right for a source that
                    # cannot say whether the race is running.
                    status=Status.UNKNOWN,
                    home=Team(id=f"motogp:{e.get('id')}", name=name),
                    away=Team(id="motogp:circuit", name=circuit or "Circuit"),
                    source=self.key,
                    source_status="calendar",
                    venue=circuit or "",
                )
            )
        return out


# Every competition verified in docs/SPORTS_SOURCES_VERIFIED.md. Adding one is a line here.
ESPN_COMPETITIONS = [
    ("football", "nfl", "nfl", "NFL"),
    ("football", "college-football", "ncaaf", "College Football"),
    ("basketball", "nba", "nba", "NBA"),
    ("basketball", "wnba", "wnba", "WNBA"),
    ("basketball", "mens-college-basketball", "ncaam", "College Basketball"),
    # MLB and NHL deliberately ABSENT here. Both have official league adapters above, and an official
    # source beats a downstream aggregator on the same competition — running both produced the same fixture
    # twice in the feed, which is the duplication a canonical-source rule exists to prevent.
    ("soccer", "eng.1", "eng.1", "Premier League"),
    ("soccer", "esp.1", "esp.1", "La Liga"),
    ("soccer", "ita.1", "ita.1", "Serie A"),
    ("soccer", "ger.1", "ger.1", "Bundesliga"),
    ("soccer", "usa.1", "usa.1", "MLS"),
    ("soccer", "uefa.champions", "uefa.champions", "Champions League"),
    ("australian-football", "afl", "afl", "AFL"),
    ("rugby", "270557", "urc", "United Rugby Championship"),
    ("rugby-league", "3", "rugby-league", "Rugby League"),
    ("mma", "ufc", "ufc", "UFC"),
    ("racing", "f1", "f1", "Formula 1"),
    ("racing", "nascar-premier", "nascar", "NASCAR Cup Series"),
    # Registered for fullraces.com replays 2026-08-19: ESPN carries the series (`irl`), the site
    # posts its races, and the one line here is all the fixture side needed.
    ("racing", "irl", "indycar", "IndyCar Series"),
]

ADAPTERS = [MlbAdapter(), NhlAdapter(), MotoGpAdapter()] + [
    EspnAdapter(sport, league, cid, name) for sport, league, cid, name in ESPN_COMPETITIONS
]


class SofaScoreAdapter(Adapter):
    """
    SofaScore — the widest coverage found, and the only one requiring a browser TLS fingerprint.

    ## Why this needs `curl_cffi`

    Every header profile in `tools/probe_source.py` returns 403 here, including a full browser header set.
    A real Chromium returns 200. That difference is **TLS fingerprinting**: the server inspects the shape of
    the handshake itself, which no combination of headers can imitate. `curl_cffi` with `impersonate` sends
    a genuine Chrome fingerprint, which is why it works where urllib cannot.

    ## What it buys

    One uniform path space — `/sport/{slug}/scheduled-events/{date}` and `/sport/{slug}/events/live` —
    across sports that have no other free source: darts, snooker, cricket, cycling, volleyball, combat.
    These are the categories that ESPN's taxonomy simply does not contain.

    ## Terms

    **SofaScore states publicly that it does not provide data through API endpoints, citing agreements with
    its data suppliers.** This adapter uses their internal endpoints anyway, on the project owner's explicit
    instruction after that was raised. Recording it here so the decision is visible at the point of use
    rather than buried in a chat log — it is a different posture from the other adapters, all of which use
    open or undocumented-but-unprotected endpoints.
    """

    BASE = "https://api.sofascore.com/api/v1"
    # Badges come from a sibling host and are keyed by team id.
    LOGO = "https://api.sofascore.app/api/v1/team/{id}/image"

    def __init__(self, slug: str, competition_id: str, competition_name: str, sport: str) -> None:
        self._slug = slug
        self.key = f"sofascore:{slug}"
        self.sport = sport
        self.competition_id = competition_id
        self.competition_name = competition_name

    def _fetch_json(self, url: str) -> dict:
        try:
            from curl_cffi import requests as cffi
        except ImportError as e:
            raise SourceError("curl_cffi is required for SofaScore; pip install curl_cffi") from e
        try:
            r = cffi.get(url, impersonate="chrome", timeout=25)
        except Exception as e:
            raise SourceError(f"{type(e).__name__} for {url}: {e}") from e
        if r.status_code != 200:
            raise SourceError(f"HTTP {r.status_code} for {url}")
        return r.json()

    def fetch(self, date: str) -> list[Event]:
        out: list[Event] = []
        seen: set[str] = set()
        # Scheduled for the day, then live. Live is fetched separately because a match that started
        # yesterday and is still running does not appear under today's scheduled list.
        for url in (
            f"{self.BASE}/sport/{self._slug}/scheduled-events/{date}",
            f"{self.BASE}/sport/{self._slug}/events/live",
        ):
            try:
                data = self._fetch_json(url)
            except SourceError:
                continue
            for e in data.get("events", []) or []:
                ev = self._event(e)
                if not ev or ev.event_id in seen:
                    continue
                # DATE GUARD. `/events/live` is not scoped to a date and returned MMA fixtures dated 2024
                # and 2025 into a feed built for today — stale rows that would have rendered as current
                # cards. Anything outside a day either side of the requested date is dropped.
                if not self._near(ev.start_utc, date):
                    continue
                seen.add(ev.event_id)
                out.append(ev)
        return out

    @staticmethod
    def _near(start_utc: str, date: str, days: int = 1) -> bool:
        try:
            d0 = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            d1 = datetime.strptime(start_utc[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return False
        return abs((d1 - d0).days) <= days

    def _event(self, e: dict) -> Event | None:
        home, away = e.get("homeTeam") or {}, e.get("awayTeam") or {}
        if not home.get("name") or not away.get("name"):
            return None
        ts = e.get("startTimestamp")
        if not ts:
            return None
        start = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        st = e.get("status") or {}
        tour = e.get("tournament") or {}
        return Event(
            event_id=f"sofa:{e.get('id')}",
            sport=self.sport,
            competition_id=self.competition_id,
            competition_name=self.competition_name,
            start_utc=start,
            status=self._status(st),
            home=self._team(home),
            away=self._team(away),
            source=self.key,
            source_status=f"{st.get('type','')}/{st.get('description','')}",
            # SofaScore's description is the live clock/period — "1st half", "Set 3". Exactly the string
            # the card wants while a fixture is running.
            detail=st.get("description", "") if st.get("type") == "inprogress" else "",
            venue=(tour.get("name") or ""),
        )

    @staticmethod
    def _status(st: dict) -> Status:
        t = (st.get("type") or "").lower()
        desc = (st.get("description") or "").lower()
        if "postpon" in desc:
            return Status.POSTPONED
        if t == "canceled" or "cancel" in desc:
            return Status.CANCELLED
        if t == "suspended" or "suspend" in desc or "interrupt" in desc:
            return Status.SUSPENDED
        if t == "inprogress":
            return Status.LIVE
        if t == "finished":
            return Status.FINAL
        if t == "notstarted":
            return Status.SCHEDULED
        return Status.UNKNOWN

    @classmethod
    def _team(cls, t: dict) -> Team:
        tid = t.get("id")
        name = t.get("name") or ""
        aliases = [a for a in {name, t.get("shortName") or "", t.get("nameCode") or ""} if a]
        return Team(
            id=f"sofa:{tid}",
            name=name,
            abbrev=t.get("nameCode") or "",
            logo=cls.LOGO.format(id=tid) if tid else None,
            aliases=sorted(aliases),
        )


# The categories ESPN's taxonomy does not contain at all. This is the whole reason SofaScore is here.
SOFA_SPORTS = [
    ("darts", "darts", "Darts", "darts"),
    ("snooker", "snooker", "Snooker", "snooker"),
    ("cricket", "cricket", "Cricket", "cricket"),
    ("cycling", "cycling", "Cycling", "cycling"),
    ("volleyball", "volleyball", "Volleyball", "volleyball"),
    ("mma", "mma-sofa", "MMA", "mma"),
    ("boxing", "boxing", "Boxing", "boxing"),
    # Table tennis deliberately NOT included: it is not one of the app's 23 categories. It was added
    # speculatively, returned 22 live fixtures, and would have shipped a sport nobody asked for.
]

ADAPTERS = ADAPTERS + [
    SofaScoreAdapter(slug, cid, name, sport) for slug, cid, name, sport in SOFA_SPORTS
]
