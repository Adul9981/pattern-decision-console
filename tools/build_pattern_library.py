#!/usr/bin/env python3
"""Build 逐小局 + 整局 price-shape entries from snapshots (read-only scan -> library/HTML).

输入：docs/data/snapshots/<event_slug>/ 下的价格序列
  - 整场：<slug>__<idx>__<Team>.jsonl
  - 小局：<slug>-gameN__<idx>__<Team>.jsonl
分类：tools/classify_pattern.py（形态库 A/B/C 启发式，v1.3）
输出：
  1) 明细 JSON（默认 /tmp）—— 每场逐局 + 整局形态、结果、粒度；
  2) 可选 --write：合并进 docs/data/intel/price_paths.json（按 slug 去重，已有条目保留）；
  3) 可选 --html：SAP/Apple 风格汇总页（形态频次 + 按联赛逐场明细）。

用法：
  python3 tools/build_pattern_library.py --range 2026-09-10:2026-10-01 --prefix lol- \
      --index runtime/pm_backfill/index_lol_games_2026-09-10_2026-10-01.json \
      --out /tmp/lol_shapes.json --write --html reports/pattern_shapes_lol_2026-0910_1001.html
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify_pattern import classify, load_series  # noqa: E402
import ingame_window as igw  # noqa: E402
import path_series as ps  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_ROOT = os.path.join(ROOT, "docs", "data", "snapshots")
LIB_PATH = os.path.join(ROOT, "docs", "data", "intel", "price_paths.json")

FAMILY_COLOR = {"A": "#0071e3", "B": "#ff453a", "C": "#8e8e93", "热": "#ff9f0a", "未": "#c7c7cc"}


def decider_game(fmt: str, wins: int) -> int:
    """Game number of the decider when its winner market is missing.

    A BO3 winner has 2 wins -> G3; a BO5 winner has 3 wins -> G5.
    """
    return {"BO1": 1, "BO3": 3, "BO5": 5}.get(fmt) or (2 * wins - 1)


def family_of(label: str) -> str:
    if label.startswith("A"):
        return "A"
    if label.startswith("B"):
        return "B"
    if label.startswith("C"):
        return "C"
    if label.startswith("热门"):
        return "热"
    return "未"


def granularity_label(spacings) -> str:
    """分钟级归档口径（2026-10-15）：把采样间隔映射成粒度标签。

    CLOB 对 >24h 的历史市场只给 600s；赛后 24h 内归档可得 60s。
    两档数据质量不同（60s 能看到 600s 看不到的深水触点），必须显式区分，
    禁止把两档混算成同一个统计口径。

    采样间隔有 ±1s 抖动（599/601），因此按区间归桶而不是原样输出秒数：
    <=120s -> 60s，>=240s -> 600s；同一场两种粒度并存 -> mixed。
    """
    def tier(x: int) -> str:
        if x <= 120:
            return "60s"
        if x >= 240:
            return "600s"
        return f"{x}s"

    tiers = {tier(int(x)) for x in spacings if x}
    if not tiers:
        return "unknown"
    if len(tiers) == 1:
        return next(iter(tiers))
    return "mixed"


def date_of(slug: str) -> str:
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", slug)
    return m.group(1) if m else ""


def parse_snapshot_dir(path: str, slug: str):
    """-> {'series': {idx: (team, file)}, 'games': {n: {idx: (team, file)}},
           'hires': bool}

    2026-10-15：分钟级序列另存为 `<slug>__<idx>__<side>.hires.jsonl`
    （见 tools/fetch_game_market_series.py --hi-res-hours），因为 CLOB 只保留
    最近约 24 小时的分钟级数据，覆盖式写法会连赛前 36 小时的 10 分钟序列一起丢。
    这里让 `.hires.jsonl` **优先于**同名 `.jsonl`，下游 spacing_s 自然反映真实粒度。
    """
    out = {"series": {}, "games": defaultdict(dict), "hires": False}
    pat_series = re.compile(rf"^{re.escape(slug)}__(\d+)__(.+?)(\.hires)?\.jsonl$")
    pat_game = re.compile(rf"^{re.escape(slug)}-game(\d+)__(\d+)__(.+?)(\.hires)?\.jsonl$")

    def put(store: dict, idx: int, team: str, file: str, hires: bool):
        prev = store.get(idx)
        if prev is None or (hires and not prev[2]):
            store[idx] = (team, file, hires)

    for name in sorted(os.listdir(path)):
        if not name.endswith(".jsonl"):
            continue
        m = pat_game.match(name)
        if m:
            put(out["games"][int(m.group(1))], int(m.group(2)), m.group(3),
                os.path.join(path, name), bool(m.group(4)))
            continue
        m = pat_series.match(name)
        if m:
            put(out["series"], int(m.group(1)), m.group(2),
                os.path.join(path, name), bool(m.group(3)))

    all_files = [f for _, f, _ in out["series"].values()] + [
        f for sides in out["games"].values() for _, f, _ in sides.values()
    ]
    out["hires"] = out["hires"] or any(f.endswith(".hires.jsonl") for f in all_files)
    out["series"] = {i: (t, f) for i, (t, f, _) in out["series"].items()}
    out["games"] = defaultdict(dict, {
        n: {i: (t, f) for i, (t, f, _) in sides.items()} for n, sides in out["games"].items()
    })
    return out


def summarize_pts(pts, market_type: str):
    if len(pts) < 3:
        return None
    r = classify(pts, market_type)
    spacing = statistics.median([pts[i + 1][0] - pts[i][0] for i in range(len(pts) - 1)])
    prep = ps.prepare(pts)
    r["spacing_s"] = int(spacing)
    r["points"] = len(pts)
    r["raw_points"] = prep.raw_points
    r["interpolated_ratio"] = prep.interpolated_ratio
    r["smoothing"] = prep.smoothing
    r["confidence"] = prep.confidence
    r["first_ts"] = pts[0][0]
    r["last_ts"] = pts[-1][0]
    return r


def summarize(path: str, market_type: str, window: tuple[float | None, float | None] | None = None):
    pts = load_series(path)
    if window:
        pts = igw.trim(pts, *window)
    return summarize_pts(pts, market_type)


def winner_of(sides: dict) -> int | None:
    if not sides:
        return None
    ends = {i: s["end"] for i, s in sides.items()}
    best = max(ends, key=lambda i: ends[i])
    if ends[best] >= 0.9:
        return best
    return None


def labels_str(s: dict) -> str:
    return "/".join(s["labels"])


def build_match(slug: str, index_meta: dict, in_game: bool = True) -> dict | None:
    path = os.path.join(SNAP_ROOT, slug)
    parts = parse_snapshot_dir(path, slug)
    if not parts["series"] and not parts["games"]:
        return None

    raw_series = {}
    for idx, (team, f) in parts["series"].items():
        try:
            raw_series[idx] = (team, load_series(f))
        except Exception:  # noqa: BLE001
            continue
    raw_games = {}
    for g, sides in sorted(parts["games"].items()):
        for idx, (team, f) in sides.items():
            try:
                raw_games.setdefault(g, {})[idx] = (team, load_series(f))
            except Exception:  # noqa: BLE001
                continue

    ev_start = igw.event_start(slug) if in_game else None
    series = {}
    games = {}
    prev_end = None
    for g in sorted(raw_games):
        raw = raw_games[g]
        side_map = {str(i): pts for i, (_t, pts) in raw.items()}
        floor = ev_start if (g == 1 or prev_end is None) else prev_end + 60
        if in_game:
            start, end, src = igw.window_for_market(side_map, floor)
        else:
            start, end, src = None, None, "full"
        got = {}
        for idx, (team, pts) in raw.items():
            trimmed = igw.trim(pts, start, end) if in_game else pts
            s = summarize_pts(trimmed, "game")
            if s:
                got[idx] = {
                    **s,
                    "team": team.replace("_", " "),
                    "window": src,
                    "window_start": start,
                    "window_end": end,
                    "points_full": len(pts),
                }
        if got:
            games[g] = got
            prev_end = end if end is not None else max(p[-1][0] for _t, p in raw.values())

    if raw_series:
        side_map = {str(i): pts for i, (_t, pts) in raw_series.items()}
        if in_game:
            forced_start = None
            if games:
                first = min(games)
                starts = [s.get("window_start") for s in games[first].values() if s.get("window_start")]
                if starts:
                    forced_start = min(starts)
            if forced_start is not None:
                pivot = max(side_map, key=lambda k: abs(side_map[k][-1][1] - 0.5))
                end = igw.settlement_end(side_map[pivot])
                forced_start = max(forced_start, end - igw.MAX_MATCH_S)
                if forced_start < end - 60:
                    start, src = forced_start, "in_game"
                else:
                    # Game windows can disagree with the series market when a
                    # map file is stale/duplicated; do not create start > end.
                    start, end, src = igw.window_for_market(side_map, ev_start)
            else:
                start, end, src = igw.window_for_market(side_map, ev_start)
        else:
            start, end, src = None, None, "full"
        for idx, (team, pts) in raw_series.items():
            trimmed = igw.trim(pts, start, end) if in_game else pts
            s = summarize_pts(trimmed, "moneyline")
            if s:
                series[idx] = {
                    **s,
                    "team": team.replace("_", " "),
                    "window": src,
                    "window_start": start,
                    "window_end": end,
                    "points_full": len(pts),
                }

    if not series and not games:
        return None

    sw = winner_of(series)
    gw = {g: winner_of(sides) for g, sides in games.items()}
    wins = Counter()
    for g, w in gw.items():
        if w is not None:
            wins[w] += 1
    note = ""
    score = ""
    winner_name = series[sw]["team"] if sw is not None else ""
    loser_name = ""
    if sw is not None:
        loser_idx = 1 - sw if sw in (0, 1) else None
        if loser_idx is not None and loser_idx in series:
            loser_name = series[loser_idx]["team"]
        w, l = wins.get(sw, 0), wins.get(1 - sw, 0) if sw in (0, 1) else 0
        if w == l and w > 0:
            w = l + 1  # 决胜局无独立市场/无成交时，按整场结算归属补足
            fmt = index_meta.get("format") or ""
            decider = decider_game(fmt, w)
            note = f"打满 {fmt or 'BO' + str(2 * w - 1)}，G{decider} 无独立市场/无成交，按整场结算归属补足"
        if w < l:
            note = "小局归属与整场结算不一致，已标注待核"
            w, l = l, w
        score = f"{w}:{l}" if w else ""

    # 未结算的小局（系列赛提前结束、市场仍在）= 噪音，不进形态串
    unresolved = [g for g, w in gw.items() if w is None]
    played_games = [g for g in sorted(games) if gw.get(g) is not None]

    shape_bits = []
    for g in played_games:
        for idx in sorted(games[g]):
            s = games[g][idx]
            shape_bits.append(f"game{g}:{s['team']}:{labels_str(s)}({s['low']:.2f}->{s['high']:.2f})")
    for idx in sorted(series):
        s = series[idx]
        shape_bits.append(f"series:{s['team']}:{labels_str(s)}({s['low']:.2f}->{s['high']:.2f})")

    if winner_name and not score and (index_meta.get("format") == "BO1"):
        score = "1:0"
        note = (note + "；" if note else "") + "BO1 单图，无逐图盘"
    result = f"{winner_name} {score} {loser_name}".strip() if winner_name else "未结算/无整场数据"
    if note:
        result = f"{result}（{note}）"

    spacings = [s["spacing_s"] for s in list(series.values()) + [x for sides in games.values() for x in sides.values()]]
    window_modes = {s.get("window") for s in list(series.values()) + [x for sides in games.values() for x in sides.values()]}
    window_mode = "in_game" if "in_game" in window_modes else ("inferred" if "inferred" in window_modes else "full")
    return {
        "slug": slug,
        "date": date_of(slug),
        "league_raw": index_meta.get("league_raw", ""),
        "league": index_meta.get("league", "-"),
        "format": index_meta.get("format", ""),
        "series": {str(k): v for k, v in series.items()},
        "games": {str(g): {str(k): v for k, v in sides.items()} for g, sides in games.items()},
        "series_winner": sw,
        "game_winners": {str(g): w for g, w in gw.items()},
        "games_unresolved": sorted(unresolved),
        "result": result,
        "price_shape": " | ".join(shape_bits),
        "resolution_s": max(spacings or [0]),
        "granularity": granularity_label(spacings),
        "window": window_mode,
    }


def meta_game(index_path: str, prefix: str) -> str:
    """索引里的事件标题 -> 游戏标签（用于页面文案与联赛归一）。"""
    key = (prefix or "").rstrip("-").lower()
    if key.startswith("cs2"):
        return "cs2"
    if key.startswith("lol"):
        return "lol"
    if index_path and os.path.exists(index_path):
        try:
            data = json.load(open(index_path))
            for m in data.get("markets", []):
                title = (m.get("event_title") or m.get("question") or "").lower()
                if title.startswith("counter-strike"):
                    return "cs2"
                if title.startswith("league of legends") or "lol" in title:
                    return "lol"
        except Exception:  # noqa: BLE001
            pass
    return "lol"


def league_meta(index_path: str) -> dict:
    """slug -> {league_raw, league, format}，来自 gamma 市场标题。"""
    game = meta_game(index_path, "")
    meta = {}
    if not index_path or not os.path.exists(index_path):
        return meta
    data = json.load(open(index_path))
    for m in data.get("markets", []):
        if m.get("group") != "Match Winner":
            continue
        q = m.get("question") or ""
        raw = ""
        if " - " in q:
            raw = q.rsplit(" - ", 1)[1].strip()
        fmt = ""
        fm = re.search(r"\((BO\d)\)", q)
        if fm:
            fmt = fm.group(1)
        # 2026-10-19 修正：混合索引（LoL + CS2 同一天）时不能用全局 meta_game，
        # 否则所有场次都会被标成第一个出现的游戏（曾把两场 LoL 标成 CS2）。
        t = q.lower()
        if t.startswith("counter-strike"):
            row_game = "cs2"
        elif t.startswith("league of legends") or "lol:" in t:
            row_game = "lol"
        elif t.startswith("dota 2"):
            row_game = "dota2"
        elif t.startswith("valorant"):
            row_game = "val"
        else:
            row_game = game
        meta[m["event_slug"]] = {"league_raw": raw, "format": fmt, "league": row_game}
    return meta


def norm_league(raw: str, league: str) -> str:
    """粗分组（展示用）：LoL 按赛区；CS2 按联赛分类标准统一为 CS2（明细看 league_raw）。"""
    t = (raw or "").lower()
    if not t:
        return "其他/未标注"
    if league == "cs2":
        return "CS2"
    if "lck challengers" in t or "challengers league" in t or "academy" in t:
        return "LCK CL"
    if "lck" in t:
        return "LCK"
    if "lpl" in t:
        return "LPL"
    if "lec" in t:
        return "LEC"
    if "lcp" in t:
        return "LCP"
    if "lcs" in t:
        return "LCS"
    if "vcs" in t:
        return "VCS"
    if "demacia" in t or "德玛西亚" in t:
        return "德杯 DCGI"
    if "kespa" in t or "k杯" in t:
        return "KeSPA Cup"
    if any(k in t for k in ("worlds", "world championship", "msi", "mid-season", "first stand", "asian games", "esports world cup", "wsci", "world star")):
        return "国际赛"
    if "emea masters" in t:
        return "EMEA Masters"
    return "其他赛区"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", required=True, help="UTC 日期区间 start:end")
    ap.add_argument("--prefix", default="lol-")
    ap.add_argument("--index", default="", help="gamma 市场索引（取联赛/赛制标签）")
    ap.add_argument("--out", default="/tmp/pattern_shapes.json")
    ap.add_argument("--write", action="store_true", help="写入 docs/data/intel/price_paths.json")
    ap.add_argument("--update", action="store_true", help="已存在且 source 为本工具产出的条目 -> 覆盖刷新")
    ap.add_argument("--html", default="")
    ap.add_argument("--updated", default=dt.date.today().isoformat())
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slugs-file", default="", help="显式 slug 列表（每行一个），给出时不按 range/prefix 全扫")
    ap.add_argument("--full-window", action="store_true",
                    help="保留赛前/赛后全窗口（旧口径；默认只取比赛内窗口）")
    args = ap.parse_args()

    start, end = args.range.split(":")
    slugs = []
    if args.slugs_file:
        want = {l.strip() for l in open(args.slugs_file) if l.strip()}
        slugs = sorted(n for n in os.listdir(SNAP_ROOT) if n in want)
    for name in ([] if args.slugs_file else sorted(os.listdir(SNAP_ROOT))):
        if not name.startswith(args.prefix):
            continue
        d = date_of(name)
        if d and start <= d <= end:
            slugs.append(name)
    if args.limit:
        slugs = slugs[: args.limit]
    game = meta_game(args.index, args.prefix)
    meta = league_meta(args.index)

    rows = []
    for slug in slugs:
        m = meta.get(slug, {})
        try:
            r = build_match(slug, m, in_game=not args.full_window)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {slug}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if r:
            r["league"] = norm_league(m.get("league_raw", ""), m.get("league") or game)
            rows.append(r)

    with_games = [r for r in rows if r["games"]]
    with_series = [r for r in rows if r["series"]]
    print(f"扫描 {len(slugs)} 场 -> 可用 {len(rows)}（逐局 {len(with_games)} / 整局 {len(with_series)}）")
    freq_game = Counter()
    freq_series = Counter()
    for r in with_games:
        unresolved_g = {int(x) for x in r.get("games_unresolved", [])}
        for g, sides in r["games"].items():
            if int(g) in unresolved_g:
                continue  # 未打到/未结算的小局（如 BO3 的 G4）不进形态频次
            for s in sides.values():
                for lab in s["labels"]:
                    freq_game[lab] += 1
    for r in with_series:
        if r.get("series_winner") is None:
            continue  # 未结算/无定价的整局市场不进形态频次
        for s in r["series"].values():
            for lab in s["labels"]:
                freq_series[lab] += 1
    print("小局形态频次：", dict(freq_game.most_common()))
    print("整局形态频次：", dict(freq_series.most_common()))
    print("联赛分布：", dict(Counter(r["league"] for r in rows).most_common()))

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "range": args.range,
        "prefix": args.prefix,
        "matches": rows,
        "freq_game": dict(freq_game),
        "freq_series": dict(freq_series),
        "league_dist": dict(Counter(r["league"] for r in rows)),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"明细 -> {args.out}")

    if args.write:
        lib = json.load(open(LIB_PATH))
        have = {p["slug"] for p in lib["paths"]}
        # 未结算（市场未定价 / 无整场数据）不入库：形态再好也没有结果锚点
        purged = 0
        lib["paths"] = [p for p in lib["paths"] if not (
            "build_pattern_library" in str(p.get("source", "")) and str(p.get("result", "")).startswith("未结算")
        )]
        added = updated = enriched = 0
        index_of = {p["slug"]: i for i, p in enumerate(lib["paths"])}
        for r in rows:
            if r["result"].startswith("未结算"):
                purged += 1
                continue
            entry = {
                "slug": r["slug"],
                "source": f"docs/data/snapshots/{r['slug']}（快照自动分类 tools/build_pattern_library.py；{r['resolution_s']}s 粒度）",
                "result": r["result"],
                "price_shape": r["price_shape"],
                "granularity": r["granularity"],
                "window": r.get("window", "in_game"),
                "key_points": [],
                "updated": args.updated,
            }
            if r["slug"] in have and r["slug"] in index_of:
                cur = lib["paths"][index_of[r["slug"]]]
                if args.update and "build_pattern_library" in str(cur.get("source", "")):
                    lib["paths"][index_of[r["slug"]]] = entry
                    entry["updated"] = args.updated
                    updated += 1
                elif cur.get("granularity") != r["granularity"]:
                    # 非破坏性补字段：人工/复盘来源的条目也带上机器判定的粒度，
                    # 让"粒度"在全库可见（不改 source / 结论，只补一个派生字段）。
                    cur["granularity"] = r["granularity"]
                    enriched += 1
                continue
            lib["paths"].append(entry)
            have.add(r["slug"])
            added += 1
        lib["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        lib["description"] = (
            "交易复盘 + 快照自动分类的盘口轨迹/形态库（按比赛 slug 索引，逐小局 + 整局 price_shape）。"
        )
        with open(LIB_PATH, "w", encoding="utf-8") as fh:
            json.dump(lib, fh, ensure_ascii=False, indent=1)
        print(f"形态库写入 {added} 条 / 刷新 {updated} 条 / 补粒度 {enriched} 条 / 跳过未结算 {purged} 场（累计 {len(lib['paths'])}）")

    if args.html:
        write_html(args.html, payload, args.range, game, in_game=not args.full_window)
        print(f"HTML -> {args.html}")
    return 0


def cents(p: float) -> int:
    return int(round(p * 100))


def label_span(s: dict) -> str:
    return f"{cents(s['low'])}→{cents(s['high'])}"


def game_cell(r: dict) -> str:
    out = []
    for g, sides in sorted(r["games"].items(), key=lambda kv: int(kv[0])):
        if int(g) in [int(x) for x in r.get("games_unresolved", [])]:
            out.append(f"<div class='g muted'><b>G{g}</b> 未打到/未结算（无价格意义）</div>")
            continue
        w = r["game_winners"].get(g)
        bits = []
        for idx, s in sorted(sides.items(), key=lambda kv: int(kv[0])):
            cls = "win" if w is not None and int(idx) == w else "lose"
            bits.append(f"<span class='{cls}'>{s['team']} {labels_str(s)} <i>{label_span(s)}</i></span>")
        out.append(f"<div class='g'><b>G{g}</b> " + " · ".join(bits) + "</div>")
    return "\n".join(out) or "<span class='muted'>无逐局/逐图数据</span>"


def series_cell(r: dict) -> str:
    if not r["series"]:
        return "<span class='muted'>无整场数据</span>"
    bits = []
    for idx, s in sorted(r["series"].items(), key=lambda kv: int(kv[0])):
        cls = "win" if r["series_winner"] is not None and int(idx) == r["series_winner"] else "lose"
        bits.append(f"<span class='{cls}'>{s['team']} {labels_str(s)} <i>{label_span(s)}</i></span>")
    return " · ".join(bits)


def freq_bars(freq: dict, total: int) -> str:
    if not freq:
        return "<p class='muted'>无样本</p>"
    mx = max(freq.values())
    html = []
    for lab, n in sorted(freq.items(), key=lambda kv: -kv[1]):
        pct = n / mx * 100
        color = FAMILY_COLOR[family_of(lab)]
        html.append(
            f"<div class='bar'><span class='bl'>{lab}</span>"
            f"<span class='bt'><i style='width:{pct:.1f}%;background:{color}'></i></span>"
            f"<span class='bn'>{n}</span></div>"
        )
    return "\n".join(html)


def write_html(path: str, payload: dict, rng: str, game: str = "lol", in_game: bool = True) -> None:
    game_label = "CS2" if game == "cs2" else "LoL"
    unit = "地图" if game == "cs2" else "小局"
    rows = payload["matches"]
    total_games = sum(len(r["games"]) for r in rows)
    gran = Counter(r.get("granularity", "unknown") for r in rows)
    gran_v = " + ".join(k for k, _ in gran.most_common()) or "—"
    gran_n = " · ".join(f"{k} {v} 场" for k, v in gran.most_common()) or "无样本"
    leagues = sorted(payload["league_dist"].items(), key=lambda kv: -kv[1])
    by_league = defaultdict(list)
    for r in rows:
        by_league[r["league"]].append(r)
    for k in by_league:
        by_league[k].sort(key=lambda r: (r["date"], r["slug"]))

    legend = (
        "<b>A 类（反转）</b>：A1 V 型极值反转 / A2 中位 U 型反转 / A3 折价修复 / A4 下狗整场反转 / "
        "A5 W 型双底 / A6 反弹确认 / A7 强强对话错杀　"
        "<b>B 类（崩塌）</b>：B1 尾盘崩塌 / B2 死亡螺旋 / B3 假反弹后归零 / B4 直线阴跌　"
        "<b>C 类（震荡）</b>：C1 中位震荡 / C2 五五开开局碾压 / C2 早期缩距·热门确立"
        "（C3 中位震荡后突破：手册里有定义，分类器尚未实现）　"
        "<b>热门全程压制</b>：赛前 ≥60c 且全程不回撤到 55c 以下收 100c"
    )

    sections = []
    for league, items in sorted(by_league.items(), key=lambda kv: -len(kv[1])):
        body = []
        for r in items:
            gz = r.get("granularity", "unknown")
            badge = f"<span class='gz{' hi' if gz == '60s' else ''}'>{gz}</span>"
            body.append(
                f"<tr id='{r['slug']}'><td class='d'>{r['date'][5:]}</td>"
                f"<td class='m'><b>{r['slug']}</b>{badge}<div class='muted'>{r['result']}"
                + (f" · {r['league_raw']}" if r.get("league_raw") else "")
                + "</div></td>"
                f"<td class='gg'>{game_cell(r)}</td><td class='ss'>{series_cell(r)}</td></tr>"
            )
        sections.append(
            f"<h3>{league} <span class='muted'>{len(items)} 场</span></h3>"
            "<div class='card tbl'><table><thead><tr><th>日期</th><th>比赛 / 结果</th><th>逐{unit}形态</th><th>整局形态</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>"
        )

    no_game_rows = [r for r in rows if not r["games"]]
    unresolved_rows = [r for r in rows if r["result"].startswith("未结算")]
    no_game = len(no_game_rows)
    no_game_ex = "、".join(r["slug"] for r in no_game_rows[:6]) or "—"
    unresolved_list = "、".join(f"{r['slug']}（{r['series'] and '序列止于 ~50c' or '无数据'}）" for r in unresolved_rows) or "—"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{game_label} 赔率形态结构库 · {rng}</title>
<style>
:root{{--blue:#0071e3;--bg:#f5f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6e6e73;--bd:#e5e5ea;--red:#ff453a;--amber:#ff9f0a}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
.bar-top{{background:#1d1d1f;color:#fff;padding:14px 20px;display:flex;justify-content:space-between;align-items:center}}
.bar-top b{{font-weight:600;letter-spacing:.3px}}
.bar-top span{{color:#a1a1a6;font-size:13px}}
.wrap{{max-width:1180px;margin:0 auto;padding:22px 16px 60px}}
h1{{font-size:26px;margin:18px 0 6px;font-weight:600}}
.sub{{color:var(--mu);margin:0 0 20px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0 8px}}
.card{{background:var(--card);border-radius:14px;padding:16px}}
.card .k{{font-size:12px;color:var(--mu)}}
.card .v{{font-size:24px;font-weight:600;margin-top:4px}}
.card .n{{font-size:12px;color:var(--mu);margin-top:2px}}
h2{{font-size:19px;margin:28px 0 10px;font-weight:600}}
h3{{font-size:16px;margin:22px 0 8px;font-weight:600}}
.muted{{color:var(--mu);font-weight:400}}
.bar{{display:flex;align-items:center;gap:10px;margin:4px 0;font-size:13px}}
.bl{{width:190px;flex:0 0 auto}}
.bt{{flex:1;background:#ececf0;border-radius:5px;height:10px;overflow:hidden}}
.bt i{{display:block;height:10px;border-radius:5px}}
.bn{{width:44px;text-align:right;color:var(--mu)}}
.tbl{{padding:6px 10px;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:var(--mu);font-weight:500;padding:8px 6px;border-bottom:1px solid var(--bd);white-space:nowrap}}
td{{padding:9px 6px;border-bottom:1px solid var(--bd);vertical-align:top}}
td.d{{white-space:nowrap;color:var(--mu)}}
td.m{{min-width:210px}}
td.gg,td.ss{{line-height:1.7}}
.g{{margin:2px 0}}
.win{{color:var(--tx);font-weight:500}}
.lose{{color:var(--mu)}}
i{{font-style:normal;color:var(--mu);font-size:12px}}
.note{{background:#fff;border-left:3px solid var(--blue);border-radius:10px;padding:14px 16px;color:#3a3a3c;font-size:13px}}
.gz{{display:inline-block;font-size:10px;line-height:1.5;padding:0 5px;border-radius:6px;background:#ececf0;color:#6e6e73;margin-left:6px;vertical-align:middle}}
.gz.hi{{background:#e3f0ff;color:#0071e3}}
.note li{{margin:4px 0}}
.gap{{margin:0;padding-left:18px;font-size:13px;color:#3a3a3c}}
.gap li{{margin:6px 0}}
</style></head><body>
<div class="bar-top"><b>Polymarket 电竞情报库 · 赔率形态结构库</b><span>{game_label} · {rng}</span></div>
<div class="wrap">
<h1>{game_label} 赔率 K 线走势形态结构（逐{unit} + 整局）</h1>
<p class="sub">区间 {rng} · 自动分类（tools/classify_pattern.py v1.3）· 数据源 docs/data/snapshots/ · 口径：{'仅比赛内窗口（已剔除赛前长平台、赛后结算平台、图间停滞）' if in_game else '赛前约 36h 至结算的全窗口价格序列（旧口径对照）'}</p>

<div class="cards">
<div class="card"><div class="k">入库比赛</div><div class="v">{len(rows)}</div><div class="n">区间内全部 {game_label} 场次</div></div>
<div class="card"><div class="k">含逐{unit}形态</div><div class="v">{len([r for r in rows if r['games']])}</div><div class="n">共 {total_games} 个{unit}</div></div>
<div class="card"><div class="k">含整局形态</div><div class="v">{len([r for r in rows if r['series']])}</div><div class="n">整场 Winner 序列</div></div>
<div class="card"><div class="k">{unit}形态种类</div><div class="v">{len(payload['freq_game'])}</div><div class="n">A/B/C + 热门压制</div></div>
<div class="card"><div class="k">数据粒度</div><div class="v">{gran_v}</div><div class="n">{gran_n}</div></div>
</div>

<h2>一、形态频次（逐{unit} vs 整局）</h2>
<div class="card">
<div class="muted" style="font-size:13px;margin-bottom:6px">逐{unit}（{total_games} 个{unit}，按两侧分别打标）</div>
{freq_bars(payload['freq_game'], total_games)}
<div class="muted" style="font-size:13px;margin:14px 0 6px">整局（{len([r for r in rows if r['series']])} 场，按两侧分别打标）</div>
{freq_bars(payload['freq_series'], len([r for r in rows if r['series']]))}
</div>

<h2>二、缺口与未结算</h2>
<div class="card">
<ul class="gap">
<li><b>无逐{unit}市场</b>（Polymarket 未开逐局盘，仅整场形态）：{no_game} 场 · 例如 {no_game_ex}</li>
<li><b>市场未定价/无整场数据</b>（不计入形态库）：{len(unresolved_rows)} 场 · {unresolved_list}</li>
<li><b>粒度（按实际采样间隔分层）</b>：<b>60s</b> = 真实分钟级序列（{unit}内数十个采样点，能看到 600s 完全错过的深水触点）；<b>600s</b> = 10 分钟采样（{unit}约 45 分钟 = 4–6 个点），仅用于未能取得分钟级的历史市场。1 分钟可回溯：正确参数为 <code>startTs/endTs + fidelity=1</code>（不传 <code>interval</code>），2026-07 的老比赛同样可抓。两档都只统计<b>比赛内窗口</b>，但 60s 的归档起点可能略晚于开赛；两档<b>分辨率不同</b>，跨场比较前必须先按粒度分层。</li>
</ul>
</div>

<h2>三、逐场明细</h2>
<p class="sub">联赛分布：{" · ".join(f"{k} {v}" for k, v in leagues)}</p>
{''.join(sections)}

<h2>四、口径说明</h2>
<div class="note">
<ul>
<li><b>形态标签</b>：{legend}</li>
<li><b>价格区间</b>：每格 <i>低→高</i>（单位：美分），取自该市场该侧的比赛内窗口序列；窗口 = 开赛到结算（赛前长平台与赛后结算平台已剔除）。</li>
<li><b>粒度</b>：{gran_n}（按<b>实际采样间隔</b>归桶，599s / 601s 的抖动一律计入 600s；判定不看文件名）。1 分钟可回溯：正确参数为 <code>startTs/endTs + fidelity=1</code>（不传 <code>interval</code>），2026-07 的老比赛同样可抓；600s 仅用于未能取得分钟级的历史市场。</li>
<li><b>逐{unit}缺失</b>：{len([r for r in rows if not r['games']])} 场无逐{unit}市场（Polymarket 未对全部场次开设逐局盘），已在表中标注"无小局数据"。</li>
<li><b>第 5 局</b>：打满 BO5 时 Polymarket 无第 5 局独立市场，按整场结算归属补足比分（表中注明）。</li>
<li><b>标签为统计描述，不是交易建议</b>；同一形态在正例里是机会、在反例里是陷阱。</li>
</ul>
</div>
</div></body></html>"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


if __name__ == "__main__":
    raise SystemExit(main())
