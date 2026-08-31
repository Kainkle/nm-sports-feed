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
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import Event

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36"

# Official league channels. Resolved by searching YouTube and reading the channel off real results rather
# than guessing ids — an earlier guess resolved to "NHL Europe" instead of the main NHL channel, which
# would have produced a feed of the wrong region's clips.
#
# THE 2026-08-30 SWEEP resolved every id below by fetching the channel page and then reading the RSS's
# own <title> — the channel's name in its own words, which is the only proof that cannot be stale. What
# the sweep also established, and why most channels here are new:
#
#   - The OFFICIAL Premier League channel posts NO per-game highlights anymore. Four days of its window
#     across a full matchweek held shorts, single-goal clips and compilations — zero game highlights.
#     Sky Sports PL posts every game ("Man Utd 5-2 Ipswich | Premier League Highlights"); that is the
#     channel a fixture can actually be matched against.
#   - NBC Sports (US PL rights) also posts none — two days of window with MLB highlights present and
#     zero PL ones. The per-game PL gap is real, not a window artifact.
#   - CBS Sports Golazo is the widest US-facing soccer source: uniform English titles ("Lazio vs. Genoa:
#     Extended Highlights | Serie A") for Serie A (every game), marquee PL recaps, and the UCL in season.
#   - A search-engine id for "UEFA Champions League" served a DEAD channel (entries a year old, shorts
#     spam) and a handle guess for Sky resolved to a 3-video impostor ("TRS SkySports"). Both are the
#     hardcoding trap in action: plausible-looking ids that would have matched nothing forever.
CHANNELS: dict[str, str] = {
    "mlb": "UCoLrcjPV5PbUrUyXq5mjc_A",
    "nba": "UCWJ2lWNubArHWmf3FIHbfcQ",
    "wnba": "UCO9a_ryN_l7DIDS-VIt-zmw",
    "afl": "UCui2LJ0N-Zi7Uw_sHyE5kgQ",
    "nrl": "UCME0EifJ3Xm3mGmLdBz1Kyw",
    # CORRECTED TWICE, and the second correction is the lesson. The 2026-08-30 sweep "verified" an id
    # that actually resolves to "UFC FIGHT PASS" - the streaming service's channel, which posts regional
    # promos (BFL, Shooto Brazil, Fury FC) and PRIDE classics, never main-card highlights. An id you have
    # not read back through the RSS <title> THIS session is unverified, full stop. This one's <title>
    # reads "UFC", resolved from @UFC's own page three ways before fetching.
    "ufc": "UCvgfXK4nTYKudb0rFR6noLA",
    # REPLACED 2026-08-30: the old id resolved to "NHL Europe". This one's RSS <title> reads "NHL" —
    # the main channel. Out of season at the swap; in-season shape ("EXTENDED HIGHLIGHTS") rides the
    # default gate, and coverage.json's highlight counts will say if it does not.
    "nhl": "UCqFMzb-4AUf6WAIbl132QKA",
    # New 2026-08-30, every id verified against its RSS title the same day:
    "nfl": "UCDVYQ4Zhbm3S2dlz7P1GBDg",          # "NFL"
    "ncaaf": "UCzRWWsFjqHk1an4OnVPsl9g",        # "ESPN College Football"
    "epl_sky": "UCNAf1k0yIjyGu3k9BwAg3lg",      # "Sky Sports Premier League"
    "cbs_golazo": "UCET00YnetHT7tOpu12v8jxg",   # "CBS Sports Golazo"
    "laliga": "UCTv-XvfzLNe4i4IGWAm4sbmA",      # "LALIGA EA SPORTS"
    "seriea": "UCBJeMCIeLQos7wacox4hmLQ",       # "Serie A"
    "bundesliga": "UC6UL29enLNe4mqwTfAyeNuw",    # "Bundesliga"
    "mls": "UCSZbXT5TLLW_i-5W8FZpFsg",          # "Major League Soccer"
    # New 2026-08-31 (the WWE/boxing commission), every id resolved from the channel's own page
    # (three patterns against the page, then the RSS <title> read back):
    "wwe": "UCJ5v_MCY6GNUBTO8-D3XoAg",          # "WWE"
    "aew": "UCFN4JkGP_bVhAdBsoV9xftA",          # "All Elite Wrestling"
    "pbc": "UCWXYAGB9SadlL6p5Bb66wWw",          # "Premier Boxing Champions"
    "toprank_boxing": "UCbzRzJNHx7ZLlJML9BjZQVQ",   # "Top Rank Boxing"
    "dazn_boxing": "UCurvRE5fGcdUgCYWgh-BDsg",  # "DAZN Boxing"
}

