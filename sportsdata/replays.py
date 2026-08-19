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

## rugby24.net (Rugby both codes + AFL), characterised by probe (r24/, 2026-08-19)

Fourth uCoz sibling and the healthiest: ~2s TTFB (NOT the 43s Cloudflare edge), and every entry
probed across NRL / Super League / Currie Cup / NPC / internationals / AFL carries a live ok.ru
iframe — no wiring outage anywhere. Titles: "{A} v {B} - Full Match Replay - {Comp} - {D Month
YYYY}" (day-first; AFL entries put the date before the comp). The comp segment is parse-extracted
and the matcher GATES on it, because this one pool mixes rugby codes and short club names overlap
them (NRL "Sharks v Storm" vs Currie Cup "Sharks v Stormers" on a shared date). Fixture side:
NRL is ESPN `rugby-league/3` (all 5 of the Aug 15-16 round's site entries match under the Eastern
date rule); AFL is ESPN `afl` but the one measured site entry is dated a day off Eastern, so the
strict rule conservatively yields nothing there; UNION (Super League, Currie Cup, NPC, tests) has
no free fixture source — ESPN's legacy rugby league ids all 404 on the scoreboard and SofaScore's
API has no rugby at all — so union entries enter the store unmatched until a fixture source
exists. An ad iframe (`bysesukior.com`) rides entry pages unordered beside the player, which is
why the ok.ru iframe is host-pinned at the regex.

## watch-wrestling.eu (UFC cards + WWE/AEW shows), characterised by probe (ww/, 2026-08-19)

WordPress (not uCoz), ~44s TTFB from the dev box like the whole family; CI expected fast. The
only source in the ledger that ships a **documented embed API** (`dailywrestling.cc/embed/...`),
addressed by (category tag reversed, mm-dd-yyyy, post/source/button index) — so the category
listing is the entire scrape and no entry page is ever fetched. `1/1` is the featured Full Show
(verified by mounting: UFC 330 source 1 = Main card, 3 = Early Prelims, 4 = Prelims). The embed
page is a JS shell mounting dailymotion iframes plus a limemint m3u8; nothing is statically
extractable, which is fine — the app's WebView runs the shell itself.

Two item kinds, deliberately different shapes:

- **UFC cards** are fixture-like: one ESPN event per card (see the MMA branch in
  `EspnAdapter.fetch` — MMA competitors carry no `homeAway`, and the card name lives in
  `Event.card` because the headliners a card is NAMED for are not the competitors listed).
  Matched by `_match_cards`: Eastern date + normalized card-name containment.
- **Weekly shows** (Raw, SmackDown, NXT via the generic /wwe/ category filtered by title
  prefix; Dynamite via its own) have **no fixture representation anywhere** — they are published
  as `feed["shows"]` (see `collect_ww_shows`), never matched. Undated posts (TUF "S34E10")
  are skipped: nothing anchors their freshness.

The date in every title is the site's local (US) date, so matching converts the fixture's
`start_utc` to America/New_York before comparing — a 9:40pm PT game is "August 17" on the site
but August 18 in UTC, and comparing raw UTC dates would miss every West Coast night game.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.request
from html import unescape
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

# rugby24.net — the fourth uCoz sibling, and the healthiest of them: fast (~2s TTFB, not the
# 43s Cloudflare edge the -video.com family sits behind), and as of 2026-08-19 EVERY entry
# probed carries a live ok.ru iframe — no wiring outage. The home feed is a mixed catalog
# (NRL, Super League, Currie Cup, NZ NPC, internationals, AFL), and unlike the NFL sibling the
# titles always carry the competition segment, which the matcher gates on: the pool holds both
# codes and short club names overlap them ("Sharks v Storm" NRL could false-hit Currie Cup's
# "Sharks v Stormers" on a shared date — an NRL-gated match cannot).
R24_BASE = "https://rugby24.net"
R24_MAX_PAGES = 4

