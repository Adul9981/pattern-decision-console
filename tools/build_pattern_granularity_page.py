#!/usr/bin/env python3
"""形态库「粒度对照页」：把样本量、形态覆盖、关键结论**按 60s / 600s 分层**摊开。

回答的问题：
  1) 现在积累了多少场比赛 / 多少个小局，其中多少是 1 分钟级、多少是 10 分钟级；
  2) 每个形态的样本来自哪一档粒度（2026-10-18 起一级赛事已全量回填 60s）；
  3) 现有结论分别建立在哪一档粒度上 —— 哪些只能算"观察"、哪些可以当"基线"。

输入：build_pattern_library.py 的分段明细 JSON（--out 产物，含 granularity 字段）。
输出：reports/pattern_shapes_granularity_<date>.html（SAP/Apple 风格）。

用法：
  python3 tools/build_pattern_granularity_page.py \
      --section lol=reports/pattern_shapes_lol_2026-0910_1001.json \
      --section lol=reports/pattern_shapes_lol_2026-1002_1014.json \
      --section cs2=reports/pattern_shapes_cs2_2026-08_2026-10.json \
      --out reports/pattern_shapes_granularity_2026-10-15.html
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_pattern_index as bi  # noqa: E402  （复用其粒度感知的抄底回测）

GRANS = ("60s", "600s")
THIN = 20  # 样本 < 20 一律标"样本不足"


def winner_side_labels(rows) -> dict:
    """-> {(gran, scope, label): n}；scope ∈ {series, game}，只统计赢家侧。"""
    out = defaultdict(int)
    for m in rows:
        gran = m.get("granularity", "unknown")
        s = m.get("series") or {}
        ends = {int(k): v["end"] for k, v in s.items()}
        if ends:
            wi = max(ends, key=lambda i: ends[i])
            if ends[wi] >= 0.9:
                for lab in s[str(wi)].get("labels") or ["未知"]:
                    out[(gran, "series", lab)] += 1
        for g, sd in (m.get("games") or {}).items():
            if int(g) in [int(x) for x in m.get("games_unresolved", [])]:
                continue
            e = {int(k): v["end"] for k, v in sd.items()}
            if not e:
                continue
            gi = max(e, key=lambda i: e[i])
            if e[gi] < 0.9:
                continue
            for lab in sd[str(gi)].get("labels") or ["未知"]:
                out[(gran, "game", lab)] += 1
    return out


def reverse_by_gran(rows) -> tuple[Counter, Counter]:
    """让一追二：G1 归属 != 最终胜者（BO3/BO5 分开）。-> (出现, 分母)"""
    rev, tot = Counter(), Counter()
    for m in rows:
        gran = m.get("granularity", "unknown")
        ends = {int(k): v["end"] for k, v in (m.get("series") or {}).items()}
        if not ends:
            continue
        wi = max(ends, key=lambda i: ends[i])
        if ends[wi] < 0.9:
            continue
        gw = {int(k): v for k, v in (m.get("game_winners") or {}).items() if v is not None}
        if 1 not in gw:
            continue
        f = m.get("format") or "(未标注)"
        tot[(gran, f)] += 1
        if gw[1] != wi:
            rev[(gran, f)] += 1
    return rev, tot


def deep_by_gran(rows) -> dict:
    """赢家侧窗口低点 <= 阈值 的场次 / 市场数，按粒度分。"""
    out = defaultdict(lambda: [0, 0])
    for m in rows:
        gran = m.get("granularity", "unknown")
        ends = {int(k): v["end"] for k, v in (m.get("series") or {}).items()}
        if ends:
            wi = max(ends, key=lambda i: ends[i])
            if ends[wi] >= 0.9:
                lo = m["series"][str(wi)]["low"]
                for thr in (0.30, 0.20, 0.10):
                    if lo <= thr:
                        out[(gran, "series", thr)][0] += 1
                out[(gran, "series", 0.30)][1] += 1
                out[(gran, "series", 0.20)][1] += 1
                out[(gran, "series", 0.10)][1] += 1
        for g, sd in (m.get("games") or {}).items():
            if int(g) in [int(x) for x in m.get("games_unresolved", [])]:
                continue
            e = {int(k): v["end"] for k, v in sd.items()}
            if not e:
                continue
            gi = max(e, key=lambda i: e[i])
            if e[gi] < 0.9:
                continue
            lo = sd[str(gi)]["low"]
            for thr in (0.30, 0.20, 0.10):
                out[(gran, "game", thr)][1] += 1
                if lo <= thr:
                    out[(gran, "game", thr)][0] += 1
    return out


def pct(a, b):
    return f"{100.0*a/b:.0f}%" if b else "—"


def build_game(game, rows, per_batch):
    """One game's context. LoL and CS2 are never merged."""
    n_match = len(rows)
    n_with_games = sum(1 for m in rows if m["games"])
    n_games = sum(len(m["games"]) for m in rows)
    n_unres = sum(len([g for g in m["games"]
                       if int(g) in [int(x) for x in m.get("games_unresolved", [])]]) for m in rows)
    c_all = Counter(m.get("granularity", "unknown") for m in rows)
    n60, n600 = c_all.get("60s", 0), c_all.get("600s", 0)
    n_games60 = sum(len(m["games"]) for m in rows if m.get("granularity") == "60s")
    n_games600 = sum(len(m["games"]) for m in rows if m.get("granularity") == "600s")

    lab = winner_side_labels(rows)
    lose = Counter()
    for m in rows:
        s = m.get("series") or {}
        ends = {int(k): v["end"] for k, v in s.items()}
        if not ends:
            continue
        wi = max(ends, key=lambda i: ends[i])
        if ends[wi] < 0.9:
            continue
        for i in ends:
            if i != wi:
                for lb in s[str(i)].get("labels") or ["未知"]:
                    lose[lb] += 1
    labels = sorted(
        {k[2] for k in lab},
        key=lambda l: -(lab[("600s", "series", l)] + lab[("60s", "series", l)]
                        + lab[("600s", "game", l)] + lab[("60s", "game", l)]),
    )
    defined = 16
    named_win = [l for l in labels if l != "未知"]
    rev, tot = reverse_by_gran(rows)
    deep = deep_by_gran(rows)
    return dict(game=game, rows=rows, per_batch=per_batch, n_match=n_match,
                n_with_games=n_with_games, n_games=n_games, n_unres=n_unres,
                c_all=c_all, n60=n60, n600=n600, n_games60=n_games60,
                n_games600=n_games600, lab=lab, labels=labels, defined=defined,
                named_win=named_win, rev=rev, tot=tot, deep=deep, lose=lose)


