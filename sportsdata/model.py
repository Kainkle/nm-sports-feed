"""
The canonical model every source is normalised into.

This is the durable asset. Sources are commodities behind adapters and will be swapped as they degrade;
this schema and the id crosswalk it implies are what survive. See `docs/SPORTS_AGGREGATOR_PLAN.md`.

Deliberately plain dataclasses and stdlib only — this runs in CI on a schedule and must not carry a
dependency tree that can break the one job the boxes depend on.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Status(str, Enum):
    """
    The only statuses the app may act on.

    **`LIVE` is never inferred.** It is set only when a source explicitly reports an in-progress state.
    Deriving it from "now falls between start and start+3h" is the single most tempting shortcut here and
    it is banned: it produces a LIVE badge on a postponed game, on a rain delay, and on anything that
    finished early. Every research pass independently concluded that a wrong LIVE badge is worse than no
    badge, and this enum is where that rule is enforced.

    `UNKNOWN` is a real, useful value. A source that does not report status gets `UNKNOWN`, the app shows a
    schedule entry with no badge, and nobody is misled.
    """

    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"

    @property
    def is_playable_now(self) -> bool:
        """Whether the app may render a LIVE badge. One place, so it cannot drift."""
        return self is Status.LIVE


@dataclass
class Team:
    """
    One side of a fixture.

    `logo` is a URL, never image bytes. The box composites the matchup card itself from two badges, so the
    feed stays small and artwork is drawn at the exact size each surface needs. `logo_dark` exists because
    NHL publishes light/dark SVG pairs and a badge that vanishes on its background is a real defect.

    `aliases` seeds the reconciliation table — the names a provider's stream title might use for this team.
    Populated by adapters where the source offers short names, and grown by hand over time.
    """

    id: str
    name: str
    abbrev: str = ""
    logo: str | None = None
    logo_dark: str | None = None
    aliases: list[str] = field(default_factory=list)
    # Official brand colour as `RRGGBB`, when the source publishes one.
    #
    # Preferred over deriving a colour from the crest: extraction guesses at identity from pixels, and a
    # league telling us the club's colour outright is simply correct. The app falls back to extraction when
    # this is absent, so a source without colours still produces branded cards.
    color: str | None = None


@dataclass
class Event:
    """
    One fixture, normalised.

    `source_status` preserves the source's own status string verbatim alongside our mapped [Status]. When a
    source changes its vocabulary — and they do — the mapping breaks silently unless the original is kept.
    This field is what makes that diagnosable after the fact rather than requiring a re-run.
    """

    event_id: str
    sport: str
    competition_id: str
    competition_name: str
    start_utc: str
    status: Status
    home: Team
    away: Team
    source: str
    source_status: str = ""
    venue: str = ""
    # A live source's own words for where the game is up to — "Bottom 7th", "HT", "Q3 04:12".
    #
    # Only meaningful while status is LIVE. Carried because it is the difference between a card that says a
    # game is live and one that shows you the game is live.
    detail: str = ""
    # Distinguishes the second game of a doubleheader from the first. Without it, two fixtures between the
    # same teams on the same day are indistinguishable to the matcher — which is exactly where naive
    # name-plus-date matching sends a viewer to the wrong stream.
    game_number: int = 1

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def utc(value: str) -> str:
    """
    Normalise a timestamp to `...Z`.

    Both current adapters emit UTC already, so this is mostly a guard for the next source that does not.
    Timezones were named by every research pass as a top failure mode: store UTC, convert only at the edge,
    and never parse a human-formatted local time back into data.
    """
    if not value:
        return ""
    v = value.strip()
    if v.endswith("+00:00"):
        return v[:-6] + "Z"
    return v
