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

## fullraces.com (motorsport sessions), characterised by probe (fr/, 2026-08-19)

Fifth uCoz sibling (~2.6s TTFB). Entry pages carry the ok.ru iframe plus dailymotion su-buttons
(alternate servers, unprobed in-app — the host-pinned extractor ships ok.ru only). The structural
difference is SESSIONS: a race weekend arrives as separate RACE / Qualifying / practice entries,
and the fixture side is ONE athlete-listed event per weekend (ESPN racing had the same
homeAway-less competitor shape as MMA — the adapter synthesizes the pair and carries the race
name in `card`). The match rule is therefore series + weekend window with **no name matching**:
the site names races by sponsor ("Cook Out 400") where ESPN names by venue ("at Richmond") and
prefixes sponsors onto GP names ("AWS Hungarian Grand Prix") — a name rule rejects the true
pairs. Window 0-3 days after the fixture's Eastern date (ESPN dates the weekend's first session;
measured F1 gap 2, NASCAR/IndyCar 0). User scope decision 2026-08-19: RACE REPLAYS ONLY —
qualifying/practice/sprint entries never match; MotoGP's dateless titles and WRC's ranges are
parser-floor drops; F2/F3/WRC have no fixture source (stored unmatched if they carried dates).
IndyCar was registered (ESPN `irl`, one line) for this source.

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
import urllib.parse
import urllib.request
from html import unescape
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .model import Event, Status

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
# 5 pages: the item window is 7 days now (weekly sports persist until their next edition), and
# the mixed-code home feed runs ~6-10 entries/day across NRL / SL / Currie / NPC / AFL.
R24_MAX_PAGES = 5

# fullraces.com — fifth uCoz sibling (~2.6s TTFB), ok.ru iframes plus dailymotion alternates.
# Sessions, not teams: a weekend carries RACE / Qualifying / practice entries and the fixture
# side (ESPN racing) is ONE athlete-listed event per weekend, so the match key is
# series + weekend window — see `_match_races` for why name matching is deliberately absent.
# User's scope decision 2026-08-19: RACE REPLAYS ONLY (quali/practice/sprint entries are not
# matched and not published as show items), extended 2026-08-22 to weekend COVERAGE posts
# ("Paddock Uncut", "Drivers Press Conference") — neither a session nor a series, dropped at
# the item filter.
FR_BASE = "https://fullraces.com"
# 5 pages for the 7-day item window: a race weekend posts ~10-20 entries (race + quali +
# practice per series), so a week spans more pages than the old 5-day store window did.
FR_MAX_PAGES = 5

# watch-wrestling.eu — WordPress, not uCoz, and unlike every source above it needs **no entry-page
# fetch at all**: the site publishes a documented embed API addressed by (category, date, post,
# source, button), so the category listing IS the whole scrape. Category tags for the API are the
# display name reversed/lowercased/spaces-removed ("UFC" -> "cfu", verified against a live embed;
# "WWE Smackdown" -> "nwodkcamsww", "AEW Dynamite" -> "etimanydwea"). source/button 1/1 is the
# post's first media block — the featured Full Show in every layout measured (UFC 330: block 1 is
# the Main card; probed: sources 1/3/4 mount Main card / Early Prelims / Prelims respectively).
WW_BASE = "https://watch-wrestling.eu"
WW_EMBED = "https://dailywrestling.cc/embed"
# The chronological home listing (the user's pointer, 2026-08-22): every UFC/wrestling replay
# post lives here newest-first — WWE, AEW (both shows), ROH, TNA, UFC, Contender Series — so
# ONE walk covers the whole family and per-show category walks are retired. WordPress
# pagination is /home-15/page/2/; the walk stops once a page's posts all predate the window.
WW_HOME = WW_BASE + "/home-15/"
# The old per-category walks, kept only as names for the embed tags they had verified:
# "UFC" -> "cfu", "WWE" -> "eww". Tags are now derived per-post from the post's own
# data-secondary-catname (below), which measured correct on every show kind probed.
WW_UFC_CATEGORY = ("ufc-78", "cfu")
WW_SHOW_CATEGORIES = {
    "wwe": ("eww", ("wwe raw", "wwe smackdown", "wwe nxt")),
    "dynamite": ("etimanydwea", ("aew dynamite",)),
}

