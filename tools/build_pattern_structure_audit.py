#!/usr/bin/env python3
"""Build a P1 audit gallery for the task-8 pattern taxonomy rework.

The page intentionally does not generate trade recommendations.  It groups raw
one-minute two-sided price paths by their current classifier labels so the
taxonomy can be reviewed before the decision workspace is rebuilt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_pattern_charts import compute_windows, lttb, market_series  # noqa: E402
from build_pattern_playbook import P as PLAYBOOK  # noqa: E402
from pattern_display import (  # noqa: E402
    CODE_PHRASES,
    DISPLAY_NAMES,
    FAMILY_NAMES,
    SHORT_NAMES,
    display_name,
    display_text,
    family_name,
)


TARGET_LABELS = [
    "B4_直线阴跌",
    "A3_折价修复",
    "A2_中位U型反转",
    "C2_五五开开局碾压",
    "A1_V型极值反转",
    "B1_尾盘崩塌",
]

SPORT_LABEL = {"lol": "英雄联盟", "cs2": "CS2"}
LEVEL_LABEL = {"series": "整场", "game": "小局"}
SCOPES = ("game", "series")
SCOPE_LABEL = {"game": "小局 / 单图", "series": "整场 / 系列赛"}
SCOPE_COPY = {
    "game": "只沿一局或一张图的终局演进，交易窗口较短；尾盘保护和单局反转优先。",
    "series": "跨越多局或多张地图，并携带当前大比分；路径中会出现局间重新定价，复活、打满和大比分条件优先。",
}
COLORS = ("#0a6ed1", "#ff9f0a")


@dataclass(frozen=True)
class Candidate:
    sport: str
    slug: str
    level: str
    game: int
    side: str
    label: str
    summary: dict[str, Any]
    all_labels: tuple[str, ...]
    granularity: str


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def fmt_price(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}c"
    except (TypeError, ValueError):
        return "-"


def collect(sections: list[tuple[str, Path]]) -> tuple[list[Candidate], dict[str, dict[str, Any]]]:
    rows: list[Candidate] = []
    metadata: dict[str, dict[str, Any]] = {}
    wanted = set(TARGET_LABELS)
    for sport, path in sections:
        data = json.loads(path.read_text(encoding="utf-8"))
        metadata[sport] = data
        for match in data.get("matches") or []:
            slug = str(match.get("slug") or "")
            if not slug:
                continue
            granularity = str(match.get("granularity") or "unknown")
            scopes: list[tuple[str, int, dict[str, dict[str, Any]]]] = []
            if match.get("series"):
                scopes.append(("series", 0, match["series"]))
            for game, sides in sorted((match.get("games") or {}).items(), key=lambda item: int(item[0])):
                scopes.append(("game", int(game), sides or {}))
            for level, game_no, sides in scopes:
                for side, summary in sorted((sides or {}).items()):
                    labels = tuple(str(x) for x in (summary.get("labels") or []) if x)
                    for label in labels:
                        if label in wanted:
                            rows.append(Candidate(
                                sport=sport, slug=slug, level=level, game=game_no,
                                side=str(side), label=label, summary=summary,
                                all_labels=labels, granularity=granularity,
                            ))
    return rows, metadata


def example_key(row: Candidate) -> int:
    """Prefer representative one-minute series without favouring extreme length."""
    points = int(row.summary.get("span_min") or 0)
    one_min = 20 if row.granularity == "60s" else 0
    return one_min + min(points, 240)


def choose_examples(rows: list[Candidate], limit: int = 2) -> list[Candidate]:
    chosen: list[Candidate] = []
    used_slugs: set[str] = set()
    for row in sorted(rows, key=example_key, reverse=True):
        if row.slug in used_slugs:
            continue
        chosen.append(row)
        used_slugs.add(row.slug)
        if len(chosen) >= limit:
            break
    if len(chosen) < limit:
        for row in sorted(rows, key=example_key, reverse=True):
            if row in chosen:
                continue
            chosen.append(row)
            if len(chosen) >= limit:
                break
    return chosen


def audit_svg(sides: dict[str, tuple[list[tuple[float, float]], int]], meta: dict[str, dict[str, Any]]) -> tuple[str, int, int]:
    if not sides:
        return "<div class='empty'>无分钟路径</div>", 0, 0
    all_points = [point for points, _gran in sides.values() for point in points]
    if not all_points:
        return "<div class='empty'>无分钟路径</div>", 0, 0
    start = min(point[0] for point in all_points)
    end = max(point[0] for point in all_points)
    span = max(60.0, end - start)
    width, height = 620, 236
    pad = {"l": 42, "r": 18, "t": 18, "b": 30}
    plot_w = width - pad["l"] - pad["r"]
    plot_h = height - pad["t"] - pad["b"]

    def x(ts: float) -> float:
        return pad["l"] + (ts - start) / span * plot_w

    def y(price: float) -> float:
        return pad["t"] + (1.0 - price) * plot_h

    parts = [f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='一分钟双方价格路径'>"]
    for value in (0, 25, 50, 75, 100):
        yy = y(value / 100)
        dash = " stroke-dasharray='5 5'" if value == 50 else ""
        color = "#9a9aa0" if value == 50 else "#ececf0"
        parts.append(f"<line x1='{pad['l']}' y1='{yy:.1f}' x2='{width-pad['r']}' y2='{yy:.1f}' stroke='{color}' stroke-width='1'{dash}/>")
        parts.append(f"<text x='{pad['l']-7}' y='{yy+3:.1f}' text-anchor='end' class='ax'>{value}c</text>")
    parts.append(f"<text x='{width-pad['r']-3}' y='{y(.5)-6:.1f}' text-anchor='end' class='mid'>50:50</text>")

    total_raw = 0
    total_drawn = 0
    for index, key in enumerate(sorted(sides)):
        points, _gran = sides[key]
        total_raw += len(points)
        shown = points if len(points) <= 320 else lttb(points, 320)
        total_drawn += len(shown)
        path = " ".join(("M" if i == 0 else "L") + f"{x(ts):.1f},{y(price):.1f}" for i, (ts, price) in enumerate(shown))
        win = float((meta.get(key) or {}).get("end") or 0) >= 0.9
        color = COLORS[index % len(COLORS)]
        width_px = 2.8 if win else 1.65
        opacity = 1.0 if win else 0.74
        parts.append(f"<path d='{path}' fill='none' stroke='{color}' stroke-width='{width_px}' opacity='{opacity}' stroke-linejoin='round' stroke-linecap='round'/>")
        if len(shown) <= 180:
            for ts, price in shown:
                parts.append(f"<circle cx='{x(ts):.1f}' cy='{y(price):.1f}' r='1.15' fill='{color}' opacity='.34'/>")
        if shown:
            tx, ty = shown[-1]
            parts.append(f"<circle cx='{x(tx):.1f}' cy='{y(ty):.1f}' r='3' fill='{color}' stroke='white' stroke-width='1.2'/>")
    parts.append("</svg>")
    return "".join(parts), total_raw, total_drawn


def render_example(row: Candidate, cache: dict[tuple[str, str, int], dict[str, tuple[list[tuple[float, float]], int]]], windows: dict[str, Any]) -> str:
    key = (row.slug, row.level, row.game)
    if key not in cache:
        cache[key] = market_series(
            row.slug,
            row.game if row.level == "game" else 0,
            "game" if row.level == "game" else "series",
            windows["games"].get(row.game) if row.level == "game" else windows.get("series"),
        ) or {}
    sides = cache[key]
    meta = {side: (row.summary if side == row.side else {}) for side in sides}
    svg, raw_count, drawn_count = audit_svg(sides, meta)
    label = LEVEL_LABEL[row.level] if row.level == "series" else f"G{row.game}"
    side_name = str(row.summary.get("team") or f"侧 {row.side}")
    labels = " / ".join(display_name(label) for label in row.all_labels) or "暂未归类"
    return f"""
      <article class="example">
        <div class="example-head"><div><b>{esc(row.slug)}</b><span>{esc(label)} · {esc(side_name)} · {fmt_price(row.summary.get('pre'))} → {fmt_price(row.summary.get('end'))}</span></div><span class="chip">{esc(row.granularity)}</span></div>
        {svg}
        <div class="example-foot"><span>原始 {raw_count} 点 / 绘制 {drawn_count} 点</span><span>{esc(labels)}</span></div>
      </article>"""


def sport_totals(metadata: dict[str, dict[str, Any]], sport: str) -> tuple[int, int]:
    data = metadata.get(sport) or {}
    matches = data.get("matches") or []
    sides = sum(len(match.get("series") or {}) for match in matches)
    sides += sum(
        len(game_sides or {})
        for match in matches
        for game_sides in (match.get("games") or {}).values()
    )
    return len(matches), sides


def scope_totals(metadata: dict[str, dict[str, Any]], sport: str, level: str) -> tuple[int, int, float]:
    """Return unique matches, market sides and median path span for one market level."""
    match_slugs: set[str] = set()
    sides = 0
    spans: list[float] = []
    for match in (metadata.get(sport) or {}).get("matches") or []:
        groups: list[dict[str, Any]] = []
        if level == "series" and match.get("series"):
            groups = [match["series"]]
        elif level == "game":
            groups = [game_sides or {} for game_sides in (match.get("games") or {}).values()]
        valid = [group for group in groups if group]
        if not valid:
            continue
        match_slugs.add(str(match.get("slug") or ""))
        for group in valid:
            sides += len(group)
            spans.extend(float(item.get("span_min") or 0) for item in group.values())
    median_span = statistics.median(spans) if spans else 0.0
    return len(match_slugs), sides, median_span


def share(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator * 100:.0f}%" if denominator else "-"


def render_shape(
    label: str,
    rows: list[Candidate],
    cache: dict[tuple[str, str, int], dict[str, tuple[list[tuple[float, float]], int]]],
    sport: str,
    level: str,
    total_matches: int,
    total_sides: int,
) -> str:
    meta = PLAYBOOK.get(label) or {}
    slugs = {row.slug for row in rows}
    one_min = sum(row.granularity == "60s" for row in rows)
    granularity = f"1分钟 {one_min}/{len(rows)}" if rows else "无样本"
    spans = [float(row.summary.get("span_min") or 0) for row in rows]
    median_span = statistics.median(spans) if spans else 0.0
    examples = choose_examples(rows, 2)
    charts = []
    for row in examples:
        windows = compute_windows(row.slug, True)
        charts.append(render_example(row, cache, windows))
    body = "".join(charts) or "<div class='empty'>该类在当前一级样本中没有分钟路径。</div>"
    return f"""
    <section class="shape-card" id="{esc(sport)}-{esc(level)}-{esc(label.replace('/', '-'))}" data-scope="{esc(level)}">
      <div class="shape-head">
        <div><div class="eyebrow">{esc(family_name(str((PLAYBOOK.get(label) or {{}}).get('family') or '')))} · {esc(LEVEL_LABEL[level])}形态</div><h3>{esc(display_name(label))}</h3><p>{esc(display_text(meta.get('shape', '尚未定义')))}</p></div>
        <div class="shape-stats"><div><b>{len(slugs)}</b><span>覆盖比赛 · {share(len(slugs), total_matches)}</span></div><div><b>{len(rows)}</b><span>本层出现 · {share(len(rows), total_sides)}</span></div><div><b>{median_span:.0f}m</b><span>中位路径 · {esc(granularity)}</span></div></div>
      </div>
      <div class="spec"><div><span>识别重点</span><b>{esc(display_text(meta.get('trigger', '-')))}</b></div><div><span>失效边界</span><b>{esc(display_text(meta.get('invalid', '-')))}</b></div></div>
      <div class="examples-head">代表案例 <span>按分钟完整度挑选，不参与胜率计算</span></div><div class="examples">{body}</div>
    </section>"""


def build_html(rows: list[Candidate], metadata: dict[str, dict[str, Any]], output: Path) -> None:
    cache: dict[tuple[str, str, int], dict[str, tuple[list[tuple[float, float]], int]]] = {}
    sections: list[str] = []
    snapshot_cards: list[str] = []
    for sport in ("lol", "cs2"):
        sport_rows = [row for row in rows if row.sport == sport]
        if not sport_rows:
            continue
        blocks: list[str] = []
        for level in SCOPES:
            level_rows = [row for row in sport_rows if row.level == level]
            total_matches, total_sides, median_span = scope_totals(metadata, sport, level)
            snapshot_cards.append(
                f"<div><span>{esc(SPORT_LABEL[sport])} · {esc(SCOPE_LABEL[level])}</span>"
                f"<b>{total_matches} 场 · {total_sides} 侧</b><small>中位路径 {median_span:.0f} 分钟</small></div>"
            )
            shape_blocks = []
            for label in TARGET_LABELS:
                label_rows = [row for row in level_rows if row.label == label]
                if label_rows:
                    shape_blocks.append(render_shape(label, label_rows, cache, sport, level, total_matches, total_sides))
            blocks.append(f"""
            <section class="scope-block" id="{esc(sport)}-{esc(level)}">
              <div class="scope-head"><div><div class="eyebrow">{esc(SPORT_LABEL[sport])} · 市场层级</div><h2>{esc(SCOPE_LABEL[level])}</h2><p>{esc(SCOPE_COPY[level])}</p></div><div class="scope-count"><b>{total_matches}</b><span>场 · {total_sides} 个市场侧 · 中位 {median_span:.0f} 分钟</span></div></div>
              {''.join(shape_blocks)}
            </section>""")
        sections.append(''.join(blocks))

    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    subtotal = len({row.slug for row in rows})
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>形态分层审阅 · 小局与整场</title>
<style>
:root{{--bg:#f5f5f7;--card:#fff;--ink:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--blue:#0a6ed1;--orange:#ff9f0a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans CJK SC",sans-serif}}
.wrap{{max-width:1220px;margin:0 auto;padding:24px 18px 72px}}.hero{{background:linear-gradient(135deg,#fff,#eef5ff);border:1px solid rgba(0,0,0,.06);border-radius:22px;padding:24px;box-shadow:0 12px 34px rgba(0,0,0,.05)}}
.hero h1{{font-size:34px;letter-spacing:-1.2px;margin:5px 0 8px}}.hero p{{margin:0;color:var(--muted);max-width:820px}}.eyebrow{{font-size:11px;color:var(--blue);font-weight:750;letter-spacing:.08em;text-transform:uppercase}}
.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:18px}}.summary div{{background:rgba(255,255,255,.8);border:1px solid #e3eaf5;border-radius:14px;padding:12px}}.summary b{{display:block;font-size:22px}}.summary span{{font-size:11px;color:var(--muted)}}
.note{{margin:14px 0 22px;padding:14px 16px;background:#fff;border-radius:16px;border:1px solid var(--line);color:#3a3a3c;font-size:13px}}
.scope-snapshot{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0 22px}}.scope-snapshot div{{background:#fff;border:1px solid var(--line);border-radius:16px;padding:13px}}.scope-snapshot span,.scope-snapshot small{{display:block;color:var(--muted);font-size:10px}}.scope-snapshot b{{display:block;font-size:17px;margin:3px 0}}
.scope-nav{{display:flex;gap:8px;flex-wrap:wrap;margin:-8px 0 18px}}.scope-nav a{{text-decoration:none;color:var(--ink);background:#fff;border:1px solid var(--line);border-radius:999px;padding:7px 11px;font-size:11px}}.scope-nav a:hover{{border-color:#b9d5f5;color:var(--blue)}}
.scope-block{{margin:32px 0 8px}}.scope-head{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:end;margin:0 0 14px;padding:0 2px}}.scope-head h2{{font-size:25px;letter-spacing:-.6px;margin:3px 0 5px}}.scope-head p{{margin:0;color:var(--muted);font-size:12px;max-width:760px}}.scope-count{{text-align:right}}.scope-count b{{display:block;font-size:26px}}.scope-count span{{display:block;color:var(--muted);font-size:10px}}
.principles,.stage-note{{margin:18px 0 22px;padding:18px;background:#fff;border:1px solid var(--line);border-radius:20px}}.principles h2,.stage-note h2{{font-size:21px;margin:0 0 10px}}.principle-grid,.stage-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.principle-grid div,.stage-grid div{{background:#fbfbfd;border:1px solid var(--line);border-radius:14px;padding:12px}}.principle-grid b,.stage-grid b{{display:block;font-size:13px;margin:4px 0}}.principle-grid p,.stage-grid p{{margin:0;color:var(--muted);font-size:11px}}.stage-note>.muted{{color:var(--muted);font-size:12px;margin:0 0 12px}}.stage-grid span{{font-size:10px;color:var(--blue);font-weight:700}}.stage-caveat{{font-size:10px;color:var(--muted);margin:10px 0 0}}
.shape-card{{background:var(--card);border:1px solid rgba(0,0,0,.06);border-radius:20px;padding:18px;margin:0 0 14px;box-shadow:0 8px 28px rgba(0,0,0,.04)}}.shape-head{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:start}}.shape-head h3{{font-size:20px;margin:3px 0 5px}}.shape-head p{{margin:0;color:var(--muted);font-size:13px;max-width:780px}}.shape-stats{{display:flex;gap:8px}}.shape-stats div{{min-width:88px;background:#f7f7f9;border-radius:12px;padding:10px}}.shape-stats b{{display:block;font-size:16px}}.shape-stats span{{font-size:10px;color:var(--muted)}}
.spec{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:13px 0}}.spec div{{background:#fbfbfd;border:1px solid var(--line);border-radius:12px;padding:10px}}.spec span{{display:block;font-size:10px;color:var(--muted);margin-bottom:3px}}.spec b{{font-size:12px;font-weight:550;line-height:1.45}}.examples-head{{display:flex;justify-content:space-between;gap:10px;align-items:baseline;margin:15px 1px 8px;font-size:12px;font-weight:650}}.examples-head span{{font-size:10px;color:var(--muted);font-weight:400}}
.examples{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.example{{border:1px solid var(--line);border-radius:15px;padding:12px;background:#fbfbfd;min-width:0}}.example-head{{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:8px}}.example-head b{{display:block;font-size:12px}}.example-head span{{display:block;color:var(--muted);font-size:10px;margin-top:2px}}.example svg{{display:block;width:100%;height:auto;background:#fff;border-radius:10px}}.ax{{font-size:9px;fill:#86868b}}.mid{{font-size:9px;font-weight:700;fill:#737378;paint-order:stroke;stroke:#fff;stroke-width:4px}}.chip{{display:inline-flex;padding:3px 7px;border-radius:999px;background:#edf4ff;color:var(--blue);font-size:10px;white-space:nowrap}}.example-foot{{display:flex;justify-content:space-between;gap:10px;color:var(--muted);font-size:9px;margin-top:6px}}.empty{{padding:20px;color:var(--muted);font-size:12px}}.foot{{font-size:11px;color:var(--muted);margin-top:26px}}
@media(max-width:850px){{.summary,.examples,.spec,.scope-snapshot,.principle-grid,.stage-grid{{grid-template-columns:1fr 1fr}}.shape-head,.scope-head{{grid-template-columns:1fr}}.shape-stats{{flex-wrap:wrap}}.scope-count{{text-align:left}}}}
@media(max-width:600px){{.summary,.examples,.spec,.scope-snapshot,.principle-grid,.stage-grid{{grid-template-columns:1fr}}.hero h1{{font-size:28px}}}}
</style></head><body><main class="wrap">
<section class="hero"><div class="eyebrow">形态审阅 · 小局与整场分开</div><h1>先看懂走势，再决定怎么做。</h1><p>同一套反转、阴跌、碾压骨架可能在两层重复，但小局与整场的剩余时间、结算规则和比分条件不同。这里全部使用中文形态名，并把英雄联盟、CS2、小局、整场彻底分开。</p><div class="summary"><div><b>6</b><span>本批审阅形态</span></div><div><b>{subtotal}</b><span>去重比赛</span></div><div><b>{len(rows)}</b><span>形态命中侧</span></div><div><b>1m</b><span>优先审阅粒度</span></div></div></section>
<div class="note"><b>读图口径：</b>蓝/橙为双方真实价格路径；粗线为最终获胜侧，细线为落败侧；灰虚线是 50c 中枢。图中保留分钟点，不再把候选未来压成 9 点。每个案例下方同时显示整段路径的全部标签，便于发现一例多形态与主标签折叠问题。<br><b>数量口径：</b>“覆盖比赛”按去重比赛计算，“出现次数”按市场侧计。同一路径可同时带多个标签，因此各类比例不能相加；这些数字只说明历史样本丰度，不代表未来命中率。</div>
<div class="scope-snapshot">{''.join(snapshot_cards)}</div>
<nav class="scope-nav" aria-label="市场层级导航"><a href="#lol-game">英雄联盟 · 小局</a><a href="#lol-series">英雄联盟 · 整场</a><a href="#cs2-game">CS2 · 小局</a><a href="#cs2-series">CS2 · 整场</a></nav>
<section class="principles"><h2>结论：形状相似，决策不能混用</h2><div class="principle-grid"><div><span class="eyebrow">可以共用</span><b>走势的几何词汇</b><p>V 型、U 型、回撤修复、持续阴跌、尾盘急跌等结构可以跨层归纳，方便快速认图。</p></div><div><span class="eyebrow">必须拆开</span><b>触发、止损、止盈与仓位</b><p>小局只看本局终局；整场还要经过多局、比分切换和局间重新定价。同名形态在两层不是同一个交易模板。</p></div><div><span class="eyebrow">数据事实</span><b>已经有层级专属形态</b><p>本批数据中，尾盘急跌归零只在小局出现，整场逆袭反转只在整场出现；整场持续坠落更偏整场，均势后单边碾压更偏小局。</p></div></div></section>
<section class="stage-note"><h2>未来比赛如何用形态</h2><p class="muted">预测输出不是直接写“必买”，而是按观察时点给出候选形态和失效边界。</p><div class="stage-grid"><div><span>赛前 · 0:0</span><b>只有先验，没有当前形态</b><p>输入联赛、赛制、强弱、阵容、动机和历史回撤分布；输出基础形态概率包络。</p></div><div><span>局中 · 当前走势</span><b>用已经发生的路径识别候选</b><p>同时观察当前小局路径和整场路径，只使用观察时点之前的数据，给出候选结构与触发、失效位置。</p></div><div><span>首局后 · 局间</span><b>加入大比分重新预测</b><p>把当前大比分、局末价格和下一局开盘反应合并，更新整场形态分布。</p></div></div><p class="stage-caveat">当前仍在形态字典复核阶段，尚不输出可执行概率。完成分钟级样本重建和时间切分回测后，候选概率与交易位置才会进入决策台。</p></section>
{''.join(sections)}
<div class="foot">生成 {generated} · 数据源 reports/pattern_shapes_lol_tier1_2026-07_2026-10.json、reports/pattern_shapes_cs2_tier1_2026-07_2026-10.json 与 docs/data/snapshots/ · 小局与整场均按 1 分钟路径独立统计。</div>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build task-8 P1 pattern taxonomy audit gallery")
    parser.add_argument("--section", action="append", required=True, help="game=path; repeatable")
    parser.add_argument("--out", default="reports/pattern_structure_audit_2026-10-22.html")
    args = parser.parse_args()
    sections: list[tuple[str, Path]] = []
    for value in args.section:
        sport, raw_path = value.split("=", 1)
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        sections.append((sport, path))
    rows, metadata = collect(sections)
    output = Path(args.out)
    if not output.is_absolute():
        output = ROOT / output
    build_html(rows, metadata, output)
    print(f"pattern structure audit -> {output.relative_to(ROOT)} ({len(rows)} labeled candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
