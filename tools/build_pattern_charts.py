#!/usr/bin/env python3
"""形态曲线图页：把每场比赛的**两条队伍赔率曲线**直接画出来（逐局 + 整局），
按比赛分组、按粒度（60s / 600s）打标并可筛选。

为什么要它：形态库此前只有标签文字（"A2_中位U型反转 0.21->1.00"），
看不到形状本身。本页把 price_paths 的抽象标签还原成真实价格轨迹：
  - 每张小图 = 一个市场（整局 / G1 / G2 …），两条线 = 两个队伍的代币价格；
  - 两条线在 50c 处交叉，交叉点即"谁领先"的翻转；
  - 每条线标注形态标签、低点▲、终点●、窗口低→高。
数据源与形态库完全一致（docs/data/snapshots/，同一套 med spacing 判定粒度）；
绘图会降采样（默认 180 点/线），页面顶部注明"绘图降采样，统计仍用全量序列"。

用法：
  python3 tools/build_pattern_charts.py --section cs2=reports/pattern_shapes_cs2_2026-08_2026-10.json \
      --title "CS2 · 赔率形态曲线（2026-08-01 ~ 10-15）" --out reports/pattern_charts_cs2.html
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_pattern_library as bpl  # noqa: E402
import ingame_window as igw  # noqa: E402
import path_series as ps  # noqa: E402
from classify_pattern import load_series  # noqa: E402

SNAP = os.path.join(bpl.ROOT, "docs", "data", "snapshots")
C0, C1 = "#0071e3", "#ff9f0a"          # 两侧队伍的颜色（side 0 / side 1）
CW, CH = 330, 118                       # 单图尺寸
PAD_L, PAD_R, PAD_T, PAD_B = 4, 4, 8, 4
CONF_CN = {"high": "高", "medium": "中", "low": "低"}


def conf_label(value: str) -> str:
    return CONF_CN.get(value, "低") + "置信"


def lttb(pts: list, n: int) -> list:
    """Largest-Triangle-Three-Buckets 降采样：保留形状与尖峰，用于绘图。"""
    if n < 3 or len(pts) <= n:
        return pts
    idx = list(range(len(pts)))
    every = (len(pts) - 2) / (n - 2)
    out = [pts[0]]
    a = 0
    for i in range(n - 2):
        s0 = int((i + 1) * every) + 1
        s1 = min(int((i + 2) * every) + 1, len(pts))
        if s1 <= s0:
            s1 = min(s0 + 1, len(pts))
        avg_x = sum(idx[s0:s1]) / (s1 - s0)
        avg_y = sum(pts[j][1] for j in range(s0, s1)) / (s1 - s0)
        r0, r1 = int(i * every) + 1, min(int((i + 1) * every) + 1, len(pts))
        ax, ay = idx[a], pts[a][1]
        best, nxt = -1.0, r0
        for j in range(r0, r1):
            area = abs((ax - avg_x) * (pts[j][1] - ay) - (ax - idx[j]) * (avg_y - ay))
            if area > best:
                best, nxt = area, j
        out.append(pts[nxt])
        a = nxt
    out.append(pts[-1])
    return out


def market_series(slug: str, idx: int, kind: str, window=None):
    """取某个市场的 (file, pts, gran)；kind ∈ {series, game(idx=局号)}。"""
    d = os.path.join(SNAP, slug)
    if not os.path.isdir(d):
        return None
    if kind == "series":
        pat = re.compile(rf"^{re.escape(slug)}__(?P<side>\d+)__(?P<team>.+?)(?P<hires>\.hires)?\.jsonl$")
    else:
        pat = re.compile(rf"^{re.escape(slug)}-game{idx}__(?P<side>\d+)__(?P<team>.+?)(?P<hires>\.hires)?\.jsonl$")
    best = {}
    for n in sorted(os.listdir(d)):
        m = pat.match(n)
        if not m:
            continue
        key = m.group("side")
        hires = bool(m.group("hires"))
        if key in best and not hires:
            continue
        if key in best and best[key][1]:
            continue
        best[key] = (os.path.join(d, n), hires)
    out = {}
    for key, (path, _h) in best.items():
        try:
            pts = load_series(path)
        except Exception:  # noqa: BLE001
            continue
        if len(pts) < 3:
            continue
        if window:
            pts = igw.trim(pts, *window)
        if len(pts) < 2:
            continue
        step = statistics.median([pts[i + 1][0] - pts[i][0] for i in range(len(pts) - 1)])
        out[key] = (pts, 60 if step <= 120 else 600)
    return out or None


def compute_windows(slug: str, in_game: bool) -> dict:
    """Return {'series': (start,end), 'games': {g: (start,end)}} for charts."""
    if not in_game:
        return {"series": None, "games": {}}
    ev = igw.event_start(slug)
    result = {"series": None, "games": {}}
    prev_end = None
    for g in range(1, 6):
        sides = market_series(slug, g, "game")
        if not sides:
            continue
        side_map = {k: v[0] for k, v in sides.items()}
        floor = ev if (g == 1 or prev_end is None) else prev_end + 60
        start, end, _src = igw.window_for_market(side_map, floor)
        result["games"][g] = (start, end)
        prev_end = end
    series_sides = market_series(slug, 0, "series")
    if series_sides:
        side_map = {k: v[0] for k, v in series_sides.items()}
        if result["games"]:
            first = min(result["games"])
            start = result["games"][first][0]
            if start:
                pivot = max(side_map, key=lambda k: abs(side_map[k][-1][1] - 0.5))
                result["series"] = (start, igw.settlement_end(side_map[pivot]))
            else:
                result["series"] = igw.window_for_market(side_map, ev)[:2]
        else:
            result["series"] = igw.window_for_market(side_map, ev)[:2]
    return result


def svg_chart(lines: list, w=CW, h=CH, max_pts=180) -> str:
    """lines = [{'key','team','pts','labels','winner'}]，画两条（或一条）价格曲线。"""
    if not lines:
        return "<div class='nochart'>无数据</div>"
    t0 = min(l["pts"][0][0] for l in lines)
    t1 = max(l["pts"][-1][0] for l in lines)
    span = (t1 - t0) or 1
    iw, ih = w - PAD_L - PAD_R, h - PAD_T - PAD_B

    def X(t):
        return PAD_L + (t - t0) / span * iw

    def Y(p):
        return PAD_T + (1 - max(0.0, min(1.0, p))) * ih

    parts = [f"<svg class='ch' viewBox='0 0 {w} {h}' preserveAspectRatio='none' role='img'>"]
    mid = Y(0.5)
    parts.append(f"<line x1='{PAD_L}' y1='{mid:.1f}' x2='{w-PAD_R}' y2='{mid:.1f}'"
                 f" stroke='#c7c7cc' stroke-width='1' stroke-dasharray='3 3'/>")
    if 0.5 * span:  # 时间中点竖线，便于看前半/后半
        parts.append(f"<line x1='{X(t0+span/2):.1f}' y1='{PAD_T}' x2='{X(t0+span/2):.1f}' y2='{h-PAD_B}'"
                     f" stroke='#e5e5ea' stroke-width='1'/>")
    for k, ln in enumerate(lines):
        prep = ln.get("prep") or ps.prepare(ln["pts"])
        pts = lttb(prep.display, max_pts)
        d = " ".join(("M" if i == 0 else "L") + f"{X(t):.1f},{Y(p):.1f}" for i, (t, p) in enumerate(pts))
        color = C0 if ln["key"] == "0" else C1
        parts.append(f"<path d='{d}' fill='none' stroke='{color}'"
                     f" stroke-width='{2.0 if ln['winner'] else 1.2}'"
                     f" opacity='{1.0 if ln['winner'] else 0.62}'"
                     f" stroke-linejoin='round' stroke-linecap='round'/>")
        # Sparse paths: show the real observations as dots so the smoothed
        # line cannot be mistaken for extra data.  Dense 1m paths skip dots
        # (hundreds of circles per line would just be noise).
        if prep.raw_points <= 300:
            for t, p in prep.raw:
                parts.append(f"<circle cx='{X(t):.1f}' cy='{Y(p):.1f}' r='1.15'"
                             f" fill='{color}' opacity='0.38'/>")
        lo = min(ln["pts"], key=lambda x: x[1])
        parts.append(f"<circle cx='{X(lo[0]):.1f}' cy='{Y(lo[1]):.1f}' r='2.6' fill='{color}'/>")
        parts.append(f"<circle cx='{X(ln['pts'][-1][0]):.1f}' cy='{Y(ln['pts'][-1][1]):.1f}' r='2.6'"
                     f" fill='none' stroke='{color}' stroke-width='1.6'/>")
    parts.append("</svg>")
    return "".join(parts)


def chart_block(slug: str, name: str, sides: dict, meta_info: dict, max_pts: int) -> str:
    """一个市场 → 一张图 + 图下两行说明。"""
    if not sides:
        return ""
    win = None
    ends = {k: v["end"] for k, v in meta_info.items()}
    if ends:
        w = max(ends, key=lambda k: ends[k])
        if ends[w] >= 0.9:
            win = w
    lines, tags, grans, confs = [], [], set(), set()
    for key in sorted(sides):
        pts, gran = sides[key]
        grans.add(gran)
        info = meta_info.get(key) or {}
        prep = ps.prepare(pts)
        confs.add(prep.confidence)
        lines.append({"key": key, "team": info.get("team", key), "pts": pts,
                      "labels": info.get("labels") or [], "winner": key == win, "prep": prep})
        tags.append((key, info.get("team", key), info.get("labels") or [], info.get("low"),
                     info.get("high"), info.get("end"), prep, gran))
    gran = "/".join(f"{g}s" for g in sorted(grans))
    legend = "".join(
        f"<div class='lg'><i style='background:{C0 if k == '0' else C1}'></i>"
        f"<b>{html.escape(str(team))}</b>"
        f"<span class='lb'>{html.escape('/'.join(labs))}</span>"
        f"<span class='pn'>{('- ' if low is not None else '')}{'' if low is None else format(low,'.2f')}→"
        f"{'' if high is None else format(high,'.2f')}</span>"
        f"<span class='pn2'>{'' if end is None else format(end,'.2f')} · 原始{n.raw_points}点"
        f"{' · 插值'+format(n.interpolated_ratio*100,'.0f')+'%' if n.interpolated_ratio else ''}"
        f" · {conf_label(n.confidence)}</span></div>"
        for k, team, labs, low, high, end, n, _g in tags
    )
    conf = "high" if confs == {"high"} else ("low" if "low" in confs else "medium")
    conf_text = {"high": "1m 高置信", "medium": "中置信", "low": "低置信"}[conf]
    anchor = f"{slug}-{'series' if name.startswith('整局') else name.lower()}"
    return (f"<div class='mk' id='{html.escape(anchor)}' data-gran='{gran}'>"
            f"<div class='mk-h'><b>{html.escape(name)}</b><span class='chip{'-on' if '60s' in gran else ''}'>{gran}</span>"
            f"<span class='chip{' cf-'+conf if conf!='medium' else ''}'>{conf_text}</span></div>"
            f"{svg_chart(lines, max_pts=max_pts)}{legend}</div>")


def build_page(sections, out_path, title, sub, max_pts, in_game=True):
    cards = []
    n_ch = 0
    for game, path in sections:
        d = json.load(open(path, encoding="utf-8"))
        for m in d["matches"]:
            slug = m["slug"]
            wins = compute_windows(slug, in_game)
            gz = m.get("granularity", "unknown")
            sw = m.get("series_winner")
            gw = {int(k): v for k, v in (m.get("game_winners") or {}).items()}
            unres = [int(x) for x in m.get("games_unresolved", [])]
            blocks = []
            # 整局
            sb = market_series(slug, 0, "series", wins.get("series"))
            if sb and m.get("series"):
                nb = chart_block(slug, "整局 Winner", sb, m["series"], max_pts)
                blocks.append(nb)
                n_ch += 1
            # 逐局
            for g in sorted({int(x) for x in (m.get("games") or {})}):
                if g in unres:
                    continue
                gb = market_series(slug, g, "game", wins.get("games", {}).get(g))
                if not gb or str(g) not in (m.get("games") or {}):
                    continue
                blocks.append(chart_block(slug, f"G{g}", gb, m["games"][str(g)], max_pts))
                n_ch += 1
            if not blocks:
                continue
            win_name = ""
            if sw is not None and str(sw) in (m.get("series") or {}):
                win_name = (m["series"][str(sw)].get("team") or "")
            cards.append(
                f"<div class='card match' id='m-{html.escape(slug)}' data-gran='{gz}'>"
                f"<div class='hd'><div><b>{html.escape(slug)}</b>"
                f"<span class='chip{'-on' if gz == '60s' else ''}'>{gz}</span></div>"
                f"<div class='muted'>{html.escape(m.get('result',''))}"
                + (f" · {html.escape(m.get('league_raw',''))}" if m.get("league_raw") else "")
                + (f" · {m.get('date','')}" if m.get("date") else "")
                + "</div></div>"
                f"<div class='row'>{''.join(blocks)}</div></div>")
    n60 = sum(1 for c in cards if "data-gran='60s'" in c)
    htmlout = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230071e3'/%3E%3Cpath d='M14 42l10-14 8 9 18-23' stroke='white' stroke-width='6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E">
<title>{html.escape(title)}</title>
<style>
:root{{--blue:#0071e3;--bg:#f5f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6e6e73;--bd:#e5e5ea}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
.bar-top{{background:#1d1d1f;color:#fff;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px}}
.bar-top b{{font-weight:600}} .bar-top span{{color:#a1a1a6;font-size:13px}}
html.embed .bar-top{{display:none}} html.embed .wrap{{padding-top:14px}}
.wrap{{max-width:1280px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:24px;margin:16px 0 6px;font-weight:600}}
.sub{{color:var(--mu);margin:0 0 14px;font-size:13.5px}}
.bar{{position:sticky;top:0;z-index:5;background:rgba(245,245,247,.92);backdrop-filter:saturate(180%) blur(12px);padding:10px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;border-bottom:1px solid var(--bd)}}
.btn{{background:#fff;border:1px solid var(--bd);border-radius:9px;padding:6px 12px;font-size:13px;color:var(--tx);cursor:pointer}}
.btn.on{{background:var(--blue);border-color:var(--blue);color:#fff}}
.legend{{background:#fff;border-radius:12px;padding:10px 14px;font-size:13px;color:#3a3a3c;margin:12px 0 6px}}
.card{{background:var(--card);border-radius:14px;padding:14px 16px;margin:12px 0}}
.match .hd{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:baseline;border-bottom:1px solid var(--bd);padding-bottom:8px}}
.muted{{color:var(--mu);font-size:12.5px}}
.chip{{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:7px;background:#ececf0;color:#6e6e73;margin-left:7px}}
.chip-on{{background:#e3f0ff;color:var(--blue)}}
.cf-high{{background:#e7f6ec;color:#1d8f4e}} .cf-low{{background:#fff2e2;color:#b25000}}
.row{{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px}}
.mk{{background:#fbfbfd;border:1px solid var(--bd);border-radius:11px;padding:8px 9px}}
.mk-h{{font-size:12px;color:var(--mu);display:flex;align-items:center;margin-bottom:2px}}
.ch{{width:{CW}px;height:{CH}px;display:block}}
.lg{{display:flex;align-items:center;gap:6px;font-size:12px;margin-top:3px;flex-wrap:wrap}}
.lg i{{width:10px;height:3px;border-radius:2px;display:inline-block}}
.lg b{{font-weight:600}} .lb{{color:var(--mu)}} .pn{{color:#3a3a3c;font-variant-numeric:tabular-nums}}
.pn2{{color:#a1a1a6;font-variant-numeric:tabular-nums;font-size:11.5px}}
.nochart{{color:var(--mu);font-size:12px;padding:10px}}
.hide{{display:none}}
</style>
<script>if(location.search.indexOf("embed=1")>=0)document.documentElement.classList.add("embed");</script></head><body>
<div class="bar-top"><b>Polymarket 电竞情报库 · 赔率形态曲线</b><span>生成 {dt.date.today().isoformat()}</span></div>
<div class="wrap">
<h1>{html.escape(title)}</h1>
<p class="sub">{html.escape(sub)}</p>
<div class="bar">
<span class="muted">共 {len(cards)} 场 · {n_ch} 张图 · 分钟级 {n60} 场</span>
<button class="btn on" data-f="all">全部</button>
<button class="btn" data-f="60s">只看 1 分钟级</button>
<button class="btn" data-f="600s">只看 10 分钟级</button>
</div>
<div class="legend">
<b>窗口：</b>{'仅比赛内窗口——已剔除赛前长平台与赛后结算平台；图与图之间的停滞不计入下一局。' if in_game else '全窗口口径（含赛前/赛后，旧版对照）。'}<br>
<b>怎么看：</b>每张小图 = 一个市场（整局 / 各小局），<b>两条线 = 两支队伍的代币价格</b>（0-100 美分）；
虚线 = 50 美分（<b>两条线在此交叉 = 领先权翻转</b>，交叉前后换边的过程就是形态）；
粗实线 = 赢家侧，细淡线 = 输家侧；实心圆 ▲= 该侧窗口低点，空心圆 = 终点价；
图下文字 = 形态标签 + 低→高 + 终点 + 原始点数 / 插值比例 / 置信度。
<b>线：</b>实线是<b>平滑展示线</b>（1 分钟数据：3 点中值 + EMA；仅对 ≤3 分钟的缺口线性补点），
小圆点 = 真实采样点（仅 ≤300 点的稀疏图显示）；600s 图保持原始折线、不做分钟级插值。
平滑只改变观看效果，<b>形态分类与统计仍用原始点</b>。
<b>粒度：</b>蓝色标签 = 1 分钟级（60s），灰色 = 10 分钟级（600s）；页面为看清形状对曲线做了降采样，
统计一律仍用全量序列（见 reports/pattern_shapes_granularity.html）。
</div>
{''.join(cards)}
</div>
<script>
const btns=document.querySelectorAll('.btn');
btns.forEach(b=>b.addEventListener('click',()=>{{
  btns.forEach(x=>x.classList.remove('on')); b.classList.add('on');
  const f=b.dataset.f;
  document.querySelectorAll('.match').forEach(c=>{{
    const g=c.dataset.gran||'';
    const mixed=g==='mixed'||g==='unknown';
    const ok = f==='all' || mixed || g.includes(f);
    c.classList.toggle('hide', !ok);
  }});
}}));
</script>
</body></html>"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="utf-8").write(htmlout)
    return len(cards), n_ch, round(len(htmlout) / 1024.0 / 1024.0, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", action="append", default=[])
    ap.add_argument("--title", default="赔率形态曲线")
    ap.add_argument("--sub", default="")
    ap.add_argument("--out", default="reports/pattern_charts.html")
    ap.add_argument("--max-points", type=int, default=180)
    ap.add_argument("--full-window", action="store_true",
                    help="保留赛前/赛后全窗口（旧口径；默认只画比赛内窗口）")
    args = ap.parse_args()
    n, ch, mb = build_page([tuple(s.split("=", 1)) for s in args.section], args.out,
                           args.title, args.sub, args.max_points, in_game=not args.full_window)
    print(f"{args.out}: {n} 场 / {ch} 张图 / {mb} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
