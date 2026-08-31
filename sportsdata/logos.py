"""League crest mirror — SofaScore's image routes 403 plain HTTP clients.

The app loads league crests with Coil (plain okhttp user-agent). SofaScore's
`/image` routes answer that with 403 — wire-measured 2026-08-31: 48-byte 403
body on every unique-tournament and category image route, while the same URLs
serve PNG/WebP bytes to a browser-impersonated client. So the app must never
hotlink SofaScore images; it reads the mirrored bytes from this repo's raw CDN
instead (`img/l/{id}.{ext}`), the same data plane the feed JSON itself uses.

Rule: WRITE IF MISSING. Crests are identity, not data — they change roughly
never, and re-downloading every run would commit byte-churn for no gain. A
missing file (new league added, or a mirror lost) heals on the next cron run.
A file that exists is never re-fetched. `boxing` has no verified source mark
and is deliberately absent — the app's name fallback is the honest state.
"""

import pathlib

from curl_cffi import requests

# id -> SofaScore image URL (UT = unique-tournament, CAT = motorsport category).
# Every URL wire-verified transparent + right-league (probe evidence in the
# 2026-08-31 session); re-verify against the league NAME on the wire if a
# route ever moves.
SOURCES = {
    "nfl": "https://api.sofascore.com/api/v1/unique-tournament/9464/image",
    "ncaaf": "https://api.sofascore.com/api/v1/unique-tournament/32199/image",
    "nba": "https://api.sofascore.com/api/v1/unique-tournament/132/image",
    "wnba": "https://api.sofascore.com/api/v1/unique-tournament/486/image",
    "mlb": "https://api.sofascore.com/api/v1/unique-tournament/11205/image",
    "nhl": "https://api.sofascore.com/api/v1/unique-tournament/234/image",
    "eng.1": "https://api.sofascore.com/api/v1/unique-tournament/17/image",
    "esp.1": "https://api.sofascore.com/api/v1/unique-tournament/8/image",
    "ita.1": "https://api.sofascore.com/api/v1/unique-tournament/23/image",
    "ger.1": "https://api.sofascore.com/api/v1/unique-tournament/35/image",
    "uefa.champions": "https://api.sofascore.com/api/v1/unique-tournament/7/image",
    "usa.1": "https://api.sofascore.com/api/v1/unique-tournament/242/image",
    "ufc": "https://api.sofascore.com/api/v1/unique-tournament/19906/image",
    "f1": "https://api.sofascore.com/api/v1/category/36/image",
    "motogp": "https://api.sofascore.com/api/v1/category/1325/image",
    "nascar": "https://api.sofascore.com/api/v1/category/150/image",
    "indycar": "https://api.sofascore.com/api/v1/category/1323/image",
    "rugby-league": "https://api.sofascore.com/api/v1/unique-tournament/294/image",
    "afl": "https://api.sofascore.com/api/v1/unique-tournament/656/image",
}

IMG_DIR = pathlib.Path(__file__).resolve().parent.parent / "img" / "l"

_WEBP_MAGIC = b"RIFF"


def _ext(data: bytes) -> str:
    # SofaScore serves PNG for crests and WebP for motorsport category marks.
    # Trust the bytes, not the content-type header.
    return "webp" if data[:4] == _WEBP_MAGIC and data[8:12] == b"WEBP" else "png"


def collect() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    have = {p.stem for p in IMG_DIR.iterdir() if p.is_file()}
    for league_id, url in SOURCES.items():
        if league_id in have:
            continue
        try:
            r = requests.get(url, impersonate="chrome", timeout=30)
            # The .app host answers a bad id with HTTP 200 and 0 bytes — gate
            # on both status and size, or we mirror an empty file forever
            # (write-if-missing would never heal it).
            if r.status_code != 200 or len(r.content) < 100:
                print(f"logos: {league_id}: HTTP {r.status_code}, {len(r.content)} bytes — skipped")
                continue
            (IMG_DIR / f"{league_id}.{_ext(r.content)}").write_bytes(r.content)
            print(f"logos: {league_id}: mirrored {len(r.content)} bytes")
        except Exception as exc:  # noqa: BLE001 — one bad league must not stop the feed build
            print(f"logos: {league_id}: {exc} — skipped")


if __name__ == "__main__":
    collect()
