"""
Match official YouTube highlight uploads to fixtures in the feed.

## How this works, and why it is shaped this way

Two facts, both measured rather than assumed:

1. **A league's main channel is only about half game highlights.** MLB's 15 most recent uploads contained
   7 full-game highlights; the rest were single-play clips, promos and interviews. So the pipeline filters
   on *title shape first* and only then tries to match a fixture. Matching everything a channel posts would
   fill the hero with clips of one home run.

2. **Highlight titles are rigidly formatted**, which is what makes matching tractable at all:

       NATIONALS vs. METS: Official Full Game Highlights (August 16) | 2026 MLB Season

   Verified by search: that upload appeared roughly an hour after the game, on the official MLB channel.

## Free, keyless, no quota

Uses YouTube's per-channel RSS (`/feeds/videos.xml?channel_id=...`). No API key, no Data API quota, no
signup. The trade is that RSS is a **rolling 15-entry window** — on a full slate a league can push a
highlight off the feed within hours, which is why matches are accumulated into a store rather than being
recomputed from whatever the feed happens to hold right now.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import Event

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36"

# Official league channels. Resolved by searching YouTube and reading the channel off real results rather
# than guessing ids — an earlier guess resolved to "NHL Europe" instead of the main NHL channel, which
# would have produced a feed of the wrong region's clips.
CHANNELS: dict[str, str] = {
    "mlb": "UCoLrcjPV5PbUrUyXq5mjc_A",
    "nba": "UCWJ2lWNubArHWmf3FIHbfcQ",
    "wnba": "UCO9a_ryN_l7DIDS-VIt-zmw",
    "afl": "UCui2LJ0N-Zi7Uw_sHyE5kgQ",
    "nrl": "UCME0EifJ3Xm3mGmLdBz1Kyw",
    # CORRECTED. The id previously here was wrong and would have matched nothing forever while looking
    # perfectly plausible in the config - the failure mode of hardcoding an id you never verified.
    "ufc": "UCPQDDlGe7lbgmEJ0ge7a_JA",
    # RESOLVES TO "NHL Europe", NOT THE MAIN NHL CHANNEL.
    #
    # Confirmed by reading the RSS feed's own <title>: resolving @NHL from here returns the European
    # variant. It is left in because it does post highlights and NHL is out of season anyway, but the
    # regional channel may carry a different slate. Re-resolve from a US egress before the season starts.
    "nhl": "UCK3CHl-6e3hq4gQaz_TOyoQ",
}

# Competitions each channel is allowed to satisfy. A channel must never be matched against a sport it does
# not cover, or an MLB highlight could be linked to a hockey fixture on a name collision.
CHANNEL_COMPETITIONS: dict[str, set[str]] = {
    "mlb": {"mlb"},
    "nba": {"nba"},
    "wnba": {"wnba"},
    "nhl": {"nhl"},
    "afl": {"afl"},
    "nrl": {"rugby-league"},
    "ufc": {"ufc", "mma-sofa"},
    # MLS and LaLiga deliberately absent: their handles did not resolve to a channel id, and soccer
    # highlight rights are fragmented and geo-restricted per competition. An unverified id here would
    # silently match nothing while looking configured.
}

# The shape gate. Both must be present or the upload is not a game highlight.
_HIGHLIGHT = re.compile(r"highlight", re.I)
_VERSUS = re.compile(r"\b(?:vs\.?|at|@)\b", re.I)
_TITLE_DATE = re.compile(
    r"\((?P<month>January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(?P<day>\d{1,2})\)",
    re.I,
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


def _entries(channel_id: str) -> list[dict]:
    """Recent uploads as (video_id, title, published). RSS holds only the latest 15."""
    try:
        xml = _get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    except Exception:
        return []
    out = []
    for block in xml.split("<entry>")[1:]:
        vid = re.search(r"<yt:videoId>(.*?)</yt:videoId>", block)
        title = re.search(r"<media:title>(.*?)</media:title>", block)
        pub = re.search(r"<published>(.*?)</published>", block)
        if vid and title:
            out.append({
                "video_id": vid.group(1),
                "title": title.group(1),
                "published": pub.group(1) if pub else "",
            })
    return out


def _title_date(title: str, year: int) -> str | None:
    """
    The date written in the title, which is trusted over the RSS `published` field.

    One MLB entry came back published `2005-12-25` — a feed quirk that would have filed a current highlight
    under a twenty-year-old date. The title's own date is the reliable one.
    """
    m = _TITLE_DATE.search(title)
    if not m:
        return None
    month = _MONTHS.get(m.group("month").lower())
    if not month:
        return None
    return f"{year:04d}-{month:02d}-{int(m.group('day')):02d}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def match(events: list[Event], today: str) -> dict[str, dict]:
    """
    Map `event_id -> {video_id, title}` for every fixture with a confidently matched highlight.

    **Both teams must match, or the fixture is skipped.** A one-sided match is how a highlight ends up on
    the wrong game — "Yankees" alone appears in several fixtures on a given day.
    """
    year = int(today[:4])
    by_comp: dict[str, list[Event]] = {}
    for e in events:
        by_comp.setdefault(e.competition_id, []).append(e)

    # Fetch every channel feed up front, concurrently. Seven sequential RSS round-trips added real time to
    # a build that already runs on a timer.
    with ThreadPoolExecutor(max_workers=6) as pool:
        feeds = dict(zip(CHANNELS, pool.map(_entries, CHANNELS.values())))

    found: dict[str, dict] = {}
    for channel, cid in CHANNELS.items():
        comps = CHANNEL_COMPETITIONS.get(channel, set())
        candidates = [e for c in comps for e in by_comp.get(c, [])]
        if not candidates:
            continue
        for entry in feeds.get(channel, []):
            title = entry["title"]
            # Shape gate first: this is what discards the ~half of uploads that are not game highlights.
            if not (_HIGHLIGHT.search(title) and _VERSUS.search(title)):
                continue
            tdate = _title_date(title, year)
            norm_title = _norm(title)
            for ev in candidates:
                if ev.event_id in found:
                    continue
                # Date must agree when the title states one. Highlights land after the game, so a title
                # dated differently from the fixture is a different fixture.
                if tdate and ev.start_utc[:10] != tdate:
                    continue
                if not tdate and not _within_days(ev.start_utc, today, 2):
                    continue
                if _matches_both(norm_title, ev):
                    found[ev.event_id] = {"video_id": entry["video_id"], "title": title}
                    break
    return found


def _within_days(start_utc: str, today: str, days: int) -> bool:
    try:
        a = datetime.strptime(start_utc[:10], "%Y-%m-%d")
        b = datetime.strptime(today, "%Y-%m-%d")
    except ValueError:
        return False
    return abs((a - b).days) <= days


def _matches_both(norm_title: str, ev: Event) -> bool:
    """Every alias set is tried; both sides must land."""
    def side(team) -> bool:
        names = [team.name, team.abbrev, *team.aliases]
        return any(len(n) >= 3 and _norm(n) in norm_title for n in names if n)
    return side(ev.home) and side(ev.away)


def apply(events: list[Event], today: str, store_path: Path) -> int:
    """
    Match, merge into the persistent store, and stamp `highlight_video_id` onto events.

    **Accumulating is the point.** RSS holds 15 entries; a busy league pushes a highlight out of that window
    within hours. Without a store, a fixture would gain a highlight and then silently lose it on the next
    build — which looks like a bug and is really just a rolling feed.
    """
    store: dict[str, dict] = {}
    if store_path.exists():
        store = json.loads(store_path.read_text() or "{}")

    store.update(match(events, today))

    # Forget anything old enough that no screen will show it again. Unbounded growth would eventually make
    # every build read and rewrite a very large file for no benefit.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    ids_recent = {e.event_id for e in events if e.start_utc[:10] >= cutoff}
    store = {k: v for k, v in store.items() if k in ids_recent or k in store}

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, indent=2))

    hit = 0
    for e in events:
        h = store.get(e.event_id)
        if h:
            e.highlight_video_id = h["video_id"]
            hit += 1
    return hit
