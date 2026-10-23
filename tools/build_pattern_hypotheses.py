#!/usr/bin/env python3
"""Build the LoL pattern-hypothesis ledger and first-pass baseline page.

This is an observation-layer tool: it never places orders.  It turns the
user's three trading intuitions into explicit, testable definitions, then
reports the first in-sample baseline with sample lists and caveats.

Outputs:
    docs/data/intel/pattern_hypotheses.json
    reports/pattern_hypotheses.html
"""

from __future__ import annotations

import datetime as dt
import glob
import html
import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reports" / "pattern_shapes_lol_tier1_2026-07_2026-10.json"
SNAP = ROOT / "docs" / "data" / "snapshots"
OUT_JSON = ROOT / "docs" / "data" / "intel" / "pattern_hypotheses.json"
OUT_HTML = ROOT / "reports" / "pattern_hypotheses.html"


def parse_ts(value):
    if isinstance(value, (int, float)):
        return float(value)
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def load_side(slug: str, idx: int) -> list[tuple[float, float]]:
    hi = glob.glob(str(SNAP / slug / f"{slug}__{idx}__*.hires.jsonl"))
    plain = glob.glob(str(SNAP / slug / f"{slug}__{idx}__*.jsonl"))
    path = hi[0] if hi else (plain[0] if plain else None)
    if not path:
        return []
    points = []
    for line in open(path, encoding="utf-8"):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "timestamp" not in row or "price" not in row:
            continue
        points.append((parse_ts(row["timestamp"]), float(row["price"])))
    return sorted(points)


def load_game_side(slug: str, game: int, idx: int) -> list[tuple[float, float]]:
    hi = glob.glob(str(SNAP / slug / f"{slug}-game{game}__{idx}__*.hires.jsonl"))
    plain = glob.glob(str(SNAP / slug / f"{slug}-game{game}__{idx}__*.jsonl"))
    path = hi[0] if hi else (plain[0] if plain else None)
    if not path:
        return []
    points = []
    for line in open(path, encoding="utf-8"):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "timestamp" not in row or "price" not in row:
            continue
        points.append((parse_ts(row["timestamp"]), float(row["price"])))
    return sorted(points)


def price_at(points: list[tuple[float, float]], ts: float):
    value = None
    for t, p in points:
        if t <= ts:
            value = p
        else:
            break
    return value


def pre_prices(match):
    return {int(k): float(v["pre"]) for k, v in match["series"].items()}


def winners(match):
    return {int(k): int(v) for k, v in match["game_winners"].items() if v is not None}


def game_window(match, number: int):
    game = match["games"].get(str(number))
    if not game:
        return None
    starts = [v.get("window_start") for v in game.values() if v.get("window_start")]
    ends = [v.get("window_end") for v in game.values() if v.get("window_end")]
    if not starts or not ends:
        return None
    return min(starts), max(ends)


def metrics(trades, threshold=None):
    if not trades:
        return {"n": 0, "wins": 0, "hit_rate": None, "avg_entry": None,
                "median_entry": None, "ev": None, "ev_at_threshold": None}
    n = len(trades)
    wins = sum(1 for t in trades if t["won"])
    entries = [t["entry"] for t in trades]
    hit = wins / n
    ev = sum(((1 / e - 1) if t["won"] else -1) for e, t in zip(entries, trades)) / n
    ev_thr = None
    if threshold is not None:
        ev_thr = hit * (1 / threshold - 1) - (1 - hit)
    return {
        "n": n,
        "wins": wins,
        "hit_rate": round(hit, 3),
        "avg_entry": round(statistics.mean(entries), 3),
        "median_entry": round(statistics.median(entries), 3),
        "ev": round(ev, 3),
        "ev_at_threshold": round(ev_thr, 3) if ev_thr is not None else None,
    }


def h1_trades(matches):
    """BO3: favourite wins G1, loses G2; buy favourite series after G2."""
    trades = []
    for m in matches:
        if m["format"] != "BO3" or not m["games"] or m.get("series_winner") is None:
            continue
        pre = pre_prices(m)
        fav = max(pre, key=pre.get)
        if pre[fav] < 0.60:
            continue
        gw = winners(m)
        if gw.get(1) != fav or gw.get(2) == fav:
            continue
        window = game_window(m, 2)
        if not window:
            continue
        points = load_side(m["slug"], fav)
        entry = price_at(points, window[1] + 30)
        if entry is None:
            continue
        low_after = min((p for t, p in points if t >= window[1] + 60), default=None)
        trades.append({
            "slug": m["slug"],
            "fav": m["series"][str(fav)]["team"],
            "pre": round(pre[fav], 4),
            "entry": round(entry, 4),
            "low_after_g2": round(low_after, 4) if low_after is not None else None,
            "held_050": bool(low_after is not None and low_after >= 0.50),
            "won": m["series_winner"] == fav,
            "series": m["result"],
        })
    return trades


def h2_rows(matches):
    """Favourite crushes G1; test series win, clean sweep, G2 win."""
    rows = []
    for m in matches:
        if m["format"] not in ("BO3", "BO5") or not m["games"] or m.get("series_winner") is None:
            continue
        pre = pre_prices(m)
        fav = max(pre, key=pre.get)
        if pre[fav] < 0.60:
            continue
        gw = winners(m)
        if gw.get(1) != fav:
            continue
        g1 = m["games"]["1"].get(str(fav))
        if not g1 or g1["low"] < 0.70 or g1["end"] < 0.99:
            continue
        wins = sum(1 for v in gw.values() if v == fav)
        losses = sum(1 for v in gw.values() if v != fav)
        need = 2 if m["format"] == "BO3" else 3
        g2 = m["games"].get("2", {}).get(str(fav))
        rows.append({
            "slug": m["slug"],
            "format": m["format"],
            "fav": m["series"][str(fav)]["team"],
            "series_win": m["series_winner"] == fav,
            "clean_sweep": wins == need and losses == 0,
            "g2_entry": round(float(g2["pre"]), 4) if g2 else None,
            "g2_win": gw.get(2) == fav if 2 in gw else None,
            "score": f"{wins}-{losses}",
            "result": m["result"],
        })
    return rows


