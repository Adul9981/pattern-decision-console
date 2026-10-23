#!/usr/bin/env python3
"""形态速查页（交易决策用）：每个形态的**线条特征 + 实测统计 + 交易动作**。

与 reports/pattern_taxonomy.html 的分工：
  - taxonomy   回答"一共有多少种、怎么分层、各格样本够不够"；
  - playbook   回答"这一种长什么样、买在哪、什么时候卖、什么情况作废"。

每个形态给三样东西：
  1. 示意线条（canonical sketch，纯 SVG 折线，画的是"形状"不是真实价格）；
  2. 真实样本曲线（从 docs/data/snapshots/ 取该形态最接近中位的那一场）；
  3. 实测统计（该形态在最大的那个主格里的 n / 起点中位 / 低点中位 / 穿越 50c 次数 /
     到 99.5c 的耗时中位 / 低点买入倍数）+ 交易动作与失效条件。

⚠ 口径：形态标签是**按事后赢家侧**标注的，天然含幸存者偏差——
"某形态 60% 场次发生" ≠ "看到该形态买入有 60% 胜率"。本页给出的是形状与位置，
不是胜率承诺；胜率/期望值另见 reports/pattern_shapes_index.html 的机械抄底回测。

用法：
  python3 tools/build_pattern_playbook.py \
      --section lol=reports/pattern_shapes_lol_2026-0910_1001.json \
      --section lol=reports/pattern_shapes_lol_2026-1002_1014.json \
      --section cs2=reports/pattern_shapes_cs2_2026-08_2026-10.json \
      --out reports/pattern_playbook.html
"""

from __future__ import annotations

import argparse
import html
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json  # noqa: E402

import build_pattern_library as bpl  # noqa: E402
from esports_tier import tier_of  # noqa: E402
from build_pattern_charts import lttb, market_series, compute_windows  # noqa: E402

NEED = 20
CELLS = [("LoL", "整局"), ("LoL", "逐局"), ("CS2", "整局"), ("CS2", "逐局")]

