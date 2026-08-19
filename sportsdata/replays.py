"""
Match full-event replays from replay sites to fixtures in the feed.

## mlblive.net (MLB), characterised by probe (docs/captures/replays/, 2026-08-18)

A uCoz `publ` catalog, fully server-rendered — every listing and entry page is parseable with
plain HTTP, no JS. That matters because **the site's own pages main-frame-redirect a real browser
to an Adsterra fake-browser preland** (`boost-you-browser.com/...`, JS-injected after load; a
plain HTTP client never sees it). Consequences, both load-bearing:

1. Scraping it with urllib is safe and stable — no ads execute, no redirects fire.
2. **The app must never load an mlblive.net page.** It plays the video *embed* the entry carries
   (an ok.ru player), extracted here and shipped in the feed as `replay_url`.

Title grammar (verified across every entry probed):

    St. Louis Cardinals @ Cincinnati Reds Game 2 - MLB Full Game Replay - August 17, 2026
    ^away                        ^home           ^optional     ^literal            ^US date

Playback facts, measured (probe_mlb_autoplay.py): the ok.ru embed is gesture-gated like videasy —
it resolves and prebuffers on load but mounts no `<video>` until a click. One synthesized centre
click starts it (MSE, `blob:` src, quality ladders from 240p; segments on `*.vkuser.net`). Full
games run ~2.5h. Native resolve is not available anonymously, so the play path is a WebView
pointed at the embed.

## basketball-video.com (NBA + WNBA), characterised by probe (docs/captures/bbv/, 2026-08-18)

Same uCoz engine and card markup as mlblive (entryID split, H3+poster per card) behind Cloudflare;
a browser-ish UA gets plain 200s, no preland. Structure differs from mlblive in two ways:

- **The embed is not an iframe on the entry page.** Each per-game entry carries "Server" Watch
  buttons (`<a class="su-button" href="//ok.ru/videoembed/...">`); some entries offer dailymotion
  or filemoon servers instead. Only the ok.ru server is extracted — it is the one host the app's
  player is verified against; any other server needs its own player probe first.
- **Placeholder entries exist and must be skipped.** When the operator has no embed, the Watch
  button points at a stale TV-schedule site (nbaontv.com, dated 2024) — an entry page with no
  ok.ru href is a shell, not a match failure. Verified window: every WNBA entry after Aug 10 2026
  was a shell at probe time, while the operator's ok.ru upload channel continued posting — so the
  site is an index that can lag or drop its wiring; the scraper takes what is wired, no more.

The ok.ru uploader behind these embeds titles every video "." (probe_bbv_okru.py) — ok.ru itself
is unmatchable by title; the index site's titles are the only match source and always will be.

Listings walked: `/wnba-video-full-game` (stable URL) and the current NBA season hub, discovered
from the home page's season links each run (`/2025-26` today; hard-coded fallback) so a new
season's hub is picked up without a code change. An alternate index exists — day-hub entries with
an inline per-game table ("NBA Summer League - July 19, 2026 ...") whose rows carry ok.ru links
directly; it is not walked (per-game listings cover the same games) but is the fallback shape if
the per-game listings ever change.

## nfl-video.com (NFL), characterised by probe (docs/captures/nflv/, 2026-08-18)

Third sibling, same operator family (the sites cross-link each other; the May 2026 wired entry's
ok.ru id sits in the same upload channel). Same uCoz engine, no preland. Differences:

- The home feed (`/?pageN`) is a **mixed catalog** — NFL games, CFL week hubs, Hard Knocks
  shows, college spring games. Hub/show titles carry no "vs" and fall to the parser floor;
  spring-game oddities parse but never match a fixture. NFL titles share basketball's grammar
  ("Dallas Cowboys vs. Seattle Seahawks - Full Game Replay - NFL Preseason - August 15, 2026").
- The wired-era form is the **iframe** (mlblive's), not the Watch button; `_okru_embed` takes
  either.
- **A second wiring outage, earlier than basketball's**: wired through **Jul 31 2026** (11
  embeds verified live on page 2 of the home feed, Jul 12–31), then every August entry — from
  the Aug 6 Hall of Fame Game through all of preseason week 1 — is a shell (six placeholder
  buttons each; the current page-1 entries Aug 13–15 were all verified shells). The NFL feed
  source is therefore live-but-waiting, like WNBA: wired July entries sit outside the 5-day
  backfill window, so nothing matches until the operator wires an in-window game.

The date in every title is the site's local (US) date, so matching converts the fixture's
`start_utc` to America/New_York before comparing — a 9:40pm PT game is "August 17" on the site
but August 18 in UTC, and comparing raw UTC dates would miss every West Coast night game.
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

BBV_BASE = "https://basketball-video.com"
# Stable per-game WNBA catalog (the user's own link). ~3-4 games/day, so 4 pages cover the window.
BBV_WNBA = f"{BBV_BASE}/wnba-video-full-game"
# NBA: the current season hub is discovered from the home page each run (season links, newest
# first); this is only the fallback if discovery fails.
BBV_SEASON_FALLBACK = f"{BBV_BASE}/2025-26"
BBV_MAX_PAGES = 4

# nfl-video.com, the third sibling (same operator, same uCoz engine, same ok.ru channel). The
# home feed is a mixed catalog (NFL games, CFL week hubs, Hard Knocks, spring games); hub and
# show titles carry no "vs" and fall to the parser floor. Wired-era entries carry the embed as
# an IFRAME (mlblive's form — a May 2026 entry measured so), unlike basketball-video's
# su-buttons; the extractor takes either.
NFLV_BASE = "https://nfl-video.com"
NFLV_MAX_PAGES = 4

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

# basketball-video title grammar, two eras observed per league (date last / date middle, "vs."
# or "vs", optional series segment between the teams and the date):
#   "Dallas Wings vs. Golden State Valkyries - WNBA Full Game Replay - August 17, 2026"
#   "Minnesota Lynx vs Indiana Fever August 22, 2025 WNBA Full Game Replay"
#   "San Antonio Spurs vs. New York Knicks - NBA Finals - Game 4 - Full Game Replay - June 10, 2026"
#   "Los Angeles Lakers vs. Golden State Warriors Gold - NBA Summer League - Full Game Replay - ..."
# Teams live in the first " - "-separated segment (whole title in the 2025 era, which had no
# dashes); the date is found anywhere. Trailing qualifiers after the second team ("Gold") are
# tolerated by substring alias matching downstream.
_BBV_TITLE = re.compile(
    r"^(?P<away>.+?)\s+vs\.?\s+(?P<home>.+?)(?:\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}\b.*)?$"
)
_BBV_DATE = re.compile(r"\b(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})\b")

# One listing entry: the entry link, the H3 title, and the poster art. The entry id is captured
# by the split in `listing()`, not by a regex of its own. Shared by both uCoz sites.
_ENTRY_LINK_TITLE = re.compile(
    r'<h3><a href="(?P<href>/[a-z0-9-]+)"[^>]*>(?P<title>[^<]+)</a></h3>'
)
_ENTRY_POSTER = re.compile(r'<div class="poster">\s*<a href="[^"]+">\s*<img src="(?P<img>[^"]+)"')

# The embed iframe on an mlblive entry page. Protocol-relative (`//ok.ru/...`) is normalised.
_IFRAME = re.compile(r'<iframe[^>]*\ssrc="(?P<src>//[^"]+|https?://[^"]+)"', re.I)

# basketball-video's ok.ru Watch button (protocol-relative). Deliberately host-pinned: the other
# servers (dailymotion, filemoon) have not been player-probed, and an unproven host in the app's
# WebView is a black screen, not a feature.
_BBV_OK = re.compile(r'href="(?P<src>//ok\.ru/videoembed/\d+)"')

# The home page's season-hub links, newest first: <a href="https://.../2025-26">2025-26 NBA Season</a>
_BBV_SEASON_LINK = re.compile(r'href="https?://(?:www\.)?basketball-video\.com/(20\d{2}-\d{2})"')


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


def _parse_bbv_title(title: str) -> dict | None:
    """
    Tolerant across the site's eras; `game` is always 1 on purpose. MLB's `Game N` is a
    doubleheader half the fixture data can corroborate; basketball's `Game N` is a *series* game
    (NBA Finals Game 4) that the fixture side does not carry at all — enforcing it here would
    reject every playoff replay. Date + both teams is the match, and a same-day rematch does not
    exist in these leagues.
    """
    m = _BBV_TITLE.match(title.strip().split(" - ")[0])
    d = _BBV_DATE.search(title)
    if not m or not d:
        return None
    month = _MONTHS.get(d.group("month").lower())
    if not month:
        return None
    return {
        "away": m.group("away").strip(" .,-"),
        "home": m.group("home").strip(" .,-"),
        "game": 1,
        "date": f"{int(d.group('year')):04d}-{month:02d}-{int(d.group('day')):02d}",
    }


def _walk_ucoz(listing_url: str, base: str, tag: str, parse, pages: int, min_date: str) -> list[dict]:
    """
    The shared uCoz listing walk (mlblive and basketball-video use the same engine and card
    markup). Newest first, `?pageN` pagination, early stop once a whole page predates `min_date`.

    Returns entries as `{replay_id, page_url, title, poster?, ...parsed}`. No entry pages are
    fetched here — embed extraction is a second, concurrent pass, so a listing problem and an
    embed problem stay separately diagnosable.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for page_n in range(1, pages + 1):
        url = listing_url if page_n == 1 else f"{listing_url}?page{page_n}"
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
            parsed = parse(t.group("title"))
            if not parsed:
                continue
            entry = {
                "replay_id": f"{tag}:{parts[i]}",
                "page_url": base + t.group("href"),
                "title": t.group("title"),
                **parsed,
            }
            poster = _ENTRY_POSTER.search(b)
            if poster:
                entry["poster"] = base + poster.group("img")
            if entry["replay_id"] in seen:
                continue
            seen.add(entry["replay_id"])
            out.append(entry)
            page_dates.append(entry["date"])
        # Newest-first: once a whole page sits before the window, later pages only get older.
        if min_date and page_dates and max(page_dates) < min_date:
            break
    return out