def h3_rows(matches):
    """BO5: underdog series deep-dip during G3/G4, split by 1-1 vs 0-2."""
    rows = []
    for m in matches:
        if m["format"] != "BO5" or not m["games"] or m.get("series_winner") is None:
            continue
        pre = pre_prices(m)
        dog = min(pre, key=pre.get)
        if pre[dog] >= 0.50:
            continue
        points = load_side(m["slug"], dog)
        if not points:
            continue
        gw = winners(m)
        before = (
            sum(1 for g in (1, 2) if gw.get(g) == dog),
            sum(1 for g in (1, 2) if gw.get(g) is not None and gw.get(g) != dog),
        )
        windows = []
        for number in (3, 4):
            window = game_window(m, number)
            if window:
                windows.append((number, window[0], window[1]))
        if not windows:
            continue
        for threshold in (0.10, 0.15, 0.20):
            touch = None
            when = None
            for t, p in points:
                for number, start, end in windows:
                    if start <= t <= end and p <= threshold:
                        touch, when = p, number
                        break
                if touch is not None:
                    break
            if touch is not None:
                rows.append({
                    "slug": m["slug"],
                    "dog": m["series"][str(dog)]["team"],
                    "entry": round(touch, 4),
                    "threshold": threshold,
                    "game": when,
                    "score_before": f"{before[0]}-{before[1]}",
                    "won": m["series_winner"] == dog,
                    "result": m["result"],
                })
    return rows


def h4_extreme_rows(matches, threshold=0.05, prior_high=None, fast_minutes=None, cache=None):
    """Game-level extreme touch: price <= threshold, optional context filters.

    prior_high: side must have traded at/above this price earlier in the game.
    fast_minutes: the prior high must have occurred within this many minutes.
    """
    rows = []
    cache = cache if cache is not None else {}
    for m in matches:
        for gs, sides in m["games"].items():
            number = int(gs)
            winner = m["game_winners"].get(gs)
            if winner is None:
                continue
            for side, stat in sides.items():
                start, end = stat.get("window_start"), stat.get("window_end")
                if not start or not end:
                    continue
                key = (m["slug"], number, int(side))
                if key not in cache:
                    cache[key] = load_game_side(m["slug"], number, int(side))
                points = [(t, p) for t, p in cache[key] if start <= t <= end]
                touch = None
                for i, (t, p) in enumerate(points):
                    if p <= threshold:
                        touch = i
                        break
                if touch is None:
                    continue
                before = points[:touch]
                if prior_high is not None:
                    if not before or max(p for _, p in before) < prior_high:
                        continue
                if fast_minutes is not None:
                    t0 = points[touch][0]
                    recent = [p for t, p in before if t0 - fast_minutes * 60 <= t]
                    if not recent or max(recent) < 0.20:
                        continue
                after = points[touch:]
                rows.append({
                    "slug": m["slug"],
                    "game": number,
                    "side": int(side),
                    "pre": float(stat["pre"]),
                    "entry": float(points[touch][1]),
                    "won": winner == int(side),
                    "max_after": max(p for _, p in after) if after else float(points[touch][1]),
                })
    return rows


def h4_bounce(rows, threshold, take_profit):
    if not rows:
        return {"take_profit": take_profit, "n": 0, "sold_rate": None, "ev": None}
    payoffs = []
    sold = 0
    for row in rows:
        if row["max_after"] >= take_profit:
            sold += 1
            payoffs.append(take_profit / threshold - 1)
        else:
            payoffs.append((1 / threshold - 1) if row["won"] else -1)
    return {
        "take_profit": take_profit,
        "n": len(rows),
        "sold_rate": round(sold / len(rows), 3),
        "ev": round(sum(payoffs) / len(payoffs), 3),
    }


def h4_confirmation_rows(matches, level=0.30, cache=None,
                         min_remaining_min=2.0, max_entry=0.90, prior_high=None):
    """After touching <=0.05, wait for the price to recover to `level`, then buy.

    Excludes settlement jumps: the confirmation must happen with at least
    `min_remaining_min` minutes left in the in-game window and below
    `max_entry` (a 0.05 -> 0.995 tick is a settled market, not a trade).
    """
    rows = []
    cache = cache if cache is not None else {}
    for m in matches:
        for gs, sides in m["games"].items():
            number = int(gs)
            winner = m["game_winners"].get(gs)
            if winner is None:
                continue
            for side, stat in sides.items():
                start, end = stat.get("window_start"), stat.get("window_end")
                if not start or not end:
                    continue
                key = (m["slug"], number, int(side))
                if key not in cache:
                    cache[key] = load_game_side(m["slug"], number, int(side))
                points = [(t, p) for t, p in cache[key] if start <= t <= end]
                touched = False
                entry_index = None
                touch_price = None
                for i, (t, p) in enumerate(points):
                    if not touched and p <= 0.05:
                        if prior_high is not None:
                            prior = [q for _, q in points[:i]]
                            if not prior or max(prior) < prior_high:
                                break  # permanently cheap, not a collapse
                        touched = True
                        touch_price = float(p)
                    if (touched and p >= level and p <= max_entry
                            and (end - t) / 60 >= min_remaining_min):
                        entry_index = i
                        break
                if entry_index is None:
                    continue
                after = [p for _, p in points[entry_index:]]
                rows.append({
                    "slug": m["slug"],
                    "game": number,
                    "side": int(side),
                    "level": level,
                    "touch": touch_price,
                    "entry": float(points[entry_index][1]),
                    "remaining_min": round((end - points[entry_index][0]) / 60, 2),
                    "won": winner == int(side),
                    "max_after": max(after) if after else float(points[entry_index][1]),
                })
    return rows


