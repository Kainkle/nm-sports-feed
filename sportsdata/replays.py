"""
Match full-event replays from replay sites to fixtures in the feed.

## The provider, characterised by probe (docs/captures/replays/, 2026-08-18)

**mlblive.net** is a uCoz `publ` catalog, fully server-rendered — every listing and entry page is
parseable with plain HTTP, no JS. That matters because **the site's own pages main-frame-redirect a
real browser to an Adsterra fake-browser preland** (`boost-you-browser.com/...`, JS-injected after
load; a plain HTTP client never sees it). Consequences, both load-bearing:

1. Scraping it with urllib is safe and stable — no ads execute, no redirects fire.
2. **The app must never load an mlblive.net page.** It plays the video *embed* the entry carries
   (an ok.ru player), extracted here and shipped in the feed as `replay_url`.

## Title grammar (verified across every entry probed)

    St. Louis Cardinals @ Cincinnati Reds Game 2 - MLB Full Game Replay - August 17, 2026
    ^away                        ^home           ^optional     ^literal            ^US date

The date is the site's local (US) date, so matching converts the fixture's `start_utc` to
America/New_York before comparing — a 9:40pm PT game is "August 17" on the site but August 18 in
UTC, and comparing raw UTC dates would miss every West Coast night game.

## Playback facts, measured (probe_mlb_autoplay.py)

The ok.ru embed is gesture-gated like videasy: it resolves and prebuffers on load but mounts no
`<video>` until a click. One synthesized centre click starts it (MSE, `blob:` src, quality ladders
from 240p; segments on `*.vkuser.net` — the verdict host for any blocklist work). Full games run
~2.5h. Native resolve is not available anonymously (`/dk?cmd=videoPlayerMetadata` 302s without a
browser session), so the play path is a WebView pointed at the embed.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .model import Event

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36"

# The 2026 season category. uCoz paginates with `?pageN`, newest first (~11-16 entries/page).
LISTING_URL = "https://mlblive.net/2026-mlb-full-game-replays"
# Enough pages to cover the fixture backfill window with margin, and a hard stop so a category
# redesign that breaks date parsing cannot turn into crawling all 169 pages.
MAX_PAGES = 6

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}

# "Away @ Home[ Game N] - ... Replay - August 17, 2026". The `.*?Replay` rather than the full
# literal keeps this alive if the site words the middle segment differently ("MLB Full Game
# Replay", "World Series Replay", ...).
_TITLE = re.compile(
    r"^(?P<away>.+?)\s*@\s*(?P<home>.+?)"
    r"(?:\s+Game\s+(?P<game>\d+))?"
    r"\s*-\s*.*Replay\s*-\s*(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})\s*$"
)

# One listing entry: the entry link, the H3 title, and the poster art. The entry id is captured
# by the split in `listing()`, not by a regex of its own.
_ENTRY_LINK_TITLE = re.compile(
    r'<h3><a href="(?P<href>/[a-z0-9-]+)"[^>]*>(?P<title>[^<]+)</a></h3>'
)
_ENTRY_POSTER = re.compile(r'<div class="poster">\s*<a href="[^"]+">\s*<img src="(?P<img>[^"]+)"')

# The embed iframe on an entry page. Protocol-relative (`//ok.ru/...`) is normalised on extraction.
_IFRAME = re.compile(r'<iframe[^>]*\ssrc="(?P<src>//[^"]+|https?://[^"]+)"', re.I)


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


def _parse_title(title: str) -> dict | None:
    m = _TITLE.search(title.strip())
    if not m:
        return None
    month = _MONTHS.get(m.group("month").lower())
    if not month:
        return None
    return {
        "away": m.group("away").strip(),
        "home": m.group("home").strip(),
        "game": int(m.group("game") or 1),
        "date": f"{int(m.group('year')):04d}-{month:02d}-{int(m.group('day')):02d}",
    }


def listing(pages: int = MAX_PAGES, min_date: str = "") -> list[dict]:
    """
    Walk the category pages, newest first, stopping early once entries fall before `min_date`.

    Returns entries as `{replay_id, page_url, title, poster, ...parsed}`. No network fetch of the
    entry pages happens here — the embed extraction is a second, concurrent pass, so a listing
    problem and an embed problem stay separately diagnosable.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for page_n in range(1, pages + 1):
        url = LISTING_URL if page_n == 1 else f"{LISTING_URL}?page{page_n}"
        try:
            html = _get(url)
        except Exception:
            break
        # Entries are `<div id="entryID...">...<div class="short_item">...`. Split on the entry id
        # *with a capture group* so each id stays attached to the block that follows it — the slug
        # appears in both the poster link and the H3, so locating an id by scanning backwards from
        # a slug would cross into the previous entry's block.
        parts = re.split(r'id="entryID(\d+)"', html)
        page_dates: list[str] = []
        # parts[0] is preamble; then alternating [id, block].
        for i in range(1, len(parts) - 1, 2):
            b = parts[i + 1]
            t = _ENTRY_LINK_TITLE.search(b)
            if not t:
                continue
            parsed = _parse_title(t.group("title"))
            if not parsed:
                continue
            entry = {
                "replay_id": f"mlblive:{parts[i]}",
                "page_url": "https://mlblive.net" + t.group("href"),
                "title": t.group("title"),
                **parsed,
            }
            poster = _ENTRY_POSTER.search(b)
            if poster:
                entry["poster"] = "https://mlblive.net" + poster.group("img")
            if entry["replay_id"] in seen:
                continue
            seen.add(entry["replay_id"])
            out.append(entry)
            page_dates.append(entry["date"])
        # Newest-first: once a whole page sits before the window, later pages only get older.
        if min_date and page_dates and max(page_dates) < min_date:
            break
    return out