def listing(pages: int = MAX_PAGES, min_date: str = "") -> list[dict]:
    """mlblive listing (MLB)."""
    return _walk_ucoz(LISTING_URL, "https://mlblive.net", "mlblive", _parse_title, pages, min_date)


def _bbv_nba_url() -> str:
    """The current NBA season hub, discovered so a new season needs no code change."""
    try:
        seasons = _BBV_SEASON_LINK.findall(_get(BBV_BASE + "/"))
        if seasons:
            return f"{BBV_BASE}/{seasons[0]}"
    except Exception:
        pass
    return BBV_SEASON_FALLBACK


def bbv_listing(min_date: str) -> list[dict]:
    """basketball-video listing (NBA + WNBA): both catalogs, deduped by replay id."""
    return _walk_sites(
        [(BBV_WNBA, BBV_BASE), (_bbv_nba_url(), BBV_BASE)], "bbv", min_date, BBV_MAX_PAGES)


def nflv_listing(min_date: str) -> list[dict]:
    """nfl-video listing: the home feed (`/?pageN`), newest first."""
    return _walk_sites([(NFLV_BASE + "/", NFLV_BASE)], "nflv", min_date, NFLV_MAX_PAGES)


def _walk_sites(sites: list[tuple[str, str]], tag: str, min_date: str, pages: int) -> list[dict]:
    """Walk each (listing_url, base) with the basketball-era title parser, dedupe by replay id.

    NFL and basketball titles share the grammar this parser tolerates (teams in the first
    `" - "` segment or before the date, "vs"/"vs.", date anywhere): "Dallas Cowboys vs. Seattle
    Seahawks - Full Game Replay - NFL Preseason - August 15, 2026" and every basketball form
    observed. One parser, three catalogs.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for listing_url, base in sites:
        for e in _walk_ucoz(listing_url, base, tag, _parse_bbv_title, pages, min_date):
            if e["replay_id"] in seen:
                continue
            seen.add(e["replay_id"])
            out.append(e)
    return out


def _embed(entry: dict) -> dict | None:
    """
    Fetch one mlblive entry page and extract its embeddable player URL.

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