# Competitions each channel is allowed to satisfy. A channel must never be matched against a sport it does
# not cover, or an MLB highlight could be linked to a hockey fixture on a name collision.
#
# uefa.champions rides cbs_golazo alone: the league phase starts mid-September, CBS posts every game in
# the same "X vs. Y: Extended Highlights | UEFA Champions League" shape as its Serie A uploads, and the
# official @UEFA channel's titles are free-form headlines ("HIGHLIGHTS: LASK's comeback against Celtic")
# that no rigid gate should be trusted on unverified.
#
# Channel order in CHANNELS is load-bearing where two channels cover one competition: dict order decides
# which video wins (first match claims the event). epl_sky precedes cbs_golazo because Sky posts every
# game against CBS's marquee-only recaps; cbs_golazo precedes seriea because the extended cut is the
# longer watch.
CHANNEL_COMPETITIONS: dict[str, set[str]] = {
    "mlb": {"mlb"},
    "nba": {"nba"},
    "wnba": {"wnba"},
    "nhl": {"nhl"},
    "afl": {"afl"},
    "nrl": {"rugby-league"},
    "ufc": {"ufc", "mma-sofa"},
    "nfl": {"nfl"},
    "ncaaf": {"ncaaf"},
    "epl_sky": {"eng.1"},
    "cbs_golazo": {"ita.1", "uefa.champions", "eng.1"},
    "laliga": {"esp.1"},
    "seriea": {"ita.1"},
    "bundesliga": {"ger.1"},
    "mls": {"usa.1"},
    # The 2026-08-31 commission: "every league we do should be in here." WWE/AEW fixtures are
    # show-vs-promotion events (the TVmaze pipeline), boxing fixtures come out of the SofaScore
    # combat category via adapters._combat_split — the matcher's both-sides rule works unchanged
    # for both shapes.
    "wwe": {"wwe"},
    "aew": {"aew"},
    "pbc": {"boxing"},
    "toprank_boxing": {"boxing"},
    "dazn_boxing": {"boxing"},
    # Deliberately absent: Ligue 1 (ESPN's fra.1 unverified from this egress AND no verified channel —
    # two blockers, revisit when either clears), motor (race titles carry no team pair to match on),
    # TNA (no verified channel yet and no fixture in the current window).
}

# The shape gate now lives per-channel (see CHANNEL_GATES below). One load-bearing detail survives from
# the original single gate: a bare `v` is the Australian convention (AFL, NRL), and it is word-boundary
# anchored precisely BECAUSE it is a single letter — unanchored it would fire inside any word containing
# a v and match essentially everything.

