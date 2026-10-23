#!/usr/bin/env python3
"""形态频率榜：按"覆盖比赛数 / 出现次数"给形态排序，并能展开看历史案例。

为什么要有它（2026-10-19）：
    形态速查页回答"这个形态长什么样、怎么买卖"；本页回答两个更基础的问题：
      1) 哪个形态最常出现（按覆盖比赛数排序，而不是只数标签次数）；
      2) 同一个形态在不同比赛里的具体样子（哪场、哪局、哪一侧、价格路径）。

统计口径：
    - 数据源与形态库、曲线图页完全一致（reports/pattern_shapes_{lol,cs2}_tier1_*.json）；
    - 60 秒级、仅比赛内窗口；
    - 一个"出现"= 某场比赛某个市场（整局 / 某小局）的某一侧价格路径命中该标签；
      同一场比赛双方、多个小局命中同一形态会被计多次，所以必须同时看：
        覆盖比赛数（去重 slug）与 标签次数（side-path 次数）；
    - 只统计已结算、已走完的路径。反转类形态（A2/A3/A4…）的终点天然接近 100c，
      其"终点胜率 100%"是标签定义的结果，不是买入胜率，不能当策略期望。

用法：
    python3 tools/build_pattern_frequency_index.py
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")

SECTIONS = [
    ("LoL", "reports/pattern_shapes_lol_tier1_2026-07_2026-10.json",
     "pattern_charts_lol_tier1_2026-07_2026-10.html"),
    ("CS2", "reports/pattern_shapes_cs2_tier1_2026-07_2026-10.json",
     "pattern_charts_cs2_tier1_2026-07_2026-10.html"),
]

# 形态释义直接复用形态速查页的定义，避免两页口径漂移。
try:
    from build_pattern_playbook import P as PLAYBOOK  # noqa: E402
except Exception:  # noqa: BLE001
    PLAYBOOK = {}

FAMILY_CN = {"反转": "反转", "确立": "确立", "压制": "压制", "崩塌": "崩塌", "震荡": "震荡"}


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.median(xs) if xs else None


def fmt(v, digits=2):
    return "-" if v is None else f"{v:.{digits}f}"


def pct(v):
    return "-" if v is None else f"{v * 100:.0f}%"


def load(game, path):
    d = json.load(open(os.path.join(ROOT, path), encoding="utf-8"))
    buckets = defaultdict(list)         # (scope,label) -> cases
    scope_totals = defaultdict(int)     # scope -> occurrences
    match_counts = defaultdict(set)     # (scope,label) -> slugs
    total_matches = 0
    for m in d.get("matches") or []:
        total_matches += 1
        slug = m.get("slug") or ""
        base = {
            "slug": slug,
            "date": m.get("date") or "",
            "league": m.get("league") or "-",
            "league_raw": m.get("league_raw") or "",
            "result": m.get("result") or "",
            "format": m.get("format") or "",
        }
        ser = m.get("series") or {}
        for side, info in ser.items():
            for lab in info.get("labels") or []:
                if lab == "未知":
                    continue
                case = dict(base, scope="整局", stage="整局", side=side, kind="series",
                            team=info.get("team") or side,
                            pre=info.get("pre"), low=info.get("low"), high=info.get("high"),
                            end=info.get("end"), low_time=info.get("low_time"),
                            span=info.get("span_min"), conf=info.get("confidence"),
                            spacing=info.get("spacing_s"), points=info.get("points"),
                            dur30=(info.get("dur_below") or {}).get("30"))
                buckets[("整局", lab)].append(case)
                scope_totals["整局"] += 1
                match_counts[("整局", lab)].add(slug)
        unresolved = {str(x) for x in (m.get("games_unresolved") or [])}
        for g, sides in (m.get("games") or {}).items():
            if str(g) in unresolved:
                # 无独立市场/无成交的小局（价格停在 50c 左右）不是有效样本。
                continue
            for side, info in sides.items():
                for lab in info.get("labels") or []:
                    if lab == "未知":
                        continue
                    case = dict(base, scope="逐局", stage=f"G{g}", side=side, kind="game", game=int(g),
                                team=info.get("team") or side,
                                pre=info.get("pre"), low=info.get("low"), high=info.get("high"),
                                end=info.get("end"), low_time=info.get("low_time"),
                                span=info.get("span_min"), conf=info.get("confidence"),
                                spacing=info.get("spacing_s"), points=info.get("points"),
                                dur30=(info.get("dur_below") or {}).get("30"))
                    buckets[("逐局", lab)].append(case)
                    scope_totals["逐局"] += 1
                    match_counts[("逐局", lab)].add(slug)
    return dict(buckets=buckets, totals=scope_totals, matches=match_counts,
                total_matches=total_matches, meta=d)


def label_cn(lab):
    return lab.split("_", 1)[-1] if "_" in lab else lab


def label_code(lab):
    return lab.split("_", 1)[0] if "_" in lab else ""


def freq_tier(n: int):
    """按覆盖比赛数分级——这是用户要的"按出现多少分类"，不是按字母。"""
    if n >= 80:
        return "S", "t-s"
    if n >= 40:
        return "A", "t-a"
    if n >= 20:
        return "B", "t-b"
    if n >= 5:
        return "C", "t-c"
    return "D", "t-d"


def render_case_table(rows, game, chart_file):
    out = []
    for r in rows:
        # 曲线图页的锚点：整局 <slug>-series，小局 <slug>-g<N>；整场卡片是 m-<slug>。
        anchor = f"{r['slug']}-{'series' if r['kind'] == 'series' else 'g' + str(r['game'])}"
        href = f"{chart_file}#{anchor}"
        path = f"{fmt(r['pre'])} → {fmt(r['low'])} → {fmt(r['high'])} → {fmt(r['end'])}"
        dur = "" if r.get("dur30") is None else f"{r['dur30']:.0f}m"
        league = r["league_raw"] or r["league"]
        out.append(
            "<tr>"
            f"<td class='nw'>{html.escape(r['date'])}</td>"
            f"<td class='nw'>{html.escape(league)}</td>"
            f"<td><a href='{href}' target='_blank' rel='noopener'>{html.escape(r['slug'])}</a>"
            f"<div class='sm'>{html.escape(r['result'])}</div></td>"
            f"<td class='nw'><b>{html.escape(r['stage'])}</b></td>"
            f"<td class='nw'>{html.escape(str(r['team']))}</td>"
            f"<td class='nw mono'>{path}</td>"
            f"<td class='nw'>{html.escape(str(r['low_time'] or '-'))}</td>"
            f"<td class='nw'>{dur or '-'}</td>"
            f"<td class='nw'>{html.escape(str(r['conf'] or '-'))}</td>"
            "</tr>")
    return "".join(out)


def build():
    games = {}
    for game, path, chart_file in SECTIONS:
        games[game] = (load(game, path), chart_file)

    blocks = []
    summary_cards = []
    for game, (data, chart_file) in games.items():
        for scope in ("逐局", "整局"):
            rows = []
            total_occ = data["totals"].get(scope, 0)
            for (sc, lab), cases in data["buckets"].items():
                if sc != scope:
                    continue
                slugs = data["matches"][(sc, lab)]
                ends = [c["end"] for c in cases]
                rows.append({
                    "label": lab,
                    "matches": len(slugs),
                    "occ": len(cases),
                    "share": len(cases) / total_occ if total_occ else 0,
                    "med_low": med([c["low"] for c in cases]),
                    "med_end": med(ends),
                    "med_pre": med([c["pre"] for c in cases]),
                    "med_span": med([c["span"] for c in cases]),
                    "near100": (sum(1 for e in ends if isinstance(e, (int, float)) and e >= 0.95) / len(cases)) if cases else None,
                    "cases": sorted(cases, key=lambda c: (c["date"], c["slug"], c["stage"]), reverse=True),
                })
            rows.sort(key=lambda r: (-r["matches"], -r["occ"], r["label"]))
            max_matches = rows[0]["matches"] if rows else 1
            cards = []
            for rank, r in enumerate(rows, 1):
                pb = PLAYBOOK.get(r["label"]) or {}
                code = label_code(r["label"])
                name = label_cn(r["label"])
                fam = pb.get("family") or ""
                thin = " <span class='tag thin'>样本不足</span>" if r["matches"] < 5 else ""
                tier, tier_cls = freq_tier(r["matches"])
                width = max(3.0, 100.0 * r["matches"] / max_matches)
                cards.append(f"""