def _okru_embed(entry: dict) -> dict | None:
    """
    Fetch one entry page and take its ok.ru server, whatever form the site wires it in.

    Two wired forms are observed across the family: basketball-video's Watch button
    (`<a class="su-button" href="//ok.ru/videoembed/...">`) and nfl-video/mlblive's iframe. None
    for every other outcome — including the placeholder shells (Watch buttons pointed at stale
    TV-schedule sites) the operators post in place of real embeds; a shell must not enter the
    store half-alive.
    """
    try:
        html = _get(entry["page_url"])
    except Exception:
        return None
    m = _BBV_OK.search(html) or _IFRAME.search(html)
    if not m:
        return None
    src = m.group("src")
    if src.startswith("//"):
        src = "https:" + src
    if "//ok.ru/" not in src and ".ok.ru/" not in src:
        return None
    return {**entry, "embed_url": src}


def collect(min_date: str) -> list[dict]:
    """mlblive: listing, then concurrent embed extraction."""
    entries = listing(min_date=min_date)
    if not entries:
        return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(_embed, entries))
    return [g for g in got if g]


def collect_bbv(min_date: str) -> list[dict]:
    """basketball-video: listing, then concurrent embed extraction."""
    entries = bbv_listing(min_date=min_date)
    if not entries:
        return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(_okru_embed, entries))
    return [g for g in got if g]


