#!/usr/bin/env python3
"""Aggregate stats for the price-shape (形态结构) library, per game (LoL first, then CS2).

输入：docs/data/intel/price_paths.json 明细（tools/build_pattern_library.py 输出的
      --out JSON，含 matches[].series / games[].labels/low/high/end）。
输出：终端 markdown 文本 + 可选 --json。

统计口径（2026-10-15 定稿）：
  1) 胜方 / 负方分开——只统计"赢家侧"的形态才有交易含义；
     负方绝大多数是 B4 直线阴跌（输家代币自然衰减），单独列出仅作对照。
  2) 深水反转：赢家侧窗口低点 <= 0.30 / 0.20 / 0.10 的场次与形态。
  3) 让一追二：系列赛 G1 归属 != 最终胜者（含 BO3/BO5）。
  4) 形态 × 收益：赢家侧"低点买入 -> 结算"的倍数（1/low-1），中位数 + 分位。
  5) 粒度分层（--by-granularity）：60s 与 600s 的采样精度与窗口都不同，
     两者混算会把"采样精度"读成"策略差异"，分层输出后仍给一份全量对照，
     全量那份必须只当参考、不得作为结论（见 AGENTS 规则 26）。

用法：
  python3 tools/pattern_library_stats.py reports/pattern_shapes_lol_2026-0910_1001.json \\
      reports/pattern_shapes_lol_2026-1002_1014.json
  python3 tools/pattern_library_stats.py --by-granularity reports/pattern_shapes_cs2_*.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict


def load_reports(paths):
    matches = []
    for p in paths:
        data = json.load(open(p, encoding="utf-8"))
        for m in data.get("matches", []):
            m["_src"] = p
            matches.append(m)
    return matches


def winner_side_of(sides: dict):
    if not sides:
        return None, None
    ends = {int(k): v for k, v in sides.items()}
    wi = max(ends, key=lambda i: ends[i]["end"])
    if ends[wi]["end"] < 0.9:
        return None, None
    li = None
    if wi in (0, 1):
        other = 1 - wi
        if other in ends:
            li = other
    return wi, li


def labels_of(side: dict):
    return side.get("labels") or ["未知"]


def collect(matches):
    game_win = Counter()
    game_lose = Counter()
    series_win = Counter()
    series_lose = Counter()
    deep_game = Counter()
    deep_series = Counter()
    gran = Counter(m.get("granularity", "unknown") for m in matches)
    n_game = n_series = 0
    gains = defaultdict(list)
    reverse = defaultdict(list)   # 让一追二（G1 输）
    samples = defaultdict(list)
    for m in matches:
        sw, sl = winner_side_of(m.get("series") or {})
        if sw is not None:
            n_series += 1
            s = m["series"][str(sw)]
            for lab in labels_of(s):
                series_win[lab] += 1
                if len(samples[lab]) < 3:
                    samples[lab].append(f"{m['slug']}({m.get('league','-')}) 整局 {s['low']:.2f}->{s['end']:.2f}")
            if s["low"] <= 0.30:
                deep_series["<=0.30"] += 1
                if s["low"] <= 0.20:
                    deep_series["<=0.20"] += 1
                if s["low"] <= 0.10:
                    deep_series["<=0.10"] += 1
                for lab in labels_of(s):
                    deep_game[f"series:{lab}"] += 1
            if sl is not None:
                for lab in labels_of(m["series"][str(sl)]):
                    series_lose[lab] += 1
            if s["low"] > 0:
                gain = 1.0 / s["low"] - 1.0
                gains["series"].append(gain)
                for lab in labels_of(s):
                    gains[f"series:{lab}"].append(gain)
        games = m.get("games") or {}
        gw = {}
        for g, sides in games.items():
            wi, li = winner_side_of(sides)
            if wi is None:
                continue
            n_game += 1
            gw[int(g)] = wi
            s = sides[str(wi)]
            for lab in labels_of(s):
                game_win[lab] += 1
                if len(samples["G:" + lab]) < 3:
                    samples["G:" + lab].append(f"{m['slug']} G{g} {s['low']:.2f}->{s['end']:.2f}")
            if s["low"] <= 0.30:
                deep_game["<=0.30"] += 1
                if s["low"] <= 0.20:
                    deep_game["<=0.20"] += 1
                if s["low"] <= 0.10:
                    deep_game["<=0.10"] += 1
                for lab in labels_of(s):
                    deep_game[f"G:{lab}"] += 1
            if li is not None:
                for lab in labels_of(sides[str(li)]):
                    game_lose[lab] += 1
            if s["low"] > 0:
                gain = 1.0 / s["low"] - 1.0
                gains["game"].append(gain)
                for lab in labels_of(s):
                    gains[f"game:{lab}"].append(gain)
        # 让一追二：G1 归属非最终胜者
        if sw is not None and 1 in gw and gw[1] != sw:
            key = f"{m.get('format','?')}"
            reverse[key].append(m["slug"])
    return dict(game_win=game_win, game_lose=game_lose, series_win=series_win, series_lose=series_lose,
                deep_game=deep_game, deep_series=deep_series, n_game=n_game, n_series=n_series,
                gains=gains, reverse=reverse, samples=samples, gran=gran)


def pct(a, b):
    return f"{a}/{b} = {100.0*a/b:.0f}%" if b else "-"


def med(v):
    return statistics.median(v) if v else float("nan")


def render(st, label):
    out = []
    add = out.append
    add(f"### {label}")
    add("")
    add(f"样本：整局 {st['n_series']} 个市场侧（含胜负两侧为 {st['n_series']*2}），逐小局 {st['n_game']} 个胜方侧")
    if st.get("gran"):
        add("数据粒度：" + " · ".join(f"{k} {v} 场" for k, v in st["gran"].most_common())
            + "（60s 与 600s 不构成互推证据：窗口长度与采样精度都不同）")
    add("")
    add("整局·胜方形态频次（分母 = 赢家侧数量）：")
    for k, v in st["series_win"].most_common():
        add(f"  - {k}: {v}（{pct(v, st['n_series'])}）")
    add("")
    add("逐小局·胜方形态频次：")
    for k, v in st["game_win"].most_common():
        add(f"  - {k}: {v}（{pct(v, st['n_game'])}）")
    add("")
    add("整局·负方形态频次（对照：输家侧自然衰减）：")
    for k, v in st["series_lose"].most_common(6):
        add(f"  - {k}: {v}")
    add("")
    add("深水反转过（赢家侧低点）：")
    for k in ("<=0.30", "<=0.20", "<=0.10"):
        add(f"  - 整局 {k}: {st['deep_series'].get(k,0)}（{pct(st['deep_series'].get(k,0), st['n_series'])}）"
            f" | 小局 {k}: {st['deep_game'].get(k,0)}（{pct(st['deep_game'].get(k,0), st['n_game'])}）")
    add("")
    add("让一追二（G1 输掉后反赢整场）：")
    tot = 0
    for k, v in sorted(st["reverse"].items()):
        add(f"  - {k}: {len(v)}")
        tot += len(v)
    if st["n_series"]:
        add(f"  - 合计 {tot}（占 {100.0*tot/st['n_series']:.0f}% 的整局市场侧；注意同一场胜方分母）")
    add("")
    add("低点买入 -> 结算 的倍数（赢家侧，1/low-1）：")
    for key in ("series", "game"):
        v = st["gains"].get(key, [])
        if v:
            vs = sorted(v)
            q = lambda p: vs[min(len(vs)-1, int(p*len(vs)))]
            add(f"  - {key}: 中位 {med(v):.2f}x | p25 {q(0.25):.2f}x | p75 {q(0.75):.2f}x | n={len(v)}")
    add("")
    add("典型样本（每条形态最多 3 例）：")
    for k in sorted(st["samples"]):
        if k.startswith("G:"):
            continue
        for s in st["samples"][k]:
            add(f"  - {k}: {s}")
    add("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--label", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--by-granularity", action="store_true",
                    help="按数据粒度（60s/600s）分层统计，禁止两档混算")
    args = ap.parse_args()
    matches = load_reports(args.reports)
    if args.by_granularity:
        groups = defaultdict(list)
        for m in matches:
            groups[m.get("granularity", "unknown")].append(m)
        for g in sorted(groups, key=lambda k: (k == "unknown", k)):
            print(render(collect(groups[g]), f"{args.label or '形态库'} · 粒度 {g}（{len(groups[g])} 场）"))
        print(render(collect(matches), f"{args.label or '形态库'} · 全量（{len(matches)} 场，两档混算，仅作对照）"))
        return 0
    st = collect(matches)
    print(render(st, args.label or ", ".join(args.reports)))
    if args.json:
        json.dump({k: (dict(v) if isinstance(v, Counter) else v) for k, v in st.items()
                   if k not in ("gains",)},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
