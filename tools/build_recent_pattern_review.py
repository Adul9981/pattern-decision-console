#!/usr/bin/env python3
"""Recent-match pattern review page: charts + per-market shape records.

Purpose (2026-10-19): after a match ends, the intel HTML already covers danmaku
and results, but the price-shape record lives only inside JSON.  This page makes
"昨天结束的几场" visible in one place: every resolved market of each match,
the two sides' price paths, the labels, and the explicit data gaps.

Usage:
  python3 tools/build_recent_pattern_review.py --since 2026-10-17 \
      --section lol=reports/pattern_shapes_lol_tier1_2026-07_2026-10.json \
      --section cs2=reports/pattern_shapes_cs2_tier1_2026-07_2026-10.json \
      --section dota2=/tmp/dota_shapes.json \
      --out reports/pattern_review_2026-10-18.html
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_pattern_charts import chart_block, compute_windows, market_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GAME_CN = {"lol": "LoL", "cs2": "CS2", "dota2": "Dota2"}
GAME_CHART = {
    "lol": "pattern_charts_lol_tier1_2026-07_2026-10.html",
    "cs2": "pattern_charts_cs2_tier1_2026-07_2026-10.html",
}

# 人工观察（只写这场实际发生了什么，不做预测）。
NOTES = {
    "lol-mvk-fur-2026-10-17": [
        "整局是教科书式 A4 下狗整场反转：MVK 36.5c 起步，低点只到 35.5c（几乎没深跌），随后单边走到 100c。",
        "G3 是 FURIA 唯一拿下的局；整局价格在 G3 期间从 ~65c 掉到 ~45c，G4 再翻回 100c——BO5 中段回撤后重新定价。",
    ],
    "lol-kc-mvk-2026-10-18": [
        "三局全是 A3 折价修复 + 热门全程压制：KC 每局起点 76-82c，低点从未跌破 74.5c，没有任何深水窗口。",
        "整局 88.5c 起步、低点 87.5c，属于'没有信息量'的碾压盘；这类盘口不适合追高，只适合赛前定价。",
        "G4 无独立市场、价格停在 50c，已按无成交小局排除，不计入形态统计。",
    ],
    "cs2-furia-bb3-2026-10-18": [
        "整局 FURIA 73.5c → 20.5c → 100c，标准 A2 中位 U 型：BO5 中段被追平后重新拉开。",
        "G2 的 BetBoom 9.5c → 100c 是全场最深水（A1 极值反转）；G3 又是 BB 28.5c → 99.5c 的 A2。",
        "同一场里连续出现两次中深水反转，说明 BO5 的局间价格惯性弱于'整场'定价。",
    ],
    "cs2-aur1-fal2-2026-10-18": [
        "Aurora 整局 42.5c → 9.5c → 100c：A1 V 型极值反转 + A4 下狗整场反转叠加。",
        "G1 Falcons 赢（C2 五五开开局碾压），G2 Aurora 从 19.5c 翻回 100c——决定系列赛的一局。",
        "这是'先输一图再让一追二'在整局盘上的典型定价：G1 后整局并没有给出足够深的折价。",
    ],
    "dota2-tr7-xtreme-2026-10-17": [
        "BLAST Slam 中国区封闭资格赛（非一级正赛）：Xtreme 64c 起步、低点 53c，A3 折价修复到 100c。",
        "G2 Resilience 40c → 99c 反扑，整局价格随之回撤；Dota 的局间波动明显大于 LoL 的碾压盘。",
        "G3 无独立市场/无成交，逐局形态只覆盖 G1-G2，已在缺口里标注。",
    ],
    "dota2-ch1-synaps-2026-10-18": [
        "EPL Masters（非一级正赛）：CyberHero 34c → 17c → 100c，A2 + A4 叠加，是本次 Dota 样本里最标准的 U 型。",
        "G1 CyberHero 30c → 100c（A2），G2 被 Synapse 58c → 98c 扳回（C2），整局在 G2 后重新定价。",
        "G3 无独立市场/无成交；Dota 采集通道可用，但一级正赛样本仍需等赛程。",
    ],
}

GAPS = [
    "CS2 BetBoom 2:1 Falcons（10-18）：Polymarket 没有对应盘口，本页无价格可复盘，只有弹幕情报页结论，禁止补造赔率。",
    "CS2 Aurora 2:1 Falcons：G3 无独立市场/无成交，逐局曲线只覆盖 G1-G2（整局结算已补齐）。",
    "CS2 FURIA 3:2 BetBoom：G5 无独立市场/无成交，逐局曲线覆盖 G1-G4。",
    "LoL KC 3:0 MVK（10-19 北京时间）：G4 无成交、价格停在 50c，已按无效小局剔除。",
    "Dota2 两场：G3 无独立市场/无成交；且均为资格赛/次级赛事，只作采集通道验证。",
]


def fmt(v):
    return "-" if v is None else f"{v:.2f}"


def beijing_start(wins: dict):
    """In-game window start is the closest authoritative marker of actual start.

    Slug dates are UTC-based; Beijing matches routinely fall on the next day
    (e.g. lol-mvk-fur-2026-10-17 actually started 2026-10-18 02:00 CST).
    """
    ts = None
    if wins.get("series"):
        ts = wins["series"][0]
    elif wins.get("games"):
        ts = min(v[0] for v in wins["games"].values())
    if not ts:
        return "", ""
    bj = dt.datetime.fromtimestamp(ts, tz=dt.timezone(dt.timedelta(hours=8)))
    return bj.strftime("%Y-%m-%d"), bj.strftime("%H:%M")


def build_match(m: dict, game: str, max_pts: int):
    slug = m["slug"]
    wins = compute_windows(slug, True)
    bj_date, bj_time = beijing_start(wins)
    blocks = []
    rows = []
    unresolved = {str(x) for x in (m.get("games_unresolved") or [])}

    sb = market_series(slug, 0, "series", wins.get("series"))
    if sb and m.get("series"):
        blocks.append(chart_block(slug, "整局 Winner", sb, m["series"], max_pts))
    for side, info in (m.get("series") or {}).items():
        rows.append(("整局", info.get("team") or side, info))

    for g in sorted({int(x) for x in (m.get("games") or {})}):
        if str(g) in unresolved:
            continue
        gb = market_series(slug, g, "game", wins.get("games", {}).get(g))
        if not gb:
            continue
        blocks.append(chart_block(slug, f"G{g}", gb, m["games"][str(g)], max_pts))
        for side, info in m["games"][str(g)].items():
            rows.append((f"G{g}", info.get("team") or side, info))

    trs = "".join(
        f"<tr><td class='nw'><b>{html.escape(scope)}</b></td>"
        f"<td>{html.escape(str(info.get('team') or '-'))}</td>"
        f"<td class='mono'>{fmt(info.get('pre'))} → {fmt(info.get('low'))} → {fmt(info.get('high'))} → {fmt(info.get('end'))}</td>"
        f"<td class='nw'>{html.escape(str(info.get('low_time') or '-'))}</td>"
        f"<td class='nw'>{info.get('span_min', 0):.0f}m</td>"
        f"<td class='labs'>{html.escape(' / '.join(info.get('labels') or []))}</td></tr>"
        for scope, team, info in rows
    )
    notes = "".join(f"<li>{html.escape(x)}</li>" for x in NOTES.get(slug, []))
    gap = ""
    if unresolved:
        gap = (f"<p class='gap'>缺口：G{', G'.join(sorted(unresolved))} 无独立市场或无成交，"
               "价格停在 50c 附近，未计入形态统计。</p>")
    chart_link = GAME_CHART.get(game)
    link = (f"<a href='{chart_link}#m-{slug}' target='_blank' rel='noopener'>在曲线图中打开</a>"
            if chart_link else "")
    body = f"""