def _embed(entry: dict) -> dict | None:
    """
    Fetch one entry page and extract its embeddable player URL.

    Returns `{**entry, embed_url}` or None (no entry page, or no embed yet — a game posted before
    its video finished uploading has no iframe, and must not enter the store half-alive).
    """
    try:
        html = _get(entry["page_url"])
    except Exception:
        return None
    m = _IFRAME.search(html)
    if not m:
        return None
    src = m.group("src")
    if src.startswith("//"):
        src = "https:" + src
    # The spam-redirect finding means only embed hosts are ever handed to a WebView. Anything that
    # is not a known player host is recorded but flagged, rather than silently shipped.
    return {**entry, "embed_url": src}


def collect(min_date: str) -> list[dict]:
    """Listing, then concurrent embed extraction."""
    entries = listing(min_date=min_date)
    if not entries:
        return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(_embed, entries))
    return [g for g in got if g]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _eastern_date(start_utc: str) -> str:
    """The US/Eastern calendar date a game was played on — the date the replay site writes."""
    try:
        dt = datetime.strptime(start_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except ValueError:
        return start_utc[:10]


def match(events: list[Event], replays: list[dict]) -> dict[str, dict]:
    """
    Map `event_id -> {embed_url, poster, title}` for confidently matched replays.

    Same strictness rule as highlights: **both team names must land in the replay title**, the date
    must agree (in the site's timezone, see `_eastern_date`), and a doubleheader's `game` number
    must match — date+teams alone sends a viewer to the wrong half of a twin bill.
    """
    found: dict[str, dict] = {}
    mlb = [e for e in events if e.competition_id == "mlb"]
    for e in mlb:
        edate = _eastern_date(e.start_utc)

        def side_hits(title: str, team) -> bool:
            names = [team.name, team.abbrev, *team.aliases]
            nt = _norm(title)
            return any(len(n) >= 3 and _norm(n) in nt for n in names if n)

        for r in replays:
            if r["date"] != edate:
                continue
            if r["game"] != (e.game_number or 1):
                continue
            if not (side_hits(r["title"], e.away) and side_hits(r["title"], e.home)):
                continue
            found[e.event_id] = {
                "embed_url": r["embed_url"],
                "poster": r.get("poster", ""),
                "title": r["title"],
                "replay_id": r["replay_id"],
            }
            break
    return found


def apply(events: list[Event], today: str, store_path: Path, backfill_days: int = 5) -> int:
    """
    Scrape, match, merge into the persistent store, and stamp replay fields onto events.

    Accumulating store, same reason as highlights: a replay that has scrolled onto page 3 of the
    category should not fall out of the feed while the fixture is still inside the backfill window.
    The window here is wider than the fixture backfill because a full game uploads hours after the
    final out — a 3-day-old fixture regularly gets its replay only on day 3 or 4.
    """
    min_date = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=backfill_days)).strftime("%Y-%m-%d")
    store: dict[str, dict] = {}
    if store_path.exists():
        try:
            store = json.loads(store_path.read_text() or "{}")
        except ValueError:
            store = {}  # a corrupt store regenerates; the scrape below re-finds recent games

    # A total scrape failure (site down, DNS) must degrade to "no new replays", never an empty feed:
    # the store still carries everything matched on previous runs.
    try:
        fresh = match(events, collect(min_date))
    except Exception:
        fresh = {}
    store.update(fresh)

    # Evict by age: keep what any screen could still show (fixtures inside the backfill window).
    ids_kept = {e.event_id for e in events}
    store = {k: v for k, v in store.items() if k in ids_kept} if len(store) > 500 else store

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, indent=2))

    hit = 0
    for e in events:
        r = store.get(e.event_id)
        if r:
            e.replay_url = r["embed_url"]
            e.replay_poster = r.get("poster") or ""
            hit += 1
    return hit
