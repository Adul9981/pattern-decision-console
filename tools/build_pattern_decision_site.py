#!/usr/bin/env python3
"""Build the read-only pattern decision workspace from minute-level price paths.

The page turns the historical shape library into a conditional decision surface:
current in-game path -> nearest historical observations -> top candidate shapes
-> direction bias, entry band, take-profit levels, and invalidation level.

It never places or cancels orders. It only reads public price snapshots and the
existing pattern reports, then writes a self-contained HTML page.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import shutil
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_pattern_library as bpl  # noqa: E402
import ingame_window as igw  # noqa: E402
from build_pattern_playbook import P as PLAYBOOK  # noqa: E402
from pattern_display import display_name, display_text, family_name  # noqa: E402

SNAP_ROOT = ROOT / "docs" / "data" / "snapshots"
MATCHES_JSON = ROOT / "docs" / "data" / "intel" / "matches.json"
TEMPLATE = ROOT / "tools" / "pattern_decision_workspace.template.html"
DEFAULT_SECTIONS = (
    ROOT / "reports" / "pattern_shapes_lol_tier1_2026-07_2026-10.json",
    ROOT / "reports" / "pattern_shapes_cs2_tier1_2026-07_2026-10.json",
)
CHECKPOINTS = (0.03, 0.12, 0.25, 0.40, 0.55, 0.70, 0.85)
MAX_PUBLIC_POINTS = 240
MAX_FUTURE_POINTS = 17

SPORT_LABEL = {"lol": "英雄联盟", "cs2": "CS2", "dota2": "Dota2"}
LEVEL_ORDER = {"series": 0, "game": 1}


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def parse_ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def load_series_file(path: str) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
                ts = parse_ts(row.get("timestamp"))
                price = float(row.get("price"))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            if ts and math.isfinite(price):
                out.append((ts, min(1.0, max(0.0, price))))
    out.sort()
    return out


def side_name_from_file(path: str, fallback: str) -> str:
    """Prefer the side stored in JSONL; mirror file names can repeat outcome 0."""
    try:
        with open(path, encoding="utf-8") as fh:
            first = json.loads(fh.readline())
        side = str(first.get("side") or "").strip()
        return side or fallback.replace("_", " ")
    except (OSError, json.JSONDecodeError):
        return fallback.replace("_", " ")


def downsample(points: list[tuple[float, float]], limit: int) -> list[tuple[float, float]]:
    if len(points) <= limit:
        return points
    picked = [points[0]]
    for i in range(1, limit - 1):
        idx = round(i * (len(points) - 1) / (limit - 1))
        if 0 < idx < len(points) - 1:
            picked.append(points[idx])
    picked.append(points[-1])
    return picked


def shape_downsample(points: list[tuple[float, float]], limit: int) -> list[tuple[float, float]]:
    """Keep direction changes and extrema when simplifying a historical shape.

    Uniform sampling turns many candidate continuations into nearly straight
    polylines. Ramer-Douglas-Peucker keeps the points that explain the visible
    shape, while still bounding the embedded dataset size.
    """
    if len(points) <= limit:
        return points
    if limit < 3:
        return [points[0], points[-1]][:limit]

    def perpendicular(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        return abs(dy * px - dx * py + bx * ay - by * ax) / math.hypot(dx, dy)

    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack and len(keep) < limit:
        start, end = stack.pop()
        if end - start <= 1:
            continue
        candidates = range(start + 1, end)
        idx = max(candidates, key=lambda i: perpendicular(points[i][0], points[i][1], points[start], points[end]))
        if idx in keep:
            continue
        keep.add(idx)
        if len(keep) >= limit:
            break
        stack.append((start, idx))
        stack.append((idx, end))

    # Fill any leftover budget at the largest remaining temporal gaps.
    while len(keep) < limit:
        ordered = sorted(keep)
        gap = max(
            ((ordered[i + 1] - ordered[i], ordered[i], ordered[i + 1]) for i in range(len(ordered) - 1)),
            default=(0, 0, 0),
        )
        if gap[0] <= 1:
            break
        keep.add((gap[1] + gap[2]) // 2)
    return [points[i] for i in sorted(keep)]


def median_spacing(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return statistics.median(points[i + 1][0] - points[i][0] for i in range(len(points) - 1))


def granularity_label(seconds: float) -> str:
    if seconds <= 120:
        return "1分钟级"
    if seconds <= 360:
        return "5分钟级"
    return "10分钟级"


def primary_label(labels: list[str] | None) -> str:
    labels = [str(x) for x in (labels or []) if x]
    for item in labels:
        if re.match(r"^[ABC]\d_", item):
            return item
    if "热门全程压制" in labels:
        return "热门全程压制"
    return labels[0] if labels else "未知"


def label_meta() -> dict[str, dict[str, str]]:
    marks = {
        "A1_V型极值反转": "V", "A2_中位U型反转": "U", "A3_折价修复": "修",
        "A4_下狗整场反转": "狗", "A5_W型双底": "W", "A6_反弹确认": "弹",
        "A7_强强对话错杀": "错", "C2_五五开开局碾压": "碾",
        "C2_早期缩距/热门确立": "立", "热门全程压制": "压",
        "B1_尾盘崩塌": "崩", "B2_死亡螺旋": "螺", "B3_假反弹后归零": "假",
        "B4_直线阴跌": "跌", "B4_低开阴跌": "低", "C1_中位震荡": "震",
    }
    out: dict[str, dict[str, str]] = {}
    for key, value in PLAYBOOK.items():
        out[key] = {
            "title": display_name(key),
            "family": family_name(str(value.get("family") or "")),
            "mark": marks.get(key, key[:1]),
            "summary": display_text(value.get("shape", "")),
            "trigger": display_text(value.get("trigger", "")),
            "entry": display_text(value.get("entry", "")),
            "exit": display_text(value.get("exit", "")),
            "invalid": display_text(value.get("invalid", "")),
        }
    out["未知"] = {
        "title": "暂未归类", "family": "观察结构", "mark": "?",
        "summary": "当前片段的拓扑特征与既有形态样本都不够接近。",
        "trigger": "需要更多分钟点或等待关键价位被突破。",
        "entry": "样本不足，不生成价格区间。",
        "exit": "样本不足，不生成止盈目标。",
        "invalid": "继续等待新数据，不硬套形态。",
    }
    return out


def metadata_index() -> dict[str, dict[str, Any]]:
    data = load_json(MATCHES_JSON, {}) or {}
    out: dict[str, dict[str, Any]] = {}
    for match in data.get("matches", []):
        for key in (match.get("id"), match.get("slug"), match.get("event_slug")):
            if key:
                out[str(key)] = match
    return out


def infer_window(
    slug: str, game: int | None, raw: dict[int, list[tuple[float, float]]]
) -> tuple[float, float, str]:
    side_map = {str(k): v for k, v in raw.items() if v}
    floor = igw.event_start(slug, game)
    start, end, src = igw.window_for_market(side_map, floor)
    if start is None or end is None:
        all_pts = [p for pts in raw.values() for p in pts]
        if not all_pts:
            return 0.0, 0.0, "empty"
        start = min(p[0] for p in all_pts)
        end = max(p[0] for p in all_pts)
        src = "full"
    return float(start), float(end), str(src)


def progress_features(
    points: list[tuple[float, float]], start: float, end: float, cutoff: float
) -> dict[str, float] | None:
    part = [p for p in points if p[0] <= cutoff]
    if not part or (len(part) == 1 and cutoff > start + 60):
        return None
    prices = [p[1] for p in part]
    current = prices[-1]
    opening = prices[0]
    low = min(prices)
    high = max(prices)
    crossings = sum(1 for a, b in zip(prices, prices[1:]) if (a - 0.5) * (b - 0.5) < 0)
    recent_n = max(2, min(len(part), max(3, len(part) // 4 + 1)))
    recent = prices[-recent_n:]
    slope = (recent[-1] - recent[0]) / max(1, recent_n - 1)
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    vol = statistics.pstdev(changes) if len(changes) > 1 else 0.0
    progress = max(0.0, min(1.0, (cutoff - start) / max(60.0, end - start)))
    return {
        "p": round(current, 4), "o": round(opening, 4), "lo": round(low, 4),
        "hi": round(high, 4), "dd": round(high - current, 4),
        "rb": round(current - low, 4), "sl": round(slope, 4),
        "cr": min(12, crossings), "vo": round(vol, 4), "pr": round(progress, 4),
    }


def future_points(points: list[tuple[float, float]], cutoff: float, end: float) -> list[list[float]]:
    future = [p for p in points if p[0] >= cutoff]
    if not future:
        return []
    span = max(60.0, end - cutoff)
    return [
        [round(max(0.0, min(1.0, (ts - cutoff) / span)) * 100, 1), round(price * 100, 1)]
        for ts, price in shape_downsample(future, MAX_FUTURE_POINTS)
    ]


def normalized_path(points: list[tuple[float, float]], start: float, end: float) -> list[list[float]]:
    span = max(60.0, end - start)
    return [
        [round(max(0.0, min(1.0, (ts - start) / span)) * 100, 1), round(price * 100, 1)]
        for ts, price in downsample(points, MAX_PUBLIC_POINTS)
    ]


def input_granularity(paths: list[list[tuple[float, float]]]) -> str:
    spacings = [median_spacing(p) for p in paths if len(p) > 1]
    return granularity_label(statistics.median(spacings)) if spacings else "数据不足"


def load_market_paths(
    parts: dict[str, Any], level: str, game: int | None
) -> tuple[dict[int, list[tuple[float, float]]], dict[int, str]]:
    files = parts.get("series") or {} if level == "series" else ((parts.get("games") or {}).get(game) or {})
    raw: dict[int, list[tuple[float, float]]] = {}
    teams: dict[int, str] = {}
    for idx, (team, path) in files.items():
        pts = load_series_file(path)
        if pts:
            raw[int(idx)] = pts
            teams[int(idx)] = side_name_from_file(path, team)
    return raw, teams


def display_path(points: list[tuple[float, float]], start: float, end: float) -> list[list[float]]:
    points = sorted(points)
    part = [p for p in points if start - 120 <= p[0] <= end + 120]
    return normalized_path(part or points, start, end)


def market_payload(
    *, slug: str, sport: str, league: str, fmt: str, date: str, level: str,
    game: int | None, raw: dict[int, list[tuple[float, float]]],
    teams: dict[int, str], summaries: dict[int, dict[str, Any]], start: float,
    end: float, window_source: str, status: str, result: str,
    training: list[dict[str, Any]], include_training: bool,
) -> dict[str, Any] | None:
    if not raw or not teams:
        return None
    if not (start and end):
        start, end, window_source = infer_window(slug, game, raw)
    trimmed = {i: ([p for p in pts if start - 120 <= p[0] <= end + 120] or pts) for i, pts in raw.items()}
    side_order = sorted(trimmed)
    public_paths = {str(i): display_path(trimmed[i], start, end) for i in side_order}
    path_granularity = input_granularity([trimmed[i] for i in side_order])

    if include_training:
        for idx in side_order:
            summary = summaries.get(idx) or {}
            side_end = float(summary.get("end", trimmed[idx][-1][1]))
            if 0.1 < side_end < 0.9:
                continue
            label = primary_label(summary.get("labels") or [])
            for checkpoint in CHECKPOINTS:
                cutoff = start + checkpoint * (end - start)
                features = progress_features(trimmed[idx], start, end, cutoff)
                future = future_points(trimmed[idx], cutoff, end)
                if not features or len(future) < 2:
                    continue
                future_prices = [p[1] / 100 for p in future]
                training.append({
                    "s": sport, "l": level, "slug": slug, "team": teams.get(idx, str(idx)),
                    "mid": f"{slug}:{level}:{game or 0}", "g": game,
                    "cut": round(checkpoint * 100, 1),
                    "gr": path_granularity,
                    "lab": label, "win": 1 if side_end >= 0.9 else 0, "f": features,
                    "fut": future, "fmin": round(min(future_prices) * 100, 1),
                    "fmax": round(max(future_prices) * 100, 1),
                })

    return {
        "id": f"{slug}:{level}:{game or 0}", "slug": slug, "sport": sport,
        "league": league or "-", "format": fmt or "-", "date": date,
        "level": level, "game": game, "label": "整场" if level == "series" else f"G{game}",
        "sides": [teams.get(i, f"侧 {i + 1}") for i in side_order],
        "paths": public_paths, "status": status, "result": result,
        "granularity": path_granularity,
        "window_source": window_source,
        "first_ts": min(trimmed[i][0][0] for i in side_order),
        "last_ts": max(trimmed[i][-1][0] for i in side_order),
        "observed_max": max(p[0] for path in public_paths.values() for p in path),
        "end_prices": {str(i): round(trimmed[i][-1][1], 4) for i in side_order},
    }


def match_status(meta: dict[str, Any] | None, markets: dict[str, dict[str, Any]]) -> str:
    if meta and (str(meta.get("status", "")).lower() in {"finished", "settled", "closed"} or meta.get("result_inferred")):
        return "已结束"
    ends = [v for market in markets.values() for v in (market.get("end_prices") or {}).values()]
    return "已结束" if ends and max(ends) >= 0.90 else "盘面进行中"


def process_pattern_match(
    match: dict[str, Any], meta: dict[str, Any] | None, training: list[dict[str, Any]]
) -> dict[str, Any] | None:
    slug = str(match.get("slug") or "")
    if not slug or not (SNAP_ROOT / slug).exists():
        return None
    sport = "cs2" if slug.startswith("cs2-") else ("dota2" if slug.startswith("dota2-") else "lol")
    parts = bpl.parse_snapshot_dir(str(SNAP_ROOT / slug), slug)
    markets: dict[str, dict[str, Any]] = {}
    base = dict(slug=slug, sport=sport,
                league=str((meta or {}).get("league") or match.get("league") or "-"),
                fmt=str((meta or {}).get("format") or match.get("format") or "-"),
                date=str(match.get("date") or (meta or {}).get("date") or ""),
                status="已结束", result=str((meta or {}).get("result_inferred") or match.get("result") or ""))

    if parts.get("series"):
        raw, teams = load_market_paths(parts, "series", None)
        if raw:
            starts = [v.get("window_start") for v in (match.get("series") or {}).values() if v.get("window_start")]
            ends = [v.get("window_end") for v in (match.get("series") or {}).values() if v.get("window_end")]
            got = market_payload(**base, level="series", game=None, raw=raw, teams=teams,
                                 summaries={int(k): v for k, v in (match.get("series") or {}).items()},
                                 start=min(starts) if starts else 0.0, end=max(ends) if ends else 0.0,
                                 window_source="in_game", training=training, include_training=True)
            if got:
                markets["series"] = got
    for game_no in sorted((parts.get("games") or {}).keys()):
        game = int(game_no)
        raw, teams = load_market_paths(parts, "game", game)
        if not raw:
            continue
        summaries = {int(k): v for k, v in ((match.get("games") or {}).get(str(game)) or {}).items()}
        starts = [v.get("window_start") for v in summaries.values() if v.get("window_start")]
        ends = [v.get("window_end") for v in summaries.values() if v.get("window_end")]
        got = market_payload(**base, level="game", game=game, raw=raw, teams=teams,
                             summaries=summaries, start=min(starts) if starts else 0.0,
                             end=max(ends) if ends else 0.0, window_source="in_game",
                             training=training, include_training=True)
        if got:
            markets[f"g{game}"] = got
    if not markets:
        return None
    teams = list((meta or {}).get("teams") or [])
    if not teams:
        teams = next((m["sides"][:2] for m in markets.values() if len(m.get("sides", [])) >= 2), [])
    return {"slug": slug, "sport": sport, "league": base["league"], "format": base["fmt"],
            "date": base["date"], "teams": teams[:2], "result": base["result"],
            "status": "已结束", "mode": "review", "markets": markets}


def process_recent_snapshot(slug: str, meta: dict[str, Any] | None) -> dict[str, Any] | None:
    path = SNAP_ROOT / slug
    if not path.is_dir():
        return None
    parts = bpl.parse_snapshot_dir(str(path), slug)
    if not parts.get("series") and not parts.get("games"):
        return None
    sport = "cs2" if slug.startswith("cs2-") else ("dota2" if slug.startswith("dota2-") else "lol")
    if sport not in SPORT_LABEL:
        return None
    date_match = re.search(r"20\d{2}-\d{2}-\d{2}", slug)
    base = dict(slug=slug, sport=sport,
                league=str((meta or {}).get("league") or "-"),
                fmt=str((meta or {}).get("format") or "-"),
                date=str((meta or {}).get("date") or (date_match.group(0) if date_match else "")),
                status="待判定", result=str((meta or {}).get("result_inferred") or ""))
    markets: dict[str, dict[str, Any]] = {}
    if parts.get("series"):
        raw, teams = load_market_paths(parts, "series", None)
        if raw:
            start, end, src = infer_window(slug, None, raw)
            got = market_payload(**base, level="series", game=None, raw=raw, teams=teams,
                                 summaries={}, start=start, end=end, window_source=src,
                                 training=[], include_training=False)
            if got:
                markets["series"] = got
    for game_no in sorted((parts.get("games") or {}).keys()):
        game = int(game_no)
        raw, teams = load_market_paths(parts, "game", game)
        if not raw:
            continue
        start, end, src = infer_window(slug, game, raw)
        got = market_payload(**base, level="game", game=game, raw=raw, teams=teams,
                             summaries={}, start=start, end=end, window_source=src,
                             training=[], include_training=False)
        if got:
            markets[f"g{game}"] = got
    if not markets:
        return None
    status = match_status(meta, markets)
    for market in markets.values():
        market["status"] = status
    teams = list((meta or {}).get("teams") or [])
    if not teams:
        teams = next((m["sides"][:2] for m in markets.values() if len(m.get("sides", [])) >= 2), [])
    return {"slug": slug, "sport": sport, "league": base["league"], "format": base["fmt"],
            "date": base["date"], "teams": teams[:2], "result": base["result"],
            "status": status, "mode": "live" if status != "已结束" else "review", "markets": markets}


def recent_snapshot_slugs(days: int, limit: int) -> list[str]:
    cutoff = dt.date.today() - dt.timedelta(days=max(1, days))
    found: list[tuple[str, str]] = []
    for path in SNAP_ROOT.iterdir():
        if not path.is_dir() or path.name == "fidelity10_backup" or path.name.split("-", 1)[0] not in SPORT_LABEL:
            continue
        match = re.search(r"(20\d{2}-\d{2}-\d{2})$", path.name)
        if not match or not any(path.glob("*.jsonl")):
            continue
        try:
            date = dt.date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if date >= cutoff:
            found.append((path.name, match.group(1)))
    found.sort(key=lambda item: (item[1], item[0]), reverse=True)
    return [name for name, _ in found[:limit]]


def build_dataset(section_paths: list[Path], recent_days: int = 14, recent_limit: int = 32) -> dict[str, Any]:
    metadata = metadata_index()
    training: list[dict[str, Any]] = []
    matches: dict[str, dict[str, Any]] = {}
    sources: list[str] = []
    for section in section_paths:
        data = load_json(section, {}) or {}
        if data:
            sources.append(str(section.relative_to(ROOT)))
        for match in data.get("matches", []):
            slug = str(match.get("slug") or "")
            got = process_pattern_match(match, metadata.get(slug), training)
            if got:
                matches[slug] = got
    for slug in recent_snapshot_slugs(recent_days, recent_limit):
        latest = process_recent_snapshot(slug, metadata.get(slug))
        if latest and slug in matches:
            matches[slug].update({k: latest.get(k) for k in ("league", "format", "teams", "status", "mode", "result", "markets")})
        elif latest:
            matches[slug] = latest
    ordered = sorted(matches.values(), key=lambda m: (
        0 if m.get("status") == "盘面进行中" else 1,
        -int(str(m.get("date", "0000-00-00")).replace("-", "") or 0),
    ))
    for match in ordered:
        match["markets"] = dict(sorted(match.get("markets", {}).items(), key=lambda kv: (
            LEVEL_ORDER.get(kv[1].get("level"), 9), kv[1].get("game") or 0)))
    training_slugs = {t["slug"] for t in training}
    return {
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
        "generated_date": dt.date.today().isoformat(), "source_reports": sources,
        "labels": label_meta(), "sports": SPORT_LABEL, "training": training,
        "matches": ordered,
        "stats": {"training_samples": len(training), "training_matches": len(training_slugs),
                  "matches": len(ordered),
                  "live_matches": sum(1 for m in ordered if m.get("status") == "盘面进行中"),
                  "sports": len({m.get("sport") for m in ordered}),
                  "levels": dict(Counter(t["l"] for t in training))},
    }


def write_site(dataset: dict[str, Any], out: Path, deploy_dir: Path | None, copy_related: bool) -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(dataset, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = template.replace("__DATA__", payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    if not deploy_dir:
        return
    deploy_dir.mkdir(parents=True, exist_ok=True)
    (deploy_dir / "index.html").write_text(html, encoding="utf-8")
    (deploy_dir / "vercel.json").write_text(json.dumps({"cleanUrls": True, "trailingSlash": False}, indent=2) + "\n", encoding="utf-8")
    if copy_related:
        for path in (ROOT / "reports").glob("pattern_*.html"):
            if path.name != out.name:
                shutil.copy2(path, deploy_dir / path.name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/pattern_decision_workspace.html")
    ap.add_argument("--section", action="append", default=[], help="pattern_shapes_*.json; repeatable")
    ap.add_argument("--recent-days", type=int, default=14)
    ap.add_argument("--recent-limit", type=int, default=32)
    ap.add_argument("--dataset", default="")
    ap.add_argument("--deploy-dir", default="", help="optional Vercel-compatible static directory")
    ap.add_argument("--no-copy-related", action="store_true")
    args = ap.parse_args(argv)
    sections = [Path(p) for p in args.section] if args.section else list(DEFAULT_SECTIONS)
    sections = [(p if p.is_absolute() else ROOT / p) for p in sections]
    dataset = build_dataset(sections, recent_days=args.recent_days, recent_limit=args.recent_limit)
    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    deploy = (Path(args.deploy_dir) if Path(args.deploy_dir).is_absolute() else ROOT / args.deploy_dir) if args.deploy_dir else None
    write_site(dataset, out, deploy, not args.no_copy_related)
    if args.dataset:
        data_out = Path(args.dataset) if Path(args.dataset).is_absolute() else ROOT / args.dataset
        data_out.parent.mkdir(parents=True, exist_ok=True)
        data_out.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[pattern-decision] {out} | training={len(dataset['training'])} matches={len(dataset['matches'])} live={dataset['stats']['live_matches']}")
    if deploy:
        print(f"[pattern-decision] deploy dir -> {deploy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