def collect_nfl(min_date: str) -> list[dict]:
    """nfl-video: listing, then concurrent embed extraction."""
    entries = nflv_listing(min_date=min_date)
    if not entries:
        return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(_okru_embed, entries))
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


def _match_league(events: list[Event], replays: list[dict], game_guard: bool) -> dict[str, dict]:
    """
    Map `event_id -> {embed_url, poster, title}` for confidently matched replays.

    Same strictness rule as highlights: **both team names must land in the replay title**, the date
    must agree (in the site's timezone, see `_eastern_date`), and — where the fixture data can
    corroborate it (`game_guard`, MLB doubleheaders) — the game number must match. For basketball
    the guard is off: the site's `Game N` is a series game the fixtures do not carry (see
    `_parse_bbv_title`), and date+both-teams cannot alias inside one league-day.
    """
    found: dict[str, dict] = {}
    for e in events:
        edate = _eastern_date(e.start_utc)

        def side_hits(title: str, team) -> bool:
            names = [team.name, team.abbrev, *team.aliases]
            nt = _norm(title)
            return any(len(n) >= 3 and _norm(n) in nt for n in names if n)

        for r in replays:
            if r["date"] != edate:
                continue
            if game_guard and r["game"] != (e.game_number or 1):
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


def match(events: list[Event], replays: list[dict]) -> dict[str, dict]:
    """Route each league's fixtures to the source that covers it. Cross-source false positives
    are impossible by construction — an event only ever sees its own source's replays."""
    by_prefix: dict[str, list[dict]] = {}
    for r in replays:
        by_prefix.setdefault(r["replay_id"].split(":", 1)[0], []).append(r)
    found: dict[str, dict] = {}
    found.update(_match_league(
        [e for e in events if e.competition_id == "mlb"],
        by_prefix.get("mlblive", []), game_guard=True))
    found.update(_match_league(
        [e for e in events if e.competition_id in ("nba", "wnba")],
        by_prefix.get("bbv", []), game_guard=False))
    found.update(_match_league(
        [e for e in events if e.competition_id == "nfl"],
        by_prefix.get("nflv", []), game_guard=False))
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

    # A total scrape failure (site down, DNS, Cloudflare challenge) must degrade to "no new
    # replays", never an empty feed: each source is guarded separately (one site being down must
    # not silence the others) and the store still carries everything matched on previous runs.
    fresh_replays: list[dict] = []
    for collector in (collect, collect_bbv, collect_nfl):
        try:
            fresh_replays.extend(collector(min_date))
        except Exception:
            continue
    fresh = match(events, fresh_replays)
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