# ---------------------------------------------------------------- 形态定义
# sketch: 归一化折线 (x 时间 0-1, y 赔率 0-1)；只是"形状示意"，不代表真实价格。
P = {
    "A1_V型极值反转": dict(
        family="反转", sketch=[(0,.50),(.22,.34),(.36,.06),(.48,.26),(.60,.62),(.78,.92),(1,1)],
        shape="极陡的 V：短时间内被砸到 <10c（常常几分钟），随后几乎同速率拉回，全程一次触底。",
        trigger="窗口低点 ≤0.10c 且终点 ≥0.70；低点后 ≤10 分钟内重新站上 50c 才算数。",
        entry="只在深水区（≤10-15c）挂单；这一档是彩票仓，不是主攻仓。",
        exit="回到 50c 出掉一半本金，站上 80c 再出四分之一，剩余跟到结算。",
        invalid="反弹慢于喷发（低点后 10 分钟仍 <35c）= 不是 V，是 B4 阴跌的前半段。"),
    "A2_中位U型反转": dict(
        family="反转", sketch=[(0,.50),(.16,.36),(.32,.27),(.50,.25),(.66,.40),(.84,.76),(1,1)],
        shape="中位 U：先阴跌到 13-35c，低点区横一段时间（不喷发），再缓坡修复到 100c。",
        trigger="低点落在 13-35c，终点 ≥70c；斜率明显缓于 V 型。",
        entry="18-30c 分档接（这是深水主力形态），跌破 13c 停手。",
        exit="70c 出 1/3，85c 再出 1/3，尾仓跟到结算。",
        invalid="低点后再次跌破前低 → 形态翻成 B3 假反弹后归零。"),
    "A3_折价修复": dict(
        family="反转", sketch=[(0,.74),(.18,.64),(.36,.79),(.56,.88),(.78,.96),(1,1)],
        shape="高位浅回调：强势方从 65-80c 被压到 55-70c（深则 38-45c），随即修复回 90-100c，全程不破位。",
        trigger="起点 ≥0.60、低点仍 ≥0.35、终点 ≥0.90。",
        entry="回撤到 55-65c 主攻；深到 38-45c 时加一档。",
        exit="修复回 85c 以上开始分批止盈，不等结算。",
        invalid="跌破 40c 仍按'折价修复'加仓 = 失效区（已不是浅回调）。"),
    "A4_下狗整场反转": dict(
        family="反转", sketch=[(0,.32),(.20,.22),(.45,.25),(.60,.45),(.76,.70),(.90,.90),(1,1)],
        shape="整场专属：下狗从 30-45c 起步，先沉到 20c 上下，再一路爬升穿 50c 到结算。",
        trigger="仅整场（Moneyline）市场：起点 ≤0.45 且终点 ≥0.90。",
        entry="赛前 30-40c 小仓 + 赛中跌破 25c 补一档；不要 all-in。",
        exit="穿 50c 出 1/3，75c 再出 1/3。",
        invalid="小局盘不适用（小局没有'A4'，只有 B 类坠落）。"),
    "A5_W型双底": dict(
        family="反转", sketch=[(0,.50),(.14,.30),(.28,.42),(.42,.28),(.60,.50),(.78,.80),(1,1)],
        shape="W 双底：砸下去、弹回来（≥15c）、再砸回原点附近，第二次触底后才真正起来。",
        trigger="低点 ≤0.45，低点后反弹 ≥15c，随后回落至低点 +5c 以内。",
        entry="只在**第二个底**接；第一个底不要抢（那里还没确认）。",
        exit="第二次反弹过 70c 出半仓，90c 再出 1/4。",
        invalid="第二个底破第一个底 >5c → 不是 W，是继续下跌。"),
    "A6_反弹确认": dict(
        family="反转", sketch=[(0,.50),(.10,.33),(.19,.05),(.30,.50),(.52,.80),(.76,.95),(1,1)],
        shape="闪电式：小于 10c 的极深水，10 分钟内直接确认反弹站上 50c，之后单边。",
        trigger="低点 <0.10c 且低点后 10 分钟内 t50 成立。",
        entry="确认站上 50c 后追（此时已不是深水价，但确定性最高）。",
        exit="75c 出一半，剩余跟结算。",
        invalid="样本极少（个位数）——当前只作观察，不进策略层。"),
    "A7_强强对话错杀": dict(
        family="反转", sketch=[(0,.50),(.20,.42),(.34,.12),(.50,.30),(.70,.66),(.90,.90),(1,1)],
        shape="势均力敌（赛前 45-55c）里一方被错杀到 <30c（常 <15c），再翻回来反超。",
        trigger="起点 0.42-0.58、低点 ≤0.30、终点 ≥0.45。",
        entry="仅限 LCK/CS2 这类信誉联赛；LPL 疑似剧本场降级处理。",
        exit="回到 50c 先撤一半本金（错杀修复最容易在 50c 二次纠结）。",
        invalid="双方实力本就不对等（赛前 >65:35）时没有'错杀'，只有必然。"),
    "C2_五五开开局碾压": dict(
        family="确立", sketch=[(0,.50),(.10,.50),(.22,.52),(.36,.62),(.56,.78),(.80,.92),(1,1)],
        shape="开局 50:50 长时间黏着，某一刻开始单边拉开，越走越远直到结算。",
        trigger="起点 0.35-0.62、低点 ≥0.33、终点 ≥0.95。",
        entry="黏着段（45-55c）先小仓，**破位确认后**再加主仓。",
        exit="80c 以上分批；这种形态常常不给深度回踩，别等回调。",
        invalid="黏着段反复穿越 50c 超过 10 次 = 还没'开局'，等破位。"),
    "C2_早期缩距/热门确立": dict(
        family="确立", sketch=[(0,.33),(.20,.12),(.40,.35),(.60,.60),(.80,.85),(1,1)],
        shape="低开高走：先下探到低位，再逐步把差距拉开并锁定，属于'先被低估再确立'。",
        trigger="起点 ≤0.40、终点 ≥0.70、且未命中 A 类反转标签。",
        entry="低位区（15-30c）接，确认穿 50c 后加。",
        exit="穿 50c 出 1/3，80c 再出 1/3。",
        invalid="低位横太久不穿 50c → 归入 B 类阴跌。"),
    "热门全程压制": dict(
        family="压制", sketch=[(0,.77),(.24,.72),(.50,.80),(.74,.90),(1,1)],
        shape="热门从头压到尾：起点就 ≥60c，全程不破 55c，没有任何买入窗口。",
        trigger="起点 ≥0.60、低点 ≥0.55、终点 ≥0.95。",
        entry="**没有买点**——这一档是卖出侧形态，用来判断'该什么时候止盈'而不是'买什么'。",
        exit="低点即回撤窗口：82c 以下出的都是亏；等到 90c+ 再分批。",
        invalid="把它当 A3 折价修复杂抄底 = 最常见的亏法。"),
    "B1_尾盘崩塌": dict(
        family="崩塌", sketch=[(0,.60),(.18,.86),(.48,.90),(.70,.84),(.80,.40),(.88,.10),(1,.02)],
        shape="小局专属：长时间高位（85-90c），30 分钟内断崖式归零。",
        trigger="小局盘：最高 ≥0.85、终点 ≤0.10、从高点到终局 ≤30 分钟。",
        entry="不买。这是负方侧形态，用来**风控**：手里有该侧仓位时立刻撤。",
        exit="破 70c 无条件减半，破 50c 清完——回不来的概率远高于回来。",
        invalid="'就快结束了不会翻'是最贵的想法。"),
    "B2_死亡螺旋": dict(
        family="崩塌", sketch=[(0,.75),(.14,.80),(.30,.60),(.50,.35),(.70,.15),(.86,.06),(1,.02)],
        shape="整场为主：热门一度 75-90c，被翻后一路阴跌到 0，中间没有像样的反弹。",
        trigger="最高 ≥0.85、终点 ≤0.10；整场盘不论耗时，小局盘耗时 >30 分钟归此。",
        entry="不买。持有该侧时执行 D3 止损纪律。",
        exit="第一次跌破 60c 就减仓，别等'反弹再走'。",
        invalid="把中途小反弹当'要回来了'——B2 的反弹都不收复 50c。"),
    "B3_假反弹后归零": dict(
        family="崩塌", sketch=[(0,.35),(.18,.14),(.34,.42),(.50,.30),(.68,.12),(.86,.05),(1,.02)],
        shape="先跌，中途有一次像样反弹（会站上 50c），随后彻底归零。",
        trigger="低点 ≤0.45 且曾站上 50c，终点 ≤0.05。",
        entry="不买。它的价值是**证伪 A2**：A2 的失效形态就是它。",
        exit="反弹到 50c 是最后的逃生窗口，那里必须走。",
        invalid="'已经反弹一次了'不等于会反弹第二次。"),
    "B4_直线阴跌": dict(
        family="崩塌", sketch=[(0,.55),(.24,.40),(.50,.28),(.74,.15),(1,.02)],
        shape="高开低走直线：起点明显高于后续低点（>20c），跌破后没有形成重新站上 50c 的修复，一路阴跌归零。",
        trigger="起点 > 低点 +0.20、低点后未重新站上 50c、终点 ≤0.10。",
        entry="不买。这是负方侧恒等式衰减（84% 的负方侧都是它）。",
        exit="尽早走；它不会给你任何反弹出场机会。",
        invalid="拿'深水便宜'当买入理由。"),
    "B4_低开阴跌": dict(
        family="崩塌", sketch=[(0,.15),(.28,.10),(.62,.06),(1,.02)],
        shape="低开低走：起点就 ≤40c、最高也没超过 40c，慢慢阴跌到 0。",
        trigger="低点 ≤0.20、终点 ≤0.10、全程最高 ≤0.40。",
        entry="不买。属于弱队被压制的默认轨迹。",
        exit="不要接。",
        invalid="把它当成'A7 错杀'——区别在：A7 的最高点 ≥40c 且最终翻回来。"),
    "C1_中位震荡": dict(
        family="震荡", sketch=[(0,.50),(.12,.42),(.25,.58),(.38,.44),(.50,.56),(.62,.45),(.76,.60),(.88,.52),(1,.58)],
        shape="长时间在 35-65c 来回震荡，反复穿越 50c（≥4 次），既不破位也不崩塌。",
        trigger="低点 ≥0.20、最高 ≤0.80、穿越 50c ≥4 次。",
        entry="等突破，不猜方向；震荡区间内只做小额高抛低吸，不做主攻仓。",
        exit="突破 80c 停止新开，只管理已有仓。",
        invalid="已结算市场里 C1 = 0（全部在未走完时才出现）——现无实盘样本。"),
}