def build(sections, lib_path):
    rows_by_game, batch_by_game = {}, {}
    for game, path in sections:
        g = "CS2" if game.lower().startswith("cs2") else "LoL"
        d = json.load(open(path, encoding="utf-8"))
        rng = d.get("range", "").replace(":", " ~ ")
        c = Counter(m.get("granularity", "unknown") for m in d["matches"])
        rows_by_game.setdefault(g, []).extend(d["matches"])
        batch_by_game.setdefault(g, []).append((f"{rng} · {g}", len(d["matches"]), c))
    order = [g for g in ("LoL", "CS2") if g in rows_by_game]
    ctxs = [build_game(g, rows_by_game[g], batch_by_game.get(g, [])) for g in order]
    total_lib = len(json.load(open(lib_path, encoding="utf-8"))["paths"])
    return ctxs, total_lib


def render(ctxs, out_path, updated, total_lib):
    def section(g):
        def lab_row(label):
            c60s = g["lab"].get(("60s", "series", label), 0)
            c600s = g["lab"].get(("600s", "series", label), 0)
            c60g = g["lab"].get(("60s", "game", label), 0)
            c600g = g["lab"].get(("600s", "game", label), 0)
            tot = c60s + c600s + c60g + c600g
            thin = " thin" if 0 < tot < THIN else ""
            cells = "".join(
                "<td class='" + (("z" if not c else "") + thin).strip() + ">" + (str(c) if c else "·") + "</td>"
                for c in (c60s, c600s, c60g, c600g)
            )
            return ("<tr><td><b>" + label + "</b></td>" + cells
                    + "<td class='tot" + thin + "'>" + str(tot) + "</td></tr>")

        def rev_row(fmt):
            a, b = g["rev"].get(("60s", fmt), 0), g["rev"].get(("600s", fmt), 0)
            na, nb = g["tot"].get(("60s", fmt), 0), g["tot"].get(("600s", fmt), 0)
            return (f"<tr><td>让一追二 {fmt}</td><td class='z'>{a}/{na or 0} = {pct(a,na)}</td>"
                    f"<td>{b}/{nb or 0} = <b>{pct(b,nb)}</b></td><td>{a+b}/{na+nb or 0} = {pct(a+b,na+nb)}</td></tr>")

        def deep_row(scope, name, thr):
            a, na = g["deep"].get(("60s", scope, thr), [0, 0])
            b, nb = g["deep"].get(("600s", scope, thr), [0, 0])
            return (f"<tr><td>{name} 赢家侧曾 ≤{thr:.2f}</td><td class='z'>{a}/{na} = {pct(a,na)}</td>"
                    f"<td>{b}/{nb} = <b>{pct(b,nb)}</b></td><td>{a+b}/{na+nb} = {pct(a+b,na+nb)}</td></tr>")

        def ev_rows():
            out = []
            for scope, unit in (("series", "整局"), ("game", "逐局/逐图")):
                for thr in (0.35, 0.25, 0.10):
                    cells = []
                    for gran in (60, 600):
                        n, hit, roi = bi.dip_buy(g["rows"], thr, scope, gran)
                        cells.append((n, hit, roi))
                    a, b = cells
                    out.append(
                        f"<tr><td>{unit} 阈值 {thr:.2f}</td>"
                        f"<td class='z'>{a[0]} 笔 · 命中 {a[1]:.0f}% · ROI {a[2]:+.2f}</td>"
                        f"<td>{b[0]} 笔 · 命中 {b[1]:.0f}% · ROI <b>{b[2]:+.2f}</b></td></tr>")
            return "\n".join(out)

        batch_rows = "\n".join(
            f"<tr><td>{name}</td><td>{n}</td><td>{c.get('600s',0)}</td><td>{c.get('60s',0)}</td>"
            f"<td>{pct(c.get('60s',0), n)}</td></tr>" for name, n, c in g["per_batch"]
        )
        lab_rows = "\n".join(lab_row(l) for l in g["labels"])
        n60_pct_match = pct(g["n60"], g["n_match"])
        n60_pct_game = pct(g["n_games60"], g["n_games"])
        return f"""
<h2 id="{g['game'].lower()}">{g['game']} · 粒度与样本</h2>
<div class="cards">
<div class="card"><div class="k">比赛（仅比赛内窗口）</div><div class="v">{g['n_match']}</div><div class="n">含 {g['n_games']} 个{'地图' if g['game']=='CS2' else '小局'}市场 · 未打到/未结算 {g['n_unres']}</div></div>
<div class="card hi"><div class="k">1 分钟级（60s）</div><div class="v">{g['n60']} 场</div><div class="n">占本游戏 {n60_pct_match} · 含 {g['n_games60']} 个小局/地图</div></div>
<div class="card"><div class="k">10 分钟级（600s）</div><div class="v">{g['n600']} 场</div><div class="n">占本游戏 {pct(g['n600'], g['n_match'])} · 含 {g['n_games600']} 个小局/地图</div></div>
<div class="card"><div class="k">形态标签（胜方侧）</div><div class="v">{len(g['named_win'])} / {g['defined']}</div><div class="n">分类器定义 {g['defined']} 个</div></div>
</div>
<div class="tbl"><table>
<thead><tr><th>批次</th><th>场次</th><th>600s</th><th>60s</th><th>分钟级占比</th></tr></thead>
<tbody>{batch_rows}</tbody></table></div>
<p class="legend">本区块只统计 {g['game']}，不与另一游戏合并。2026-10-18 起一级赛事已全量回填 60s（{g['n60']} 场）；60s 是采样价格，不是订单簿，无成交分钟可能沿用前值。</p>

<h3>{g['game']} · 形态样本 × 粒度（只统计胜方侧）</h3>
<div class="tbl"><table>
<thead><tr><th rowspan="2">形态</th><th colspan="2">整局赢家侧</th><th colspan="2">逐局/逐图赢家侧</th><th rowspan="2">合计</th></tr>
<tr><th>1 分钟级</th><th>10 分钟级</th><th>1 分钟级</th><th>10 分钟级</th></tr></thead>
<tbody>{lab_rows}</tbody></table></div>
<p class="legend">"·" = 该粒度下 0 例；<span class="thin">橙色</span> = 合计样本 &lt; {THIN}，只能记观察、不能下结论。小局维度 {g['n_games']} 个市场里只有 {g['n_games60']} 个（{n60_pct_game}）拿到分钟级。</p>

<h3>{g['game']} · 关键结论建立在哪一档粒度上</h3>
<div class="tbl"><table>
<thead><tr><th>指标</th><th>1 分钟级（{g['n60']} 场）</th><th>10 分钟级（{g['n600']} 场）</th><th>合计（本游戏）</th></tr></thead>
<tbody>
{rev_row("BO3")}
{rev_row("BO5")}
{deep_row("series", "整局", 0.30)}
{deep_row("series", "整局", 0.20)}
{deep_row("game", "逐局/逐图", 0.30)}
{deep_row("game", "逐局/逐图", 0.20)}
{ev_rows()}
</tbody></table></div>
<div class="note"><ul>
<li><b>本区块的合计只在本游戏内</b>：{g['game']} 的 60s 与 600s 合并列，不与另一游戏相加。</li>
<li><b>60s 口径</b>：一级赛事已全量回填，不再有“样本极少”的选择性偏差；但仍需注意「归档起点可能略晚」与「采样价格非订单簿」，不能把 60s 点当可成交价。</li>
<li><b>深水与让一追二</b>：赢家侧低点只统计比赛内窗口，赛前挂牌价不再计入。</li>
<li><b>负方侧对照</b>：B4 直线阴跌 {g['lose'].get('B4_直线阴跌', 0)} 例、B4 低开 {g['lose'].get('B4_低开阴跌', 0)} 例、B2 {g['lose'].get('B2_死亡螺旋', 0)} 例；属输家代币自然衰减，不构成胜方侧样本。</li>
</ul></div>
"""

    body = "".join(section(g) for g in ctxs)
    nav = " · ".join(f"<a href='#{g['game'].lower()}'>{g['game']}</a>" for g in ctxs)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230071e3'/%3E%3Cpath d='M14 42l10-14 8 9 18-23' stroke='white' stroke-width='6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E">