# watch-wrestling.eu — WordPress, not uCoz, and unlike every source above it needs **no entry-page
# fetch at all**: the site publishes a documented embed API addressed by (category, date, post,
# source, button), so the category listing IS the whole scrape. Category tags for the API are the
# display name reversed/lowercased/spaces-removed ("UFC" -> "cfu", verified against a live embed;
# "WWE Smackdown" -> "nwodkcamsww", "AEW Dynamite" -> "etimanydwea"). source/button 1/1 is the
# post's first media block — the featured Full Show in every layout measured (UFC 330: block 1 is
# the Main card; probed: sources 1/3/4 mount Main card / Early Prelims / Prelims respectively).
WW_BASE = "https://watch-wrestling.eu"
WW_EMBED = "https://dailywrestling.cc/embed"
# slug -> (reversed API tag, accepted title prefixes). ufc-78 feeds the fixture-matched UFC
# replays; the rest are weekly SHOWS (no fixture representation anywhere — published as
# feed["shows"], not matched). Raw/SmackDown/NXT posts carry only the generic /wwe/ category
# (measured on the Aug 17 Raw post), so that one walk is filtered by title prefix; SmackDown's
# dedicated archive exists but one walk of /wwe/ covers all three shows with one fetch.
WW_UFC_CATEGORY = ("ufc-78", "cfu")
WW_SHOW_CATEGORIES = {
    "wwe": ("eww", ("wwe raw", "wwe smackdown", "wwe nxt")),
    "dynamite": ("etimanydwea", ("aew dynamite",)),
}

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

# rugby24 title grammar — teams separated by a bare "v", day-first date, competition segment
# always present but positionally inconsistent (AFL entries put the date before the comp):
#   "Hull KR v Warrington Wolves - Full Match Replay - Super League - 18 August 2026"
#   "Essendon Bombers v Sydney Swans - Full Match Replay - 15 August 2026 - AFL"
#   "Wests Tigers v St. George Illawarra Dragons - Full Match Replay - NRL - 16 August 2026"
# The comp is whichever dash segment is neither the date nor the replay boilerplate; a stricter
# known-comp list would break the day the operator words a competition differently.
_R24_TITLE = re.compile(r"^(?P<away>.+?)\s+v\.?\s+(?P<home>.+?)$")
_R24_DATE = re.compile(r"\b(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})\b")
_R24_FILLER = re.compile(r"full.?match.?replay|replay", re.I)

# One listing entry: the entry link, the H3 title, and the poster art. The entry id is captured
# by the split in `listing()`, not by a regex of its own. Shared by both uCoz sites.
_ENTRY_LINK_TITLE = re.compile(
    r'<h3><a href="(?P<href>/[a-z0-9-]+)"[^>]*>(?P<title>[^<]+)</a></h3>'
)
_ENTRY_POSTER = re.compile(r'<div class="poster">\s*<a href="[^"]+">\s*<img src="(?P<img>[^"]+)"')

# The embed iframe on an mlblive entry page. Protocol-relative (`//ok.ru/...`) is normalised.
_IFRAME = re.compile(r'<iframe[^>]*\ssrc="(?P<src>//[^"]+|https?://[^"]+)"', re.I)

# basketball-video's ok.ru Watch button (protocol-relative; rugby24's variant appends
# `?nochat=1&autoplay=1`). Deliberately host-pinned: the other servers (dailymotion, filemoon)
# have not been player-probed, and an unproven host in the app's WebView is a black screen, not a
# feature.
_BBV_OK = re.compile(r'href="(?P<src>//ok\.ru/videoembed/\d+(?:\?[^"]*)?)"')

# The ok.ru iframe, host-pinned at the regex rather than checked after a generic iframe search.
# rugby24 mounts an ad iframe (`bysesukior.com`) alongside the player and nothing orders them;
# first-iframe-then-check would skip a live entry the day the ad loads first.
_OKRU_IFRAME = re.compile(r'<iframe[^>]*\ssrc="(?P<src>(?:https?:)?//ok\.ru/videoembed/\d+(?:\?[^"]*)?)"')

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


