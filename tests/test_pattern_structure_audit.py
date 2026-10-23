"""Regression checks for the task-8 pattern taxonomy audit gallery."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_pattern_structure_audit as audit  # noqa: E402


def test_audit_svg_keeps_both_one_minute_sides_and_midline():
    sides = {
        "0": ([(0.0, 0.55), (60.0, 0.30), (120.0, 0.00)], 60),
        "1": ([(0.0, 0.45), (60.0, 0.70), (120.0, 1.00)], 60),
    }
    svg, raw_points, drawn_points = audit.audit_svg(sides, {"1": {"end": 1.0}})
    assert raw_points == 6
    assert drawn_points == 6
    assert "#0a6ed1" in svg
    assert "#ff9f0a" in svg
    assert "50:50" in svg
    assert "stroke-dasharray='5 5'" in svg


def test_audit_targets_cover_first_taxonomy_review_batch():
    assert len(audit.TARGET_LABELS) == 6
    assert "B4_直线阴跌" in audit.TARGET_LABELS
    assert "A3_折价修复" in audit.TARGET_LABELS
    assert "B1_尾盘崩塌" in audit.TARGET_LABELS


def test_audit_sport_totals_count_unique_matches_and_market_sides():
    metadata = {"lol": {"matches": [{
        "series": {"0": {}, "1": {}},
        "games": {"1": {"0": {}, "1": {}}, "2": {"0": {}, "1": {}}},
    }]}}
    assert audit.sport_totals(metadata, "lol") == (1, 6)
    assert audit.share(3, 6) == "50%"


def test_audit_scope_totals_do_not_mix_games_and_series():
    metadata = {"lol": {"matches": [{
        "slug": "demo",
        "series": {"0": {"span_min": 120}, "1": {"span_min": 120}},
        "games": {
            "1": {"0": {"span_min": 40}, "1": {"span_min": 40}},
            "2": {"0": {"span_min": 50}, "1": {"span_min": 50}},
        },
    }]}}
    assert audit.scope_totals(metadata, "lol", "game") == (1, 4, 45)
    assert audit.scope_totals(metadata, "lol", "series") == (1, 2, 120)


def test_audit_user_facing_names_are_plain_chinese():
    for internal in audit.TARGET_LABELS:
        shown = audit.display_name(internal)
        assert "_" not in shown
        assert not shown.startswith(("A", "B", "C"))
    prose = audit.display_text("失效后变成 B3 假反弹归零，或 B4 阴跌")
    assert "B3" not in prose
    assert "B4" not in prose