<details class="pat" data-search="{html.escape((r['label'] + ' ' + ' '.join(sorted({c['league_raw'] or c['league'] for c in r['cases']}))).lower())}" data-matches="{r['matches']}" data-occ="{r['occ']}">
  <summary>
    <span class="rk">{rank:02d}</span>
    <span class="nm"><b>{html.escape(name)}</b><small><i class="ft {tier_cls}">{tier}</i>{html.escape(code)} · {html.escape(fam)}</small>{thin}</span>
    <span class="bar"><i style="width:{width:.1f}%"></i></span>
    <span class="kpi"><b>{r['matches']}</b><small>覆盖场次</small></span>
    <span class="kpi"><b>{r['occ']}</b><small>标签次数</small></span>
    <span class="kpi"><b>{pct(r['share'])}</b><small>占本组</small></span>
    <span class="cr">›</span>
  </summary>
  <div class="body">
    <div class="shape">
      <div><span class="lbl">线条特征</span><p>{html.escape(pb.get('shape') or '未登记形状说明。')}</p></div>
      <div><span class="lbl">买点</span><p>{html.escape(pb.get('entry') or '-')}</p></div>
      <div><span class="lbl">卖点 / 处理</span><p>{html.escape(pb.get('exit') or '-')}</p></div>
      <div><span class="lbl">失效条件</span><p>{html.escape(pb.get('invalid') or '-')}</p></div>
    </div>
    <div class="stats">
      <span>中位：赛前 → 低点 → 终点<b>{fmt(r['med_pre'])} → {fmt(r['med_low'])} → {fmt(r['med_end'])}</b></span>
      <span>低点中位<b>{fmt(r['med_low'])}</b></span>
      <span>终点≥95c 占比<b>{pct(r['near100'])}</b></span>
      <span>窗口时长中位<b>{fmt(r['med_span'],0)} 分钟</b></span>
      <span>案例<b>{len(r['cases'])} 条</b></span>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th>日期</th><th>联赛</th><th>比赛 / 结果</th><th>局</th><th>命中侧</th><th>赛前→低→高→终</th><th>低点</th><th>≤30c</th><th>置信</th></tr></thead>
        <tbody>{render_case_table(r['cases'], game, chart_file)}</tbody>
      </table>
    </div>
    <p class="fine">点比赛名可跳到该场曲线图（{html.escape(chart_file)}，锚点定位到整局/对应小局）。反转类形态的终点被标签条件限定在高位，"终点≥95c 占比"只描述样本，不代表买入胜率。</p>
  </div>
