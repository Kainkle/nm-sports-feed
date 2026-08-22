"""
The mut.st intersection: which live games our live provider actually carries.

SofaScore (via the adapters) is the sole authority on WHAT IS LIVE — it is never inferred, never
borrowed from mut's listing, because mut does not delist finished games in any timely way and a
finished game with a live badge is the worst outcome here. What mut's listing answers is the other
half: of the games SofaScore says are live, which ones have streams to show? The app used to learn
that only at play time (the resolver walking the ladder to exhaustion), which is how "no stream for
this match" cards reached the live row. So at build time every LIVE event is marked
`has_live_source` — true only when mut lists the game WITH at least one embed source — and the
app's live row skips the ones marked false.

This module is a port of the matcher already verified in the field twice: `match_proto.py`
(2026-08-19, a full day of real fixtures — every listed matchup matched at 1.0, every non-match was
mut genuinely not carrying the game) and `MutResolver.kt` in the app (same match, resolving at play
time since 2026-08-19). The two MUST NOT drift apart: a game the resolver can play is a game this
module must have marked true, or the feed will hide a playable game. Any change to one is a change
to the other.

The listing is one plain GET — `https://mut.st/api/streams`, the same array mut's own home page
renders from. Not a page scrape: no browser, no HTML, and it stays fresh because it is fetched on
every build.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .model import Event, Status

API = "https://mut.st/api/streams"

# Browser UA — the API answers it plainly; no referer needed (measured, 2026-08-19).
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")

STOP = {"fc", "cf", "ac", "sc", "afc", "the", "de", "cd", "as"}

# The feed's `sport` taxonomy -> mut's group ids. A prior: mismatch discounts, never vetoes —
# an unknown or shifted taxonomy degrades to name+date matching, never to "no match".
#
# The two footballs point OPPOSITE ways and that is not a typo: the feed says "soccer" for the
# world game and "football" for the American one; mut says "football" for the world game and
# "american-football" for the American one.
CAT = {
    "baseball": "baseball",
    "basketball": "basketball",
    "soccer": "football",
    "football": "american-football",
    "mma": "fight",
    "motor-sports": "motor-sports",
    "australian-football": "afl",
    "rugby-league": "rugby",
    "rugby-union": "rugby",
}

PAIR_FLOOR = 0.6

# "A vs B", "A vs. B", "A at B" (mut uses "at" in NFL titles — measured 2026-08-19). Non-versus
# listings ("Little League Baseball World Series", "NFL Streams Schedule") have no sides to match.
_VS = re.compile(r"^(.+?)\s+(?:vs\.?|at)\s+(.+)$", re.IGNORECASE)
# mut prints the US Pacific date in its `time` field: "04:00 PM PST - (08/22/2026)".
_PT_DATE = re.compile(r"\((\d{2})/(\d{2})/(\d{4})\)")


def _norm(s: str) -> frozenset[str]:
    flat = re.sub(r"[^0-9a-zA-Z\s]", " ", unicodedata.normalize("NFKD", s)).lower()
    return frozenset(t for t in flat.split() if t and t not in STOP)


def _side_score(a: frozenset[str], b: frozenset[str]) -> float:
    """Token-set similarity of one club name vs another; containment covers "Athletics" vs "Oakland Athletics"."""
    if not a or not b:
        return 0.0
    jac = len(a & b) / len(a | b)
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return max(jac, len(small & big) / len(small))


def _split_title(title: str) -> tuple[frozenset[str], frozenset[str]] | None:
    m = _VS.match(title)
    if not m:
        return None
    a, b = _norm(m.group(1)), _norm(m.group(2))
    return (a, b) if a and b else None


def _pt_dates(start_utc: str) -> set[str]:
    """
    Both UTC-7 and UTC-8 (DST edge). The feed omits seconds when they are zero ("T02:00Z"), so the
    parse tolerates both lengths. Unparseable input yields no dates, which date-gates every
    candidate to zero: a blank beats a guessed match.
    """
    s = start_utc.strip()
    if re.fullmatch(r"\S+T\d{2}:\d{2}Z", s):
        s = s[:-1] + ":00Z"
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return set()
    return {(t + timedelta(hours=o)).date().isoformat() for o in (-7, -8)}


class Entry:
    """One mut stream row, pre-parsed — the category rides on the stream (`groupId`), not the section."""

    __slots__ = ("a", "b", "date", "group", "sources")

    def __init__(self, stream: dict[str, Any]) -> None:
        sides = _split_title(stream.get("title", ""))
        m = _PT_DATE.search(stream.get("time", ""))
        if not sides or not m:
            raise ValueError("not a dated versus listing")
        self.a, self.b = sides
        self.date = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
        self.group = stream.get("groupId", "")
        self.sources = [s for s in stream.get("sources") or [] if s.get("embedUrl")]


def listing() -> list[Entry] | None:
    """Fetch and pre-parse mut's whole listing. None means the API was unreachable."""
    req = urllib.request.Request(API, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  mut listing fetch failed: {type(e).__name__}: {e}")
        return None
    entries = []
    for section in body:
        for stream in section.get("streams") or []:
            try:
                entries.append(Entry(stream))
            except ValueError:
                continue
    return entries


def carrying(ev: Event, entries: list[Entry]) -> bool:
    """The resolver's match, verbatim: best-scoring dated matchup with at least one embed source."""
    pt_dates = _pt_dates(ev.start_utc)
    cat = CAT.get(ev.sport)
    ev_home, ev_away = _norm(ev.home.name), _norm(ev.away.name)
    # Best candidate FIRST, its sources judged after — exactly the resolver's order. Reversing
    # them (letting a lower-scoring entry win because it happens to carry sources) is a drift:
    # the resolver would still pick the sourceless entry and fail, and a `true` the resolver
    # cannot play is the dead end this module exists to remove.
    best_score = 0.0
    best_has_sources = False
    for e in entries:
        pair = max(
            min(_side_score(ev_home, e.a), _side_score(ev_away, e.b)),
            min(_side_score(ev_home, e.b), _side_score(ev_away, e.a)),
        )
        if pair < PAIR_FLOOR:
            continue
        date_ok = e.date in pt_dates
        cat_ok = cat is None or e.group == cat
        score = pair * (1.0 if date_ok else 0.0) * (1.0 if cat_ok else 0.6)
        if score > best_score:
            best_score = score
            best_has_sources = bool(e.sources)
    return best_score > 0.0 and best_has_sources


def mark_live(events: list[Event]) -> tuple[int, int] | None:
    """
    Set `has_live_source` on every LIVE event — True only when mut lists it with a live source.

    On a listing fetch failure returns None and touches nothing: the field stays absent, old-feed
    behaviour holds (the app shows on status alone), and the play-time resolver remains the
    backstop. Blanketing the live row with `false` during a mut outage would empty it entirely —
    a 30-second hiccup must not take every live game off the air.
    """
    live = [e for e in events if e.status is Status.LIVE]
    if not live:
        return (0, 0)
    entries = listing()
    if entries is None:
        return None
    carried = 0
    for e in live:
        e.has_live_source = carrying(e, entries)
        carried += 1 if e.has_live_source else 0
    return (carried, len(live))