# The option tree on a watch-wrestling post page (probed 2026-08-22, AEW Dynamite 8/19 and
# seven other posts — every show kind carries the same shape):
#
#     <div class="src-name">VidQ FHD@50fps MyAEW</div>
#     <div class="srccontainer">
#       <button class="srcbtn" data-src="…scrambled…">Full Show</button>
#     </div>
#
# Sections repeat down the page (VidQ FHD, VidQ SD, VidFrame FHD/SD, Dailymotion, OK.ru,
# "Other Hosts" with one button per host, "Dailymotion (Live replay)"), and the set is UNIQUE
# PER REPLAY — fewer, more, and never-seen-before hosts appear from post to post. The
# scrambled data-src is the website's own click path and is deliberately NOT decoded: the
# documented embed API addresses every option as select-post-{p}/{source}/{button}, where
# source = the section's index AMONG MEDIA SECTIONS (the "Quick links!" section carries no
# srcbtn and does not count — confirmed by the site's own "Source 1/2/3" labels on posts
# that number their sections) and button = the button's index within its section. Every
# address returns the same dailywrestling JS-shell family the app already plays.
_WW_CAT = re.compile(r'data-secondary-catname="([^"]*)"')
_WW_SELECT_POST = re.compile(r'data-select-post="(\d+)"')
_WW_SRC_SPLIT = '<div class="src-name">'
_WW_SECTION_LABEL = re.compile(r'^([^<]+)</div>')
_WW_SRCBTN = re.compile(r'<button class="srcbtn"[^>]*>(.*?)</button>', re.S)
_WW_BTN_TEXT = re.compile(r'<[^>]+>')
# A multi-part button: "Part 1".."Part N" or "last part". Everything else (Full Show, host
# names like "Abyss (HD)") is a single-file option.
_WW_PART = re.compile(r'\bpart\b|last part', re.I)

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

# fullraces title grammar — no teams at all. Two shapes by series:
#   "NASCAR Cook Out 400 - Full Race Replay - August 15, 2026"            (series-led, no session)
#   "RACE - F1 2026 - Hungarian Grand Prix - Full Race Replay - July 26, 2026 - Formula 1"  (session-led)
# MotoGP entries are dateless, WRC spans a date range (its LAST day is the rally's end), F2/F3
# carry only a year. The parser floors those to no-date and the walk drops them — an entry with
# no month-day cannot sit in a day-keyed store, and inventing its date would be fabrication.
_FR_SESSION_WORDS = ("race", "qualifying", "quali", "practice", "fp1", "fp2", "fp3", "sprint",
                     "warmup", "shootout")
_FR_SERIES = {
    "nascar": "nascar", "f1": "f1", "motogp": "motogp", "indycar": "indycar", "indy car": "indycar",
    "wrc": "wrc", "formula2": "f2", "formula 2": "f2", "f2": "f2",
    "formula3": "f3", "formula 3": "f3", "f3": "f3",
}

# One listing entry: the entry link, the H3 title, and the poster art. The entry id is captured
# by the split in `listing()`, not by a regex of its own. Shared by both uCoz sites.
_ENTRY_LINK_TITLE = re.compile(
    r'<h3><a href="(?P<href>/[a-z0-9-]+)"[^>]*>(?P<title>[^<]+)</a></h3>'
)
_ENTRY_POSTER = re.compile(r'<div class="poster">\s*<a href="[^"]+">\s*<img src="(?P<img>[^"]+)"')

# The embed iframe forms live at `_OKRU_IFRAME`/`_DM_IFRAME` below, host-pinned — see the
# comment there for why the generic first-iframe scan this file once used is gone.

# basketball-video's ok.ru Watch button (protocol-relative; rugby24's variant appends
# `?nochat=1&autoplay=1`). Deliberately host-pinned: the other servers (dailymotion, filemoon)
# have not been player-probed, and an unproven host in the app's WebView is a black screen, not a
# feature.
_BBV_OK = re.compile(r'href="(?P<src>//ok\.ru/videoembed/\d+(?:\?[^"]*)?)"')

# The ok.ru iframe, host-pinned at the regex rather than checked after a generic iframe search.
# rugby24 mounts an ad iframe (`bysesukior.com`) alongside the player and nothing orders them;
# first-iframe-then-check would skip a live entry the day the ad loads first.
_OKRU_IFRAME = re.compile(r'<iframe[^>]*\ssrc="(?P<src>(?:https?:)?//ok\.ru/videoembed/\d+(?:\?[^"]*)?)"')

