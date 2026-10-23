#!/usr/bin/env python3
"""Trim Polymarket esports winner series to the in-game window.

Why: esports winner markets open days before the match and keep trading after
settlement. The long flat pre-match line and the 0.99/0.01 post-match plateau
carry no shape information, but they dominate the SVG charts and distort
low/high statistics. This module removes both ends and keeps only the live
match window.

Window rules (all local, no network):

1. floor = event start from runtime/pm_backfill/index_*.json, with
   docs/data/intel/matches.json start_time as fallback.
2. start = first sustained move away from the pre-window plateau, backtracked
   by 3 minutes so the opening rounds stay in the window.
3. end = first point of the terminal settlement plateau (>=0.99 or <=0.01).
   Requiring the plateau to run to the last point avoids cutting a live
   0.99 spike that later reverses.
5. The start is always inferred from the price series itself, anchored on the
   settlement end and bounded to the last 7 hours. ``game_start`` metadata is
   used only when it falls inside that plausible span: historical gamma
   timestamps are sometimes shifted by hours (UTC/CST mix-ups), and trusting
   them blindly re-introduces the pre-match plateau.
4. map N>1 floor = map N-1 settlement end + 1 minute, so the between-map flat
   segment is not counted as map N's shape.

If no start metadata exists, the series is returned unchanged and the caller
marks it ``window=full`` (explicit fallback, never silent).
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import statistics
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKFILL_GLOB = os.path.join(ROOT, "runtime", "pm_backfill", "index_*.json")
MATCHES_JSON = os.path.join(ROOT, "docs", "data", "intel", "matches.json")

CST = dt.timezone(dt.timedelta(hours=8))

# Longest plausible single match (BO5 with pauses / technical delays). Anything
# earlier than this is pre-match trading, not in-game shape.
MAX_MATCH_S = 7 * 3600


def _parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def _parse_start_time(value: str | None) -> float | None:
    """Parse matches.json start_time strings such as ``2026-10-17 22:00 CST``."""
    if not value:
        return None
    text = str(value)
    if "UTC" in text and "CST" not in text:
        return None
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", text)
    if not m:
        return None
    d = dt.datetime(*(int(x) for x in m.groups()), tzinfo=CST)
    return d.timestamp()


@lru_cache(maxsize=1)
def starts() -> dict[str, float]:
    """event_slug / market_slug / match id -> event start (unix seconds)."""
    out: dict[str, float] = {}
    for path in glob.glob(BACKFILL_GLOB):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for market in data.get("markets", []):
            ts = _parse_iso(market.get("game_start"))
            if not ts:
                continue
            for key in (market.get("market_slug"), market.get("event_slug")):
                if key:
                    out.setdefault(str(key), ts)
    try:
        matches = json.load(open(MATCHES_JSON, encoding="utf-8")).get("matches", [])
    except Exception:  # noqa: BLE001
        matches = []
    for match in matches:
        ts = _parse_start_time(match.get("start_time"))
        if not ts:
            continue
        for key in (match.get("id"), match.get("slug"), match.get("event_slug")):
            if key:
                out.setdefault(str(key), ts)
    return out


def event_start(slug: str, game: int | None = None) -> float | None:
    table = starts()
    if game is not None:
        return table.get(f"{slug}-game{game}") or table.get(slug)
    return table.get(slug)


def spacing_of(pts: list[tuple[float, float]]) -> float:
    if len(pts) < 2:
        return 60.0
    return statistics.median(pts[i + 1][0] - pts[i][0] for i in range(len(pts) - 1))


def settlement_end(pts: list[tuple[float, float]]) -> float:
    """First timestamp of the decided plateau (>=0.985 or <=0.015).

    A settled market sometimes wobbles around 0.98 for hours after the match
    (e.g. 0.9995 -> 0.984 -> 0.9995). Requiring a strictly unbroken terminal
    run would push the end hours late. Instead walk back through the final
    "decided" run (>=0.90 / <=0.10) and take the first tick that actually
    reaches 0.985 / 0.015 inside it. A genuine in-game reversal below 0.90
    still stops the walk, so live 0.99 spikes are not mistaken for settlement.
    """
    if len(pts) < 2:
        return pts[-1][0]
    last = pts[-1][1]
    if last >= 0.985:
        idx = len(pts) - 1
        while idx > 0 and pts[idx - 1][1] >= 0.90:
            idx -= 1
        while idx < len(pts) and pts[idx][1] < 0.985:
            idx += 1
        return pts[idx][0] if idx < len(pts) else pts[-1][0]
    if last <= 0.015:
        idx = len(pts) - 1
        while idx > 0 and pts[idx - 1][1] <= 0.10:
            idx -= 1
        while idx < len(pts) and pts[idx][1] > 0.015:
            idx += 1
        return pts[idx][0] if idx < len(pts) else pts[-1][0]
    return pts[-1][0]


def detect_start(
    pts: list[tuple[float, float]],
    floor: float | None,
    end: float | None = None,
) -> float:
    """First sustained exit from the pre-match baseline, minus 3 minutes.

    The pre-match phase is usually a long plateau around one price (the mode
    is close to the median). The match starts when the price leaves that
    baseline for good. Using a baseline-relative threshold avoids two failure
    modes seen in the data: a gradual 4-hour drift that never produces a
    single 8-cent jump, and small pre-match wobbles that look like "moves".
    """
    if not pts:
        return 0.0
    end = end if end is not None else pts[-1][0]
    lo = max(floor if floor is not None else pts[0][0], end - MAX_MATCH_S)
    window = [p for p in pts if lo - 1 <= p[0] <= end]
    if len(window) < 3:
        return lo
    spacing = spacing_of(window)
    hist = [p[1] for p in pts if end - 24 * 3600 <= p[0] <= end]
    baseline = statistics.median(hist) if hist else pts[0][1]
    threshold = 0.04 if spacing <= 120 else 0.05
    horizon = max(2, min(90, int(round(3600 / max(spacing, 1)))))
    for i, p in enumerate(window):
        if abs(p[1] - baseline) < threshold:
            continue
        # A real exit keeps moving: within the next hour the price must reach
        # at least twice the threshold away from the baseline. This drops
        # isolated pre-match blips that immediately mean-revert.
        future = window[i : i + horizon]
        if any(abs(x[1] - baseline) >= threshold * 2 for x in future):
            return max(lo, p[0] - 180)
    # No exit at all (never traded): keep the final 30 minutes, not 7 hours of
    # pre-match plateau.
    return max(lo, end - 1800)


def trim(
    pts: list[tuple[float, float]],
    start: float | None,
    end: float | None,
) -> list[tuple[float, float]]:
    if start is None and end is None:
        return pts
    if start is not None and end is not None and start > end:
        # Inconsistent metadata / overlapping market files: never return an
        # empty or reversed window. Fall back to the last hour before the end.
        start = max(pts[0][0], end - 3600)
    lo = start if start is not None else pts[0][0]
    hi = end if end is not None else pts[-1][0]
    # The two outcome tokens are not sampled at identical seconds; allow two
    # minutes of slack so the settlement tick is not cut on one side only.
    out = [p for p in pts if lo - 120 <= p[0] <= hi + 120]
    return out or pts


def window_for_market(
    sides: dict[str, list[tuple[float, float]]],
    floor: float | None,
) -> tuple[float | None, float | None, str]:
    """Return (start, end, source) for one market (both outcome sides)."""
    if not sides:
        return None, None, "empty"
    if len(sides) > 1:
        def _activity(series: list[tuple[float, float]]) -> float:
            if not series:
                return 0.0
            tail = [p for p in series if p[0] >= series[-1][0] - MAX_MATCH_S]
            return sum(abs(tail[i + 1][1] - tail[i][1]) for i in range(len(tail) - 1))
        pivot = max(sides, key=lambda k: _activity(sides[k]))
    else:
        pivot = next(iter(sides))
    pts = sides[pivot]
    end = settlement_end(pts)
    lo = max(pts[0][0], end - MAX_MATCH_S)
    source = "inferred"
    if floor is not None and lo <= floor <= end:
        # Only trust metadata that lands inside the plausible match span.
        lo = max(lo, floor - 60)
        source = "in_game"
    start = detect_start(pts, lo, end)
    if start > end - 1200:
        # Heavy favourite / no visible in-game move until the final jump:
        # keep at least the last hour so the chart still shows the run-in.
        start = max(lo, end - 3600)
    if start >= end - 60:
        start = max(lo, end - 60)
    return start, end, source