def _parse_r24_title(title: str) -> dict | None:
    """
    rugby24's grammar keeps teams in the first `" - "` segment and the date anywhere (day-first).
    The extra `comp` key is what makes this pool safe to match against: the site lists both rugby
    codes plus AFL in one feed, and `_match_league` gates on it (see the source comment above).
    `game` is always 1 — rugby has no doubleheaders.
    """
    m = _R24_TITLE.match(title.strip().split(" - ")[0])
    d = _R24_DATE.search(title)
    if not m or not d:
        return None
    month = _MONTHS.get(d.group("month").lower())
    if not month:
        return None
    comp = ""
    for seg in title.split(" - ")[1:]:
        if _R24_DATE.search(seg) or _R24_FILLER.search(seg):
            continue
        comp = seg.strip()
        break
    return {
        "away": m.group("away").strip(" .,-"),
        "home": m.group("home").strip(" .,-"),
        "game": 1,
        "date": f"{int(d.group('year')):04d}-{month:02d}-{int(d.group('day')):02d}",
        "comp": comp,
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


def r24_listing(min_date: str) -> list[dict]:
    """rugby24 listing: the mixed-code home feed (`/?pageN`), newest first, r24 parser for `comp`."""
    return _walk_ucoz(R24_BASE + "/", R24_BASE, "r24", _parse_r24_title, R24_MAX_PAGES, min_date)


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


# WordPress archive entries: `<h1 class="entry-title"><a href="...">Title</a></h1>`, newest first.
_WW_ENTRY = re.compile(
    r'entry-title[^>]*>\s*<a href="(?P<href>[^"]+)"[^>]*>(?P<title>[^<]+)</a>')
# Two date forms on one SmackDown page: "WWE Smackdown 8/7/2026" and
# "WWE Smackdown 8/14/26 – August 14, 2026" (the latter also matches _BBV_DATE).
_WW_SLASH_DATE = re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>20\d{2})\b")
_WW_SHORT_DATE = re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y2>\d{2})\b")


def _ww_date(title: str) -> str:
    """`M/D/YYYY` (or `M/D/YY`, or a spelled-out month) anywhere in a watch-wrestling title."""
    m = _WW_SLASH_DATE.search(title)
    if m:
        return f"{m['y']}-{int(m['m']):02d}-{int(m['d']):02d}"
    m = _BBV_DATE.search(title)  # "August 14, 2026"
    if m:
        return f"{m['year']}-{_MONTHS[m['month'].lower()]:02d}-{int(m['day']):02d}"
    m = _WW_SHORT_DATE.search(title)
    if m:
        return f"20{m['y2']}-{int(m['m']):02d}-{int(m['d']):02d}"
    return ""


def _ww_embed_url(tag: str, date: str) -> str:
    """The documented embed API address for a category+date. Post/source/button 1/1 = the
    featured Full Show (see WW_BASE comment)."""
    y, m, d = date.split("-")
    return f"{WW_EMBED}/{tag}/{m}-{d}-{y}/select-post-1/1/1"


def _ww_walk(slug: str, tag: str, min_date: str, prefixes: tuple[str, ...] = ()) -> list[dict]:
    """One watch-wrestling category page. No entry fetches, no embed parsing — the API is
    addressed by category+date, so the listing is the entire scrape. Undated posts (The
    Ultimate Fighter episode numbers) are skipped: nothing anchors their freshness or identity.
    `prefixes` filters a mixed category (/wwe/ holds every WWE show) by title prefix."""
    try:
        html = _get(f"{WW_BASE}/{slug}/")
    except Exception:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for m in _WW_ENTRY.finditer(html):
        href = m.group("href")
        if href in seen:
            continue
        seen.add(href)
        title = unescape(m.group("title")).strip()
        if prefixes and not title.lower().startswith(prefixes):
            continue
        date = _ww_date(title)
        if not date:
            continue
        if min_date and date < min_date:
            continue
        out.append({
            "replay_id": "ww:" + href.rstrip("/").rsplit("/", 1)[-1][:50],
            "title": title,
            "date": date,
            "url": href,
            "embed_url": _ww_embed_url(tag, date),
        })
    return out


def collect_ww(min_date: str) -> list[dict]:
    """watch-wrestling UFC cards, ready for the matcher. One post per card, dated."""
    slug, tag = WW_UFC_CATEGORY
    return _ww_walk(slug, tag, min_date)