# Dailymotion alternates, both wired forms across the family (iframe, and basketball-video's
# su-button). Host-pinned for the same reason as ok.ru above — the generic iframe scan would
# admit ad frames. Player-proven: the watch-wrestling shells mount dailymotion iframes and the
# app plays them, so a dm rung is a real fallback, not a hopeful one.
_DM_IFRAME = re.compile(
    r'<iframe[^>]*\ssrc="(?P<src>(?:https?:)?//(?:www\.)?dailymotion\.com/embed/video/[^"]+)"')
_DM_LINK = re.compile(
    r'href="(?P<src>(?:https?:)?//(?:www\.)?dailymotion\.com/video/[^"]+)"')

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


def _parse_fr_title(title: str) -> dict | None:
    """
    fullraces: extract `{series, session, date}` — there are no teams to extract. The first
    dash-segment is a session word ("RACE", "Sprint Qualifying" — then the series lives in the
    second segment) or the series-led event name itself ("NASCAR Cook Out 400", which carries
    the series inline). A date range (WRC) yields its last day. NBSP entities in titles are
    normalised first.

    The session is found by WORD-SCAN of the first segment, not exact equality: "Sprint
    Qualifying" is as much a non-race as "Qualifying", but an exact rule parsed it as
    session-less — the bucket meant for series-led races, which are always the race itself.
    Weekend coverage posts ("Paddock Uncut", "Drivers Press Conference") contain neither a
    session word nor a series name and parse to `{series: "", session: ""}`; the item filter
    drops that pair (see `_replay_items`). Series detection scans the first segment, then the
    second only when the entry is session-led — a coverage post's series ("Paddock Uncut -
    F1 2026 - ...") must NOT promote it to a series-led race.
    """
    title = title.replace("\xa0", " ").replace("&#39;", "'")
    segs = [s.strip() for s in title.split(" - ")]
    d = _BBV_DATE.search(title)
    if not d:
        return None
    month = _MONTHS.get(d.group("month").lower())
    if not month:
        return None
    first = segs[0].lower().strip(" .")
    # "3rd Practice" -> "practice": F1's numbered sessions carry an ordinal prefix.
    sess_word = re.sub(r"\d+(st|nd|rd|th)\s+", "", first)
    session = next((w for w in sess_word.replace(":", " ").split()
                    if w in _FR_SESSION_WORDS), "")
    series = ""
    series_segs = [segs[0]] + ([segs[1]] if session and len(segs) > 1 else [])
    for seg in series_segs:
        for key, cid in _FR_SERIES.items():
            if key in seg.lower():
                series = cid
                break
        if series:
            break
    return {
        "away": "", "home": "", "game": 1,
        "date": f"{int(d.group('year')):04d}-{month:02d}-{int(d.group('day')):02d}",
        "series": series,
        "session": session,
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


def fr_listing(min_date: str) -> list[dict]:
    """fullraces listing: the mixed-series home feed (`/?pageN`), newest first, fr parser."""
    return _walk_ucoz(FR_BASE + "/", FR_BASE, "fr", _parse_fr_title, FR_MAX_PAGES, min_date)


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


def _ww_embed_url(tag: str, date: str, post: int, source: int, button: int) -> str:
    """One option's documented embed API address."""
    y, m, d = date.split("-")
    return f"{WW_EMBED}/{tag}/{m}-{d}-{y}/select-post-{post}/{source}/{button}"


def _ww_post_options(html: str, date: str) -> list[dict]:
    """
    Parse one post page's option tree into ranked candidates.

    The ranking encodes how a person picks: the FULL SHOW single file first (the site's own
    featured order — VidQ/ VidFrame/ OK.ru "Full Show" buttons), then multi-part fallbacks,
    then "Live replay" sections last (a broadcast capture, not the finished upload). Host
    names are never special-cased — a never-seen-before host rides through on the same
    (source, button) address, and the app's door+latch decide if it plays.
    """
    cat = _WW_CAT.search(html)
    if not cat:
        return []
    tag = cat.group(1).lower()[::-1].replace(" ", "")
    post_m = _WW_SELECT_POST.search(html)
    post = int(post_m.group(1)) if post_m else 1

    singles: list[dict] = []
    parts: list[dict] = []
    live: list[dict] = []
    source = 0
    for chunk in html.split(_WW_SRC_SPLIT)[1:]:
        label_m = _WW_SECTION_LABEL.search(chunk)
        buttons = [unescape(_WW_BTN_TEXT.sub("", b)).strip()
                   for b in _WW_SRCBTN.findall(chunk)]
        if not label_m or not buttons:
            continue  # "Quick links!" and other non-media sections carry no srcbtn
        source += 1  # the API counts media sections only — see the regex docs above
        section = unescape(label_m.group(1)).strip()
        is_live = "live replay" in section.lower()
        for button, blabel in enumerate(buttons, 1):
            cand = {
                "url": _ww_embed_url(tag, date, post, source, button),
                "label": (section + " — " + blabel).strip()[:80],
                "part": bool(_WW_PART.search(blabel)),
                "live": is_live,
            }
            (live if is_live else parts if cand["part"] else singles).append(cand)
    return singles + parts + live


# home-15 pagination, probed 2026-08-22: the pretty `/home-15/page/2/` and `?paged=2` forms both
# silently serve page 1 (same posts, `data-paged` stuck at 2) — the theme pages exclusively via
# `#load-more-posts`, which POSTs `action=_load_more_posts&paged=N&search=&catid=` to
# admin-ajax.php (handler: generatepress menu.min.js `gpLoadMorePosts`). Plain urllib POST works;
# `catid` may be empty. Each page carries 12 posts, newest first.
_WW_AJAX = WW_BASE + "/wp-admin/admin-ajax.php"
_WW_WALK_DAYS = 7      # one walk serves every caller; callers filter to their own window
_WW_MAX_PAGES = 8      # ~12 posts/page; hard stop so a parsing regression cannot crawl forever
_WW_HOME_CACHE: list[dict] | None = None  # per-process: apply() and build.py both walk


def _ww_ajax_page(paged: int) -> str:
    body = urllib.parse.urlencode(
        {"action": "_load_more_posts", "paged": str(paged), "search": "", "catid": ""}
    ).encode()
    req = urllib.request.Request(
        _WW_AJAX, data=body,
        headers={"User-Agent": UA, "Referer": WW_HOME,
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=90, context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


def _ww_home(min_date: str) -> list[dict]:
    """
    Walk home-15 once per process, fetch each dated post's page, and parse its option tree.
    Both consumers — UFC cards for the matcher and everything else for the shows array — are
    category splits of this one walk. The walk runs at a fixed 7-day boundary (wider than any
    caller's window) so caller order cannot shrink what a later caller sees.

    Posts whose titles carry no date (TUF "S34E11", Contender "Season 10, Week 2") are skipped,
    same standing decision as before: nothing anchors their freshness. A post that parses to no
    candidates is dropped too — an option-less post is not playable through the ladder.
    """
    global _WW_HOME_CACHE
    if _WW_HOME_CACHE is None:
        floor = (datetime.now(timezone.utc) - timedelta(days=_WW_WALK_DAYS)).strftime("%Y-%m-%d")
        posts: list[dict] = []
        seen: set[str] = set()
        paged = 1
        while paged <= _WW_MAX_PAGES:
            try:
                html = _get(WW_HOME) if paged == 1 else _ww_ajax_page(paged)
            except Exception:
                break  # a listing page that will not load: ship what the earlier pages held
            page_dates: list[str] = []
            for m in _WW_ENTRY.finditer(html):
                href = m.group("href")
                if href in seen:
                    continue
                seen.add(href)
                title = unescape(m.group("title")).strip()
                date = _ww_date(title)
                if not date:
                    continue
                page_dates.append(date)
                if date < floor:
                    continue
                posts.append({
                    "replay_id": "ww:" + href.rstrip("/").rsplit("/", 1)[-1][:50],
                    "title": title,
                    "date": date,
                    "page_url": href,
                })
            # newest-first: when a page's NEWEST dated post predates the window, the walk is done
            if not page_dates or page_dates[0] < floor:
                break
            paged += 1

        def load(p: dict) -> None:
            try:
                html = _get(p["page_url"])
            except Exception:
                return
            cat = _WW_CAT.search(html)
            p["category"] = unescape(cat.group(1)).strip() if cat else ""
            p["candidates"] = _ww_post_options(html, p["date"])

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(load, posts))
        _WW_HOME_CACHE = [p for p in posts if p.get("candidates")]
    return [p for p in _WW_HOME_CACHE if p["date"] >= min_date]


def collect_ww(min_date: str) -> list[dict]:
    """watch-wrestling UFC cards, ready for the matcher: one post per card, dated, carrying the
    full ranked option ladder. `embed_url` stays the first ranked option — the matcher and the
    persistent store speak it, and it is the ladder's rung 0."""
    out = []
    for p in _ww_home(min_date):
        if p["category"].upper() != "UFC":
            continue
        out.append({
            "replay_id": p["replay_id"],
            "title": p["title"],
            "date": p["date"],
            "page_url": p["page_url"],
            "embed_url": p["candidates"][0]["url"],
            "candidates": p["candidates"],
        })
    return out


def collect_ww_shows(min_date: str) -> list[dict]:
    """
    Every dated watch-wrestling post that is NOT a UFC card — weekly shows (Raw, SmackDown,
    Dynamite, Collision, NXT) and the specials the home walk surfaces with no extra code (ROH,
    TNA, NJPW, GCW, TJPW) — as first-class replay items, each carrying its own option ladder.

    These have no fixture representation — no feed event will ever carry them — so they are not
    matched; they are the feed's `shows` array. Sorted newest first; the window is the caller's
    (a weekly cadence wants ~6 days so exactly one episode per show is live at a time).
    """
    out = []
    for p in _ww_home(min_date):
        if p["category"].upper() == "UFC":
            continue  # UFC cards surface through event matching, not the shows array
        out.append({
            "id": p["replay_id"],
            "title": p["title"],
            "date": p["date"],
            "replay_url": p["candidates"][0]["url"],
            "replay_candidates": p["candidates"],
            "source_url": p["page_url"],
        })
    out.sort(key=lambda s: s["date"], reverse=True)
    return out


def _ucandidates(html: str) -> list[dict]:
    """
    A uCoz entry page's playable servers as a ranked ladder: ok.ru first (the host the app's
    tap pump is calibrated for), dailymotion alternates behind it. Both are host-pinned at
    their regexes — a generic iframe scan would ship the ad frames that ride entry pages
    unordered (rugby24's `bysesukior.com`).

    Query strings (rugby24's `?nochat=1&autoplay=1`) are dropped: the bare videoembed URL is
    the canonical form every other source ships and the one the app's tap pump is calibrated
    for — an operator's autoplay flag is not ours to honour.
    """
    urls: list[str] = []
    for rx in (_OKRU_IFRAME, _BBV_OK, _DM_IFRAME, _DM_LINK):
        for m in rx.finditer(html):
            src = m.group("src")
            if src.startswith("//"):
                src = "https:" + src
            src = src.split("?")[0]
            if src not in urls:
                urls.append(src)
    return [{"url": u, "label": "OK.ru" if "ok.ru" in u else "Dailymotion",
             "part": False, "live": False} for u in urls]


def _embed(entry: dict) -> dict | None:
    """
    Fetch one mlblive entry page and extract its option ladder. Same extraction as
    `_okru_embed` (kept as its own name — the mlblive walk and its docs speak it).

    None for no entry page or no embed yet — a game posted before its video finished uploading
    has no iframe, and must not enter the store half-alive.
    """
    return _okru_embed(entry)


def _okru_embed(entry: dict) -> dict | None:
    """
    Fetch one entry page and take its option ladder: ok.ru first, whatever form the site wires
    it in (basketball-video's Watch button, nfl-video/mlblive's iframe), dailymotion alternates
    behind it.

    None for every other outcome — including the placeholder shells (Watch buttons pointed at
    stale TV-schedule sites) the operators post in place of real embeds. A shell must not enter
    the store half-alive: an entry with no ok.ru at all stays out, even if a dailymotion
    alternate happens to be wired — dm ships as a rung on a known-good entry, never as the
    reason to admit one.
    """
    try:
        html = _get(entry["page_url"])
    except Exception:
        return None
    cands = _ucandidates(html)
    if not any("ok.ru" in c["url"] for c in cands):
        return None
    return {**entry, "embed_url": cands[0]["url"], "candidates": cands}


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


def collect_fr(min_date: str) -> list[dict]:
    """fullraces: listing, then concurrent embed extraction."""
    entries = fr_listing(min_date=min_date)
    if not entries:
        return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(_okru_embed, entries))
    return [g for g in got if g]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _cands(r: dict) -> list[dict]:
    """A replay row's option ladder. The `or` fallback re-wraps a bare `embed_url` — the
    persistent store carries matches from runs that predate the candidates field, and those
    must keep playing as one-rung ladders."""
    return r.get("candidates") or [
        {"url": r["embed_url"], "label": "Replay", "part": False, "live": False}]


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
                "candidates": _cands(r),
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
                "candidates": _cands(r),
                "poster": r.get("poster", ""),
                "title": r["title"],
                "replay_id": r["replay_id"],
            }
            break
    return found