ORDER = list(P)


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def collect(paths, tiers=None):
    """胜方侧与负方侧分别统计；B 类形态只活在负方侧，必须分开算。

    返回 (win_stats, win_ex, lose_stats, lose_ex)，键统一为 (game, scope, label)。
    """
    W = defaultdict(lambda: defaultdict(list))
    L = defaultdict(lambda: defaultdict(list))
    exW, exL, raw = {}, {}, []          # raw: (slug, kind, idx, side_kind, cell)
    for game, path in paths:
        d = json.load(open(path, encoding="utf-8"))
        for m in d["matches"]:
            if tiers is not None and tier_of(m.get("league_raw"), game) not in tiers:
                continue
            slug = m["slug"]
            win_cells, lose_cells = [], []
            ser = m.get("series") or {}
            e = {int(k): v["end"] for k, v in ser.items()}
            if e:
                wi = max(e, key=lambda i: e[i])
                if e[wi] >= 0.9:
                    for i in e:
                        (win_cells if i == wi else lose_cells).append(("整局", "series", None, ser[str(i)]))
            unres = [int(x) for x in (m.get("games_unresolved") or [])]
            for g, sd in (m.get("games") or {}).items():
                if int(g) in unres:
                    continue
                ee = {int(k): v["end"] for k, v in sd.items()}
                if not ee:
                    continue
                gi = max(ee, key=lambda i: ee[i])
                if ee[gi] >= 0.9:
                    for i in ee:
                        (win_cells if i == gi else lose_cells).append(("逐局", "game", int(g), sd[str(i)]))
            for side_kind, cells in (("win", win_cells), ("lose", lose_cells)):
                for scope, kind, idx, c in cells:
                    for lab in c.get("labels") or []:
                        if lab == "未知" or lab not in P:
                            continue
                        tgt = W if side_kind == "win" else L
                        k = (game, scope, lab)
                        tgt[k]["pre"].append(c.get("pre"))
                        tgt[k]["low"].append(c.get("low"))
                        tgt[k]["cross"].append(c.get("cross50"))
                        tgt[k]["t99"].append(c.get("t99_min"))
                        raw.append((slug, kind, idx, side_kind, (game, scope, lab), c.get("team"), c.get("low")))

    def finalize(store, want_side):
        out, sel = {}, {}
        for k, v in store.items():
            low_m = med(v["low"])
            out[k] = dict(n=len(v["pre"]), pre=med(v["pre"]), low=low_m, cross=med(v["cross"]),
                          t99=med(v["t99"]),
                          mult=(round(1.0 / low_m, 1) if low_m else None))
        for slug, kind, idx, side_kind, key, team, low in raw:
            if side_kind != want_side or key not in out:
                continue
            tgt = out[key]["low"]
            if tgt is None:
                continue
            score = abs((low or 0) - tgt)
            cur = sel.get(key)
            if cur is None or score < cur[0]:
                sel[key] = (score, (slug, kind, idx, team, low))
        return out, {k: v[1] for k, v in sel.items()}

    win, win_ex = finalize(W, "win")
    lose, lose_ex = finalize(L, "lose")
    return win, win_ex, lose, lose_ex


