"""
Top Stories — official league news, matched to the competitions the feed already carries.

## Why this source and not an RSS reader

ESPN publishes two public, keyless news endpoints per competition. The obvious one is RSS
(`espn.com/espn/rss/{league}/news`) and it works — but **it carries no artwork**, and a TV row of text-only
cards at three metres reads as a list of error messages. The site API at
`site.api.espn.com/.../news` returns the same editorial with a 1024x576 16:9 photograph attached to every
article. Measured across eight competitions: 8 of 8 articles carried art in every one of them.

That single difference is the whole reason this is not an RSS parser.

## Same contract as the fixtures

Keyless, no quota, no signup, one central poller — the rule that governs everything in `sportsdata/`. The
boxes never call this; they read the static JSON it lands in. And the competition ids are the *same ids the
fixture adapters emit*, so a story and a game about the same league agree on what league that is without a
second crosswalk to maintain.

## Editorial shape

A league feed is a firehose and a home row is eight cards, so selection is explicit rather than "the most
recent N overall":

* [PER_COMPETITION] from each league, so a busy NFL Monday cannot push every other sport off the row.
* Then global recency ordering, capped at [TOTAL].

Without the per-competition cap the row degenerates into one league's news, which is exactly what a
multi-sport home screen exists not to be.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any

# The competitions worth carrying news for, as (espn_sport, espn_league, competition_id, name).
#
# Deliberately NOT every adapter in the feed. SofaScore covers darts, snooker and cycling for fixtures, but
# ESPN publishes little or no editorial for them, and a league that contributes one stale article to a
# "Top Stories" row is worse than a league that contributes none.
#
# MLB and NHL appear here even though their FIXTURES come from the official league APIs: the league APIs
# publish schedules, not editorial, so there is no duplication to avoid — unlike scoreboards, where running
# both sources produced the same game twice.
NEWS_SOURCES: list[tuple[str, str, str, str]] = [
    ("baseball", "mlb", "mlb", "MLB"),
    ("hockey", "nhl", "nhl", "NHL"),
    ("football", "nfl", "nfl", "NFL"),
    ("basketball", "nba", "nba", "NBA"),
    ("basketball", "wnba", "wnba", "WNBA"),
    ("soccer", "eng.1", "eng.1", "Premier League"),
    ("soccer", "uefa.champions", "uefa.champions", "Champions League"),
    ("soccer", "usa.1", "usa.1", "MLS"),
    ("mma", "ufc", "ufc", "UFC"),
    ("racing", "f1", "f1", "Formula 1"),
    ("australian-football", "afl", "afl", "AFL"),
    ("rugby-league", "3", "rugby-league", "Rugby League"),
]

NEWS_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/news?limit={limit}"

# The keyless clip API. A `Media` story's link is the clip PAGE (espn.com/video/clip/_/id/<n>) — a full
# website whose player gates the video behind a preroll ad decision and refuses to start under any ad
# filtering (measured on the bench: the ad stack retries forever, the <video> never gets a source). This
# endpoint instead hands over the clip's own master HLS manifest, no page, no ads, no auth. Undocumented,
# so a clip whose resolution fails simply ships without `stream_url` and the app opens the reader instead.
CLIP_API = "https://api.espn.com/v1/video/clips/{clip_id}"

# Fetched per league, before the caps below are applied. Higher than [PER_COMPETITION] so that discarding
# premium and art-less articles still leaves a full quota.
FETCH_LIMIT = 12

# The editorial caps. See the module docstring — these are what keep the row multi-sport.
PER_COMPETITION = 3
TOTAL = 24


@dataclass
class Story:
    """
    One headline, as the app renders it.

    `image` is required, not optional, and that is enforced at selection time rather than left to the UI.
    A story card is a photograph with type over it; without art there is no card, only a caption, and the
    row is better one item shorter than carrying a grey box that looks like a failed download.
    """

    story_id: str
    headline: str
    summary: str
    image: str
    published_utc: str
    competition_id: str
    competition_name: str
    byline: str
    source: str
    link: str
    # The master HLS manifest for a clip story, resolved from [CLIP_API] at build time. Empty for text
    # stories and for clips whose resolution failed — the app treats empty as "open the reader", so the
    # field is an upgrade, never a dependency.
    stream_url: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_stream(story: Story) -> Story:
    """Attach the clip's HLS manifest, feed-side, for stories whose link is a clip page."""
    m = re.search(r"/video/clip/_/id/(\d+)", story.link)
    if not m:
        return story
    try:
        d = _get(CLIP_API.format(clip_id=m.group(1)))
        source = (((d.get("videos") or [{}])[0].get("links") or {}).get("source")) or {}
        story.stream_url = ((source.get("HLS") or {}).get("href")) or source.get("href") or ""
    except Exception:
        # A clip that cannot be resolved is a story that opens as text — never a failed build.
        pass
    return story