<title>形态库粒度对照 · 1 分钟级 vs 10 分钟级（{updated}）</title>
<style>
:root{{--blue:#0071e3;--bg:#f5f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6e6e73;--bd:#e5e5ea;--red:#ff453a}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
.bar-top{{background:#1d1d1f;color:#fff;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px}}
.bar-top b{{font-weight:600}} .bar-top span{{color:#a1a1a6;font-size:13px}}
html.embed .bar-top{{display:none}} html.embed .wrap{{padding-top:14px}}
.wrap{{max-width:1180px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:24px;margin:16px 0 6px;font-weight:600}} h2{{font-size:19px;margin:30px 0 8px}} h3{{font-size:16px;margin:22px 0 6px}}
.sub,.legend{{color:var(--mu);font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}}
.card{{background:var(--card);border-radius:14px;padding:14px 16px}}
.card .k{{font-size:12.5px;color:var(--mu)}} .card .v{{font-size:24px;font-weight:600;margin:4px 0}} .card .n{{font-size:12px;color:var(--mu)}}
.card.hi{{background:#eaf3ff;outline:1px solid #cfe4ff}}
.tbl{{background:var(--card);border-radius:14px;padding:6px 10px;overflow-x:auto;margin:8px 0}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}} th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--bd)}}
th{{color:var(--mu);font-weight:500;font-size:12.5px}} td.z{{color:#c7c7cc}} .thin{{color:#ff9f0a}} td.tot{{font-weight:600}}
.note{{background:var(--card);border-radius:14px;padding:10px 18px;margin:10px 0}} li{{margin:4px 0}}
.pg{{display:block;background:#fff;border-radius:12px;padding:10px 12px;margin:6px 0;text-decoration:none;color:inherit}}
@media(max-width:760px){{.cards{{grid-template-columns:1fr 1fr}}}}
</style>
<script>if(location.search.indexOf("embed=1")>=0)document.documentElement.classList.add("embed");</script></head><body>
<div class="bar-top"><b>Polymarket 电竞情报库 · 形态粒度对照</b><span>生成 {updated} · 仅比赛内窗口</span></div>
<div class="wrap">
<h1>粒度对照：1 分钟级 vs 10 分钟级</h1>
<p class="sub">本页按游戏分开：{nav}。两个游戏的口径、样本与统计不混算。所有形态只使用<b>比赛内窗口</b>（已剔除赛前长平台、赛后结算平台、图间停滞）；旧全窗口数据备份在 <code>reports/archive/pattern_backup_2026-10-18/</code>。</p>
{body}
<div class="note">形态库条目（全库，含人工复盘）：{total_lib} · 本页为只读统计，粒度按实际采样间隔归桶。</div>
</div></body></html>"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="utf-8").write(html)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", action="append", default=[], help="game=path（可重复）")
    ap.add_argument("--out", default="reports/pattern_shapes_granularity.html")
    ap.add_argument("--lib", default="docs/data/intel/price_paths.json")
    ap.add_argument("--updated", default="2026-10-18")
    args = ap.parse_args()
    sections = [tuple(s.split("=", 1)) for s in args.section]
    ctxs, total_lib = build(sections, args.lib)
    render(ctxs, args.out, args.updated, total_lib)
    summary = " · ".join(f"{c['game']} {c['n_match']} 场（60s {c['n60']} / 600s {c['n600']}）" for c in ctxs)
    print(f"granularity page -> {args.out}（{summary}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