<section class="match">
  <div class="mh">
    <div><h3>{html.escape(m.get('result') or slug)}</h3>
      <p class="meta">{html.escape(m.get('league_raw') or '-')} · {html.escape(m.get('format') or '')} · 比赛内窗口起于北京时间 {bj_date} {bj_time}
      · <code>{html.escape(slug)}</code></p></div>
    <div class="mh-r">{link}</div>
  </div>
  <div class="charts">{''.join(blocks)}</div>
  <div class="tblw"><table class="tbl">
    <thead><tr><th>市场</th><th>一侧</th><th>赛前→低→高→终</th><th>低点时刻</th><th>窗口</th><th>形态标签</th></tr></thead>
    <tbody>{trs}</tbody></table></div>
  {gap}
  <ul class="notes">{notes}</ul>
</section>"""
    return body, bj_date


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", action="append", default=[])
    ap.add_argument("--since", default="")
    ap.add_argument("--out", default="reports/pattern_review_recent.html")
    ap.add_argument("--max-points", type=int, default=160)
    args = ap.parse_args()

    since = args.since or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    groups: dict[str, list] = {}
    meta = {}
    for item in args.section:
        game, path = item.split("=", 1)
        data = json.load(open(os.path.join(ROOT, path), encoding="utf-8"))
        # Slug date is UTC; allow one day of slack, then filter by Beijing start.
        since_minus = (dt.date.fromisoformat(since) - dt.timedelta(days=1)).isoformat()
        picks = [m for m in data.get("matches") or [] if (m.get("date") or "") >= since_minus]
        groups[game] = picks
        meta[game] = data

    sections = []
    counts = []
    for game, picks in groups.items():
        if not picks:
            continue
        rendered = [build_match(m, game, args.max_points) for m in picks]
        rendered = [(body, bj) for body, bj in rendered if bj and bj >= since]
        if not rendered:
            continue
        counts.append((game, len(rendered)))
        cards = "".join(body for body, _ in rendered)
        note = ""
        if game == "dota2":
            note = ("<div class='warn'>Dota2：最近几天 Polymarket 上没有一级正赛，"
                    "下列为 DreamLeague / BLAST Slam 中国区封闭资格赛与 EPL Masters 样本，"
                    "<b>只用于验证采集通道，不计入一级形态库</b>。</div>")
        sections.append(f"<h2 class='gh'>{GAME_CN.get(game, game)} <span>{len(rendered)} 场</span></h2>{note}{cards}")

    out = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>最近结束比赛 · 逐局与整局形态复盘</title>
<style>
:root{{--blue:#0071e3;--bg:#f5f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6e6e73;--bd:#e5e5ea}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
.top{{background:#1d1d1f;color:#fff;padding:13px 20px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}}
.top span{{color:#a1a1a6;font-size:12.5px}}
.wrap{{max-width:1240px;margin:0 auto;padding:24px 16px 70px}}
h1{{font-size:26px;margin:6px 0 6px;font-weight:650}}
.sub{{color:var(--mu);font-size:13.5px;margin:0 0 16px}}
.note{{background:#fff;border-radius:14px;padding:13px 16px;font-size:13px;color:#3a3a3c;margin:0 0 18px}}
.gh{{font-size:19px;margin:26px 0 8px}} .gh span{{color:var(--mu);font-size:13px;font-weight:400;margin-left:6px}}
.warn{{background:#fff7e8;border:1px solid #ffe2b3;color:#8a5a00;border-radius:12px;padding:10px 14px;font-size:12.5px;margin:0 0 10px}}
.match{{background:#fff;border-radius:16px;padding:16px;margin:12px 0}}
.mh{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--bd);padding-bottom:10px;margin-bottom:12px}}
.mh h3{{margin:0 0 4px;font-size:16px;font-weight:650}}
.meta{{margin:0;color:var(--mu);font-size:12px}}
.mh-r a{{color:var(--blue);text-decoration:none;font-size:12.5px}}
.charts{{display:flex;gap:14px;flex-wrap:wrap}}
.mk{{background:#fbfbfd;border:1px solid var(--bd);border-radius:11px;padding:8px 9px}}
.mk-h{{font-size:12px;color:var(--mu);display:flex;align-items:center;gap:6px;margin-bottom:2px}}
.ch{{width:330px;height:118px;display:block}}
.lg{{display:flex;align-items:center;gap:6px;font-size:12px;margin-top:3px;flex-wrap:wrap}}
.lg i{{width:10px;height:3px;border-radius:2px;display:inline-block}}
.lg b{{font-weight:600}} .lb{{color:var(--mu)}} .pn{{color:#3a3a3c}}
.pn2{{color:#a1a1a6;font-size:11.5px}}
.chip{{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:7px;background:#ececf0;color:#6e6e73}}
.chip-on{{background:#e3f0ff;color:var(--blue)}} .cf-high{{background:#e7f6ec;color:#1d8f4e}} .cf-low{{background:#fff2e2;color:#b25000}}
.tblw{{overflow:auto;border:1px solid var(--bd);border-radius:10px;margin-top:14px}}
.tbl{{width:100%;border-collapse:collapse;font-size:12px}}
.tbl th{{background:#fbfbfd;text-align:left;color:var(--mu);font-weight:600;padding:8px;border-bottom:1px solid var(--bd);white-space:nowrap}}
.tbl td{{padding:7px 8px;border-bottom:1px solid #f2f2f7;vertical-align:top}}
.mono{{font-variant-numeric:tabular-nums;white-space:nowrap}}
.nw{{white-space:nowrap}}
.labs{{color:#3a3a3c;min-width:220px}}
.gap{{color:#b25000;background:#fff7e8;border-radius:8px;padding:7px 10px;font-size:12px;margin:10px 0 0}}
.notes{{margin:10px 0 0;padding-left:18px;color:#3a3a3c;font-size:12.5px}}
.notes li{{margin:3px 0}}
.foot{{color:#8e8e93;font-size:11.5px;margin-top:26px;line-height:1.6}}
</style></head><body>
<div class="top"><b>Polymarket 电竞情报库 · 最近比赛形态复盘</b><span>生成 {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}</span></div>
<div class="wrap">
<h1>最近结束比赛：逐局 + 整局形态复盘</h1>
<p class="sub">60 秒级 · 仅比赛内窗口 · 已剔除赛前长平台与赛后结算平台。曲线两条线=双方代币价格，虚线 50c 为领先权分界。</p>
<div class="note"><b>覆盖</b>：{' · '.join(f'{GAME_CN.get(g, g)} {n} 场' for g, n in counts)}（起始日期 {since}）。
<b>口径</b>：形态标签由 tools/classify_pattern.py v1.3 自动判定；本页只记录已发生路径，不构成预测或交易建议。</div>
<div class="warn"><b>数据缺口（显式标注，不用推断补数）</b><ul style="margin:6px 0 0;padding-left:18px">{''.join(f'<li>{html.escape(x)}</li>' for x in GAPS)}</ul></div>
{''.join(sections)}
<p class="foot">数据源：docs/data/snapshots/&lt;event&gt;/（Polymarket CLOB 1 分钟中间价）；分类与窗口：tools/build_pattern_library.py + tools/ingame_window.py；
BetBoom vs Falcons（10-18）在 Polymarket 无对应盘口，故本页不展示价格，仅保留情报页结论。生成器：tools/build_recent_pattern_review.py。</p>
</div></body></html>"""
    path = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"{path}: {len(out)/1024:.0f} KB · " + " / ".join(f"{g} {n}" for g, n in counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
