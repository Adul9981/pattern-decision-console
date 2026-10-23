#!/usr/bin/env python3
"""Refresh the read-only pattern decision workspace with live Polymarket prices.

This is the cloud-side minimum loop for V1.1. It scans the registered esports
watchlist, fetches one-minute Winner histories from Polymarket CLOB, overlays
currently relevant matches on the existing historical training dataset, and
atomically replaces the public self-contained HTML page.

The tool never places, cancels, or settles orders. It never reads wallet keys.
An API failure aborts before writing, so the previous public page is preserved.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import difflib
import json
import math
import os
import re
import statistics
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pattern_display as pd

ROOT = Path(__file__).resolve().parents[1]
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
STAKE = "https://odds-data.stake.com"
SPORTS = {"lol": "英雄联盟", "cs2": "CS2", "dota2": "Dota2"}
STAKE_SPORTS = {
    "lol": ("league-of-legends", "LoL:"),
    "cs2": ("counter-strike", "Counter-Strike:"),
    "dota2": ("dota-2", "Dota 2:"),
}
EVENT_RE = re.compile(r"^(lol|cs2|dota2)-.+-20\d{2}-\d{2}-\d{2}$")
GAME_RE = re.compile(r"-game(\d+)$", re.IGNORECASE)
MAP_WINNER_RE = re.compile(r"\b(?:Game|Map)\s+(\d+)\s+Winner\b", re.IGNORECASE)
STAKE_MAP_RE = re.compile(r"\b(?:Game|Map)\s+(\d+)\s+Winner\b", re.IGNORECASE)

EXPECTED_SECONDS = {
    "lol": {"game": 35 * 60, "series_bo3": 150 * 60, "series_bo5": 250 * 60},
    "cs2": {"game": 65 * 60, "series_bo3": 210 * 60, "series_bo5": 330 * 60},
    "dota2": {"game": 50 * 60, "series_bo3": 180 * 60, "series_bo5": 300 * 60},
}


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_ts(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        value = float(value)
        return value / 1000 if value > 10_000_000_000 else value
    raw = str(value).strip()
    try:
        if re.fullmatch(r"\d+(?:\.\d+)?", raw):
            value = float(raw)
            return value / 1000 if value > 10_000_000_000 else value
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def http_json(url: str, params: dict[str, Any] | None = None, *, tries: int = 4,
              timeout: int = 25) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "polymarket-pattern-decision-live/1.0", "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(max(1, tries)):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - bounded public read retries
            last_error = exc
            if attempt + 1 < tries:
                time.sleep(min(4.0, 0.6 * (2 ** attempt)))
    raise RuntimeError(f"public API request failed: {url}: {last_error}") from last_error


def watchlist_match(event: dict[str, Any], watchlist: dict[str, Any]) -> str | None:
    text = f"{event.get('title', '')} {event.get('slug', '')}".lower()
    for sport, config in (watchlist.get("games") or {}).items():
        include = [str(item).lower() for item in config.get("include_keywords", [])]
        leagues = [str(item).lower() for item in config.get("league_keywords", [])]
        if any(keyword in text for keyword in include) and any(keyword in text for keyword in leagues):
            return str(sport)
    return None


def event_start(event: dict[str, Any]) -> float | None:
    starts = []
    for market in event.get("markets") or []:
        ts = parse_ts(market.get("gameStartTime"))
        if ts:
            starts.append(ts)
    return min(starts) if starts else None


def event_date(event: dict[str, Any]) -> str:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})$", str(event.get("slug") or ""))
    return match.group(1) if match else dt.datetime.now(dt.timezone.utc).date().isoformat()


def event_league(event: dict[str, Any]) -> str:
    title = str(event.get("title") or "")
    if " - " in title:
        return title.rsplit(" - ", 1)[1].strip() or "-"
    sport = str(event.get("slug") or "").split("-", 1)[0]
    return SPORTS.get(sport, "-")


def time_window_days(watchlist: dict[str, Any]) -> tuple[float, float]:
    window = watchlist.get("time_window") or {}
    recent = parse_float(window.get("recent_started_days"), 2.0)
    upcoming = parse_float(window.get("upcoming_days"), 2.0)
    return max(0.0, recent), max(0.0, upcoming)


def local_date(ts: float) -> str:
    timezone = dt.timezone(dt.timedelta(hours=8))
    return dt.datetime.fromtimestamp(ts, timezone).date().isoformat()


def iso_utc(ts: float | None) -> str:
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def event_format(event: dict[str, Any]) -> str:
    match = re.search(r"\bBO([135])\b", str(event.get("title") or ""), re.IGNORECASE)
    return f"BO{match.group(1)}" if match else "BO?"


def target_games(event: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[int, dict[str, Any]]]:
    """Return the series Winner market and numbered Game/Map Winner markets."""
    series: dict[str, Any] | None = None
    games: dict[int, dict[str, Any]] = {}
    event_slug = str(event.get("slug") or "")
    for market in event.get("markets") or []:
        if not isinstance(market, dict) or market.get("closed") is True:
            continue
        if len(parse_json_list(market.get("outcomes"))) < 2 or len(parse_json_list(market.get("clobTokenIds"))) < 2:
            continue
        sports_type = str(market.get("sportsMarketType") or "").lower()
        group = str(market.get("groupItemTitle") or "")
        slug = str(market.get("slug") or "")
        if sports_type == "moneyline" or group.lower() == "match winner" or slug == event_slug:
            series = market
            continue
        game_match = GAME_RE.search(slug) or MAP_WINNER_RE.search(f"{market.get('question', '')} {group}")
        if not game_match or sports_type not in {"", "child_moneyline"}:
            continue
        game = int(game_match.group(1))
        if game >= 1:
            games[game] = market
    return series, games


def stake_markets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten Stake's nested group views and deduplicate by market id."""
    found: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("id") and node.get("name") and isinstance(node.get("outcomes"), list):
            found[str(node["id"])] = node
            return
        for value in node.values():
            walk(value)

    walk(payload.get("groups") or [])
    return list(found.values())