def _match_races(events: list[Event], replays: list[dict]) -> dict[str, dict]:
    """
    The motorsport variant: **series + weekend window, deliberately no name matching**. The site
    names races by sponsor ("NASCAR Cook Out 400") where ESPN names them by venue ("NASCAR Cup
    Series at Richmond") and prefixes sponsors onto Grand Prix names ("AWS Hungarian Grand
    Prix") — neither string contains the other, so a name rule would reject the TRUE pairs it was
    meant to confirm. One race per series per weekend makes series+date sufficient instead.

    Window: ESPN dates a race weekend at its FIRST session (measured: F1 Hungary event Jul 24,
    race Sunday Jul 26) while the site dates the race itself, so a replay matches when its date
    falls 0-3 days AFTER the fixture's Eastern date. NASCAR/IndyCar measured gap: 0.

    Session rule (user decision 2026-08-19): only race replays match — an entry whose parsed
    session is a qualifying/practice/sprint word never enters, and a bare session-less entry
    (NASCAR/IndyCar style, always the race) does.
    """
    found: dict[str, dict] = {}
    for e in events:
        edate = _eastern_date(e.start_utc)
        try:
            base = datetime.strptime(edate, "%Y-%m-%d")
        except ValueError:
            continue
        for r in replays:
            if r.get("series") != e.competition_id:
                continue
            if r.get("session") not in ("", "race"):
                continue
            try:
                rday = datetime.strptime(r["date"], "%Y-%m-%d")
            except ValueError:
                continue
            if not (0 <= (rday - base).days <= 3):
                continue
            found[e.event_id] = {
                "embed_url": r["embed_url"],
                "candidates": _cands(r),
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
    # fullraces: series + weekend window, race sessions only (see `_match_races`).
    found.update(_match_races(
        [e for e in events if e.competition_id in ("f1", "nascar", "motogp", "indycar")],
        by_prefix.get("fr", [])))
    return found


def apply(events: list[Event], today: str, store_path: Path, backfill_days: int = 7) -> tuple[int, list[dict]]:
    """
    Scrape, match, merge into the persistent store, stamp replay fields onto events, and assemble
    the feed's `replays` array — replay items that exist by THEIR OWN DATE, not their fixture's.

    Returns `(stamped_event_count, replay_items)`.

    ## The two rules this function enforces (user directive, 2026-08-22)

    **Aired only.** SofaScore's `final` is the only finished signal the feed trusts, so a replay
    is stamped onto an event — and a matched item is published — only when the fixture has
    actually finished. A replay post that exists while the card is still live is a broadcast
    capture, not a replay; it enters when the fixture flips final (the feed rebuilds every ~5
    minutes, so that flip is itself near-real-time).

    **A replay outlives its fixture.** A replay card used to vanish the moment its fixture left
    the 3-day fixture window — a Sunday race was unwatchable by Wednesday, mid-week, while the
    replay itself was hours old. Items in the `replays` array carry their own date and their own
    ladder, exactly like `shows`, so they persist by the fan's timeline instead: daily sports
    (baseball, basketball, NFL) hold ~4 days, weekly ones (rugby rounds, race weekends, fight
    cards) hold ~7 — the latest edition of a weekly thing stays watchable until the next one
    replaces it. Nothing older than 7 days is published.
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
    #
    # Per-source windows, not one global one: the fan's timeline differs by cadence. Daily sports
    # are watched same-day or next-day (4 days covers the fixture window plus upload lag); weekly
    # sports are watched until the next edition (7 days). A single 7-day window for all would
    # mean ~100 MLB entry fetches per build on a 5-minute loop for cards nobody wants on day 5.
    daily = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=4)).strftime("%Y-%m-%d")
    weekly = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    fresh_replays: list[dict] = []
    for collector, floor in ((collect, daily), (collect_bbv, daily), (collect_nfl, daily),
                             (collect_ww, weekly), (collect_r24, weekly), (collect_fr, weekly)):
        try:
            fresh_replays.extend(collector(floor))
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
        # FINAL only — the aired rule. SofaScore stays the sole authority on finished (the same
        # rule as the live intersection: never infer from the clock, never borrow from the site).
        if r and e.status is Status.FINAL:
            e.replay_url = r["embed_url"]
            e.replay_candidates = _cands(r)
            e.replay_poster = r.get("poster") or ""
            hit += 1
    return hit, _replay_items(fresh_replays, fresh, events, min_date)


# A source's item defaults, used when no fixture enriches the entry. `competition` is the card's
# kicker on screen — the site's own comp segment when it parses one (rugby24 always does), the
# league name otherwise.
_ITEM_DEFAULTS = {
    "mlblive": ("baseball", "MLB"),
    "bbv": ("basketball", "Basketball"),
    "nflv": ("american-football", "NFL"),
    "fr": ("motor-sports", "Motorsports"),
    "ww": ("mma", "UFC"),
}


def _replay_items(fresh_replays: list[dict], fresh_matches: dict[str, dict],
                  events: list[Event], min_date: str) -> list[dict]:
    """
    The feed's `replays` array: every source's recent, AIRED entries as self-dating items —
    `{id, title, date, sport, competition, replay_url, replay_candidates, poster, source_url}` —
    the same shape contract as `shows`, so a card never depends on a fixture existing.

    Aired, for an entry WITH a matched fixture, means the fixture is FINAL (a live capture under
    an in-progress card waits for the flip). For an entry with no fixture — union rugby, F2/F3,
    anything the fixture sources do not carry — the replay site posting it is itself the aired
    signal, with one guard: a watch-wrestling card whose every rung is a live-replay capture is
    still in progress and stays out.

    `sport` is the SOURCE's, never the fixture's: rugby24's pool is one feed of both codes plus
    AFL, and taking sport from whichever entries happen to match fixtures split that one pool
    into two buckets ("rugby-league" vs "rugby") — two shares of the app's per-sport row cap for
    what is one sport family, and a mix that double-counts. The source defaults are already the
    fixture taxonomy's names for every matched league, so enrichment would add nothing anyway.

    fullraces items are RACE sessions only, the same standing scope decision (2026-08-19) the
    matcher enforces: a fan who missed the weekend watches the race, not Tuesday's qualifying.
    """
    ev_by_id = {e.event_id: e for e in events}
    by_replay_id = {v["replay_id"]: ev_by_id[k] for k, v in fresh_matches.items() if k in ev_by_id}
    items: list[dict] = []
    for r in fresh_replays:
        if r["date"] < min_date:
            continue
        prefix = r["replay_id"].split(":", 1)[0]
        sport, comp = _ITEM_DEFAULTS.get(prefix, ("", ""))
        if prefix == "r24":  # the site's own comp segment separates AFL from both rugby codes
            c = _norm(r.get("comp", ""))
            if c == "afl":
                sport, comp = "australian-football", "AFL"
            else:
                sport, comp = "rugby", r.get("comp", "") or "Rugby"
        elif prefix == "fr":
            if r.get("session") not in ("", "race"):
                continue  # qualifying/practice/sprint: matcher scope is races, items match it
            if not r.get("session") and not r.get("series"):
                continue  # neither session-led nor series-led: weekend coverage, not a race
            if r.get("series"):
                comp = r["series"].upper()
        ev = by_replay_id.get(r["replay_id"])
        if ev is not None:
            if ev.status is not Status.FINAL:
                continue  # matched but not aired yet — a broadcast capture, not a replay
        elif prefix == "ww":
            if all(c.get("live") for c in r.get("candidates", []) if c.get("url")):
                continue  # every rung is a live capture: the card is still in progress
        items.append({
            "id": r["replay_id"],
            # uCoz listing titles carry HTML entities raw ("Hawke&#39;s Bay"); the feed ships text.
            "title": unescape(r["title"]),
            "date": r["date"],
            "sport": sport,
            "competition": comp,
            "replay_url": r["embed_url"],
            "replay_candidates": _cands(r),
            "poster": r.get("poster", ""),
            "source_url": r.get("page_url", ""),
        })
    items.sort(key=lambda i: i["date"], reverse=True)
    return items
