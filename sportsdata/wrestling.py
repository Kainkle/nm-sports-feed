"""
Wrestling status: the one place a clock is allowed near `LIVE` — and even here it only draws the
boundaries.

`model.py` bans inferring LIVE from "now falls between start and end" for every sport, and the ban
is right: a postponed game, a rain delay, anything that finished early would wear a live badge that
lies. Weekly wrestling is the argued exception, and it earns it on three grounds:

1. **These are taped TV slots, not contests.** Raw airs because USA/Netflix aired it, at the minute
   the network published. There is no postponement channel, no weather, no "called early" — the
   failure mode the ban exists for does not exist here.
2. **The clock never sets LIVE alone.** It only brackets the window; the badge is set only when
   mut.st's fight section is simultaneously carrying the show with at least one playable source.
   Two independent confirmations, one of them the provider we would actually play through.
3. **Every other transition obeys the doctrine.** Before the window: SCHEDULED (a calendar fact).
   After: FINAL (an aired fact). Mid-window with no mut row: UNKNOWN — no badge, nobody misled.

The matcher here is deliberately NOT `mut.Entry`: these rows have no "vs", so the versus machinery
rejects them by design. Instead a row matches when its title contains exactly one weekly-show key
("raw", "smackdown", …) and the event's `card` contains the same one — a TNA Lockdown row therefore
cannot light up the iMPACT! event. `MutResolver.matchWrestling` in the app is this rule's twin: the
feed marks carried what the resolver resolves, and the two MUST NOT drift — any change to one is a
change to the other.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone

from . import mut
from .model import Event, Status

# The weekly shows' title keys, in the order they appear in `adapters.WRESTLING`. A title is scanned
# case-insensitively for these as whole words ("impact!" matches, "impactful" would not). Mirrored
# by MutResolver.SHOW_KEYS in the app — see the module docstring for why they cannot drift.
SHOW_KEYS = ("raw", "smackdown", "nxt", "dynamite", "collision", "impact")

_KEY_RE = re.compile(r"\b(" + "|".join(SHOW_KEYS) + r")\b")

# A weekly show runs 2-3 hours; TVmaze's `runtime` is the aired cut and is sometimes absent (Raw's
# future episodes). The floor keeps the window honest when it is, and the pad absorbs the network's
# overrun — wrestling shows start late and run long as a matter of course.
_RUNTIME_FLOOR_MIN = 120
_PAD_MIN = 45


def _keys_in(text: str) -> frozenset[str]:
    return frozenset(k for k in _KEY_RE.findall(text.lower()))


def _parse_utc(start_utc: str) -> datetime | None:
    """Same tolerance as `mut._pt_dates` — the feed omits seconds when they are zero."""
    s = start_utc.strip()
    if re.fullmatch(r"\S+T\d{2}:\d{2}Z", s):
        s = s[:-1] + ":00Z"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def fight_rows() -> list[tuple[str, str, int]] | None:
    """
    The fight section as `(title, PST date, playable-source count)` tuples.

    Reused from `mut.listing`'s fetch rather than its parsing: the Entry type rejects exactly these
    rows (no "vs" to split on), which is correct for versus sports and useless here. One extra GET
    per build is the whole cost.
    """
    req = urllib.request.Request(mut.API, headers={"User-Agent": mut.UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  wrestling: mut fight rows fetch failed: {type(e).__name__}: {e}")
        return None
    rows: list[tuple[str, str, int]] = []
    for section in body:
        for stream in section.get("streams") or []:
            if stream.get("groupId") != "fight":
                continue
            m = mut._PT_DATE.search(stream.get("time", ""))
            if not m:
                continue
            playable = sum(
                1 for s in stream.get("sources") or [] if s.get("embedUrl"))
            rows.append((stream.get("title", ""),
                         f"{m.group(3)}-{m.group(1)}-{m.group(2)}", playable))
    return rows


def _carried(ev: Event, rows: list[tuple[str, str, int]]) -> bool:
    """The event's one show key, mut's date, and at least one playable source — all three or no."""
    want = _keys_in(ev.card)
    if len(want) != 1:
        return False
    dates = mut._pt_dates(ev.start_utc)
    for title, date, playable in rows:
        if date in dates and _keys_in(title) == want and playable > 0:
            return True
    return False


def mark(events: list[Event], runtimes: dict[str, int]) -> tuple[int, int, int, int] | None:
    """
    Set status (and `has_live_source` where LIVE) on every wrestling event.

    A mut outage degrades exactly one thing — the LIVE transition — to UNKNOWN with the field left
    absent; SCHEDULED and FINAL still come from the window, because those are calendar facts the
    provider has no say in. Returns the counts for the build log, or None when there was nothing
    wrestling to mark and nothing was fetched.
    """
    wrest = [e for e in events if e.sport == "wrestling"]
    if not wrest:
        return None
    rows = fight_rows()
    now = datetime.now(timezone.utc)
    n_sched = n_live = n_final = n_unknown = 0
    for e in wrest:
        start = _parse_utc(e.start_utc)
        if start is None:
            continue
        end = start + timedelta(
            minutes=max(runtimes.get(e.event_id) or 0, _RUNTIME_FLOOR_MIN) + _PAD_MIN)
        if now < start:
            e.status = Status.SCHEDULED
            n_sched += 1
        elif now > end:
            e.status = Status.FINAL
            n_final += 1
        elif rows is not None and _carried(e, rows):
            e.status = Status.LIVE
            e.has_live_source = True
            n_live += 1
        else:
            # Mid-window, not carried (or mut unreachable). No badge — the honest answer when the
            # provider has not listed the show, and indistinguishable-on-purpose from "no data".
            e.status = Status.UNKNOWN
            if rows is not None:
                e.has_live_source = False
            n_unknown += 1
    return (n_sched, n_live, n_final, n_unknown)