</details>""")
            blocks.append(f"""
<section class="pane" data-game="{game}" data-scope="{scope}">
  <div class="pane-h"><h2>{game} · {scope}</h2>
    <p>{data['total_matches']} 场比赛 · 本组 {total_occ} 次标签 · {len(rows)} 种形态 · 按覆盖场次降序
    <span class="muted">｜频次分级：S ≥80 场 · A 40–79 · B 20–39 · C 5–19 · D &lt;5（样本不足）</span></p></div>
  <div class="sort"><button class="sbtn on" data-sort="matches">按覆盖场次</button><button class="sbtn" data-sort="occ">按标签次数</button></div>
  <div class="search"><input type="search" placeholder="筛选形态 / 联赛…" data-filter="{game}-{scope}"></div>
  {''.join(cards) if cards else '<div class="card">本组暂无样本。</div>'}
</section>""")
            if scope == "逐局":
                summary_cards.append((game, scope, rows, total_occ, data["total_matches"]))
            else:
                summary_cards.append((game, scope, rows, total_occ, data["total_matches"]))

    top_lines = []
    for game, scope, rows, total_occ, n_matches in summary_cards:
        if not rows:
            continue
        top = rows[0]
        top3 = rows[:3]
        share3 = sum(r["share"] for r in top3)
        top_lines.append(
            f"<div class='sc'><div class='sc-g'>{game} · {scope}</div>"
            f"<div class='sc-b'>{html.escape(label_cn(top['label']))}</div>"
            f"<div class='sc-m'>覆盖 <b>{top['matches']}</b> 场 / {n_matches} 场 · {top['occ']} 次标签</div>"
            f"<div class='sc-m'>前三形态合计占 {share3*100:.0f}%（{html.escape('、'.join(label_cn(r['label']) for r in top3))}）</div></div>")

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>形态频率排行 · 覆盖场次与历史案例</title>
<style>
:root{{--blue:#0071e3;--bg:#f5f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6e6e73;--bd:#e5e5ea}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
.top{{background:#1d1d1f;color:#fff;padding:13px 20px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}}
.top b{{font-weight:600}} .top span{{color:#a1a1a6;font-size:12.5px}}
.wrap{{max-width:1240px;margin:0 auto;padding:24px 16px 70px}}
h1{{font-size:27px;margin:6px 0 6px;font-weight:650;letter-spacing:-.2px}}
.sub{{color:var(--mu);margin:0 0 18px;font-size:13.5px}}
.note{{background:#fff;border-radius:14px;padding:14px 16px;font-size:13px;color:#3a3a3c;margin:0 0 16px;box-shadow:0 1px 0 rgba(0,0,0,.03)}}
.note b{{color:var(--tx)}}
.sc-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 18px}}
@media(max-width:900px){{.sc-row{{grid-template-columns:repeat(2,1fr)}}}}
.sc{{background:#fff;border-radius:14px;padding:12px 14px}}
.sc-g{{font-size:11.5px;color:var(--mu);letter-spacing:.03em}}
.sc-b{{font-size:17px;font-weight:650;margin:2px 0 4px}}
.sc-m{{font-size:12px;color:var(--mu)}}
.tabs{{display:flex;gap:8px;flex-wrap:wrap;position:sticky;top:0;z-index:6;background:rgba(245,245,247,.93);backdrop-filter:saturate(180%) blur(12px);padding:10px 0;border-bottom:1px solid var(--bd);margin-bottom:14px}}
.tab{{background:#fff;border:1px solid var(--bd);border-radius:10px;padding:7px 14px;font-size:13.5px;cursor:pointer;color:var(--tx)}}
.tab.on{{background:var(--blue);color:#fff;border-color:var(--blue)}}
.pane{{display:none}} .pane.on{{display:block}}
.pane-h h2{{font-size:18px;margin:16px 0 2px;font-weight:650}}
.pane-h p{{margin:0 0 10px;color:var(--mu);font-size:12.5px}}
.search{{margin:0 0 8px}}
.sort{{display:flex;gap:6px;margin:0 0 8px}}
.sbtn{{background:#fff;border:1px solid var(--bd);border-radius:8px;padding:5px 11px;font-size:12.5px;color:var(--mu);cursor:pointer}}
.sbtn.on{{background:#e8f1fd;color:var(--blue);border-color:#cfe4fb}}
.search input{{width:100%;max-width:360px;border:1px solid var(--bd);border-radius:10px;padding:8px 12px;font-size:13.5px;background:#fff;outline:none}}
.search input:focus{{border-color:var(--blue)}}
details.pat{{background:#fff;border-radius:14px;margin:9px 0;overflow:hidden}}
details.pat>summary{{list-style:none;cursor:pointer;display:grid;grid-template-columns:34px minmax(180px,1.5fr) 2fr 84px 84px 74px 16px;align-items:center;gap:10px;padding:12px 15px}}
details.pat>summary::-webkit-details-marker{{display:none}}
details.pat[open]>summary{{border-bottom:1px solid var(--bd)}}
.rk{{font-variant-numeric:tabular-nums;color:#a1a1a6;font-size:12px}}
.nm b{{font-size:14.5px;font-weight:600}} .nm small{{display:block;color:var(--mu);font-size:11.5px}}
.ft{{display:inline-block;font-style:normal;font-size:9.5px;font-weight:700;line-height:1;padding:3px 5px;border-radius:5px;margin-right:5px;vertical-align:1px}}
.t-s{{background:#e8f1fd;color:#005bb8}} .t-a{{background:#e7f6ec;color:#1d8f4e}}
.t-b{{background:#f2f2f7;color:#48484a}} .t-c{{background:#f2f2f7;color:#8e8e93}} .t-d{{background:#fff2e2;color:#b25000}}
.bar{{height:6px;background:#ececf0;border-radius:4px;overflow:hidden}}
.bar i{{display:block;height:100%;background:var(--blue);border-radius:4px;opacity:.75}}
.kpi{{text-align:right;font-variant-numeric:tabular-nums}}
.kpi b{{font-size:15px;font-weight:650;display:block}} .kpi small{{color:var(--mu);font-size:10.5px}}
.cr{{color:#c7c7cc;font-size:18px;transition:transform .15s}}
details[open] .cr{{transform:rotate(90deg)}}
.tag.thin{{background:#fff2e2;color:#b25000;font-size:10px;padding:1px 6px;border-radius:6px;margin-left:6px}}
.body{{padding:14px 16px 16px}}
.shape{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px 22px;margin-bottom:12px}}
@media(max-width:820px){{.shape{{grid-template-columns:1fr}}}}
.lbl{{font-size:11px;color:var(--mu);letter-spacing:.05em}}
.shape p{{margin:2px 0 0;font-size:13px;color:#3a3a3c}}
.stats{{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}}
.stats span{{background:#f5f5f7;border-radius:8px;padding:5px 9px;font-size:12px;color:var(--mu)}}
.stats b{{color:var(--tx);font-weight:600;margin-left:4px}}
.tbl-wrap{{overflow:auto;border:1px solid var(--bd);border-radius:10px;max-height:520px}}
.tbl{{width:100%;border-collapse:collapse;font-size:12px}}
.tbl th{{position:sticky;top:0;background:#fbfbfd;text-align:left;font-weight:600;color:var(--mu);padding:8px;border-bottom:1px solid var(--bd);white-space:nowrap}}
.tbl td{{padding:7px 8px;border-bottom:1px solid #f2f2f7;vertical-align:top}}
.tbl tr:hover td{{background:#fbfbfd}}
.tbl a{{color:var(--blue);text-decoration:none}}
.sm{{color:var(--mu);font-size:11px;margin-top:1px}}
.mono{{font-variant-numeric:tabular-nums;white-space:nowrap}}
.nw{{white-space:nowrap}}
.fine{{color:#8e8e93;font-size:11.5px;margin:10px 0 0}}
.foot{{color:#8e8e93;font-size:11.5px;margin-top:26px;line-height:1.6}}
@media(max-width:700px){{
  details.pat>summary{{grid-template-columns:26px 1fr 56px 56px 14px}}
  .bar,.kpi:nth-of-type(3){{display:none}}
}}
</style></head><body>
<div class="top"><b>Polymarket 电竞情报库 · 形态频率排行</b><span>生成 {now}</span></div>
<div class="wrap">
<h1>形态频率排行：哪些形态真的反复出现</h1>
<p class="sub">LoL 与 CS2 分开、逐小局与整局分开。默认按<b>覆盖比赛数</b>降序；展开任一形态可看历史案例与小差异，点比赛名跳到对应曲线图。</p>

<div class="note">
<b>统计口径</b>：60 秒级 · 仅比赛内窗口 · 只含已走完并结算的路径。一次"出现"= 某场某个市场（整局 / 某小局）的某一侧命中该标签；
同一场双方、多个小局命中同一形态会计多次，因此同时给出<b>覆盖场次</b>（去重比赛数）与<b>标签次数</b>，两者不能混着说。
已排除"未知"标签与无独立市场/无成交的小局（价格停在 50c 左右，不是有效样本）。
<br><b>读法提醒</b>：反转类（A1/A2/A3/A4…）的终点被标签条件限定在高位，"终点≥95c 占比"是标签定义的结果，
<b>不是买入胜率</b>；真正的期望还要乘以买点价位与仓位。本页只做归纳与案例对照，不升级任何策略。
</div>

<div class="sc-row">{''.join(top_lines)}</div>

<div class="tabs">
  <button class="tab on" data-game="LoL" data-scope="逐局">LoL · 逐小局</button>
  <button class="tab" data-game="LoL" data-scope="整局">LoL · 整局</button>
  <button class="tab" data-game="CS2" data-scope="逐局">CS2 · 逐小局</button>
  <button class="tab" data-game="CS2" data-scope="整局">CS2 · 整局</button>
</div>

{''.join(blocks)}

<p class="foot">
数据源：reports/pattern_shapes_lol_tier1_2026-07_2026-10.json（{games['LoL'][0]['total_matches']} 场）、reports/pattern_shapes_cs2_tier1_2026-07_2026-10.json（{games['CS2'][0]['total_matches']} 场）；
分类：tools/classify_pattern.py v1.3；释义：tools/build_pattern_playbook.py；本页由 tools/build_pattern_frequency_index.py 生成。
案例里的联赛为原始赛事名；仅一级赛事，未混入 EMEA / 小区域联赛。曲线图：pattern_charts_lol_tier1_2026-07_2026-10.html / pattern_charts_cs2_tier1_2026-07_2026-10.html。
Dota2：已采 2 场资格赛样本（reports/pattern_shapes_dota2_qualifiers_2026-10.json），样本不足（&lt;5 场），暂不进入频次榜，见最近比赛复盘页。
</p>
</div>
<script>
function show(game, scope){{
  document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('on',p.dataset.game===game&&p.dataset.scope===scope));
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.game===game&&t.dataset.scope===scope));
}}
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>show(t.dataset.game,t.dataset.scope)));
show('LoL','逐局');
document.querySelectorAll('input[data-filter]').forEach(inp=>{{
  inp.addEventListener('input',()=>{{
    const q=inp.value.trim().toLowerCase();
    const pane=inp.closest('.pane');
    pane.querySelectorAll('details.pat').forEach(d=>{{
      d.style.display=(!q||d.dataset.search.includes(q))?'':'none';
    }});
  }});
}});
document.querySelectorAll('.pane').forEach(pane=>{{
  const list=pane;
  pane.querySelectorAll('.sbtn').forEach(btn=>btn.addEventListener('click',()=>{{
    pane.querySelectorAll('.sbtn').forEach(b=>b.classList.toggle('on',b===btn));
    const key=btn.dataset.sort;
    const items=[...pane.querySelectorAll('details.pat')];
    items.sort((a,b)=>Number(b.dataset[key])-Number(a.dataset[key]));
    items.forEach((el,i)=>{{ const rk=el.querySelector('.rk'); if(rk) rk.textContent=String(i+1).padStart(2,'0'); list.appendChild(el); }});
  }}));
}});
</script>
</body></html>"""
    out_path = os.path.join(REPORTS, "pattern_frequency_index.html")
    open(out_path, "w", encoding="utf-8").write(out)
    print(f"{out_path}: {len(out)/1024:.0f} KB")
    for game, scope, rows, total_occ, n in summary_cards:
        print(f"{game} {scope}: {n} 场 / {total_occ} 次 / {len(rows)} 形态；top={rows[0]['label']} ({rows[0]['matches']}场)")


if __name__ == "__main__":
    build()
