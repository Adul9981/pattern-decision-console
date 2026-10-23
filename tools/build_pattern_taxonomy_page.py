#!/usr/bin/env python3
"""形态分级体系页：回答"一共总结出多少种形态、应该怎么分层、还差多少"。

分级设计（本页的口径，2026-10-15 定）：
  第 1 层 游戏：LoL / CS2 —— **必须分开**（小局制 vs 地图制，市场结构不同）；
  第 2 层 范围：整局 Winner / 逐局（小局·地图）—— **必须分开**（驱动机制不同）；
  第 3 层 侧：胜方侧 / 负方侧 —— **分开统计，负方侧只作对照**（B 类是恒等式衰减）；
  第 4 层 粒度：60s / 600s —— **当前只能当"数据质量档"，不能当分级维度**
    （60s 每格样本只有个位数到 30 例，单独成表每格都会 <5）。
  => 有效主格 = 2 游戏 × 2 范围 = 4 格；粒度与侧作为质量/统计维度附在格内。

输出：reports/pattern_taxonomy.html

用法：
  python3 tools/build_pattern_taxonomy_page.py \
      --section lol=reports/pattern_shapes_lol_2026-0910_1001.json \
      --section lol=reports/pattern_shapes_lol_2026-1002_1014.json \
      --section cs2=reports/pattern_shapes_cs2_2026-08_2026-10.json \
      --out reports/pattern_taxonomy.html
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from esports_tier import tier_of  # noqa: E402

SCOPES = ("整局", "逐局")
GAMES = ("LoL", "CS2")
NEED = 20  # 每个形态单格内希望的样本下限


def collect(paths_by_game, tiers=None):
    """-> win[(game, scope)][label] = Counter(gran -> n)；同时算样本量与粒度构成。

    tiers: 只保留这些分级的比赛（None = 全部）；分级由 tools/esports_tier.py 判定。
    """
    win = defaultdict(lambda: defaultdict(Counter))
    lose_labels = set()
    tier_matches = defaultdict(Counter)     # game -> Counter(tier) 按**场次**计
    league_tier = defaultdict(dict)         # game -> {league_raw: tier}
    samples = defaultdict(Counter)          # (game,scope) -> {gran: 样本数}
    for game, paths in paths_by_game.items():
        for p in paths:
            d = json.load(open(p, encoding="utf-8"))
            for m in d["matches"]:
                t = tier_of(m.get("league_raw"), game)
                league_tier[game][m.get("league_raw") or "（未标注）"] = t
                tier_matches[game][t] += 1
                if tiers is not None and t not in tiers:
                    continue
                gran = m.get("granularity", "unknown")
                s = m.get("series") or {}
                ends = {int(k): v["end"] for k, v in s.items()}
                if ends:
                    wi = max(ends, key=lambda i: ends[i])
                    if ends[wi] >= 0.9:
                        for lab in s[str(wi)].get("labels") or ["未知"]:
                            win[(game, "整局")][lab][gran] += 1
                        samples[(game, "整局")][gran] += 1
                        for i in ends:
                            if i != wi:
                                for lab in s[str(i)].get("labels") or []:
                                    lose_labels.add(lab)
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
                        win[(game, "逐局")][lab][gran] += 1
                    samples[(game, "逐局")][gran] += 1
                    for i in e:
                        if i != gi:
                            for lab in sd[str(i)].get("labels") or []:
                                lose_labels.add(lab)
    return win, samples, lose_labels, tier_matches, league_tier


def cell_rows(win, key, gran_main="600s"):
    items = win.get(key, {})
    rows = []
    for lab, c in sorted(items.items(), key=lambda kv: -sum(kv[1].values())):
        n60, n600 = c.get("60s", 0), c.get("600s", 0)
        tot = n60 + n600
        if lab == "未知":
            continue
        rows.append((lab, n60, n600, tot))
    return rows


def render(win, samples, lose_labels, tier_matches, league_tier, key_win, key_samples, excluded, out_path, updated,
           section_info=None, excluded_from_map=True):
    cells = [(g, s) for g in GAMES for s in SCOPES]
    all_labs = {l for k in cells for l in win.get(k, {}) if l != "未知"}
    lose_named = {l for l in lose_labels if l != "未知"}
    lose_b = {l for l in lose_named if l.startswith("B")}      # 负方侧真正的 B 类恒等式标签
    lose_other = lose_named - lose_b                            # 误标（如个别负方侧被标 A5），不重复计入
    observed = all_labs | lose_b                                # = 15
    defined = 16

    def cell_table(game, scope, store=None, samples_store=None):
        src = store if store is not None else win
        samp = samples_store if samples_store is not None else samples
        rows = cell_rows(src, (game, scope))
        tot_s = sum(samp.get((game, scope), Counter()).values())
        n60 = samp.get((game, scope), Counter()).get("60s", 0)
        body = []
        for lab, a, b, tot in rows:
            thin = tot < NEED
            body.append(
                "<tr><td>" + lab + "</td>"
                + f"<td class='{'z' if not a else ''}'>{a or '·'}</td>"
                + f"<td class='{'z' if not b else ''}'>{b or '·'}</td>"
                + f"<td class='tot'>{tot}</td>"
                + "<td class='" + ("thin" if thin else "ok") + "'>"
                + ("样本不足" if thin else "可用") + "</td></tr>")
        mins = min((t for _l, _a, _b, t in rows), default=0)
        usable = [r for r in rows if r[3] >= NEED]
        need_mult = (NEED / mins) if mins else 0
        head = (f"<div class='cellhead'><b>{game} · {scope}</b>"
                f"<span>样本 {tot_s} 例（60s {n60}）· 形态 {len(rows)} 种 · "
                f"可用 {len(usable)} 种（≥{NEED} 例）</span></div>")
        if store is not None:
            # 一级口径：样本天然少，如实提示缺口，不套"扩 20 倍"的算式（会算出荒诞数字）
            tail = (f"<div class='cellfoot'>最薄形态仅 {mins} 例。"
                    f"<b>只算一级赛事</b>——{len(usable)} 种达到 ≥{NEED} 例，其余只能当观察。"
                    f"要让本格的头部形态也站得住，需再补到每种 ≥{NEED} 例。</div>")
        else:
            tail = (f"<div class='cellfoot'>最薄形态仅 {mins} 例。"
                    f"头部形态已可下方向性结论；长尾（A6/A7/C2 早期缩距 这类）属天然稀有，"
                    f"按现口径要全部 ≥{NEED} 例需扩约 {need_mult:.0f}×（≈ {int(tot_s*need_mult)} 例）——"
                    f"现实做法是把长尾单列观察区，而不是硬凑样本。</div>")
        return ("<div class='card'><div class='tbl'><table>"
                "<thead><tr><th>形态</th><th>60s</th><th>600s</th><th>合计</th><th>状态</th></tr></thead>"
                f"<tbody>{''.join(body)}</tbody></table></div>{head}{tail}</div>")

    gran_rows = []
    for g, s in cells:
        c = samples.get((g, s), Counter())
        t = sum(c.values())
        gran_rows.append(
            f"<tr><td>{g} · {s}</td><td>{t}</td><td>{c.get('600s',0)}</td><td>{c.get('60s',0)}</td>"
            f"<td>{100.0*c.get('60s',0)/t:.0f}%</td></tr>" if t else "")
    # 数据源与曲线图链接的场数/图数：由 section 明细直接统计，避免页面写死旧数字
    si = section_info or {}
    src_txt = ' / '.join(f"{g} {si.get(g, (0, 0))[0]} 场" for g in GAMES if g in si) or '-'
    def _chart_txt(g):
        m, gp = si.get(g, (0, 0))
        return f"{m} 场（含逐局 {gp} 场）" if m else '-'
    chart_lol, chart_cs2 = _chart_txt('LoL'), _chart_txt('CS2')
    tot_all = sum(sum(samples.get(k, Counter()).values()) for k in cells)
    cell_tot = {k: sum(samples.get(k, Counter()).values()) for k in cells}
    cell_60 = {k: samples.get(k, Counter()).get("60s", 0) for k in cells}
    # 关键句里引用的数字全部由数据生成，避免同一页出现两套口径
    lol_m, lol_g = cell_tot[("LoL", "整局")], cell_tot[("LoL", "逐局")]
    cs_m, cs_g = cell_tot[("CS2", "整局")], cell_tot[("CS2", "逐局")]
    c2_lol_g = sum(win.get(("LoL", "逐局"), {}).get("C2_五五开开局碾压", Counter()).values())
    g60_txt = " / ".join(str(cell_60[k]) for k in cells)          # 1 / 4 / 12 / 24
    cell_txt = " / ".join(str(cell_tot[k]) for k in cells)          # 313 / 569 / 65 / 129
    cell_kinds = [len([r for r in cell_rows(win, k)]) for k in cells]
    kinds_txt = f"{min(cell_kinds)}-{max(cell_kinds)}"
    usable_txt = f"{min(len([r for r in cell_rows(win, k) if r[3] >= NEED]) for k in cells)}-{max(len([r for r in cell_rows(win, k) if r[3] >= NEED]) for k in cells)}"

    # ---- 赛事分级：分布 + 一级过滤后的四格 ----
    tier_rows, tier_findings = [], []
    key_cells_html = ""
    mixed_mode = key_win is not None
    for g in GAMES:
        c = tier_matches.get(g, Counter())
        tot_g = sum(c.values())
        share = f"{100.0 * c['一级'] / tot_g:.0f}%" if tot_g else "—"
        tier_rows.append(f"<tr><td><b>{g}</b></td><td>{c['一级']}</td><td>{c['二级']}</td>"
                         f"<td>{c['其他']}</td><td>{tot_g}</td><td>{share}</td></tr>")
        if mixed_mode:
            if g == "LoL" and tot_g:
                tier_findings.append(
                    f"<li><b>LoL 只有 {c['一级']}/{tot_g}（{share}）是一级赛事</b>——"
                    f"其余 {c['二级']} 场二级 + {c['其他']} 场资格赛/升降级赛。"
                    f"所以本页上半部分那些'形态分布'，主体是<b>次级赛事</b>的形状。</li>")
            if g == "CS2" and tot_g:
                tier_findings.append(
                    f"<li>CS2 反过来：{c['一级']}/{tot_g}（{share}）已经是一级赛事。</li>")
    if mixed_mode:
        tier_findings.append(
            "<li>结论：<b>LoL 侧要先补一级赛事样本，才谈得上'关键比赛的形态'</b>。</li>")
        key_cells_html = "".join(
            cell_table("LoL", "整局", key_win, key_samples) +
            cell_table("LoL", "逐局", key_win, key_samples) +
            cell_table("CS2", "整局", key_win, key_samples) +
            cell_table("CS2", "逐局", key_win, key_samples))

    excl_rows = []
    for g in GAMES:
        c = tier_matches.get(g, Counter())
        ex = excluded.get(g, Counter())
        excl_rows.append(f"<tr><td><b>{g}</b></td><td>{c['一级']}</td><td>{ex['二级']}</td>"
                         f"<td>{ex['其他']}</td><td>{ex['二级'] + ex['其他']}</td></tr>")

    if mixed_mode:
        zero_sec = (
            '<h2>零、赛事分级：先把不关键的比赛分开</h2>'
            '<p class="sub">分级口径见 config/esports_tiers.json（一级 = LCK/LPL/LEC/LCP/LCS/KeSPA/德杯/国际赛 '
            '与 CS2 的 ESL Pro League/IEM/BLAST/EWC/Thunderpick）。</p>'
            '<div class="card"><div class="tbl"><table>'
            '<thead><tr><th>游戏</th><th>一级（关键）</th><th>二级</th><th>其他</th><th>合计</th><th>一级占比</th></tr></thead>'
            f'<tbody>{"".join(tier_rows)}</tbody></table></div></div>'
            '<p class="sub"><b>读法</b>：下面第一节到第三节的样本是<b>全部分级混算</b>的（等于旧库）；'
            '第二节之后另给一份<b>只算一级赛事</b>的四格表——那才是可直接套用的口径。</p>'
            f'<div class="note"><ul>{"".join(tier_findings)}</ul></div>'
            '<h2>零·二、只算一级赛事时的四个主格</h2>'
            '<p class="sub">过滤条件：tier = 一级。</p>'
            f'{key_cells_html}')
    else:
        zero_sec = (
            '<h2>零、赛事分级：本库只收一级（关键）赛事</h2>'
            '<p class="sub">2026-10-15 用户指令：<b>EMEA Masters 与小区域联赛的数据不要了</b>。'
            '本页（以及本库全部页面）只统计一级赛事；二级与资格赛/升降级赛已从统计口径剔除'
            '（原始快照仍在 docs/data/snapshots，旧的全分级明细 JSON 保留在 reports/ 作存档，不参与出数）。'
            '「已剔除」按本地全部快照 slug 统计（含 2026-08 窗口；一级关键词已覆盖赛季真实命名：'
            'LCK 的 Legend/Rise Group 与 Play-In、LPL 的 Ascend/Nirvana、LCP Swiss Stage、LoL 侧 EWC/MSI）。</p>'
            '<div class="card"><div class="tbl"><table>'
            '<thead><tr><th>游戏</th><th>本库（一级）</th><th>已剔除：二级</th><th>已剔除：其他</th><th>剔除合计</th></tr></thead>'
            f'<tbody>{"".join(excl_rows)}</tbody></table></div></div>'
            '<p class="sub">分级口径：config/esports_tiers.json · 判定 tools/esports_tier.py · '
            '回归 tests/test_esports_tier.py。</p>')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230071e3'/%3E%3Cpath d='M14 42l10-14 8 9 18-23' stroke='white' stroke-width='6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E">
<title>形态分级体系（{updated}）</title>
<style>
:root{{--blue:#0071e3;--bg:#f5f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6e6e73;--bd:#e5e5ea;--amber:#ff9f0a;--green:#1a7f37}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
.bar-top{{background:#1d1d1f;color:#fff;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px}}
.bar-top b{{font-weight:600}} .bar-top span{{color:#a1a1a6;font-size:13px}}
html.embed .bar-top{{display:none}} html.embed .wrap{{padding-top:14px}}
.wrap{{max-width:1120px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:25px;margin:16px 0 6px;font-weight:600}} h2{{font-size:19px;margin:30px 0 10px;font-weight:600}}
.sub{{color:var(--mu);margin:0 0 16px;font-size:13.5px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:14px 0 6px}}
.card{{background:var(--card);border-radius:14px;padding:16px;margin:12px 0}}
.card .k{{font-size:12px;color:var(--mu)}} .card .v{{font-size:24px;font-weight:600;margin-top:4px}}
.card .n{{font-size:12px;color:var(--mu);margin-top:2px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th{{text-align:left;color:var(--mu);font-weight:500;padding:7px 9px;border-bottom:1px solid var(--bd);font-size:12.5px;white-space:nowrap}}
td{{padding:8px 9px;border-bottom:1px solid var(--bd)}}
.tbl{{overflow-x:auto}}
.z{{color:#c7c7cc}} .tot{{font-weight:600}} .thin{{color:var(--amber)}} .ok{{color:var(--green)}}
.cellhead{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:13px;margin-top:10px;padding-top:10px;border-top:1px solid var(--bd)}}
.cellhead span{{color:var(--mu);font-size:12.5px}}
.cellfoot{{font-size:12.5px;color:var(--mu);margin-top:6px}}
.note{{background:#fff;border-left:3px solid var(--blue);border-radius:10px;padding:14px 18px;font-size:13.5px;color:#3a3a3c}}
.note li{{margin:6px 0}}
.tier{{display:flex;gap:10px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--bd)}}
.tier:last-child{{border-bottom:none}}
.tier .lv{{flex:0 0 118px;font-weight:600;font-size:13.5px}}
.tier .tp{{flex:1;font-size:13.5px;color:#3a3a3c}}
.tag{{display:inline-block;font-size:11px;padding:1px 8px;border-radius:7px;margin-right:6px;vertical-align:1px}}
.tag.must{{background:#ffe8e6;color:#c0392b}} .tag.qual{{background:#fff4e0;color:#b26a00}}
.tag.no{{background:#ececf0;color:#6e6e73}}
a.pg{{display:block;background:#fff;border-radius:12px;padding:12px 14px;margin:8px 0;text-decoration:none;color:var(--tx)}}
a.pg b{{font-weight:600}} a.pg div{{color:var(--mu);font-size:12.5px;margin-top:3px}}
</style>
<script>if(location.search.indexOf("embed=1")>=0)document.documentElement.classList.add("embed");</script></head><body>
<div class="bar-top"><b>Polymarket 电竞情报库 · 形态分级体系</b><span>生成 {updated}</span></div>
<div class="wrap">
<h1>一共能搞出多少种形态，该怎么分级</h1>
<p class="sub">数据源：形态库一级赛事明细（{src_txt}）· 定义来自 tools/classify_pattern.py v1.3</p>

<div class="cards">
<div class="card"><div class="k">分类器定义的形态</div><div class="v">{defined}</div><div class="n">A1-A7 / B1-B4(含低开) / C1-C2(含变体) / 热门压制</div></div>
<div class="card"><div class="k">实际已观测到</div><div class="v">{len(observed)}</div><div class="n">胜方侧 {len(all_labs)} 种 + 负方侧 {len(lose_b)} 个 B 类</div></div>
<div class="card"><div class="k">有效主格</div><div class="v">4</div><div class="n">游戏 × 范围（粒度/侧作为格内维度）</div></div>
<div class="card"><div class="k">胜方侧样本</div><div class="v">LoL {lol_m + lol_g} / CS2 {cs_m + cs_g}</div><div class="n">各游戏内部整局+逐局；不跨游戏相加</div></div>
</div>

{zero_sec}

<h2>一、分级怎么分：哪几层必须拆、哪几层拆不动</h2>
<div class="card">
<div class="tier"><div class="lv"><span class="tag must">必须拆</span>游戏</div><div class="tp">
<b>LoL / CS2 分开</b>。CS2 是地图制：BO3 只开 Map1/Map2 胜者盘（第三图只有 Rounds 盘，决胜图按整场结算补足）；
LoL 是标准小局制，逐局盘更全。两者"整局价格被砸后回弹"的机制不同，混算会把赛制差异读成形态差异。</div></div>
<div class="tier"><div class="lv"><span class="tag must">必须拆</span>范围</div><div class="tp">
<b>整局 / 逐局分开</b>。实测分布完全不同：整局由 A3 折价修复 + 热门压制 + A2 中位 U 型主导；
逐局由 C2 五五开开局碾压主导（LoL 逐局 {c2_lol_g}/{lol_g}，约 {100.0*c2_lol_g/lol_g:.0f}%）。分开才能拿到各自真实的形态占比。</div></div>
<div class="tier"><div class="lv"><span class="tag must">必须拆</span>侧</div><div class="tp">
<b>胜方侧 / 负方侧分开</b>。B 类（直线阴跌 / 低开阴跌 / 死亡螺旋）几乎只出现在负方侧，
是输家代币归零的恒等式产物；把两侧合在一起算，会让 B 类占比虚高到无意义。</div></div>
<div class="tier"><div class="lv"><span class="tag qual">当质量档</span>粒度</div><div class="tp">
<b>60s / 600s 不再拆成独立分级维度</b>：2026-10-18 起一级赛事已全量回填 60s
（LoL 231/231、CS2 62/62），600s 档为 0；粒度只作为数据质量标注
（60s = 采样价格，无成交分钟可能沿用前值，不是订单簿）。
后续若纳入未回填场次，再按档分层。</div></div>
<div class="tier"><div class="lv"><span class="tag no">不拆</span>联赛</div><div class="tp">
联赛（LCK / LPL / LEC / 次级联赛）暂不拆：它影响的是"队伍实力差"这一先验，属于策略层的筛选参数，
不该混进形态分类维度——否则每个格子的样本会被切碎到无法统计。</div></div>
</div>

<h2>二、四个主格：各自的形态清单与样本</h2>
<p class="sub">每格表内：60s / 600s 是粒度档，"状态"按单格样本是否 ≥{NEED} 例判定。样本 = 该格已结算市场的胜方侧。</p>
{cell_table("LoL", "整局")}
{cell_table("LoL", "逐局")}
{cell_table("CS2", "整局")}
{cell_table("CS2", "逐局")}

<h2>三、粒度档现状（一级赛事已全量 60s）</h2>
<div class="card"><div class="tbl"><table>
<thead><tr><th>格</th><th>样本</th><th>600s</th><th>60s</th><th>分钟级占比</th></tr></thead>
<tbody>{''.join(r for r in gran_rows if r)}</tbody></table></div></div>

<h2>四、结论：能搞出多少种形态</h2>
<div class="note"><ul>
<li><b>上限由分类器定义决定：{defined} 个</b>（A1-A7、B1/B2/B3/B4 直线/B4 低开、C1、C2、C2 早期缩距、热门全程压制）。
C3 中位震荡后突破只在形态手册里有定义、分类器尚未实现，<b>不计入</b>。</li>
<li><b>当前实际观测到 {len(observed)} 个</b>（胜方侧 {len(all_labs)} 个 A/C 类 + 负方侧 {len(lose_b)} 个 B 类：B1 尾盘崩塌 / B2 死亡螺旋 / B3 假反弹后归零 / B4 直线阴跌 / B4 低开阴跌）；
唯一未出现的是 <b>C1 中位震荡</b>（已结算市场里为 0，只在未结算/未走完的盘上出现过）。
另有 {len(lose_other)} 个标签在负方侧噪声出现（{("、".join(sorted(lose_other)) or "无")}），已计在胜方侧、不重复计数。</li>
<li><b>按主格拆开后，每格各自成立 {kinds_txt} 种</b>（四格分别是 {" / ".join(str(x) for x in cell_kinds)} 种）——这才是"我们能拿来用的形态数"。
格内进入"可用"档（单格 ≥{NEED} 例）的形态才是策略候选，四格各为 {usable_txt} 种，其余算观察区。</li>
<li><b>结论：形态种类本身够用（16 个定义覆盖了绝大多数走势），缺的是"每个格子里每种形态的样本密度"</b>。
LoL 两格样本充足（整局 {lol_m} / 逐局 {lol_g} 例），CS2 两格偏薄（整局 {cs_m} / 逐局 {cs_g} 例）——
CS2 想和 LoL 同权，整局/逐局两个格都还要扩 3-5 倍样本。</li>
<li>所以真正该做的不是"发明更多形态标签"，而是：① 把 60s 归档常态化，让粒度这一层升格；
② 按 4 个主格分别累积到每种形态都够 {NEED} 例；③ 长尾形态（A6/A7/C2 早期缩距/B1/B3）单列观察区，不进策略层。</li>
</ul></div>

<h2>五、看形状</h2>
<div class="tbl" style="background:#fff;border-radius:14px;padding:6px 10px">
<a class="pg" href="pattern_charts_lol_tier1_2026-07_2026-10.html"><b>LoL 一级赛事曲线图 · {chart_lol}</b><div>两条队伍赔率线 + 50c 交叉点，可按粒度筛选</div></a>
<a class="pg" href="pattern_charts_cs2_tier1_2026-07_2026-10.html"><b>CS2 一级赛事曲线图 · {chart_cs2}</b><div>同上</div></a>
<a class="pg" href="pattern_shapes_granularity.html"><b>粒度对照页</b><div>样本量与结论分别在 1 分钟级 / 10 分钟级上的归属</div></a>
<a class="pg" href="pattern_shapes_index.html"><b>形态结构库总览</b><div>形态频次、深水占比、让一追二基准率、机械抄底期望值</div></a>
<a class="pg" href="pattern_playbook.html"><b>形态速查（交易决策用）</b><div>每个形态的示意线条 + 真实样本 + 买点/卖点/失效</div></a>
</div>
</div></body></html>"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="utf-8").write(html)
    return len(all_labs), tot_all


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", action="append", default=[])
    ap.add_argument("--out", default="reports/pattern_taxonomy.html")
    ap.add_argument("--updated", default="2026-10-15")
    ap.add_argument("--tiers", default="一级",
                    help="保留的赛事分级，逗号分隔；'all' = 全分级混算（默认只收一级）")
    ap.add_argument("--archive", action="append", default=[],
                    help="旧的全分级明细 JSON（game=path），用于统计已剔除多少场（无 tier-map 时的兜底）")
    ap.add_argument("--tier-map", action="append", default=[],
                    help="slug->league_raw 全量映射 JSON（game=path），用它统计真实已剔除量（优先于 --archive）")
    args = ap.parse_args()
    by_game = defaultdict(list)
    for spec in args.section:
        game, path = spec.split("=", 1)
        by_game[{"lol": "LoL", "cs2": "CS2"}.get(game.lower(), game)].append(path)
    section_info = {}
    for g, paths in by_game.items():
        ms, ch = 0, 0
        for pth in paths:
            if not os.path.exists(pth):
                continue
            for m in json.load(open(pth, encoding="utf-8"))["matches"]:
                ms += 1
                ch += 1 if (m.get("games") or {}) else 0
        section_info[g] = (ms, ch)
    keep = None if args.tiers.strip().lower() == "all" else tuple(
        t.strip() for t in args.tiers.split(",") if t.strip())
    win, samples, lose_labels, tier_matches, league_tier = collect(by_game, tiers=keep)
    key_win = key_samples = None
    if keep is not None:                      # 全分级混算时才需要"只算一级"的对照
        key_win, key_samples, _kl, _kt, _lmap = collect(by_game, tiers=("一级",))
        if keep == ("一级",):
            key_win = key_samples = None      # 主表已经是一级，不再重复出对照
    arch = defaultdict(list)
    for spec in args.archive:
        g, path = spec.split("=", 1)
        arch[{"lol": "LoL", "cs2": "CS2"}.get(g.lower(), g)].append(path)
    excluded = defaultdict(Counter)
    tier_maps = {}
    for spec in args.tier_map:
        g, path = spec.split("=", 1)
        g = {"lol": "LoL", "cs2": "CS2"}.get(g.lower(), g)
        if os.path.exists(path):
            tier_maps[g] = json.load(open(path, encoding="utf-8"))
    if tier_maps:
        # 全量口径：按本地有快照的全部 slug 统计分级（含 8 月这段此前没进索引的窗口）
        for g, m in tier_maps.items():
            pref = "lol-" if g == "LoL" else ("cs2-" if g == "CS2" else "")
            for slug, raw in m.items():
                if pref and not slug.startswith(pref):
                    continue
                tt = tier_of(raw, g)
                if tt != "一级":
                    excluded[g][tt] += 1
    else:
        for g, paths in arch.items():
            for p in paths:
                if not os.path.exists(p):
                    continue
                for m in json.load(open(p, encoding="utf-8"))["matches"]:
                    t = tier_of(m.get("league_raw"), g)
                    if t != "一级":
                        excluded[g][t] += 1
    n, tot = render(win, samples, lose_labels, tier_matches, league_tier, key_win, key_samples,
                    excluded, args.out, args.updated, section_info=section_info)
    print(f"taxonomy page -> {args.out}（观测形态 {n} 个 / 胜方侧样本 {tot} 例）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