def _get(url: str) -> dict:
    # A BARE REQUEST, no headers. Verified in `docs/SPORTS_SOURCES_VERIFIED.md`: ESPN refuses a curl-style
    # User-Agent and accepts a header-less request. Adding a browser UA here would break it.
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as r:
        return json.loads(r.read())


def _art(article: dict) -> str:
    """
    The widest 16:9 image on the article.

    Shape matters more than size: ESPN attaches portrait crops and square headshots alongside the landscape
    header image, and a portrait photo in a 16:9 card is cropped to somebody's chin. Filtering on aspect
    first and picking the widest survivor is what gets the intended header shot.
    """
    best, best_w = "", 0
    for im in article.get("images") or []:
        url = im.get("url")
        w, h = im.get("width") or 0, im.get("height") or 0
        if not url or not w or not h:
            continue
        if abs((w / h) - (16 / 9)) > 0.12:
            continue
        if w > best_w:
            best, best_w = url, w
    return best


def _fetch(spec: tuple[str, str, str, str]) -> list[Story]:
    sport, league, cid, name = spec
    try:
        data = _get(NEWS_URL.format(sport=sport, league=league, limit=FETCH_LIMIT))
    except Exception:
        # A league's news failing must never take the build down; the fixture feed is the product and this
        # is an enrichment on top of it.
        return []

    out: list[Story] = []
    for a in data.get("articles") or []:
        # PREMIUM IS SKIPPED. Those articles are behind ESPN+, and a card that cannot be opened is a
        # promise the app cannot keep.
        if a.get("premium"):
            continue
        headline = (a.get("headline") or "").strip()
        image = _art(a)
        if not headline or not image:
            continue
        out.append(
            Story(
                story_id=str(a.get("id") or ""),
                headline=headline,
                summary=(a.get("description") or "").strip(),
                image=image,
                published_utc=(a.get("published") or a.get("lastModified") or "").strip(),
                competition_id=cid,
                competition_name=name,
                byline=(a.get("byline") or "").strip(),
                source="ESPN",
                link=(((a.get("links") or {}).get("web") or {}).get("href") or ""),
            )
        )
    return out[:PER_COMPETITION]


def collect() -> list[Story]:
    """
    Every competition's news, de-duplicated, **interleaved by league**, newest first within each.

    De-duplication is by story id and is not optional: ESPN files one article under several competitions,
    so a Champions League story reliably appears in the Premier League feed too. Without this the row shows
    the same headline twice, which reads as a bug in the app rather than in the source.

    ## Why round-robin and not a straight sort by time

    A global recency sort was the first version and it silently deleted whole sports. Twelve leagues at
    [PER_COMPETITION] each is 36 candidates trimmed to [TOTAL]; the American majors publish through the US
    evening, so the newest 24 were MLB, NFL and NBA - NHL, UFC and Rugby League fetched fine, ranked
    outside the cut, and vanished. The row looked like a three-sport app.

    Taking one story from each league before any league takes its second makes the cap trim *depth*
    instead of *breadth*, which is the correct thing to lose on a home screen. Leagues are ordered by their
    own newest story, so the row still opens on the freshest news in the world rather than on whichever
    league happens to sort first alphabetically.
    """
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(_fetch, NEWS_SOURCES))

    seen: set[str] = set()
    lanes: list[list[Story]] = []
    for batch in batches:
        lane = []
        for s in batch:
            key = s.story_id or s.headline
            if key in seen:
                continue
            seen.add(key)
            lane.append(s)
        if lane:
            lane.sort(key=lambda s: s.published_utc, reverse=True)
            lanes.append(lane)

    lanes.sort(key=lambda lane: lane[0].published_utc, reverse=True)

    out: list[Story] = []
    for depth in range(PER_COMPETITION):
        for lane in lanes:
            if depth < len(lane):
                out.append(lane[depth])
    out = out[:TOTAL]

    # Stream resolution runs on the FINAL selection, not on every fetched article — the boxes read
    # static JSON, so this is the only place a sports API is called for clip playback.
    with ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(_resolve_stream, out))
    return out