def collect_ww_shows(min_date: str) -> list[dict]:
    """Weekly wrestling shows (Raw, SmackDown, Dynamite, ...) as first-class replay items.

    These have NO fixture representation — no feed event will ever carry them — so they are not
    matched; they are the feed's `shows` array, each row entry already playable via `replay_url`.
    Sorted newest first; the window is the caller's choice (a weekly cadence wants ~6 days so
    exactly one episode per show is live at a time).
    """
    out: list[dict] = []
    for slug, (tag, prefixes) in WW_SHOW_CATEGORIES.items():
        for p in _ww_walk(slug, tag, min_date, prefixes):
            out.append({
                "id": p["replay_id"],
                "title": p["title"],
                "date": p["date"],
                "replay_url": p["embed_url"],
                "source_url": p["url"],
            })
    out.sort(key=lambda s: s["date"], reverse=True)
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
    m = _BBV_OK.search(html) or _OKRU_IFRAME.search(html)
    if not m:
        return None
    src = m.group("src")
    if src.startswith("//"):
        src = "https:" + src
    if "//ok.ru/" not in src and ".ok.ru/" not in src:
        return None
    # Query strings (rugby24's `?nochat=1&autoplay=1`) are dropped: the bare videoembed URL is
    # the canonical form every other source ships and the one the app's tap pump is calibrated
    # for — an operator's autoplay flag is not ours to honour.
    return {**entry, "embed_url": src.split("?")[0]}


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


def collect_r24(min_date: str) -> list[dict]:
    """rugby24: listing, then concurrent embed extraction."""
    entries = r24_listing(min_date=min_date)
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


def _match_league(events: list[Event], replays: list[dict], game_guard: bool,
                  comp_gate: str = "") -> dict[str, dict]:
    """
    Map `event_id -> {embed_url, poster, title}` for confidently matched replays.

    Same strictness rule as highlights: **both team names must land in the replay title**, the date
    must agree (in the site's timezone, see `_eastern_date`), and — where the fixture data can
    corroborate it (`game_guard`, MLB doubleheaders) — the game number must match. For basketball
    the guard is off: the site's `Game N` is a series game the fixtures do not carry (see
    `_parse_bbv_title`), and date+both-teams cannot alias inside one league-day.

    `comp_gate` serves pools that mix codes (rugby24): when set, only replays whose parsed
    competition segment normalizes to it are eligible — an NRL "Sharks v Storm" fixture must not
    grab Currie Cup's "Sharks v Stormers" title off a shared Saturday.
    """
    gate = _norm(comp_gate)
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
            if gate and _norm(r.get("comp", "")) != gate:
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


def _match_cards(events: list[Event], replays: list[dict]) -> dict[str, dict]:
    """
    The UFC variant of the matcher: a fight CARD is one event named for its headliners
    ("UFC 330: Makhachev vs. Machado Garry"), and the competitors the API happens to list are an
    undercard pairing — team-name matching would misfire. The card name IS the identity, so the
    rule is Eastern-date equality plus normalized card-name containment in the replay title
    (punctuation stripped on both sides, so "vs." and "vs" agree). One card per date on both
    sides makes this as strict as the team rule.
    """
    found: dict[str, dict] = {}
    for e in events:
        if not e.card:
            continue
        key = _norm(e.card)
        edate = _eastern_date(e.start_utc)
        for r in replays:
            if r["date"] != edate or key not in _norm(r["title"]):
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
    # rugby24: NRL now; AFL rides the same pool but its one measured entry is dated a day off the
    # fixture's Eastern date ("15 August" vs Aug 16 ET), so the strict rule yields nothing until
    # that pattern is re-measured — conservative by design, not a bug.
    found.update(_match_league(
        [e for e in events if e.competition_id == "rugby-league"],
        by_prefix.get("r24", []), game_guard=False, comp_gate="nrl"))
    found.update(_match_league(
        [e for e in events if e.competition_id == "afl"],
        by_prefix.get("r24", []), game_guard=False, comp_gate="afl"))
    found.update(_match_cards(
        [e for e in events if e.competition_id == "ufc"],
        by_prefix.get("ww", [])))
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
    for collector in (collect, collect_bbv, collect_nfl, collect_ww, collect_r24):
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
