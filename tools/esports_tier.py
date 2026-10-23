#!/usr/bin/env python3
"""赛事分级判定（关键比赛过滤口径，config/esports_tiers.json 驱动）。

为什么要它（2026-10-15 用户指令"把不关键的比赛筛选过滤掉"）：
形态库目前把所有比赛混在一起统计，而样本里大部分是 EMEA Masters、区域联赛、
资格赛/升降级赛——用这些"次级/弱队互啄"样本得出的形态占比，直接套到
LCK/LPL/LEC 与 CS2 一级赛事上是不成立的。分级口径统一后：

  - 页面可以分开给"仅一级 / 二级 / 其他"的样本与形态分布；
  - 一级样本不足时，如实显示"样本不足"，而不是用次级样本硬撑。

判定顺序：其他（Qualifier/Promotion/空）-> 一级 -> 二级 -> default_tier。
先判"其他"是必须的：'Esports World Cup Open Qualifier Group 14' 含有一级关键词
'Esports World Cup'，不先拦就会被误判成一级（同 EWC Open Qualifier 的教训）。

用法：
    from esports_tier import tier_of
    tier_of("ESL Pro League Group Stage", "CS2")   # -> '一级'
    tier_of("EMEA Masters Swiss Stage", "LoL")     # -> '二级'
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "esports_tiers.json")

TIERS = ("一级", "二级", "其他")
_cache: dict | None = None


def load_config(path: str = CONFIG) -> dict:
    global _cache
    if path == CONFIG and _cache is not None:
        return _cache
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        cfg = {"default_tier": "其他", "games": {}}
    if path == CONFIG:
        _cache = cfg
    return cfg


def normalize_game(game: str) -> str:
    g = (game or "").strip().lower()
    if g.startswith("cs2") or g.startswith("csgo") or g == "counter-strike":
        return "CS2"
    if g.startswith("dota"):
        return "Dota2"
    if g.startswith("lol") or g.startswith("league"):
        return "LoL"
    return game or ""


def tier_of(league_raw: str, game: str, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    game = normalize_game(game)
    rules = (cfg.get("games") or {}).get(game) or {}
    raw = (league_raw or "").strip()
    for key in ("其他", "一级", "二级"):
        for kw in rules.get(key) or []:
            if kw == "":
                if raw == "":
                    return key
                continue
            if kw.lower() in raw.lower():
                return key
    return cfg.get("default_tier", "其他")


def tier_counts(rows, game_of=lambda r: r.get("game") or r.get("league"),
                raw_of=lambda r: r.get("league_raw") or ""):
    """rows -> {tier: n}，方便页面直接出分级分布。"""
    out = {t: 0 for t in TIERS}
    for r in rows:
        out[tier_of(raw_of(r), game_of(r))] += 1
    return out


if __name__ == "__main__":  # 自检
    cases = [
        ("ESL Pro League Group Stage", "CS2", "一级"),
        ("BLAST Open Porto Group A", "CS2", "一级"),
        ("Esports World Cup Group C", "CS2", "一级"),
        ("Esports World Cup Open Qualifier Group 14", "CS2", "其他"),
        ("Thunderpick World Championship Group A", "CS2", "一级"),
        ("EMEA Masters Swiss Stage", "LoL", "二级"),
        ("LCK Playoffs", "LoL", "一级"),
        ("LPL Regional Finals Playoffs", "LoL", "一级"),
        ("LEC Playoffs", "LoL", "一级"),
        ("Demacia Cup Global Invitational Group Stage", "LoL", "一级"),
        ("LCP Promotion: Wild Card Playoffs", "LoL", "其他"),
        ("World Star Challengers Invitational Group C", "LoL", "二级"),
        ("LCK Challengers League Playoffs", "LoL", "二级"),
        ("LCK Round 3-4 Legend Group", "LoL", "一级"),
        ("LCK Play-In", "LoL", "一级"),
        ("LPL Group Ascend", "LoL", "一级"),
        ("LCP Swiss Stage", "LoL", "一级"),
        ("Mid-Season Invitational Play-In", "LoL", "一级"),
        ("Esports World Cup Group A", "LoL", "一级"),
        ("LCK Challengers League Play-In", "LoL", "二级"),
        ("TCL Play-Ins", "LoL", "二级"),
        ("", "LoL", "其他"),
    ]
    bad = 0
    for raw, game, want in cases:
        got = tier_of(raw, game)
        flag = "ok " if got == want else "ERR"
        if got != want:
            bad += 1
        print(f"{flag} {game:5} {raw[:48]:50} -> {got}（期望 {want}）")
    print("self-check:", "PASS" if not bad else f"FAIL {bad}")
    raise SystemExit(1 if bad else 0)
