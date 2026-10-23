#!/usr/bin/env python3
"""Shared price-path preprocessing: raw -> gap-filled -> smoothed display.

Why (2026-10-18 user feedback):
    Sparse 600s paths looked like straight lines with 4-6 vertices.  Simply
    "adding points" would fabricate detail.  This module keeps three layers
    explicit and separable:

      raw        real observations (never modified)
      filled     raw + linear fill for *small* gaps only (<= 3x median step)
      display    filled + robust smoothing (median-3 then EMA) for drawing

    Classification / statistics must keep using `raw`.  Pages may draw
    `display`, but must label it as a smoothed display line and show the
    interpolation ratio + confidence.

Confidence is deliberately conservative:
    high   : median step <= 90s, >= 30 raw points, interpolated <= 15%
    medium : median step <= 360s and >= 12 raw points
    low    : everything else (600s paths, tiny samples)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class Prepared:
    raw: list[tuple[float, float]]
    filled: list[tuple[float, float]]
    display: list[tuple[float, float]]
    median_step: float
    raw_points: int
    display_points: int
    interpolated_ratio: float
    smoothing: str
    confidence: str
    notes: list[str] = field(default_factory=list)


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    by_t: dict[float, float] = {}
    for t, p in points:
        by_t[float(t)] = max(0.0, min(1.0, float(p)))
    return sorted(by_t.items())


def _median_step(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return float(statistics.median(points[i + 1][0] - points[i][0] for i in range(len(points) - 1)))


def _fill_gaps(points: list[tuple[float, float]], max_gap: float) -> list[tuple[float, float]]:
    """Linear fill only for gaps <= max_gap; never bridge a real outage."""
    if len(points) < 2 or max_gap <= 0:
        return list(points)
    out = [points[0]]
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        gap = t1 - t0
        if gap <= 0:
            continue
        if 0 < gap <= max_gap:
            steps = int(round(gap / 60.0))
            if steps >= 2:
                for k in range(1, steps):
                    tk = t0 + gap * k / steps
                    out.append((tk, p0 + (p1 - p0) * k / steps))
        out.append((t1, p1))
    return out


def _median3(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    for i in range(1, len(points) - 1):
        vals = sorted((points[i - 1][1], points[i][1], points[i + 1][1]))
        out.append((points[i][0], vals[1]))
    out.append(points[-1])
    return out


def _ema(points: list[tuple[float, float]], alpha: float) -> list[tuple[float, float]]:
    if len(points) < 2:
        return list(points)
    out = [points[0]]
    prev = points[0][1]
    for t, p in points[1:]:
        prev = alpha * p + (1 - alpha) * prev
        out.append((t, prev))
    # Anchor both ends to real observations so the line never invents a level.
    out[0] = points[0]
    out[-1] = points[-1]
    return out


def prepare(points: list[tuple[float, float]], smooth: bool = True) -> Prepared:
    raw = _dedupe(points)
    if len(raw) < 2:
        return Prepared(raw, raw, raw, 0.0, len(raw), len(raw), 0.0,
                        "none", "low", ["样本不足"])
    step = _median_step(raw)
    notes: list[str] = []

    if step <= 90:
        max_gap = 3 * step
        smoothing = "median3+ema(0.45)"
        alpha = 0.45
    elif step <= 360:
        max_gap = 2 * step
        smoothing = "median3+ema(0.6)"
        alpha = 0.6
    else:
        # 600s paths: do not invent minute detail; keep the real vertices.
        max_gap = 0
        smoothing = "none"
        alpha = 0.0
        notes.append("10 分钟采样：曲线保持原始折线，不做分钟级插值")

    filled = _fill_gaps(raw, max_gap) if max_gap else list(raw)
    if smooth and alpha and len(filled) >= 5:
        display = _ema(_median3(filled), alpha)
    else:
        display = list(filled)

    added = max(0, len(filled) - len(raw))
    interp_ratio = added / len(filled) if filled else 0.0
    if step <= 90 and len(raw) >= 30 and interp_ratio <= 0.15:
        confidence = "high"
    elif step <= 360 and len(raw) >= 12:
        confidence = "medium"
    else:
        confidence = "low"
    if confidence == "low" and not notes:
        notes.append("低置信度：采样稀疏或样本点过少，形态只作方向参考")

    return Prepared(
        raw=raw,
        filled=filled,
        display=display,
        median_step=step,
        raw_points=len(raw),
        display_points=len(display),
        interpolated_ratio=round(interp_ratio, 4),
        smoothing=smoothing if smooth else "none",
        confidence=confidence,
        notes=notes,
    )


if __name__ == "__main__":  # tiny self-check
    demo = [(0, 0.5), (60, 0.52), (120, 0.50), (300, 0.60), (360, 0.58)]
    p = prepare(demo)
    print(p)