def stake_match_winner(markets: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for market in markets:
        name = str(market.get("name") or "")
        outcomes = market.get("outcomes") or []
        if str(market.get("status") or "").lower() != "active":
            continue
        if not name.lower().startswith("match winner") or len(outcomes) != 2:
            continue
        if any(str(outcome.get("name") or "").strip().lower() == "draw" for outcome in outcomes):
            continue
        odds = [parse_float(outcome.get("odds"), -1.0) for outcome in outcomes]
        active = [bool(outcome.get("active")) for outcome in outcomes]
        if not all(active) or not all(value > 1.0 for value in odds):
            continue
        candidates.append(market)
    if not candidates:
        return None
    selected = next((item for item in candidates if "twoway" in str(item.get("name") or "").lower()), candidates[0])
    outcomes = selected.get("outcomes") or []
    odds = [parse_float(outcome.get("odds"), 0.0) for outcome in outcomes]
    implied = [1.0 / value for value in odds]
    margin = sum(implied)
    fair = [value / margin for value in implied]
    return {
        "market_id": str(selected.get("id") or ""),
        "market_name": str(selected.get("name") or ""),
        "updated_at": iso_utc(parse_ts(selected.get("updatedAt"))),
        "updated_ts": parse_ts(selected.get("updatedAt")) or 0.0,
        "margin": round(margin - 1.0, 6),
        "outcomes": [
            {
                "name": str(outcome.get("name") or ""),
                "decimal_odds": round(odds[index], 4),
                "raw_implied": round(implied[index], 6),
                "fair_probability": round(fair[index], 6),
            }
            for index, outcome in enumerate(outcomes)
        ],
    }


def stake_game_numbers(markets: list[dict[str, Any]]) -> list[int]:
    games: set[int] = set()
    for market in markets:
        if str(market.get("status") or "").lower() != "active":
            continue
        name = str(market.get("name") or "")
        if "threeway" in name.lower() or len(market.get("outcomes") or []) != 2:
            continue
        match = STAKE_MAP_RE.search(name)
        if match:
            games.add(int(match.group(1)))
    return sorted(games)


def stake_schedule_entry(*, sport: str, tournament: dict[str, Any], fixture: dict[str, Any],
                         detail: dict[str, Any], now: float) -> dict[str, Any] | None:
    start = parse_ts(fixture.get("startTime"))
    name = str(fixture.get("name") or "").strip()
    teams = [part.strip() for part in name.split(" - ", 1)]
    if not start or len(teams) != 2 or not all(teams):
        return None
    markets = stake_markets(detail)
    winner = stake_match_winner(markets)
    games = stake_game_numbers(markets)
    status = str(fixture.get("status") or "")
    if status == "live" and winner is None:
        display_status = "进行中 · 盘口暂停/缺口"
    elif status == "live":
        display_status = "进行中"
    elif start <= now:
        display_status = "已到开赛时间 · 待状态"
    else:
        display_status = "待开赛"
    format_label = f"BO{5 if games and max(games) >= 5 else 3}" if games else "待确认"
    fixture_updated = parse_ts(fixture.get("updatedAt")) or 0.0
    market_updated = (winner or {}).get("updated_ts") or 0.0
    return {
        "id": f"stake:{fixture.get('slug') or fixture.get('id')}",
        "sport": sport,
        "league": str(tournament.get("name") or "-"),
        "tournament": str(tournament.get("name") or "-"),
        "format": format_label,
        "date": local_date(start),
        "start_ts": int(start),
        "teams": teams,
        "status": display_status,
        "stake_status": status,
        "stake_fixture_slug": str(fixture.get("slug") or ""),
        "stake_url": f"{STAKE}/fixtures/{urllib.parse.quote(str(fixture.get('slug') or ''))}",
        "stake_updated_at": iso_utc(max(fixture_updated, market_updated) or None),
        "match_winner": winner,
        "game_numbers": games,
        "market_count": len(markets),
        "source": "stake_odds_data",
        "source_tier": "B · 赛前跨市场参考",
        "polymarket_slug": "",
        "polymarket_status": "待 Polymarket 开盘",
        "fetched_at": iso_utc(now),
    }


def fetch_stake_upcoming(watchlist: dict[str, Any], now: float, days: float,
                         max_fixtures: int, workers: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    errors: list[str] = []
    fixture_rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    tournaments_seen = 0
    fixtures_seen = 0
    for sport, (sport_slug, include_prefix) in STAKE_SPORTS.items():
        try:
            categories = http_json(f"{STAKE}/sports/{sport_slug}/categories", tries=2, timeout=20)
        except Exception as exc:  # noqa: BLE001 - source is optional cross-reference
            errors.append(f"{sport} categories: {exc}")
            continue
        for category in (categories or {}).get("categories", []) if isinstance(categories, dict) else []:
            if not isinstance(category, dict) or category.get("enabled") is not True:
                continue
            category_slug = str(category.get("slug") or "")
            if not category_slug:
                continue
            try:
                payload = http_json(f"{STAKE}/sports/{sport_slug}/{category_slug}/tournaments", tries=2, timeout=20)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{sport} tournaments: {exc}")
                continue
            for tournament in (payload or {}).get("tournaments", []) if isinstance(payload, dict) else []:
                if not isinstance(tournament, dict) or tournament.get("enabled") is not True:
                    continue
                tournaments_seen += 1
                tournament_slug = str(tournament.get("slug") or "")
                tournament_name = str(tournament.get("name") or "")
                pseudo = {
                    "title": f"{include_prefix} Upcoming - {tournament_name}",
                    "slug": f"{sport_slug}/{category_slug}/{tournament_slug}",
                }
                if not tournament_slug or watchlist_match(pseudo, watchlist) != sport:
                    continue
                try:
                    fixtures = http_json(
                        f"{STAKE}/sports/{sport_slug}/{category_slug}/{tournament_slug}/fixtures",
                        tries=2,
                        timeout=20,
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{sport} {tournament_slug} fixtures: {exc}")
                    continue
                for fixture in (fixtures or {}).get("fixtures", []) if isinstance(fixtures, dict) else []:
                    if not isinstance(fixture, dict) or fixture.get("enabled") is not True:
                        continue
                    start = parse_ts(fixture.get("startTime"))
                    if not start or start < now - 30 * 60 or start > now + days * 86400:
                        continue
                    fixture_rows.append((sport, tournament, fixture))
    fixture_rows.sort(key=lambda row: parse_ts(row[2].get("startTime")) or 0.0)
    fixture_rows = fixture_rows[:max(0, max_fixtures)]
    fixtures_seen = len(fixture_rows)
    schedule: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        future_map = {
            executor.submit(
                http_json,
                f"{STAKE}/fixtures/{urllib.parse.quote(str(fixture.get('slug') or ''))}",
                None,
                tries=2,
                timeout=20,
            ): (sport, tournament, fixture)
            for sport, tournament, fixture in fixture_rows
        }
        for future, (sport, tournament, fixture) in future_map.items():
            try:
                detail = future.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"fixture {fixture.get('slug')}: {exc}")
                continue
            entry = stake_schedule_entry(
                sport=sport,
                tournament=tournament,
                fixture=fixture,
                detail=detail if isinstance(detail, dict) else {},
                now=now,
            )
            if entry:
                schedule.append(entry)
    schedule.sort(key=lambda row: (row.get("start_ts") or 0, row.get("sport") or ""))
    diagnostics = {
        "ok": not errors,
        "tournaments_seen": tournaments_seen,
        "fixtures_seen": fixtures_seen,
        "schedule_rows": len(schedule),
        "with_winner_odds": sum(1 for row in schedule if row.get("match_winner")),
        "errors": errors[:12],
        "error_count": len(errors),
    }
    return schedule, diagnostics


def norm_team(value: Any) -> str:
    raw = str(value or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", raw)


def team_similarity(left: Any, right: Any) -> float:
    a, b = norm_team(left), norm_team(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    left_tokens = {norm_team(token) for token in re.split(r"[\s._-]+", str(left or "")) if token}
    right_tokens = {norm_team(token) for token in re.split(r"[\s._-]+", str(right or "")) if token}
    if a in right_tokens or b in left_tokens:
        return 0.98
    if min(len(a), len(b)) >= 3 and (a in b or b in a):
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def stake_matches_polymarket(schedule: dict[str, Any], match: dict[str, Any]) -> bool:
    if schedule.get("sport") != match.get("sport"):
        return False
    start = parse_float(schedule.get("start_ts"), 0.0)
    match_start = parse_float(match.get("start_ts"), 0.0)
    if not start or not match_start or abs(start - match_start) > 6 * 3600:
        return False
    left = list(schedule.get("teams") or [])
    right = list(match.get("teams") or [])
    if len(left) != 2 or len(right) != 2:
        return False
    forward = team_similarity(left[0], right[0]) >= 0.66 and team_similarity(left[1], right[1]) >= 0.66
    reverse = team_similarity(left[0], right[1]) >= 0.66 and team_similarity(left[1], right[0]) >= 0.66
    return forward or reverse


def fetch_history(token_id: str, start_ts: int, end_ts: int) -> list[tuple[float, float]]:
    payload = http_json(
        f"{CLOB}/prices-history",
        {"market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": 1},
        tries=3,
        timeout=25,
    )
    out: list[tuple[float, float]] = []
    for row in (payload or {}).get("history", []) if isinstance(payload, dict) else []:
        ts = parse_ts(row.get("t"))
        price = parse_float(row.get("p"), -1.0)
        if ts and 0.0 <= price <= 1.0:
            out.append((float(ts), float(price)))
    out.sort()
    return out


def fetch_market_sides(market: dict[str, Any], start_ts: int, end_ts: int) -> dict[int, list[tuple[float, float]]]:
    tokens = parse_json_list(market.get("clobTokenIds"))
    result: dict[int, list[tuple[float, float]]] = {}
    for index, token in enumerate(tokens[:2]):
        try:
            points = fetch_history(str(token), start_ts, end_ts)
        except Exception:  # noqa: BLE001 - individual missing sides are data gaps
            points = []
        if points:
            result[index] = points
    return result


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


def downsample(points: list[tuple[float, float]], limit: int = 96) -> list[tuple[float, float]]:
    if len(points) <= limit:
        return points
    indexes = {0, len(points) - 1}
    indexes.update(round(i * (len(points) - 1) / (limit - 1)) for i in range(limit))
    return [points[i] for i in sorted(indexes)]


def expected_end(start: float, sport: str, format_label: str, level: str) -> float:
    config = EXPECTED_SECONDS.get(sport, EXPECTED_SECONDS["lol"])
    if level == "game":
        duration = config["game"]
    else:
        duration = config["series_bo5"] if format_label == "BO5" else config["series_bo3"]
    return start + duration


def infer_live_start(sides: dict[int, list[tuple[float, float]]], floor: float,
                     level: str, game: int | None, now: float) -> tuple[float, str]:
    if level == "game" and game and game > 1:
        pivot = max(sides.values(), key=lambda pts: sum(abs(pts[i + 1][1] - pts[i][1]) for i in range(max(0, len(pts) - 90), len(pts) - 1)), default=[])
        baseline_points = [p[1] for p in pivot if floor - 2 * 3600 <= p[0] <= floor + 3600]
        baseline = statistics.median(baseline_points) if baseline_points else (pivot[0][1] if pivot else 0.5)
        lookahead = max(2, min(90, int(3600 / max(60.0, median_spacing(pivot) or 60.0))))
        for index, point in enumerate(pivot):
            if point[0] < floor - 60 or abs(point[1] - baseline) < 0.04:
                continue
            future = pivot[index:index + lookahead]
            if any(abs(item[1] - baseline) >= 0.08 for item in future):
                return max(floor - 60, point[0] - 180), "live_detected_start"
    return max(floor - 180, now - 7 * 3600), "live_game_start"


def market_payload(*, event: dict[str, Any], sport: str, level: str, game: int | None,
                   market: dict[str, Any], sides: dict[int, list[tuple[float, float]]],
                   floor: float, now: float, phase: str) -> dict[str, Any] | None:
    outcomes = [str(item) for item in parse_json_list(market.get("outcomes"))[:2]]
    if not outcomes or not sides:
        return None
    if phase == "prematch":
        latest = max((points[-1] for points in sides.values() if points), default=None, key=lambda p: p[0])
        if not latest:
            return None
        start, end = now - 60, max(floor, now + 60)
        points_mode = "prematch"
        status = "赛前待走势"
    else:
        start, source = infer_live_start(sides, floor, level, game, now)
        planned_end = expected_end(start, sport, event_format(event), level)
        end = max(now + 120, planned_end)
        points_mode = source
        status = "盘面进行中"
    paths: dict[str, list[list[float]]] = {}
    end_prices: dict[str, float] = {}
    trimmed_all: list[tuple[float, float]] = []
    for index in sorted(sides):
        points = [p for p in sides[index] if start - 120 <= p[0] <= now + 1]
        if phase != "prematch" and len(points) < 2:
            continue
        if phase == "prematch":
            points = [points[-1]] if points else []
        if not points:
            continue
        if phase == "prematch":
            paths[str(index)] = [[0.0, round(points[-1][1] * 100, 1)]]
        else:
            span = max(60.0, end - start)
            paths[str(index)] = [
                [round(max(0.0, min(1.0, (ts - start) / span)) * 100, 1), round(price * 100, 1)]
                for ts, price in downsample(points)
            ]
        end_prices[str(index)] = round(points[-1][1], 4)
        trimmed_all.extend(points)
    if not paths or not trimmed_all:
        return None
    spacings = [median_spacing(points) for points in sides.values() if len(points) > 1]
    spacing = statistics.median(spacings) if spacings else 0.0
    raw_scores = parse_json_list(market.get("outcomePrices"))
    scores = [parse_float(raw_scores[index], -1) for index in range(min(2, len(outcomes), len(raw_scores)))]
    if any(score >= 0.985 for score in scores):
        status = "已结束"
    observed_max = max(point[0] for path in paths.values() for point in path)
    return {
        "id": f"{event.get('slug')}:{level}:{game or 0}",
        "slug": str(event.get("slug") or ""),
        "sport": sport,
        "league": event_league(event),
        "format": event_format(event),
        "date": local_date(floor),
        "level": level,
        "game": game,
        "label": "整场" if level == "series" else f"G{game}",
        "sides": [outcomes[index] for index in sorted(sides) if str(index) in paths],
        "paths": paths,
        "status": status,
        "result": "",
        "granularity": granularity_label(spacing) if spacing else "数据不足",
        "window_source": points_mode,
        "first_ts": min(point[0] for point in trimmed_all),
        "last_ts": max(point[0] for point in trimmed_all),
        "observed_max": observed_max,
        "end_prices": end_prices,
    }


def build_match(event: dict[str, Any], sport: str, now: float, workers: int) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    floor = event_start(event)
    if floor is None:
        return None, ["missing real gameStartTime"]
    series_market, game_markets = target_games(event)
    targets: list[tuple[str, int | None, dict[str, Any]]] = []
    if series_market:
        targets.append(("series", None, series_market))
    targets.extend(("game", game, market) for game, market in sorted(game_markets.items()))
    if not targets:
        return None, ["no Winner markets"]
    phase = "live" if now >= floor - 5 * 60 else "prematch"
    # Distant future events only need the current Gamma snapshot. Pulling CLOB
    # history for every future match would multiply API traffic without adding
    # information; start fetching minute history when the match is near.
    fetch_histories = phase == "live" or floor - now <= 90 * 60
    histories: dict[tuple[str, int | None], dict[int, list[tuple[float, float]]]] = {}
    if fetch_histories:
        fetch_start = int(max(floor - 6 * 3600, now - 10 * 3600))
        fetch_end = int(now)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
            future_map = {
                executor.submit(fetch_market_sides, market, fetch_start, fetch_end): (level, game)
                for level, game, market in targets
            }
            for future, key in future_map.items():
                try:
                    histories[key] = future.result()
                except Exception as exc:  # noqa: BLE001
                    histories[key] = {}
                    errors.append(f"{event.get('slug')} {key}: {exc}")
    markets: dict[str, dict[str, Any]] = {}
    for level, game, market in targets:
        market_sides = histories.get((level, game), {})
        if not market_sides and phase == "prematch":
            prices = parse_json_list(market.get("outcomePrices"))
            market_sides = {
                index: [(now, parse_float(price, -1.0))]
                for index, price in enumerate(prices[:2])
                if 0.0 <= parse_float(price, -1.0) <= 1.0
            }
        payload = market_payload(
            event=event,
            sport=sport,
            level=level,
            game=game,
            market=market,
            sides=market_sides,
            floor=floor,
            now=now,
            phase=phase,
        )
        if payload:
            markets["series" if level == "series" else f"g{game}"] = payload
    if not markets:
        return None, errors or ["no usable minute-level price paths"]
    teams: list[str] = []
    for key in ("series", *(f"g{game}" for game in sorted(game_markets))):
        payload = markets.get(key)
        if payload and len(payload.get("sides", [])) >= 2:
            teams = list(payload["sides"][:2])
            break
    statuses = {market.get("status") for market in markets.values()}
    status = "已结束" if statuses == {"已结束"} else ("赛前待走势" if phase == "prematch" else "盘面进行中")
    return {
        "slug": str(event.get("slug") or ""),
        "sport": sport,
        "league": event_league(event),
        "format": event_format(event),
        "date": local_date(floor),
        "teams": teams,
        "result": "",
        "status": status,
        "mode": "live",
        "markets": markets,
        "source": "live_polymarket_cloud",
        "start_ts": int(floor),
    }, errors


def collect_events(watchlist: dict[str, Any], now: float, pages: int, page_size: int) -> tuple[list[tuple[dict[str, Any], str]], dict[str, Any]]:
    tag_id = str((watchlist.get("esports") or {}).get("tag_id") or "64")
    candidates: list[tuple[dict[str, Any], str, float]] = []
    seen: set[str] = set()
    fetched = 0
    match_events = 0
    pages_fetched = 0
    recent_days, upcoming_days = time_window_days(watchlist)
    for page in range(max(1, pages)):
        batch = http_json(
            f"{GAMMA}/events",
            {"tag_id": tag_id, "closed": "false", "archived": "false", "limit": page_size,
             "offset": page * page_size, "order": "startDate", "ascending": "false"},
        )
        if not isinstance(batch, list) or not batch:
            break
        pages_fetched += 1
        fetched += len(batch)
        for event in batch:
            if not isinstance(event, dict):
                continue
            slug = str(event.get("slug") or "")
            if not slug or slug in seen or not EVENT_RE.match(slug):
                continue
            seen.add(slug)
            match_events += 1
            sport = watchlist_match(event, watchlist)
            start = event_start(event)
            if not sport or start is None:
                continue
            if start < now - recent_days * 86400 or start > now + upcoming_days * 86400:
                continue
            candidates.append((event, sport, start))
    diagnostics = {
        "fetched_events": fetched,
        "pages_fetched": pages_fetched,
        "match_events": match_events,
        "supported_candidates": len(candidates),
        "recent_days": recent_days,
        "upcoming_days": upcoming_days,
    }
    if fetched == 0:
        raise RuntimeError("Gamma returned zero events; refusing to replace page")
    if match_events == 0:
        raise RuntimeError("Gamma returned no match-shaped events; refusing to replace page")
    candidates.sort(key=lambda item: (0 if item[2] <= now else 1, abs(item[2] - now), item[0].get("slug", "")))
    return [(event, sport) for event, sport, _ in candidates], diagnostics


def merge_dataset(base: dict[str, Any], live_matches: list[dict[str, Any]], diagnostics: dict[str, Any],
                  source_report: str, upcoming_schedule: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    live_slugs = {match.get("slug") for match in live_matches if match.get("slug")}
    historical = [
        match for match in base.get("matches", [])
        if match.get("slug") not in live_slugs and match.get("source") != "live_polymarket_cloud"
    ]
    matches = live_matches + historical
    matches.sort(key=lambda match: (
        0 if match.get("status") == "盘面进行中" else (1 if match.get("status") == "赛前待走势" else 2),
        -int(str(match.get("date", "0000-00-00")).replace("-", "") or 0),
    ))
    schedule: list[dict[str, Any]] = []
    for row in upcoming_schedule or []:
        item = dict(row)
        linked = next((match for match in live_matches if stake_matches_polymarket(item, match)), None)
        if linked:
            item["polymarket_slug"] = str(linked.get("slug") or "")
            item["polymarket_status"] = str(linked.get("status") or "")
            if item.get("format") in {"", "待确认"} and linked.get("format") not in {None, "", "BO?"}:
                item["format"] = str(linked.get("format"))
        schedule.append(item)
    dataset = dict(base)
    dataset["generated_at"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    dataset["generated_date"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    dataset["matches"] = matches
    dataset["upcoming_schedule"] = schedule
    reports = list(base.get("source_reports") or [])
    if source_report not in reports:
        reports.append(source_report)
    dataset["source_reports"] = reports
    stats = dict(base.get("stats") or {})
    stats.update({
        "matches": len(matches),
        "live_matches": sum(1 for match in matches if match.get("status") == "盘面进行中"),
        "prematch_matches": sum(1 for match in matches if match.get("status") == "赛前待走势"),
        "upcoming_schedule": len(schedule),
        "live_refresh_ok": True,
    })
    dataset["stats"] = stats
    dataset["live_refresh"] = diagnostics
    return dataset


def normalise_display_labels(dataset: dict[str, Any]) -> dict[str, Any]:
    """Overlay the current Chinese naming contract on a cached base dataset."""
    refreshed = dict(dataset)
    labels: dict[str, dict[str, Any]] = {}
    for key, raw_meta in (dataset.get("labels") or {}).items():
        meta = dict(raw_meta or {})
        meta["title"] = pd.display_name(key)
        meta["family"] = pd.family_name(str(meta.get("family") or ""))
        for field in ("summary", "trigger", "entry", "exit", "invalid"):
            if field in meta:
                meta[field] = pd.display_text(meta[field])
        labels[key] = meta
    refreshed["labels"] = labels
    return refreshed


def render_page(template: str, dataset: dict[str, Any]) -> str:
    dataset = normalise_display_labels(dataset)
    payload = json.dumps(dataset, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if "__DATA__" not in template:
        raise RuntimeError("template is missing __DATA__ placeholder")
    return template.replace("__DATA__", payload)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-data", default=str(ROOT / "reports" / "pattern_decision_data.json"))
    parser.add_argument("--template", default=str(ROOT / "tools" / "pattern_decision_workspace.template.html"))
    parser.add_argument("--watchlist", default=str(ROOT / "config" / "market_watchlist.json"))
    parser.add_argument("--out", default=str(ROOT / "reports" / "pattern_decision_workspace.html"))
    parser.add_argument("--also", action="append", default=[], help="additional output path; repeatable")
    parser.add_argument("--pages", type=int, default=15)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-events", type=int, default=48)
    parser.add_argument("--stake-days", type=float, default=3.0,
                        help="future Stake schedule horizon; 0 disables Stake fixtures")
    parser.add_argument("--max-stake-fixtures", type=int, default=48)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--json-out", default="", help="optional diagnostics/base-merged JSON output")
    args = parser.parse_args(argv)

    base = load_json(Path(args.base_data))
    watchlist = load_json(Path(args.watchlist))
    template = Path(args.template).read_text(encoding="utf-8")
    if not isinstance(base, dict) or not base.get("training"):
        raise SystemExit("base dataset is missing or has no training observations")
    if not isinstance(watchlist, dict):
        raise SystemExit("watchlist is missing")

    now = time.time()
    candidates, diagnostics = collect_events(watchlist, now, args.pages, args.page_size)
    live_matches: list[dict[str, Any]] = []
    errors: list[str] = []
    for event, sport in candidates[:max(0, args.max_events)]:
        match, match_errors = build_match(event, sport, now, args.workers)
        errors.extend(match_errors)
        if match:
            live_matches.append(match)
    diagnostics.update({
        "built_matches": len(live_matches),
        "built_markets": sum(len(match.get("markets", {})) for match in live_matches),
        "market_errors": len(errors),
        "sample_errors": errors[:12],
        "max_events": args.max_events,
        "fetched_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds"),
    })
    if candidates and not live_matches:
        raise RuntimeError(
            "supported events were found but no live page could be built; preserving previous page; "
            f"diagnostics={json.dumps(diagnostics, ensure_ascii=False)}"
        )

    upcoming_schedule: list[dict[str, Any]] = []
    if args.stake_days > 0 and args.max_stake_fixtures > 0:
        try:
            upcoming_schedule, stake_diagnostics = fetch_stake_upcoming(
                watchlist,
                now,
                args.stake_days,
                args.max_stake_fixtures,
                args.workers,
            )
        except Exception as exc:  # noqa: BLE001 - optional cross-reference must not break the page
            stake_diagnostics = {"ok": False, "schedule_rows": 0, "error_count": 1, "errors": [str(exc)]}
    else:
        stake_diagnostics = {"ok": True, "schedule_rows": 0, "error_count": 0, "errors": []}
    stake_diagnostics["days"] = args.stake_days
    diagnostics["stake_schedule"] = stake_diagnostics

    dataset = merge_dataset(
        base,
        live_matches,
        diagnostics,
        "Polymarket CLOB · 云端实时刷新；Stake Odds Data · 未来赛程参考",
        upcoming_schedule,
    )
    html = render_page(template, dataset)
    targets = [Path(args.out), *(Path(item) for item in args.also)]
    for target in targets:
        atomic_write(target, html)
    if args.json_out:
        json_target = Path(args.json_out)
        atomic_write(json_target, json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    print(
        f"[pattern-live] page={targets[0]} events={diagnostics['supported_candidates']} "
        f"live_matches={len(live_matches)} markets={diagnostics['built_markets']} "
        f"stake_schedule={len(upcoming_schedule)} errors={diagnostics['market_errors']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