def wilson_interval(wins, n, z=1.96):
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return None, None
    p = wins / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(max(0.0, center - half), 3), round(min(1.0, center + half), 3)


def bootstrap_ev_interval(rows, iterations=5000, seed=7):
    """Non-parametric bootstrap CI for EV at the observed entry prices."""
    if not rows:
        return None, None
    rng = random.Random(seed)
    n = len(rows)
    evs = []
    for _ in range(iterations):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        evs.append(sum(((1 / r["entry"] - 1) if r["won"] else -1) for r in sample) / n)
    evs.sort()
    low = evs[int(iterations * 0.025)]
    high = evs[int(iterations * 0.975) - 1]
    return round(low, 3), round(high, 3)


def pct(value):
    return "—" if value is None else f"{value * 100:.0f}%"


def money(value):
    return "—" if value is None else f"{value:+.2f}"


def status_badge(kind, text):
    return f"<span class='badge {kind}'>{html.escape(text)}</span>"


def sample_table(rows, columns):
    head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    body = []
    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col, "")
            if isinstance(value, bool):
                value = "赢" if value else "输"
            cells.append(f"<td>{html.escape(str(value))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def build_html(data):
    h1 = data["hypotheses"][0]
    h2 = data["hypotheses"][1]
    h3 = data["hypotheses"][2]
    h4 = data["hypotheses"][3]

    def metric_cards(metrics_obj):
        cards = [
            ("样本", metrics_obj["n"]),
            ("命中", pct(metrics_obj["hit_rate"])),
            ("均价", metrics_obj["avg_entry"] if metrics_obj["avg_entry"] is not None else "—"),
            ("EV", money(metrics_obj["ev"])),
        ]
        return "".join(
            f"<div class='metric'><div class='k'>{k}</div><div class='v'>{v}</div></div>"
            for k, v in cards
        )

    h4_thr_rows = "".join(
        f"<tr><td>≤{item['threshold']:.2f}</td><td>{item['n']}</td>"
        f"<td>{pct(item['hit_rate'])}</td><td>{money(item['ev_at_threshold'])}</td>"
        f"<td>{pct(item['reach20'])}</td></tr>"
        for item in h4["thresholds"]
    )
    h4_bounce_rows = "".join(
        f"<tr><td>{item['take_profit']:.2f}</td><td>{pct(item['sold_rate'])}</td>"
        f"<td>{money(item['ev'])}</td></tr>"
        for item in h4["bounce_005"]
    )
    h4_filter_rows = "".join(
        f"<tr><td>{html.escape(item['name'])}</td><td>{item['n']}</td>"
        f"<td>{pct(item['hit_rate'])}</td><td>{money(item['ev_at_threshold'])}</td></tr>"
        for item in h4["filters"]
    )
    h4_conf_rows = "".join(
        f"<tr><td>重新站上 {item['level']:.2f}</td><td>{item['n']}</td>"
        f"<td>{pct(item['hit_rate'])}</td><td>{item['avg_entry']}</td>"
        f"<td>{item['median_entry']}</td><td>{money(item['ev'])}</td>"
        f"<td>{money(item['ev_ci'][0])} ~ {money(item['ev_ci'][1])}</td>"
        f"<td>{pct(item['reach70'])}</td></tr>"
        for item in h4.get("confirmations", [])
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LoL 形态假设库 · 观察中</title>
<style>
:root{{--blue:#0071e3;--bg:#f5f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6e6e73;--bd:#e5e5ea;--green:#1d8f4e;--amber:#b25000;--red:#c7362c}}
*{{box-sizing:border-box}}html,body{{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif;line-height:1.6}}
.top{{background:#1d1d1f;color:#fff;padding:10px 24px;font-size:12px;display:flex;justify-content:space-between}}
.wrap{{max-width:1040px;margin:0 auto;padding:28px 20px 60px}}
.hero{{padding:8px 0 18px}}.eyebrow{{font-size:11px;letter-spacing:.12em;color:var(--blue);font-weight:700;text-transform:uppercase}}
h1{{font-size:30px;line-height:1.2;margin:6px 0 10px;letter-spacing:-.02em}}
.lede{{color:var(--mu);font-size:14px;max-width:820px;margin:0}}
.warn{{background:#fff8e6;border:1px solid #f0d9a8;color:#6b4b00;padding:10px 14px;border-radius:10px;font-size:12.5px;margin:16px 0 22px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:18px 20px;margin:14px 0}}
.card h2{{font-size:18px;margin:0 0 4px}}.sub{{color:var(--mu);font-size:12px;margin:0 0 12px}}
.quote{{border-left:3px solid var(--blue);background:#f7fbff;padding:8px 12px;color:#334;font-size:13px;margin:10px 0}}
.badge{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:8px;margin-left:6px;vertical-align:middle}}
.badge.red{{background:#fde8e6;color:var(--red)}}.badge.amber{{background:#fff3e0;color:var(--amber)}}.badge.green{{background:#e8f6ee;color:var(--green)}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}}
.metric{{background:#fafafc;border:1px solid #eee;border-radius:10px;padding:8px 10px}}
.metric .k{{font-size:10.5px;color:var(--mu)}}.metric .v{{font-size:18px;font-weight:650;margin-top:2px}}
.verdict{{font-size:13px;margin:10px 0 0}}.verdict b{{font-weight:650}}
.refine{{background:#f7f7fa;border:1px dashed #d8d8e0;border-radius:10px;padding:10px 12px;margin-top:10px;font-size:12.5px}}
table{{width:100%;border-collapse:collapse;font-size:11.5px;margin-top:10px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top}}
th{{color:var(--mu);font-weight:600;background:#fafafc}}
.checklist{{font-size:13px}}.checklist li{{margin:6px 0}}
.foot{{color:var(--mu);font-size:11.5px;margin-top:24px;border-top:1px solid var(--bd);padding-top:12px}}
@media(max-width:640px){{.metrics{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:24px}}
table{{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}}th,td{{white-space:nowrap}}}}
</style></head><body>
<div class="top"><b>Polymarket 电竞情报库 · 形态假设</b><span>生成 {html.escape(data['generated_at'])} · 只读分析</span></div>
<div class="wrap">
<div class="hero"><div class="eyebrow">Pattern Hypotheses · LoL</div>
<h1>四条盘感，先当成假设，不当成结论</h1>
<p class="lede">来源：用户 2026-10-18 复盘观察。样本：LoL 一级赛事 231 场（真实 60s 赔率，仅比赛内窗口）。以下是第一版定义与粗基线，目的是把“感觉”变成可被证伪的规则。</p></div>
<div class="warn"><b>纪律：</b>本页全部为 L0 观察层，不构成交易建议，不自动下单。EV 用“赢则 1/入场价−1、输则 −1”计算，未计手续费、点差、滑点；价格是 CLOB 中间价，不是可成交价。样本小，禁止据此放大仓位。</div>

<div class="card">
<h2>先看总览</h2>
<div class="metrics">
<div class="metric"><div class="k">H1 样本</div><div class="v">{h1['metrics']['n']}</div></div>
<div class="metric"><div class="k">H2 样本</div><div class="v">{h2['metrics']['n']}</div></div>
<div class="metric"><div class="k">H3 样本（≤0.20）</div><div class="v">{h3['metrics']['n']}</div></div>
<div class="metric"><div class="k">H4 极值触点</div><div class="v">{h4['metrics']['n']}</div></div>
</div>
<p class="verdict">一句话：<b>H1 目前不支持直接抄底整场；H2 支持“系列赛胜率高”，但不支持“必 3:0”；H3 只在 1-1 且极深水时有正期望迹象，0-2 落后时是陷阱；H4 盲买 0.01–0.05 负期望，但“先砸到 0.05 以下、再站回 0.20–0.30”的确认反转样本内有正期望，只做卫星仓、待样本外验证。</b></p>
</div>

<div class="card">
<h2>H1 · 强打弱 BO3：输掉 G2 后买强队 {status_badge('red', '初步不支持')}</h2>
<p class="sub">强队 = 赛前整场赔率 ≥60c；G1 强队赢、G2 强队输；在 G2 结束后 30 秒买强队整场赢。</p>
<div class="quote">“强打弱 BO3：强队第一局赢了、第二局输了，在第二局输之后买入，类似 U 型反转。”</div>
<div class="metrics">{metric_cards(h1['metrics'])}</div>
<p class="verdict"><b>结果：</b>{h1['verdict']}</p>
<div class="refine"><b>最有价值的反例：</b>按“G2 后整场价是否守住 50c”分层——守住 50c 的 {h1['refine']['held']['n']} 场赢了 {h1['refine']['held']['wins']} 场；跌破 50c 的 {h1['refine']['collapsed']['n']} 场只赢 {h1['refine']['collapsed']['wins']} 场。也就是说，<b>深水 U 型更像“市场在告诉你强队要输”，不是免费的反转期权</b>。这条分层只有 {h1['metrics']['n']} 场，必须在样本外复核。</div>
<div class="refine"><b>稳健性：</b>剔除 1 场入场价 0.995（已无交易空间）后，12 场 7 胜、均价 0.659、EV −0.13，结论不变。</div>
<div class="refine"><b>赛前强度分档：</b>{h1['pre_bucket_text']}</div>
{sample_table(h1['samples'], ['slug','fav','entry','low_after_g2','won','series'])}
</div>

<div class="card">
<h2>H2 · 强队 G1 碾压后的后续小局 {status_badge('amber', '部分支持')}</h2>
<p class="sub">强队 = 赛前 ≥60c；G1 强队赢且全程未跌破 70c、收在 99c+；观察系列赛胜率、干净横扫率、G2 胜率。BO3 的“3:0”按 2:0 计。</p>
<div class="quote">“强队第一局完全碾压，很大概率 3:0 带走，第二、第三局就可以大胆下注。”</div>
<p class="sub">下表指标口径：<b>G2 单局</b>买入（均价 {h2['g2_metrics']['avg_entry']}），不是系列赛胜率。</p>
<div class="metrics">{metric_cards(h2['metrics'])}</div>
<p class="verdict"><b>结果：</b>{h2['verdict']}</p>
<div class="refine"><b>关键区分：</b>“系列赛赢”与“干净横扫”不是一回事。当前样本里系列赛胜率 {pct(h2['series_win_rate'])}，但 2-0/3-0 只有 {pct(h2['clean_sweep_rate'])}；G2 单局胜率 {pct(h2['g2_win_rate'])}（{h2['g2_metrics']['wins']}/{h2['g2_metrics']['n']}），按均价 {h2['g2_metrics']['avg_entry']} 计算的 G2 EV 为 {money(h2['g2_metrics']['ev'])}。所以“第二局大胆下注”比“第三局/横扫”更有数据支撑。</div>
{sample_table(h2['samples'], ['slug','format','fav','score','series_win','clean_sweep','g2_entry','g2_win'])}
</div>

<div class="card">
<h2>H3 · BO5 的 G3/G4 弱队深水抄底 {status_badge('amber', '有条件支持')}</h2>
<p class="sub">弱队 = 赛前 &lt;50c；在 G3/G4 的局内窗口，弱队整场赔率首次触及阈值即买入；按 G3 前的比分拆分。</p>
<div class="quote">“BO5 焦灼局，在 G3、G4 关注弱队那一方去抄底买，收益率会更高。”</div>
<div class="metrics">
<div class="metric"><div class="k">样本（≤0.20）</div><div class="v">{h3['metrics']['n']}</div></div>
<div class="metric"><div class="k">命中</div><div class="v">{pct(h3['metrics']['hit_rate'])}</div></div>
<div class="metric"><div class="k">均价（首次触价）</div><div class="v">{h3['metrics']['avg_entry']}</div></div>
<div class="metric"><div class="k">EV@0.20</div><div class="v">{money(h3['metrics']['ev_at_threshold'])}</div></div>
</div>
<p class="sub">注意：按实际首次触价（均价 {h3['metrics']['avg_entry']}）计算 EV 为 {money(h3['metrics']['ev'])}，偏乐观；按保守的 0.20 限价成交则 EV 为 {money(h3['metrics']['ev_at_threshold'])}。</p>
<p class="verdict"><b>结果：</b>{h3['verdict']}</p>
<div class="refine"><b>最重要的分层：</b>G3 前 1-1 时，≤0.10 阈值 {h3['split']['1-1_010']['n']} 场、命中 {pct(h3['split']['1-1_010']['hit_rate'])}、按 0.10 成交 EV {money(h3['split']['1-1_010']['ev_at_threshold'])}；0-2 时 {h3['split']['0-2_010']['n']} 场、命中 {pct(h3['split']['0-2_010']['hit_rate'])}、EV {money(h3['split']['0-2_010']['ev_at_threshold'])}。结论：<b>只在 1-1 的深水买，0-2 的深水不买</b>。这是彩票仓逻辑，必须小注、必须等价格，不能用市价追。</div>
{sample_table(h3['samples'][:12], ['slug','dog','score_before','threshold','entry','game','won','result'])}
</div>

<div class="card">
<h2>H4 · 整场基本盘 + 小局极值卫星仓 {status_badge('amber', '盲买不支持 · 确认后待验证')}</h2>
<p class="sub">基本盘 = 整场/系列赛形态；卫星仓 = 小局价格跌到 0.01–0.05 后的极值反转，只用小注博高赔。先记录原则，再验证“哪些极值是假死”。</p>
<div class="quote">“有时候关注整场比赛形态的话，交易会比较容易一点；小局中可以关注极值反转，0.01 到 0.05 之间突然反转，偶尔抓到一两个机会就很好。先有基本盘，再有一些额外的暴利收益。”</div>
<p class="sub">盲买基线：只要某侧小局价首次触及阈值就买入，持有到该小局结算；<b>不筛比赛上下文</b>。EV 按阈值成交计算。</p>
<table><thead><tr><th>阈值</th><th>触及小局</th><th>该侧最终赢</th><th>EV@阈值</th><th>之后到过 0.20</th></tr></thead><tbody>{h4_thr_rows}</tbody></table>
<p class="verdict"><b>结果：</b>{h4['verdict']}</p>
<div class="refine"><b>反弹卖出也不救：</b>以 0.05 买入为例，反弹到 0.10 的只有 {pct(h4['bounce_005'][0]['sold_rate'])}，按 0.10 卖出 EV {money(h4['bounce_005'][0]['ev'])}；反弹到 0.20 的只有 {pct(h4['bounce_005'][1]['sold_rate'])}，EV {money(h4['bounce_005'][1]['ev'])}。极值本身的赔率很高，但触发频率太低。</div>
<table><thead><tr><th>0.05 买入后的卖点</th><th>触发比例</th><th>EV</th></tr></thead><tbody>{h4_bounce_rows}</tbody></table>
<p class="sub" style="margin-top:10px">再加两个“看起来像假死”的过滤器，仍然没有救回来：</p>
<table><thead><tr><th>过滤器（0.05 买入）</th><th>n</th><th>命中</th><th>EV@0.05</th></tr></thead><tbody>{h4_filter_rows}</tbody></table>
<p class="sub" style="margin-top:14px">换个入场方式：<b>不在极值接飞刀</b>，而是等价格从 ≤0.05 重新站上确认位再买（同一小局、同一侧）。已剔除结算跳价（要求确认时距窗口结束 ≥2 分钟且成交价 ≤0.90）。价格是确认后首个可观测到的中间价，跳空时可能高于确认位。</p>
<table><thead><tr><th>确认位</th><th>n</th><th>命中</th><th>均价</th><th>中位价</th><th>EV（实际价）</th><th>EV 95% 区间</th><th>之后到过 0.70</th></tr></thead><tbody>{h4_conf_rows}</tbody></table>
<div class="refine"><b>这是本页目前最强的一条信号：</b>极值本身负期望，但“先砸到 0.05 以下、再重新站上 0.30”的样本 {h4['confirmations'][1]['n']} 个、命中 {pct(h4['confirmations'][1]['hit_rate'])}、按实际入场价 EV {money(h4['confirmations'][1]['ev'])}；站上 0.20 更早、赔率更好（EV {money(h4['confirmations'][0]['ev'])}），但只有 {h4['confirmations'][0]['n']} 个样本。站上 0.50 才买，赔率已经被吃掉，EV 只剩 {money(h4['confirmations'][3]['ev'])}。方向是“等确认”，不是“抄最低”。</div>
<div class="refine"><b>代价是绝大多数时间不出手：</b>473 个触及 ≤0.05 的小局里，只有 {h4['confirmations'][0]['n']} 个（{pct(h4['confirmations'][0]['n']/h4['metrics']['n'])}）后来重新站上 0.20。也就是说，极值策略的常态是“看着它归零、什么都不做”。这恰好符合卫星仓的定位：低频、可选、错过无所谓。</div>
<div class="refine"><b>但必须先泼冷水：</b>这些是<b>条件样本</b>——已经筛掉了“砸下去就再没回来”的绝大多数小局；样本只有 {h4['confirmations'][1]['n']} 个，未扣点差/滑点。更关键的是 <b>EV 的 95% 自助区间跨零</b>（0.20 档 {money(h4['confirmations'][0]['ev_ci'][0])} ~ {money(h4['confirmations'][0]['ev_ci'][1])}，0.30 档 {money(h4['confirmations'][1]['ev_ci'][0])} ~ {money(h4['confirmations'][1]['ev_ci'][1])}），命中率的 Wilson 区间也只有 {pct(h4['confirmations'][1]['hit_ci'][0])}–{pct(h4['confirmations'][1]['hit_ci'][1])}。它证明“确认反转值得观察”，不证明可以实盘放大。任何使用都必须同时满足：基本盘独立、卫星仓 1–2%、不补仓、有本场上下文（经济/资源/阵容/团战）而非只看价格。</div>
<div class="refine"><b>稳健性检查：</b>要求“触价前曾站上 0.20”（排除一直很便宜的弱侧，只保留真正的崩塌后反转），0.30 确认档剩 {h4['collapse_030']['n']} 个样本、命中 {pct(h4['collapse_030']['hit_rate'])}、EV {money(h4['collapse_030']['ev'])}（95% 区间 {money(h4['collapse_030']['ev_ci'][0])} ~ {money(h4['collapse_030']['ev_ci'][1])}）。方向一致，但样本更小，同样不能算验证通过。</div>
<div class="refine"><b>结论：</b>0.01–0.05 这个价格本身不是 edge。真正可能存在的 edge 是“判断哪一次极值是假死”——经济差、资源、阵容、团战失误、选手状态。没有这些上下文，盲目扫极值是负期望；有了上下文，也只能当卫星仓；而且入场点应是反转确认位（0.20–0.30），不是最低点。</div>
<div class="refine"><b>资金分层（必须）：</b>① 基本盘与卫星仓分账，卫星预算只来自已实现利润，单日上限固定；② 极值仓单笔 ≤ 总资金 1–2%，每场最多 1 笔、每日最多 2 笔；③ 不补仓、不摊平，0.01 不是“免费期权”；④ 双条件才买：反转确认（站上 0.20–0.30）+ 本场上下文，缺一不买；⑤ 确认后又跌回 0.15 以下视为失败，认亏离场，不二次摊平。</div>
</div>

<div class="card">
<h2>高频形态 ≠ 有边际</h2>
<p class="sub">用户观察“有些形态频繁反复出现”。当前库的标签出现次数（双方合计，不是独立场次）：</p>
<p style="font-size:13px;margin:6px 0"><b>逐小局：</b>B4 直线阴跌 462 · A2 中位 U 型反转 162 · A3 折价修复 157 · C2 五五开开局碾压 120 · 热门全程压制 111 · B1 尾盘崩塌 63 · A1 V 型极值反转 41 · A5 W 型双底 36。</p>
<p style="font-size:13px;margin:6px 0"><b>整局：</b>B4 直线阴跌 175 · A3 折价修复 95 · 热门全程压制 75 · A2 中位 U 型反转 68 · A4 下狗整场反转 56 · C2 五五开开局碾压 37 · B2 死亡螺旋 26。</p>
<p class="verdict">出现频率高，只说明它常见；能不能交易，取决于“在这个形态下、以什么价格入场”的 EV。后续统计必须绑定入场价与成本。</p>
</div>

<div class="card">
<h2>下一步怎么验证</h2>
<ul class="checklist">
<li><b>样本外：</b>用 2026-10-15 之后的新比赛做前瞻验证，不回头改阈值；H1/H2/H3/H4 各自至少 30 场独立样本。</li>
<li><b>成交价：</b>把中间价换成 ask/成交 tape，重算 EV；当前结果对 1-2c 的点差非常敏感。</li>
<li><b>分层：</b>按联赛（LCK/LPL/LEC/LCP）、BO3/BO5、赛前热门程度（60-70 / 70-80 / 80+）分别统计，禁止混算。</li>
<li><b>反例库：</b>每次验证必须同时记录输的场次和“价格没触及”的场次，避免只看命中的幸存者偏差。</li>
<li><b>升级门槛：</b>H1 当前负 EV，不升级；H2 只升级“G2 单局”观察；H3 只升级“1-1 + ≤0.10 彩票仓”观察；H4 只把“≤0.05 后重新站上 0.30”列为观察候选，盲买极值不升级。任何一条都不到 L1。</li>
</ul>
</div>

<div class="foot">数据：reports/pattern_shapes_lol_tier1_2026-07_2026-10.json（231 场，60s）· 明细：docs/data/intel/pattern_hypotheses.json · 规则：knowledge/PATTERN_HYPOTHESES.md<br>
生成器：tools/build_pattern_hypotheses.py · 本页只读，不执行交易。</div>
</div></body></html>"""


def main():
    data = json.loads(SRC.read_text(encoding="utf-8"))
    matches = data["matches"]

    h1 = h1_trades(matches)
    h1_m = metrics(h1)
    pre_buckets = [
        {"threshold": thr, **metrics([t for t in h1 if t["pre"] >= thr])}
        for thr in (0.60, 0.65, 0.70, 0.75)
    ]
    pre_bucket_text = " · ".join(
        f"≥{b['threshold']:.2f}: n={b['n']} / 命中 {pct(b['hit_rate'])} / EV {money(b['ev'])}"
        for b in pre_buckets
    ) + "。≥0.75 只有 4 场，不能当结论。"
    held = [t for t in h1 if t["held_050"]]
    collapsed = [t for t in h1 if not t["held_050"]]
    h1_verdict = (
        f"命中 {h1_m['wins']}/{h1_m['n']}（{pct(h1_m['hit_rate'])}），均价 {h1_m['avg_entry']}，"
        f"EV {money(h1_m['ev'])}——方向不算错，但当前定价没有给出正期望；"
        "“输 G2 后无脑买强队”暂时不成立。"
    )

    rows2 = h2_rows(matches)
    h2_m = metrics([{"entry": r["g2_entry"], "won": r["g2_win"]} for r in rows2
                    if r["g2_entry"] is not None and r["g2_win"] is not None])
    h2_series = sum(1 for r in rows2 if r["series_win"]) / len(rows2) if rows2 else None
    h2_sweep = sum(1 for r in rows2 if r["clean_sweep"]) / len(rows2) if rows2 else None
    h2_g2 = sum(1 for r in rows2 if r["g2_win"]) / len(rows2) if rows2 else None
    h2_verdict = (
        f"系列赛 {sum(1 for r in rows2 if r['series_win'])}/{len(rows2)} 赢，"
        f"但干净横扫只有 {sum(1 for r in rows2 if r['clean_sweep'])}/{len(rows2)}；"
        f"G2 单局 {sum(1 for r in rows2 if r['g2_win'])}/{len(rows2)} 赢。"
        "“系列赛大概率赢”成立，“必 3:0”不成立。"
    )

    rows3 = h3_rows(matches)
    rows3_020 = [r for r in rows3 if r["threshold"] == 0.20]
    h3_m = metrics(rows3_020, 0.20)
    h3_verdict = (
        f"阈值 ≤0.20 时共 {h3_m['n']} 场，命中 {pct(h3_m['hit_rate'])}，"
        f"按 0.20 成交 EV {money(h3_m['ev_at_threshold'])}；"
        "整体不是稳定正期望，必须按比分和更深阈值拆开看。"
    )

    h4_cache = {}
    h4_thresholds = []
    h4_primary = []
    for thr in (0.01, 0.03, 0.05, 0.10):
        subset = h4_extreme_rows(matches, threshold=thr, cache=h4_cache)
        item = metrics(subset, thr)
        item["threshold"] = thr
        item["ev_observed"] = item["ev"]
        item["ev"] = item["ev_at_threshold"]
        item["reach20"] = round(
            sum(1 for r in subset if r["max_after"] >= 0.20) / len(subset), 3
        ) if subset else None
        h4_thresholds.append(item)
        if thr == 0.05:
            h4_primary = subset
    h4_m = next(item for item in h4_thresholds if item["threshold"] == 0.05)
    h4_bounces = [h4_bounce(h4_primary, 0.05, tp) for tp in (0.10, 0.20, 0.30)]
    h4_filters = []
    for name, kwargs in (
        ("触价前曾到过 0.30", {"prior_high": 0.30}),
        ("5 分钟内从 0.20 砸到 0.05", {"fast_minutes": 5}),
    ):
        subset = h4_extreme_rows(matches, threshold=0.05, cache=h4_cache, **kwargs)
        item = metrics(subset, 0.05)
        item["name"] = name
        h4_filters.append(item)
    h4_confirmations = []
    for level in (0.20, 0.30, 0.40, 0.50):
        subset = h4_confirmation_rows(matches, level=level, cache=h4_cache)
        item = metrics(subset)
        item["level"] = level
        item["ev_at_level"] = metrics(subset, level)["ev_at_threshold"]
        item["reach70"] = round(
            sum(1 for r in subset if r["max_after"] >= 0.70) / len(subset), 3
        ) if subset else None
        item["hit_ci"] = wilson_interval(item["wins"], item["n"])
        item["ev_ci"] = bootstrap_ev_interval(subset)
        h4_confirmations.append(item)
    collapse_subset = h4_confirmation_rows(matches, level=0.30, cache=h4_cache, prior_high=0.20)
    h4_collapse = metrics(collapse_subset)
    h4_collapse["level"] = 0.30
    h4_collapse["prior_high"] = 0.20
    h4_collapse["hit_ci"] = wilson_interval(h4_collapse["wins"], h4_collapse["n"])
    h4_collapse["ev_ci"] = bootstrap_ev_interval(collapse_subset)
    h4_confirm_030 = next(i for i in h4_confirmations if i["level"] == 0.30)
    h4_verdict = (
        f"0.05 阈值共 {h4_m['n']} 个小局触及，命中 {pct(h4_m['hit_rate'])}，"
        f"按 0.05 成交 EV {money(h4_m['ev_at_threshold'])}；"
        f"0.01 阈值 EV {money(next(i for i in h4_thresholds if i['threshold']==0.01)['ev_at_threshold'])}（接近打平但极难反弹）。"
        "盲买极值是负期望，极值本身不是 edge；反弹卖出更差（0.05 买入、0.10 卖出 EV "
        f"{money(h4_bounces[0]['ev'])}），因为它砍掉了尾部。"
        f"但先跌到 ≤0.05、再重新站上 0.30 才买，{h4_confirm_030['n']} 个样本命中 "
        f"{pct(h4_confirm_030['hit_rate'])}、EV {money(h4_confirm_030['ev'])}"
        f"（95% 区间 {money(h4_confirm_030['ev_ci'][0])} ~ {money(h4_confirm_030['ev_ci'][1])}）——"
        "方向是“等确认而不是抄最低”，但样本太小，区间跨零，还不能算验证通过。"
    )

    def split_rows(score, threshold):
        subset = [r for r in rows3 if r["score_before"] == score and r["threshold"] == threshold]
        return metrics(subset, threshold)

    h3_split = {
        f"{score}_{str(int(thr*100)).zfill(2)}": split_rows(score, thr)
        for score in ("1-1", "0-2") for thr in (0.10, 0.15)
    } | {
        f"{score}_{str(int(thr*100)).zfill(3)}": split_rows(score, thr)
        for score in ("1-1", "0-2") for thr in (0.20,)
    }
    # Normalize keys used by the template.
    h3_split["1-1_010"] = split_rows("1-1", 0.10)
    h3_split["0-2_010"] = split_rows("0-2", 0.10)

    payload = {
        "generated_at": dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "source": str(SRC.relative_to(ROOT)),
        "scope": "LoL tier-1, 231 matches, 60s CLOB midpoint, in-game window",
        "hypotheses": [
            {
                "id": "H1",
                "title": "强打弱 BO3：输 G2 后买强队整场",
                "status": "initial_not_supported",
                "claim": "强队 G1 赢、G2 输，G2 结束后买强队整场赢，类似 U 型反转。",
                "definition": "BO3；赛前整场 ≥0.60；G1 强队赢、G2 强队输；G2 窗口结束后 30s 入场；标的=强队整场 Winner。",
                "metrics": h1_m,
                "pre_buckets": pre_buckets,
                "pre_bucket_text": pre_bucket_text,
                "refine": {
                    "held": metrics(held),
                    "collapsed": metrics(collapsed),
                    "note": "G2 后整场价是否守住 0.50 的分层，样本极小，仅作假设。",
                },
                "verdict": h1_verdict,
                "samples": h1,
                "caveats": ["中间价非成交价", "样本 13 场（已剔除未结算）", "未计点差/滑点", "深水段可能存在流动性缺口"],
            },
            {
                "id": "H2",
                "title": "强队 G1 碾压后的后续小局",
                "status": "partially_supported",
                "claim": "强队 G1 完全碾压，很大概率 3:0 带走，G2/G3 可以大胆下注。",
                "definition": "BO3/BO5；赛前 ≥0.60；G1 强队赢、G1 最低价 ≥0.70、收 ≥0.99；统计系列赛胜、干净横扫、G2 胜率。",
                "metrics": h2_m,
                "series_win_rate": round(h2_series, 3) if h2_series is not None else None,
                "clean_sweep_rate": round(h2_sweep, 3) if h2_sweep is not None else None,
                "g2_win_rate": round(h2_g2, 3) if h2_g2 is not None else None,
                "g2_metrics": h2_m,
                "verdict": h2_verdict,
                "samples": rows2,
                "caveats": ["G3 独立市场缺失较多，无法单独验证 G3", "样本 13 场", "“碾压”阈值 0.70 是人为设定"],
            },
            {
                "id": "H3",
                "title": "BO5 的 G3/G4 弱队深水抄底",
                "status": "conditional",
                "claim": "BO5 焦灼局，在 G3/G4 关注弱队抄底，收益率更高。",
                "definition": "BO5；赛前弱队 <0.50；G3/G4 局内窗口首次触及阈值即入场；标的=弱队整场 Winner；按 G3 前比分拆分。",
                "metrics": h3_m,
                "split": h3_split,
                "verdict": h3_verdict,
                "samples": [r for r in rows3 if r["threshold"] == 0.20],
                "caveats": ["首次触价按阈值成交是保守假设，但真实排队可能不成交", "样本 50 场（≤0.20）", "1-1 深水子样本仅 17 场", "未计手续费/点差"],
            },
            {
                "id": "H4",
                "title": "整场基本盘 + 小局极值卫星仓",
                "status": "principle_recorded_blind_entry_negative",
                "claim": "整场形态做基本盘；小局价格跌到 0.01–0.05 后若突然反转，偶尔抓到一两个就有很好的额外收益。",
                "definition": "小局内某侧价格首次触及阈值（0.01/0.03/0.05/0.10）即买入，持有到该小局结算；不筛比赛上下文。另测反弹到 0.10/0.20/0.30 卖出的变体。",
                "metrics": h4_m,
                "thresholds": h4_thresholds,
                "bounce_005": h4_bounces,
                "filters": h4_filters,
                "confirmations": h4_confirmations,
                "collapse_030": h4_collapse,
                "verdict": h4_verdict,
                "samples": h4_primary[:20],
                "rules": [
                    "基本盘与卫星仓分账；卫星预算只来自已实现利润，单日上限固定。",
                    "极值仓单笔 ≤ 总资金 1–2%，每场最多 1 笔、每日最多 2 笔。",
                    "不补仓、不摊平；0.01 不是免费期权。",
                    "双条件才买：反转确认（重新站上 0.20–0.30）+ 本场上下文（经济/资源/阵容/团战失误），缺一不买。",
                    "不做“接飞刀”：不在 0.01–0.05 挂低价等成交，不在下跌途中摊平。",
                    "确认后又跌回 0.15 以下视为信号失败，卫星仓认亏，不二次摊平。",
                    "确认后再买也不是稳定盈利：0.30 档样本仅 25 个、95% 区间跨零，必须样本外复核。",
                ],
                "caveats": ["未筛比赛上下文，正是为了证明“价格本身不是 edge”", "中间价非成交价", "未计点差/滑点", "样本覆盖 2026-07~10 一级 LoL"],
            },
        ],
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(build_html(payload), encoding="utf-8")
    print(f"hypotheses -> {OUT_JSON.relative_to(ROOT)}")
    print(f"page -> {OUT_HTML.relative_to(ROOT)}")
    for h in payload["hypotheses"]:
        print(h["id"], h["metrics"]["n"], h["metrics"]["hit_rate"], h["metrics"]["ev"])


if __name__ == "__main__":
    main()
