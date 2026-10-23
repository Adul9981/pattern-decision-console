#!/usr/bin/env python3
"""Regenerate the price-shape library index page (LoL + CS2 one place, read-only).

输入：tools/build_pattern_library.py 的分段明细 JSON（--out 产物）。
输出：reports/pattern_shapes_index.html（SAP/Apple 风格）。

页面口径（2026-10-15 定稿，与 knowledge/PATTERN_STRUCTURE_LIBRARY.md 一致）：
  1) 形态频次分「胜方侧 / 负方侧」——负方侧 84% 是 B4 恒等式衰减，只作对照；
  2) 让一追二基准率按赛制（BO3/BO5）；
  3) 期望值：机械抄底回测（首次触及阈值价买入、持有到结算），读快照序列现算。

用法：
  python3 tools/build_pattern_index.py --section lol=reports/pattern_shapes_lol_2026-0910_1001.json \
      --section lol=reports/pattern_shapes_lol_2026-1002_1014.json \
      --section cs2=reports/pattern_shapes_cs2_2026-08_2026-10.json \
      --out reports/pattern_shapes_index.html --updated 2026-10-15
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify_pattern import load_series  # noqa: E402
import ingame_window as igw  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, "docs", "data", "snapshots")
LIB = os.path.join(ROOT, "docs", "data", "intel", "price_paths.json")
COLOR = {"A": "#0071e3", "B": "#ff453a", "C": "#8e8e93", "热": "#ff9f0a", "未": "#c7c7cc"}


def fam(lab: str) -> str:
    for p, c in (("A", "A"), ("B", "B"), ("C", "C"), ("热门", "热")):
        if lab.startswith(p):
            return COLOR[c]
    return COLOR["未"]


def bars(freq: Counter, base: int) -> str:
    if not freq:
        return "<p class='muted'>无样本</p>"
    mx = max(freq.values())
    out = []
    for lab, n in freq.most_common():
        out.append(
            f"<div class='bar'><span class='bl'>{lab}</span>"
            f"<span class='bt'><i style='width:{n/mx*100:.1f}%;background:{fam(lab)}'></i></span>"
            f"<span class='bn'>{n}<em>{100.0*n/base:.0f}%</em></span></div>" if base else
            f"<div class='bar'><span class='bl'>{lab}</span><span class='bn'>{n}</span></div>"
        )
    return "\n".join(out)


def sides_files(slug: str, kind: str):
    """-> [(idx, path)]；同一市场存在 .hires 时优先（去重，2026-10-15）。"""
    d = os.path.join(SNAP, slug)
    if not os.path.isdir(d):
        return []
    pat = (re.compile(rf"^{re.escape(slug)}-game(\d+)__0__(?P<team>.+?)(?P<hires>\.hires)?\.jsonl$") if kind == "game"
           else re.compile(rf"^{re.escape(slug)}__0__(?P<team>.+?)(?P<hires>\.hires)?\.jsonl$"))
    best: dict = {}
    for n in sorted(os.listdir(d)):
        m = pat.match(n)
        if not m:
            continue
        idx = m.group(1)
        hires = bool(m.group("hires"))
        if idx in best and not hires:
            continue                            # 已有一条序列，且新的不是 .hires -> 跳过
        if idx in best and best[idx][1]:
            continue                            # 已有 .hires -> 不再覆盖
        best[idx] = (os.path.join(d, n), hires)
    return [(i, p) for i, (p, _h) in sorted(best.items())]


_SIDES_CACHE: dict = {}


def load_sides(slug: str, kind: str):
    """-> [(pts, gran)]，gran ∈ {60, 600}，按**实际采样间隔**判定，不看文件名。

    保持二元组契约（既有调用方与回归测试依赖）；需要市场 key 时使用
    load_sides_keyed()。
    """
    return [(pts, g) for _k, pts, g in load_sides_keyed(slug, kind)]


def load_sides_keyed(slug: str, kind: str):
    """-> [(market_key, pts, gran)]，gran ∈ {60, 600}，按**实际采样间隔**判定，不看文件名。

    2026-10-15：主序列里已有多场是真实的 60s 数据（赛后不久抓取，CLOB 仍返回
    分钟级），不能只把 .hires 当 60s —— 否则粒度分层会把它们错分进 600s 组。
    """
    ck = (slug, kind)
    if ck in _SIDES_CACHE:
        return _SIDES_CACHE[ck]
    out = []
    for mkey, p in sides_files(slug, kind):
        try:
            pts = load_series(p)
        except Exception:  # noqa: BLE001
            continue
        if len(pts) < 3:
            continue
        step = statistics.median([pts[i + 1][0] - pts[i][0] for i in range(len(pts) - 1)])
        out.append((mkey, pts, 60 if step <= 120 else 600))
    _SIDES_CACHE[ck] = out
    return out


def dip_buy(matches, thr: float, kind: str, gran: int | None = None):
    """机械抄底：首次触及阈值价买入、持有到结算。gran=None 表示不限粒度。

    只在比赛内窗口内寻找买点（2026-10-18 口径）：赛前平台里的深水价格不计入。
    """
    n = w = 0
    tot = 0.0
    for mm in matches:
        for mkey, pts, g in load_sides_keyed(mm["slug"], kind):
            if gran is not None and g != gran:
                continue
            if kind == "series":
                win = next(iter((mm.get("series") or {}).values()), None)
            else:
                win = next(iter(((mm.get("games") or {}).get(str(mkey)) or {}).values()), None)
            if win and win.get("window_start") is not None:
                pts = igw.trim(pts, win.get("window_start"), win.get("window_end"))
            if len(pts) < 3:
                continue
            ent = next((pr for _, pr in pts if pr <= thr), None)
            if not ent or ent <= 0.001:
                continue
            won = pts[-1][1] >= 0.5
            n += 1
            w += won
            tot += (1.0 / ent - 1.0) if won else -1.0
    return n, (100.0 * w / n if n else 0.0), (tot / n if n else 0.0)


def analyze(matches):
    win = Counter()
    lose = Counter()
    gwin = Counter()
    deep = Counter()
    n_series = n_game = 0
    fmt = Counter()
    rev = Counter()
    for m in matches:
        s = m.get("series") or {}
        ends = {int(k): v["end"] for k, v in s.items()}
        if ends:
            wi = max(ends, key=lambda i: ends[i])
            if ends[wi] >= 0.9:
                n_series += 1
                side = s[str(wi)]
                for lab in side.get("labels") or ["未知"]:
                    win[lab] += 1
                for other in ends:
                    if other != wi:
                        for lab in s[str(other)].get("labels") or ["未知"]:
                            lose[lab] += 1
                if side["low"] <= 0.30:
                    deep["<=0.30"] += 1
                    if side["low"] <= 0.20:
                        deep["<=0.20"] += 1
                    if side["low"] <= 0.10:
                        deep["<=0.10"] += 1
                f = m.get("format") or "(未标注)"
                gw = {int(k): v for k, v in (m.get("game_winners") or {}).items() if v is not None}
                if 1 in gw:
                    fmt[f] += 1
                    if gw[1] != wi:
                        rev[f] += 1
        for g, sd in (m.get("games") or {}).items():
            if int(g) in [int(x) for x in m.get("games_unresolved", [])]:
                continue
            e = {int(k): v["end"] for k, v in sd.items()}
            if not e:
                continue
            gi = max(e, key=lambda i: e[i])
            if e[gi] < 0.9:
                continue
            n_game += 1
            side = sd[str(gi)]
            for lab in side.get("labels") or ["未知"]:
                gwin[lab] += 1
            if side["low"] <= 0.30:
                deep["g<=0.30"] += 1
                if side["low"] <= 0.20:
                    deep["g<=0.20"] += 1
                if side["low"] <= 0.10:
                    deep["g<=0.10"] += 1
    return dict(win=win, lose=lose, gwin=gwin, deep=deep, n_series=n_series, n_game=n_game, fmt=fmt, rev=rev)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", action="append", default=[], help="game=path（可重复）")
    ap.add_argument("--out", default="reports/pattern_shapes_index.html")
    ap.add_argument("--updated", default="")
    args = ap.parse_args()

    groups: dict[str, list] = defaultdict(list)
    sections = []
    for spec in args.section:
        game, path = spec.split("=", 1)
        data = json.load(open(path, encoding="utf-8"))
        groups[game].append(data)
        sections.append((game, data, path))

    total = len(json.load(open(LIB, encoding="utf-8"))["paths"])
    label = {"lol": "LoL", "cs2": "CS2"}

    # ---- per-game analysis
    per_game = {}
    for game, payloads in groups.items():
        matches = [m for d in payloads for m in d["matches"]]
        st = analyze(matches)
        per_game[game] = (matches, st)

    # ---- combined frequency (胜方/负方)
    comb = {"win": Counter(), "lose": Counter(), "gwin": Counter()}
    for game, (matches, st) in per_game.items():
        comb["win"].update(st["win"])
        comb["lose"].update(st["lose"])
        comb["gwin"].update(st["gwin"])

    # ---- EV table per game × 粒度（读快照现算）
    # 2026-10-15：60s 与 600s 必须分层——60s 能看到 600s 完全错过的深水触点，
    # 混算会把"采样精度"当成"策略收益"，所以两档各出一组行。
    ev_rows = []
    for game, (matches, st) in per_game.items():
        for gran in (600, 60):
            rows_here = []
            for thr in (0.35, 0.30, 0.25, 0.20, 0.15, 0.10):
                rows_here.append((thr, dip_buy(matches, thr, "series", gran),
                                  dip_buy(matches, thr, "game", gran)))
            if all(r[1][0] == 0 and r[2][0] == 0 for r in rows_here):
                continue
            ev_rows.append((label.get(game, game), f"{gran}s", rows_here))

    def gran_mix(matches) -> str:
        c = Counter(m.get("granularity", "unknown") for m in matches)
        return " · ".join(f"{k} {v} 场" for k, v in c.most_common())

    all_gran = Counter()
    for _, (mm, _st) in per_game.items():
        all_gran.update(m.get("granularity", "unknown") for m in mm)

    links = []
    for game, data, path in sections:
        html_name = os.path.basename(path).replace(".json", ".html")
        n = len(data["matches"])
        rng = data.get("range", "").replace(":", " ~ ")
        dist = " / ".join(f"{k} {v}" for k, v in sorted(data.get("league_dist", {}).items(), key=lambda kv: -kv[1])[:8])
        links.append(f"<a class='pg' href='{html_name}'><b>{rng} · {n} 场</b><div>{dist}</div></a>")
        # 同名曲线图页（pattern_shapes_X -> pattern_charts_X）：存在才挂入口
        ch = os.path.basename(path).replace("pattern_shapes_", "pattern_charts_").replace(".json", ".html")
        if os.path.exists(os.path.join(os.path.dirname(path), ch)):
            links.append(f"<a class='pg' href='{ch}'><b>↳ 曲线图：{rng} · {n} 场</b>"
                         "<div>把形态还原成图形——两条队伍赔率线 + 50c 交叉，可按粒度筛选</div></a>")
    links.append("<a class='pg' href='reference/PATTERN_STRUCTURE_LIBRARY.md'>"
                 "<b>形态结构库（知识库文档）</b><div>口径 / 频次 / 期望值 / 策略映射：knowledge/PATTERN_STRUCTURE_LIBRARY.md</div></a>")
    links.append("<a class='pg' href='reference/REVERSAL_PATTERN_LIBRARY.md'>"
                 "<b>形态定义（A1-C3 + 热门压制）</b><div>每个形态的图形特征、路由策略、正反例</div></a>")
    links.append("<a class='pg' href='pattern_playbook.html'>"
                 "<b>形态速查（交易决策用）</b>"
                 "<div>每个形态的示意线条 + 真实样本曲线 + 买点/卖点/失效条件</div></a>")
    links.append("<a class='pg' href='pattern_taxonomy.html'>"
                 "<b>形态分级体系：一共能搞出多少种</b>"
                 "<div>游戏 / 整局·逐局 / 胜方·负方 四主格 + 每格可用形态数与样本缺口</div></a>")
    links.append("<a class='pg' href='pattern_shapes_granularity.html'>"
                 "<b>粒度对照：1 分钟级 vs 10 分钟级</b>"
                 "<div>样本量、形态覆盖、关键结论分别建立在哪一档粒度上（60s 场次仅 3%）</div></a>")

    def fmt_rows(st):
        return "\n".join(
            f"<tr><td>{k}</td><td>{st['fmt'][k]}</td><td>{st['rev'][k]}</td><td><b>{100.0*st['rev'][k]/st['fmt'][k]:.0f}%</b></td></tr>"
            for k in ("BO3", "BO5", "BO1") if st["fmt"].get(k)
        )

    ev_html = "\n".join(
        f"<tr><td>{g}</td><td>{gl}</td><td>{thr:.2f}</td><td>{se[0]}</td><td>{se[1]:.0f}%</td>"
        f"<td class='{'pos' if se[2]>0 else 'neg'}'>{se[2]:+.2f}</td>"
        f"<td>{ga[0]}</td><td>{ga[1]:.0f}%</td><td class='{'pos' if ga[2]>0 else 'neg'}'>{ga[2]:+.2f}</td></tr>"
        for g, gl, rows in ev_rows for thr, se, ga in rows
    )

    game_blocks = []
    for game in ("lol", "cs2"):
        if game not in per_game:
            continue
        matches, st = per_game[game]
        unit = "逐图" if game == "cs2" else "逐小局"
        game_blocks.append(f"""