def sketch_svg(points, w=154, h=56, color="#0071e3"):
    iw, ih = w - 8, h - 8

    def X(x):
        return 4 + x * iw

    def Y(y):
        return 4 + (1 - y) * ih

    d = " ".join(("M" if i == 0 else "L") + f"{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(points))
    lo = min(points, key=lambda p: p[1])
    hi = max(points, key=lambda p: p[1])
    return (
        f"<svg class='sk' viewBox='0 0 {w} {h}' role='img'>"
        f"<line x1='4' y1='{Y(.5):.1f}' x2='{w-4}' y2='{Y(.5):.1f}' stroke='#d2d2d7' stroke-width='1'"
        " stroke-dasharray='3 3'/>"
        f"<path d='{d}' fill='none' stroke='{color}' stroke-width='2' stroke-linejoin='round'"
        " stroke-linecap='round'/>"
        f"<circle cx='{X(lo[0]):.1f}' cy='{Y(lo[1]):.1f}' r='2.6' fill='{color}'/>"
        f"<circle cx='{X(hi[0]):.1f}' cy='{Y(hi[1]):.1f}' r='2.4' fill='none' stroke='{color}'"
        " stroke-width='1.4'/></svg>")


def real_svg(slug, kind, idx, w=240, h=74):
    wins = compute_windows(slug, True)
    window = wins["games"].get(int(idx)) if kind == "game" else wins.get("series")
    sides = market_series(slug, idx, kind, window) if kind == "game" else market_series(slug, 0, "series", window)
    if not sides:
        return "<div class='nochart'>样本无序列</div>"
    t0 = min(v[0][0][0] for v in sides.values())
    t1 = max(v[0][-1][0] for v in sides.values())
    span = (t1 - t0) or 1
    iw, ih = w - 8, h - 8

    def X(t):
        return 4 + (t - t0) / span * iw

    def Y(p):
        return 4 + (1 - max(0.0, min(1.0, p))) * ih

    parts = [f"<svg class='ch2' viewBox='0 0 {w} {h}' role='img'>"]
    parts.append(f"<line x1='4' y1='{Y(.5):.1f}' x2='{w-4}' y2='{Y(.5):.1f}' stroke='#d2d2d7'"
                 " stroke-width='1' stroke-dasharray='3 3'/>")
    for key in sorted(sides):
        pts, _gran = sides[key]
        c = "#0071e3" if key == "0" else "#ff9f0a"
        d = " ".join(("M" if i == 0 else "L") + f"{X(t):.1f},{Y(p):.1f}"
                     for i, (t, p) in enumerate(lttb(pts, 150)))
        parts.append(f"<path d='{d}' fill='none' stroke='{c}' stroke-width='1.6'"
                     " stroke-linejoin='round' stroke-linecap='round'/>")
    parts.append("</svg>")
    return "".join(parts)


def render(win, win_ex, lose, lose_ex, key, colors, out_path, updated, meta, counts):
    def is_lose(lab):
        return lab.startswith("B")

    def cell_n(lab, game, scope):
        src = lose if is_lose(lab) else win
        return src.get((game, scope, lab), {}).get("n", 0)

    def stat_of(lab, cell):
        return (lose if is_lose(lab) else win).get((cell[0], cell[1], lab), {})

    def ex_of(lab, cell):
        return (lose_ex if is_lose(lab) else win_ex).get((cell[0], cell[1], lab))

    def key_n(lab):
        """该形态在**一级（关键）赛事**里的胜/负方侧样本合计。"""
        store = key[2] if is_lose(lab) else key[0]
        return sum(store.get((g, sc, lab), {}).get("n", 0) for g, sc in CELLS)

    def status_game(lab, game):
        """成熟度按游戏单独判：LoL 的样本不能替 CS2 背书。"""
        tot = [cell_n(lab, game, s) for s in ("整局", "逐局")]
        ok = [x for x in tot if x >= NEED]
        if is_lose(lab):
            # B 类只活在负方侧：它们不是买入信号，是风控/证伪信号
            if len(ok) >= 2:
                return ("ok", "风控可用")
            return ("obs", "只作观察") if not ok else ("part", "风控·部分可用")
        if not ok:
            return ("obs", "只作观察")
        if len(ok) >= 2:
            return ("ok", "可用")
        return ("part", "部分可用")

    def badges(lab):
        return "".join(f"<span class='badge {status_game(lab, g)[0]}'>{g} {status_game(lab, g)[1]}</span>"
                       for g in ("LoL", "CS2"))

    rows = []
    for lab in ORDER:
        cells = "".join(f"<td class='{'z' if cell_n(lab, g, s) == 0 else ''}'>{cell_n(lab, g, s) or '·'}</td>"
                        for g, s in CELLS)
        kn = key_n(lab)
        keycell = f"<td class='{'z' if kn == 0 else 'keyc'}'>{kn or '·'}</td>"
        rows.append(
            f"<tr><td><b>{lab}</b></td><td class='fam'>{P[lab]['family']}</td>"
            f"<td class='shp'>{html.escape(P[lab]['shape'])}</td>{cells}"
            f"<td class='statuscell'>{badges(lab)}</td></tr>")

    cards = []
    for lab in ORDER:
        d = P[lab]
        side_cn = "负方侧" if is_lose(lab) else "胜方侧"
        stat_bits, real_bits = [], []
        for game in ("LoL", "CS2"):
            cells_g = [(game, "整局"), (game, "逐局")]
            big = max(cells_g, key=lambda c: cell_n(lab, *c))
            st = stat_of(lab, big)
            if not st:
                stat_bits.append(f"<b>{game}</b>：样本不足")
                continue
            mult = (f" · 低点买入到结算 ≈ <b>{st.get('mult')}×</b>（事后倍数，非胜率）"
                    if st.get("mult") and not is_lose(lab) else "")
            stat_bits.append(
                f"<b>{game} · {big[1]}</b>（{side_cn}）：n={st.get('n', 0)}"
                f" · 起点中位 {st.get('pre')} · 低点中位 {st.get('low')}"
                f" · 穿越50c中位 {st.get('cross')} · 到99.5c中位 "
                f"{st.get('t99') if st.get('t99') is not None else '—'} 分钟{mult}")
            sel = ex_of(lab, big)
            if sel:
                slug, kind, idx, team, low = sel
                real_bits.append(
                    real_svg(slug, kind, idx)
                    + f"<div class='cap'><b>{game}</b> 真实样本：{html.escape(slug)}"
                    + (f" · 第 {idx} 局" if kind == "game" else " · 整场")
                    + f" · 赢家 {html.escape(str(team or ''))} · 低点 {low}</div>")
        sttxt = " ｜ ".join(stat_bits) + f" · <b>其中一级赛事 {key_n(lab)} 例</b>"
        real = "".join(real_bits)
        if is_lose(lab):
            sttxt = sttxt.replace("（负方侧）", "（负方侧 · 风控视角）")
        cards.append(f"""<div class="card pat">
<div class="phead"><div><span class="famtag">{d['family']}</span><b>{lab}</b></div>
<span>{badges(lab)}</span></div>
<div class="pbody">
<div class="pvis">{sketch_svg(d['sketch'], color=colors.get(lab, '#0071e3'))}
<div class="cap">示意线条（形状，非价格）</div>
<div class="realbox">{real}</div></div>
<div class="ptxt">
<p><b>线条特征</b>：{html.escape(d['shape'])}</p>
<p><b>机器判定</b>：{html.escape(d['trigger'])}</p>
<p><b>买点</b>：{html.escape(d['entry'])}</p>
<p><b>卖点 / 目标</b>：{html.escape(d['exit'])}</p>
<p class="warn"><b>失效 / 反例</b>：{html.escape(d['invalid'])}</p>
<p class="stat">{sttxt}{(' · ' + mult) if mult else ''}</p>
</div></div></div>""")

    obs = [lab for lab in ORDER if any(status_game(lab, g)[0] != "ok" for g in ("LoL", "CS2"))]
    lol_ok = len([l for l in ORDER if status_game(l, "LoL")[0] == "ok"])
    cs_ok = len([l for l in ORDER if status_game(l, "CS2")[0] == "ok"])
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230071e3'/%3E%3Cpath d='M14 42l10-14 8 9 18-23' stroke='white' stroke-width='6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E">
<title>形态速查（交易决策用 · {updated}）</title>
<style>
:root{{--blue:#0071e3;--bg:#f5f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6e6e73;--bd:#e5e5ea;--amber:#b25000;--green:#1a7f37;--red:#c0392b}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
.bar-top{{background:#1d1d1f;color:#fff;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px}}
.bar-top b{{font-weight:600}} .bar-top span{{color:#a1a1a6;font-size:13px}}
html.embed .bar-top{{display:none}} html.embed .wrap{{padding-top:14px}}
.wrap{{max-width:1140px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:25px;margin:16px 0 6px;font-weight:600}} h2{{font-size:19px;margin:30px 0 10px;font-weight:600}}
.sub{{color:var(--mu);margin:0 0 14px;font-size:13.5px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:12px 0}}
.card{{background:var(--card);border-radius:14px;padding:16px}}
.card .k{{font-size:12px;color:var(--mu)}} .card .v{{font-size:24px;font-weight:600;margin-top:4px}}
.card .n{{font-size:12px;color:var(--mu);margin-top:2px}}
.warnbox{{background:#fff8e6;border:1px solid #ffe0a3;border-radius:14px;padding:14px 16px;margin:14px 0;font-size:13.5px;color:#5a4300}}
.tbl{{background:var(--card);border-radius:14px;padding:6px 12px;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:var(--mu);font-weight:500;padding:9px 8px;border-bottom:1px solid var(--bd);white-space:nowrap}}
td{{padding:9px 8px;border-bottom:1px solid #f2f2f5;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
td.shp{{max-width:330px;color:#3a3a3c}}
td.fam{{color:var(--mu);white-space:nowrap}}
td.z{{color:#c7c7cc}}
td.ok{{color:var(--green);white-space:nowrap;font-weight:600}}
td.keyc{{background:#f0f7ff;color:#0058b0;font-weight:600}}
td.part{{color:var(--amber);white-space:nowrap;font-weight:600}}
td.obs{{color:var(--mu);white-space:nowrap}}
.pat{{margin:14px 0}}
.phead{{display:flex;justify-content:space-between;align-items:center;gap:8px;border-bottom:1px solid var(--bd);padding-bottom:10px}}
.phead b{{font-size:17px;margin-left:8px}}
.famtag{{font-size:11px;background:#eef4ff;color:var(--blue);border-radius:999px;padding:2px 9px}}
.badge{{font-size:12px;border-radius:999px;padding:3px 10px;white-space:nowrap}}
.badge.ok{{background:#e8f6ec;color:var(--green)}}
.badge.part{{background:#fdf3e3;color:var(--amber)}}
.badge.obs{{background:#f0f0f2;color:var(--mu)}}
.pbody{{display:grid;grid-template-columns:290px 1fr;gap:18px;padding-top:12px}}
@media(max-width:760px){{.pbody{{grid-template-columns:1fr}}}}
.pvis{{text-align:center}}
svg.sk{{width:154px;height:56px}}
svg.ch2{{width:100%;max-width:290px;height:74px;margin-top:10px}}
.realbox{{border-top:1px dashed var(--bd);margin-top:10px;padding-top:6px}}
.cap{{font-size:11.5px;color:var(--mu);margin-top:4px}}
.nochart{{font-size:12px;color:var(--mu);padding:14px 0}}
.ptxt p{{margin:6px 0;font-size:13.5px}}
.ptxt p.warn{{color:#8a4b00}}
.ptxt p.stat{{color:var(--mu);font-size:12.5px;border-top:1px dashed var(--bd);padding-top:8px}}
.pg{{display:block;background:#fff;border-radius:12px;padding:12px 14px;margin:8px 0;text-decoration:none;color:inherit}}
.pg b{{display:block;font-size:14px}} .pg div{{font-size:12.5px;color:var(--mu);margin-top:2px}}
</style>
<script>if(location.search.indexOf("embed=1")>=0)document.documentElement.classList.add("embed");</script></head><body>
<div class="bar-top"><b>Polymarket 电竞情报库 · 形态速查（交易决策用）</b><span>生成 {updated} · {meta}</span></div>
<div class="wrap">
<h1>每个形态：长什么样、买在哪、什么时候作废</h1>
<p class="sub">样本 = 一级赛事已结算市场（LoL {counts['LoL']} 场 / CS2 {counts['CS2']} 场；仅比赛内窗口）· 定义来自 tools/classify_pattern.py v1.3 · 分级口径 config/esports_tiers.json</p>

<div class="cards">
<div class="card"><div class="k">分类器定义</div><div class="v">16</div><div class="n">A 类 7 + B 类 5 + C 类 3 + 热门压制</div></div>
<div class="card"><div class="k">已观测到</div><div class="v">15</div><div class="n">胜方侧 10 · 负方侧 5 个 B 类</div></div>
<div class="card"><div class="k">当前可用（≥20 例 × 两格）</div><div class="v">LoL {lol_ok} / CS2 {cs_ok}</div><div class="n">按游戏分别判定；其余进观察区</div></div>
<div class="card"><div class="k">只作观察</div><div class="v">{len(obs)}</div><div class="n">样本不足，不进策略层</div></div>
</div>

<div class="warnbox"><b>读之前必须知道的一件事</b>：下面每个形态的样本，都是<b>已经赢下来的那一侧</b>的价格轨迹。
所以这是"赢家长什么样"，不是"看到这个形状买入就有胜率"。同一个形状在输家身上也出现过——
要变成交易信号，必须加上"当下能不能判定这是赢家侧"的条件（如破位确认、多源情报、联赛信誉）。
胜率与期望值的回测另见 <a href="pattern_shapes_index.html">形态结构库总览</a> 的"机械抄底回测"一节（LoL / CS2 分开读）。</div>

<h2>一、一览：16 种形态 × 四主格样本</h2>
<p class="sub"><b>本库只收一级（关键）赛事</b>：LCK/LPL/LEC/LCP/LCS/KeSPA/德杯/国际赛，
与 CS2 的 ESL Pro League/IEM/BLAST/EWC/Thunderpick 正赛。
EMEA Masters 与小区域联赛、资格赛/升降级赛已按 2026-10-15 指令从统计口径剔除
（LoL 剔除 276 场、CS2 剔除 7 场；原始快照与旧全分级 JSON 仍留档，不参与出数）。</p>
<p class="sub">A / C / 热门压制 这 11 行是<b>胜方侧</b>样本（赢家价格长什么样）；
B1 / B2 / B3 / B4 直线 / B4 低开 这 5 行是<b>负方侧</b>样本（输家价格怎么归零）——
两类含义完全不同，不要横向比较频次。</p>
<div class="tbl"><table>
<thead><tr><th>形态</th><th>家族</th><th>线条特征（一句话）</th><th>LoL<br>整局</th><th>LoL<br>逐局</th><th>CS2<br>整局</th><th>CS2<br>逐局</th><th>状态</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="sub">样本为 0 的格用「·」表示。状态<b>按游戏分别判定</b>：某游戏的整局/逐局两格都 ≥{NEED} 例才标"可用"；一个游戏够、另一个不够时分开标注，绝不互相背书。</p>

<h2>二、逐个形态：示意线条 + 真实样本 + 动作</h2>
{''.join(cards)}

<h2>三、观察区（样本不足，先别当策略用）</h2>
<div class="tbl"><table><thead><tr><th>形态</th><th>侧</th><th>四格样本</th><th>为什么先放观察区 / 怎么用</th></tr></thead><tbody>
{''.join("<tr><td><b>" + l + "</b></td><td>" + ("负方侧" if is_lose(l) else "胜方侧") + "</td><td>" + " / ".join(str(cell_n(l, g, s)) for g, s in CELLS) + "</td><td>" + html.escape(P[l]['invalid']) + "</td></tr>" for l in obs)}
</tbody></table></div>

<h2>四、相关页面</h2>
<div class="tbl" style="padding:6px 10px">
<a class="pg" href="pattern_taxonomy.html"><b>形态分级体系</b><div>一共多少种、怎么分层、每个格子还差多少样本</div></a>
<a class="pg" href="pattern_charts_lol_tier1_2026-07_2026-10.html"><b>LoL 一级赛事曲线图 · {counts['LoL']} 场 / {counts['LoL_charts']} 张</b><div>两条队伍赔率线 + 50c 交叉点，可按粒度筛选</div></a>
<a class="pg" href="pattern_charts_cs2_tier1_2026-07_2026-10.html"><b>CS2 一级赛事曲线图 · {counts['CS2']} 场 / {counts['CS2_charts']} 张</b><div>同上</div></a>
<a class="pg" href="pattern_shapes_index.html"><b>形态结构库总览</b><div>频次 / 深水占比 / 让一追二 / 机械抄底期望值</div></a>
<a class="pg" href="pattern_frequency_index.html"><b>形态频率排行 · 按覆盖场次</b><div>哪个形态最常出现；展开看每场案例与小差异</div></a>
</div>
</div></body></html>"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="utf-8").write(html_doc)
    return html_doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", action="append", default=[], help="game=path（可重复）")
    ap.add_argument("--out", default="reports/pattern_playbook.html")
    ap.add_argument("--updated", default="2026-10-15")
    args = ap.parse_args()
    if not args.section:
        print("need --section game=path")
        return 2
    paths = [("CS2" if s.split("=", 1)[0].lower().startswith("cs2") else "LoL",
              s.split("=", 1)[1]) for s in args.section]
    win, win_ex, lose, lose_ex = collect(paths)
    key = collect(paths, tiers=("一级",))
    colors = {"B1_尾盘崩塌": "#c0392b", "B2_死亡螺旋": "#c0392b", "B3_假反弹后归零": "#c0392b",
              "B4_直线阴跌": "#c0392b", "B4_低开阴跌": "#c0392b", "C1_中位震荡": "#6e6e73",
              "热门全程压制": "#1a7f37"}
    meta = " · ".join(f"{g} {p.split('/')[-1]}" for g, p in paths)
    counts = {"LoL": 0, "CS2": 0, "LoL_charts": 0, "CS2_charts": 0}
    for game, path in paths:
        d = json.load(open(path, encoding="utf-8"))
        for m in d.get("matches", []):
            counts[game] += 1
            counts[game + "_charts"] += len(m.get("series") or {}) + sum(
                len(s) for s in (m.get("games") or {}).values()
            )
    doc = render(win, win_ex, lose, lose_ex, key, colors, args.out, args.updated, meta, counts)
    print(f"playbook -> {args.out}（{len(doc)//1024} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
