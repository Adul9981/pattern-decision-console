#!/usr/bin/env python3
"""Merge freshly extracted shape entries into an existing pattern section JSON.

Why: the tier-1 section files are built by tools/build_pattern_library.py, but new
matches arrive after the original range.  Re-running the whole range would refetch
everything; instead we extract only the new slugs into a temp JSON and merge here.

Guards:
  - only tier-1 matches are kept (config/esports_tiers.json);
  - same slug is replaced, never duplicated;
  - the previous file is copied to reports/archive/pattern_backup_<date>/ first;
  - freq_game / freq_series / league_dist / range are recomputed from matches.

Usage:
  python3 tools/merge_pattern_shapes.py \
      --base reports/pattern_shapes_lol_tier1_2026-07_2026-10.json \
      --new /tmp/new_shapes.json --range 2026-07-01:2026-10-19
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from esports_tier import tier_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def game_of(slug: str) -> str:
    if slug.startswith("cs2-"):
        return "CS2"
    if slug.startswith("dota2-"):
        return "Dota2"
    if slug.startswith("lol-"):
        return "LoL"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--range", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--game", default="", choices=("", "LoL", "CS2", "Dota2"),
                    help="只合并该游戏的场次（防止 LoL/CS2 互相污染）")
    args = ap.parse_args()

    base = json.load(open(args.base, encoding="utf-8"))
    new = json.load(open(args.new, encoding="utf-8"))
    merged = {m["slug"]: m for m in base.get("matches") or []}

    added = replaced = skipped = 0
    for m in new.get("matches") or []:
        row_game = game_of(m.get("slug") or "")
        if args.game and row_game != args.game:
            skipped += 1
            continue
        tier = tier_of(m.get("league_raw") or "", row_game)
        if tier != "一级":
            skipped += 1
            print(f"  skip (tier={tier}): {m.get('slug')} | {m.get('league_raw')}")
            continue
        if m["slug"] in merged:
            replaced += 1
        else:
            added += 1
        merged[m["slug"]] = m

    matches = sorted(merged.values(), key=lambda m: (m.get("date") or "", m.get("slug") or ""))
    freq_game, freq_series = Counter(), Counter()
    for m in matches:
        for sides in (m.get("games") or {}).values():
            for info in sides.values():
                for lab in info.get("labels") or []:
                    freq_game[lab] += 1
        for info in (m.get("series") or {}).values():
            for lab in info.get("labels") or []:
                freq_series[lab] += 1

    backup_dir = os.path.join(ROOT, "reports", "archive", f"pattern_backup_{dt.date.today().isoformat()}")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, os.path.basename(args.base))
    if not os.path.exists(backup):
        shutil.copy2(args.base, backup)

    base["matches"] = matches
    base["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if args.range:
        base["range"] = args.range
    base["freq_game"] = dict(freq_game.most_common())
    base["freq_series"] = dict(freq_series.most_common())
    base["league_dist"] = dict(Counter(m.get("league") or "-" for m in matches).most_common())

    out = args.out or args.base
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(base, fh, ensure_ascii=False, indent=1)
    print(f"{out}: {len(matches)} 场（新增 {added} / 替换 {replaced} / 跳过低级 {skipped}）")
    print(f"备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