<h2>{label[game]} · 胜方侧形态频次</h2>
<div class="card">
<div class="sub" style="margin:0 0 6px">数据粒度构成：{gran_mix(matches)}</div>
<div class="sub" style="margin:0 0 6px">整局（分母 = {st['n_series']} 个赢家侧）</div>{bars(st['win'], st['n_series'])}
<div class="sub" style="margin:14px 0 6px">{unit}（分母 = {st['n_game']} 个赢家侧）</div>{bars(st['gwin'], st['n_game'])}
<div class="sub" style="margin:14px 0 6px">负方侧对照（整局，分母 = {st['n_series']}；B4 = 输家代币自然衰减，恒等式产物）</div>{bars(st['lose'], st['n_series'])}
</div>

<h2>{label[game]} · 深水与让一追二</h2>
<div class="card tbl"><table>
<thead><tr><th>口径</th><th>整局</th><th>{unit}</th></tr></thead><tbody>
<tr><td>赢家侧曾 ≤0.30</td><td>{st['deep'].get('<=0.30',0)} / {st['n_series']} = {100.0*st['deep'].get('<=0.30',0)/max(st['n_series'],1):.0f}%</td><td>{st['deep'].get('g<=0.30',0)} / {st['n_game']} = {100.0*st['deep'].get('g<=0.30',0)/max(st['n_game'],1):.0f}%</td></tr>
<tr><td>赢家侧曾 ≤0.20</td><td>{st['deep'].get('<=0.20',0)} / {st['n_series']} = {100.0*st['deep'].get('<=0.20',0)/max(st['n_series'],1):.0f}%</td><td>{st['deep'].get('g<=0.20',0)} / {st['n_game']} = {100.0*st['deep'].get('g<=0.20',0)/max(st['n_game'],1):.0f}%</td></tr>
<tr><td>赢家侧曾 ≤0.10</td><td>{st['deep'].get('<=0.10',0)} / {st['n_series']} = {100.0*st['deep'].get('<=0.10',0)/max(st['n_series'],1):.0f}%</td><td>{st['deep'].get('g<=0.10',0)} / {st['n_game']} = {100.0*st['deep'].get('g<=0.10',0)/max(st['n_game'],1):.0f}%</td></tr>
</tbody></table></div>
<div class="card tbl"><table>
<thead><tr><th>让一追二（G1 输掉后仍赢整场）</th><th>系列赛</th><th>出现</th><th>基准率</th></tr></thead>
<tbody>{fmt_rows(st)}</tbody></table></div>
""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230071e3'/%3E%3Cpath d='M14 42l10-14 8 9 18-23' stroke='white' stroke-width='6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E">
<title>赔率形态结构库 · LoL + CS2（{args.updated}）</title>
<style>
:root{{--blue:#0071e3;--bg:#f5f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6e6e73;--bd:#e5e5ea;--red:#ff453a}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
.bar-top{{background:#1d1d1f;color:#fff;padding:14px 20px;display:flex;justify-content:space-between;align-items:center}}
.bar-top b{{font-weight:600}} .bar-top span{{color:#a1a1a6;font-size:13px}}
html.embed .bar-top{{display:none}} html.embed .wrap{{padding-top:14px}}
.wrap{{max-width:1100px;margin:0 auto;padding:22px 16px 60px}}
h1{{font-size:26px;margin:18px 0 6px;font-weight:600}} h2{{font-size:19px;margin:28px 0 10px;font-weight:600}}
.sub,.muted{{color:var(--mu)}} .sub{{margin:0 0 18px}}
.card{{background:var(--card);border-radius:14px;padding:16px;margin:10px 0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0 8px}}
.card .k{{font-size:12px;color:var(--mu)}} .card .v{{font-size:24px;font-weight:600;margin-top:4px}} .card .n{{font-size:12px;color:var(--mu);margin-top:2px}}
.pg{{display:block;background:var(--card);border-radius:14px;padding:14px 16px;margin:10px 0;text-decoration:none;color:var(--tx)}}
.pg b{{font-weight:600}} .pg div{{color:var(--mu);font-size:13px;margin-top:4px}}
.bar{{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:13px}}
.bl{{flex:0 0 210px;color:#3a3a3c}} .bt{{flex:1;background:#f2f2f7;height:10px;border-radius:5px;overflow:hidden}}
.bt i{{display:block;height:100%}} .bn{{flex:0 0 90px;text-align:right;color:var(--mu)}} .bn em{{font-style:normal;color:#a1a1a6;margin-left:6px}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--bd)}}
th{{color:var(--mu);font-weight:500;font-size:13px}} .pos{{color:#1a7f37;font-weight:600}} .neg{{color:var(--red);font-weight:600}}
.tbl{{padding:6px 10px;overflow-x:auto}}
li{{margin:5px 0}} .note{{background:#fff;border-radius:14px;padding:14px 18px}}
</style>
<script>if(location.search.indexOf("embed=1")>=0)document.documentElement.classList.add("embed");</script></head><body>
<div class="bar-top"><b>Polymarket 电竞情报库 · 赔率形态结构库</b><span>LoL / CS2 分开统计 · 生成 {args.updated}</span></div>
<div class="wrap">
<h1>赔率形态结构库（逐小局/逐图 + 整局）</h1>
<p class="sub">每场比赛的价格走势形态（A/B/C + 热门压制）沉淀进 docs/data/intel/price_paths.json，按胜方侧/负方侧分开统计。<b>只统计比赛内窗口</b>：赛前长平台、赛后结算平台、图间停滞均已剔除；LoL 与 CS2 的样本与统计不混算。</p>
<div class="cards">
<div class="card"><div class="k">形态库总量</div><div class="v">{total}</div><div class="n">price_paths.json 条目</div></div>
<div class="card"><div class="k">本批比赛</div><div class="v">{' / '.join(str(sum(len(d['matches']) for gg,d,_ in sections if gg==g)) for g in groups)}</div><div class="n">{' · '.join(label.get(g,g) for g in groups)}（不混算）</div></div>
<div class="card"><div class="k">含逐局形态</div><div class="v">{' / '.join(str(sum(len([m for m in d['matches'] if m['games']]) for gg,d,_ in sections if gg==g)) for g in groups)}</div><div class="n">{' · '.join(label.get(g,g) for g in groups)}；其余仅有整局形态</div></div>
<div class="card"><div class="k">数据粒度</div><div class="v">{' + '.join(k for k, _ in all_gran.most_common()) or '—'}</div><div class="n">{' · '.join(f'{k} {v} 场' for k, v in all_gran.most_common())}</div></div>
</div>

<h2>一、分段页面</h2>
{''.join(links)}
{''.join(game_blocks)}
<h2>三、期望值：机械抄底回测</h2>
<div class="card tbl"><table>
<thead><tr><th>游戏</th><th>粒度</th><th>阈值</th><th>整局 n</th><th>命中率</th><th>单笔 ROI</th><th>逐图/小局 n</th><th>命中率</th><th>单笔 ROI</th></tr></thead>
<tbody>{ev_html}</tbody></table></div>
<p class="muted" style="font-size:13px">口径：任一 side 的价格首次触及阈值价即买入，持有到结算；胜 = 结算价 ≥0.5 记 (1/买入价−1)，负 = −1。
<b>两档粒度分开算，禁止合并成一个结论</b>。两档的 n 与 ROI <u>不构成直接证据</u>，因为差异同时来自三处：
① 采样精度（60s 能看到 600s 采样点之间漏掉的深水触点）；② 窗口起点（两档都只算比赛内，但 60s 归档起点可能略晚于开赛）；
③ 2026-10-18 起一级赛事已全量回填 60s，不再存在“60s 只覆盖少量场次”的选择性偏差；
   但 60s 仍是采样价格（无成交分钟沿用前值），不是订单簿。
当前 60s 样本量：见上表 n 列；60s 分组只用于<b>观察</b>，不得用于策略取舍。
<b>结论必须按游戏、按粒度分开读</b>：CS2 的深水机械抄底仍呈明显负期望；LoL 在改为比赛内窗口后，部分阈值不再稳定为负。
这不等于"抄底可行"——它只说明旧全窗口口径下的"深水必负"不能直接套到新口径。任何下单仍必须叠加信号筛选与仓位纪律，禁止把两游戏 ROI 平均。</p>

<h2>四、口径与缺口</h2>
<div class="note"><ul>
<li><b>标签含义</b>（分类器 v1.3 实际会输出的 16 个）：A1 V型极值反转 / A2 中位U型反转 / A3 折价修复 / A4 下狗整场反转 / A5 W型双底 / A6 反弹确认 / A7 强强对话错杀 / B1 尾盘崩塌 / B2 死亡螺旋 / B3 假反弹后归零 / B4 直线阴跌 / B4 低开阴跌 / C1 中位震荡 / C2 五五开开局碾压 / C2 早期缩距·热门确立 / 热门全程压制。
<b>C3 中位震荡后突破</b>：形态手册（REVERSAL_PATTERN_LIBRARY.md）中有定义，但分类器尚未实现，当前不会输出（不应计入形态种类数）。</li>
<li><b>比赛内窗口（2026-10-18 起）</b>：所有形态统计只使用开赛到结算之间的价格段。起点 = 价格离开赛前基线后的第一段持续移动（向前保留约 3 分钟），终点 = 首次进入结算平台（≈100c / ≈0c）；第二局起以前一局结算后为下限，图间停滞不计入。旧的全窗口（含赛前 ~24-36h 与赛后平台）数据完整备份在 <code>reports/archive/pattern_backup_2026-10-18/</code>，<b>新旧口径不可直接混算</b>。</li>
<li><b>粒度（两档口径，勿混算）</b>：<b>600s</b> = 未回填/仅拿到 10 分钟采样的市场（一个小局/地图约 45 分钟 = 4-6 个点，标签只作方向参考）；<b>60s</b> = 用 <code>startTs/endTs + fidelity=1</code>（不传 interval）回溯的分钟级序列。<b>2026-10-18 起一级赛事已全量回填 60s</b>（LoL 231/231、CS2 62/62）。</li>
<li><b>分钟级可回溯（2026-10-18 修正）</b>：此前“超过 24 小时只能拿 600s”是旧工具固定传 <code>interval=1d</code> 导致的误判；不传 interval 时，2 个月前的老比赛仍可返回 60s（实测 7 天窗口 9981 点）。归档工具 tools/backfill_minute_snapshots.py（只写 .hires，不覆盖原 600s）。</li>
<li><b>结果</b>：以 Polymarket 结算价定胜负；比分由逐局归属统计，决胜局无独立市场时按整场结算归属补足（条目里注明）。</li>
<li><b>游戏不混算</b>：LoL 与 CS2 的赛制、地图/小局结构、盘口深度都不同；本页所有频次、深水比例、让一追二与抄底回测均按游戏分开，禁止合并成一个结论。</li>
<li><b>CS2 结构差异</b>：Polymarket 的 CS2 BO3 只开 Map 1 / Map 2 胜者盘（Map 3 只有 Rounds 盘），决胜图按整场归属补足，已在 result 标注。</li>
<li><b>赛事分级（2026-10-15 起）</b>：<b>本库只收一级（关键）赛事</b>——
一级 = LCK/LPL/LEC/LCP/LCS/KeSPA/德杯/国际赛；CS2 的 ESL Pro League/IEM/BLAST/EWC/Thunderpick 正赛。
EMEA Masters、小区域联赛、资格赛/升降级赛已按用户指令剔除（LoL −276 场、CS2 −7 场），
原始快照与旧全分级 JSON 仍留档、不参与出数。<br>分级口径：config/esports_tiers.json ·
判定 tools/esports_tier.py · 回归 tests/test_esports_tier.py ·
见 <a href='pattern_taxonomy.html'>形态分级体系</a> 与 <a href='pattern_playbook.html'>形态速查</a>。</li>
<li><b>尚未采集</b>：CS2 三级赛事（ESEA / CCT / Dust2.dk / United21 等）按已定口径<b>不纳入</b>；Thunderpick 正赛已并入一级口径（10-14 两场），PGL / Thunderpick 资格赛（约 56 场事件）<b>待定</b>；10-15 之后的比赛待赛后回补。</li>
</ul></div>
</div></body></html>
"""
    open(args.out, "w", encoding="utf-8").write(html)
    print(f"index -> {args.out}（形态库总量 {total}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