# Full names AND 3-letter abbreviations (WWE/AEW write "Aug. 29" — the abbreviated form with a
# period is their house style, and without it every one of their titles silently fell through to
# the +/- 2 day window instead of the exact-date rule). `Sept` gets its own branch because it is
# the only 4-letter abbreviation in use.
_MONTHS_RE = (
    "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?"
    "|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)

# Two shapes: MLB parenthesises the date and omits the year, WNBA writes it plainly with one. The leading
# paren is optional so both match. Titles carrying no date at all — AFL and NRL give a round number
# instead — fall through to the +/- 2 day window against the fixture list.
_TITLE_DATE = re.compile(
    r"\(?\b(?P<month>" + _MONTHS_RE + r")\.?\s+(?P<day>\d{1,2})\b",
    re.I,
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
_MONTHS.update({m[:3]: n for m, n in _MONTHS.items()})   # jan feb … dec
_MONTHS["sept"] = 9

# ── The shape gate, per channel ─────────────────────────────────────────────────────────────────────
#
# One gate stopped fitting at eight leagues. Reading the leagues' real windows (2026-08-30) showed four
# title dialects where the original code assumed one:
#
#   - NFL game recaps carry NO keyword at all — "Chicago Bears vs Tennessee Titans | 2026 Preseason
#     Week 3" is the whole title. Versus + both teams + the date window IS the gate there.
#   - LaLiga writes Spanish: "REAL MADRID 4 - 0 MÁLAGA CF | RESUMEN LALIGA EA SPORTS" — a SCORE as the
#     separator, RESUMEN as the keyword — and posts RUEDA DE PRENSA pressers and PREVIA previews that
#     also name both teams, which is why the channel carries exclusions.
#   - Serie A and the Bundesliga put a bare dash between the clubs: "CAGLIARI-INTER | HIGHLIGHTS",
#     "SV ELVERSBERG - BAYER 04 LEVERKUSEN | Highlights".
#   - Sky's PL titles carry a score ("Man Utd 5-2 Ipswich | Premier League Highlights") inside a river
#     of podcasts, interviews and reaction videos that name both teams too — excluded by word.
#
# The config is (keywords, separators, excludes), each read off a real title; an empty keyword list means
# no keyword is required. The default below is the ORIGINAL rule, so the seven channels that were already
# matching keep matching exactly as they did.
_VS_WORDS = re.compile(r"\b(?:vs\.?|v|at|@|against)\b", re.I)
_DASH = re.compile(r"[-–]")
_SCORE = re.compile(r"\d\s*[-–:]\s*\d")
_SEP_VS = [_VS_WORDS]
_SEP_SCORE = [_SCORE, _VS_WORDS]
_SEP_DASH = [_DASH, _VS_WORDS]

DEFAULT_GATE = (["highlight"], _SEP_VS, [])

CHANNEL_GATES: dict[str, tuple[list[str], list[str], list[str]]] = {
    # Verified on the real UFC window 2026-08-31: the main-card video is "Song Yadong vs Umar Nurmagomedov
    # | UFC Shanghai" - NO keyword. Around it, shorts spam ("OH MY GOD SONG YADONG #ufcshanghai") carries
    # no vs and dies on the separator; the pre-event "Hooker vs Parnasse | Fight Promo" and the
    # "Full Fight Marathon" compilations name both men too, so they are excluded by word.
    "ufc": ([], _SEP_VS,
            ["press conference", "interview", "promo", "watch along", "marathon"]),
    # Verified: NFL recap titles carry no keyword; versus + both teams is precise enough (single-team
    # "Best Plays vs Titans" content dies on the both-teams rule).
    "nfl": ([], _SEP_VS, []),
    "ncaaf": (["highlight"], _SEP_VS, []),
    "nhl": (["highlight"], _SEP_VS, []),
    # Sky: score OR versus separates the clubs; the studio river around the games is excluded by word.
    # Out-of-season "FULL REPLAY" classics hold no "highlight" keyword and die on the keyword gate.
    "epl_sky": (["highlight"], _SEP_SCORE,
                ["podcast", "interview", "reaction", "analyse", "analysis",
                 "post-match", "full-time"]),
    "cbs_golazo": (["extended highlights", "match recap", "highlights"], _SEP_VS,
                   ["podcast", "scoreline", "preview", "predicting", "ranking"]),
    "laliga": (["resumen", "goles", "highlight"], [_SCORE, _DASH, _VS_WORDS],
               ["rueda de prensa", "previa"]),
    "seriea": (["highlight"], _SEP_DASH, []),
    "bundesliga": (["highlight"], _SEP_DASH, []),
    # MLS floods its own window with reserve-league uploads; first-team fixtures never contain them.
    "mls": (["highlight"], _SEP_VS, ["next pro"]),
    # ── Wrestling and boxing, 2026-08-31 ─────────────────────────────────────────────────────────
    # WWE/AEW fixtures are show-vs-promotion, and a show's highlight reel carries NO separator —
    # "WWE SmackDown highlights: Aug. 29, 2026" is the whole title. Their separator lists are empty
    # (match() skips the check) and the BOTH-SIDES rule does the discriminating: home side must name
    # the show, away side the promotion. PLE videos self-exclude the same way — "Full NXT Heatwave
    # 2026 highlights" names no promotion, so no weekly-show fixture can claim it.
    "wwe": (["highlight"], [], ["top 10", "press conference", "preview", "promo"]),
    "aew": (["highlight"], [], ["press conference", "media scrum", "preview", "promo"]),
    # Boxing, read off live windows 2026-08-31. PBC posts exactly the fixture shape ("PBC FIGHT
    # HIGHLIGHTS: Brown vs Wiggins | August 22, 2026" — keyword, vs, date, all three). Top Rank's
    # window is classics, marathons and sparring around the live uploads — keyword-gated
    # conservative, the date agreement rule is what keeps an old "FULL FIGHT" off a current
    # fixture. DAZN's window is a river of pressers, workouts and reaction clips the keyword gate
    # alone kills; the excludes are the second line.
    "pbc": (["highlight"], _SEP_VS, ["preview"]),
    "toprank_boxing": (["highlight"], _SEP_VS, ["marathon", "livestream", "sparring"]),
    "dazn_boxing": (["highlight"], _SEP_VS,
                    ["press conference", "open workout", "livestream", "react"]),
}

# ── Team-name short forms ───────────────────────────────────────────────────────────────────────────
#
# Broadcast titles use short forms ESPN's own name fields never contain — Sky writes "Man Utd", headlines
# write "Spurs", CBS writes "Inter". Both sides of a fixture must land in the title, so one missing short
# form silently drops that club's every game. Keyed by _norm of a name the team already carries; the
# values are appended at match time. Grown from what live verification actually showed missing.
EXTRA_ALIASES: dict[str, list[str]] = {
    "manchesterunited": ["Man Utd"],
    "tottenhamhotspur": ["Spurs"],
    "wolverhamptonwanderers": ["Wolves"],
    "brightonandhovealbion": ["Brighton"],
    "newcastleunited": ["Newcastle"],
    "westhamunited": ["West Ham"],
    "leedsunited": ["Leeds"],
    "intermilan": ["Inter"],
    "internazionale": ["Inter"],
    "acmilan": ["Milan"],
    "asroma": ["Roma"],
    "hellasverona": ["Verona"],
    "atalantabc": ["Atalanta"],
    "athleticbilbao": ["Athletic"],
    "deportivolacoruna": ["Deportivo"],
    "bayernmunich": ["Bayern"],
    "bayernmunchen": ["Bayern"],
    # Wrestling shows: broadcast titles say "SmackDown"/"WWE Raw"/"Dynamite"; the TVmaze names are
    # long-form ("WWE Friday Night SmackDown"). One missing short form drops that show's every
    # episode — the same failure mode as the soccer clubs above.
    "wwefridaynightsmackdown": ["SmackDown"],
    "wwemondaynightraw": ["WWE Raw", "Monday Night Raw"],
    "wwenxt": ["NXT"],
    "aewdynamite": ["AEW Dynamite", "Dynamite"],
    "aewcollision": ["AEW Collision", "Collision"],
}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


def _entries(channel_id: str) -> list[dict]:
    """Recent uploads as (video_id, title, published). RSS holds only the latest 15."""
    xml = None
    # YouTube's RSS 404s transiently under load — measured 2026-08-31: two channels that had served
    # full windows minutes earlier returned 404 in the same sweep. One retry closes most of it; a
    # channel that still fails yields an empty list and the next build's store-accumulate heals the
    # rest (a fixture is never lost, only late).
    for attempt in (0, 1):
        try:
            xml = _get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
            break
        except Exception:
            if attempt:
                return []
            time.sleep(2)
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
        kws, seps, excs = CHANNEL_GATES.get(channel, DEFAULT_GATE)
        kw_re = re.compile("|".join(kws), re.I) if kws else None
        exc_re = re.compile("|".join(excs), re.I) if excs else None
        for entry in feeds.get(channel, []):
            title = entry["title"]
            # Shape gate first: this is what discards the ~half of uploads that are not game highlights.
            # Per-channel since 2026-08-30 — the dialect table at CHANNEL_GATES has the evidence.
            if exc_re and exc_re.search(title):
                continue
            if kw_re and not kw_re.search(title):
                continue
            if seps and not any(r.search(title) for r in seps):
                # An empty separator list is a configured dialect (wwe/aew): show-reel titles
                # carry no vs/dash/score, so there is nothing to require here.
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
                if not tdate and not _within_days(ev.start_utc, today, 2):  # +/- 2 days
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


# Show "abbreviations" are 3-letter TV codes — RAW, COL — that sit inside ordinary words ("drawn",
# "column") as substrings. For these competitions the sides match on names + aliases only; the
# short forms that DO identify a show arrive through EXTRA_ALIASES, which is word-shaped by
# construction ("WWE Raw", "Collision").
ABBREV_OFF = frozenset({"wwe", "aew"})


def _matches_both(norm_title: str, ev: Event) -> bool:
    """Every alias set is tried; both sides must land."""
    def side(team) -> bool:
        names = [team.name]
        if ev.competition_id not in ABBREV_OFF and team.abbrev:
            names.append(team.abbrev)
        names += list(team.aliases or [])
        names += EXTRA_ALIASES.get(_norm(team.name), [])
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
    # Cap the store by size rather than by a membership test that was always true: the previous filter
    # read `k in ids_recent or k in store`, and the second clause made the whole expression a no-op, so
    # nothing was ever evicted.
    if len(store) > 500:
        store = dict(list(store.items())[-500:])

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, indent=2))

    hit = 0
    for e in events:
        h = store.get(e.event_id)
        if h:
            e.highlight_video_id = h["video_id"]
            hit += 1
    return hit
