"""Regression checks for the read-only pattern decision workspace."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_pattern_decision_site as site  # noqa: E402


def snapshot_corpus_available() -> bool:
    return site.SNAP_ROOT.is_dir() and any(
        path for path in site.SNAP_ROOT.iterdir() if not path.name.startswith(".")
    )


def test_dataset_counts_unique_training_matches():
    if not snapshot_corpus_available():
        pytest.skip("optional raw snapshot corpus is not included in the standalone repository")
    dataset = site.build_dataset(list(site.DEFAULT_SECTIONS), recent_days=1, recent_limit=1)
    training = dataset["training"]
    assert training
    assert dataset["stats"]["training_matches"] == len({row["slug"] for row in training})
    assert dataset["stats"]["training_matches"] < dataset["stats"]["training_samples"]


def test_workspace_uses_match_dedup_and_calibrated_rates():
    template = site.TEMPLATE.read_text(encoding="utf-8")
    assert "uniqueMatchMass" in template
    assert "betaRate" in template
    assert "count>=8" in template
    assert "按比赛去重" in template
    assert "相似度 ≠ 胜率" in template


def test_workspace_keeps_entry_band_constructive():
    template = site.TEMPLATE.read_text(encoding="utf-8")
    assert "entryStatus='等待回踩'" in template
    assert "未进区不追" in template
    assert "历史触达" in template


def test_label_metadata_uses_plain_chinese_names():
    labels = site.label_meta()
    assert labels["A3_折价修复"]["title"] == "高位回撤修复"
    assert labels["B4_直线阴跌"]["title"] == "持续阴跌"
    assert labels["C1_中位震荡"]["title"] == "中线反复拉锯"
    for meta in labels.values():
        public_text = " ".join(str(value) for value in meta.values())
        assert "A1" not in public_text
        assert "A2" not in public_text
        assert "A3" not in public_text
        assert "B1" not in public_text
        assert "B4" not in public_text
        assert "C2" not in public_text


def test_workspace_exposes_level_split_and_history_comparison():
    template = site.TEMPLATE.read_text(encoding="utf-8")
    assert "小局预测" in template
    assert "整场预测" in template
    assert "新版形态审计" in template
    assert "对比同类历史案例" in template
    assert "P1 观察版" in template
    assert "候选" in template


def test_future_shape_keeps_extrema_instead_of_uniform_summary_points():
    points = [(0, 50), (1, 80), (2, 50), (3, 20), (4, 50)]
    picked = site.shape_downsample(points, 4)
    assert picked[0] == points[0]
    assert picked[-1] == points[-1]
    assert (1, 80) in picked or (3, 20) in picked
    assert site.MAX_PUBLIC_POINTS >= 180


def test_training_records_reference_real_minute_market_paths():
    if not snapshot_corpus_available():
        pytest.skip("optional raw snapshot corpus is not included in the standalone repository")
    dataset = site.build_dataset(list(site.DEFAULT_SECTIONS), recent_days=0, recent_limit=0)
    market_ids = {market["id"] for match in dataset["matches"] for market in match.get("markets", {}).values()}
    assert dataset["training"]
    for row in dataset["training"][:200]:
        assert row["mid"] in market_ids
        assert row["gr"] in {"1分钟级", "5分钟级", "10分钟级", "数据不足"}
        assert 0 <= row["cut"] <= 100


def test_candidate_evidence_chart_uses_real_paths_and_decision_levels():
    template = site.TEMPLATE.read_text(encoding="utf-8")
    assert "function candidateChart" in template
    assert "representatives" in template
    assert "25%–75% 区间" in template
    assert "双方真实价格路径" in template
    assert "买入观察" in template
    assert "失效边界" in template
    assert "不使用固定摘要点" in template


def test_precomputed_dataset_supports_standalone_deployment():
    dataset = site.load_json(ROOT / "data" / "pattern_decision_data.json", {})
    assert dataset
    assert dataset["training"]
    assert dataset["matches"]
    assert dataset["stats"]["training_samples"] == len(dataset["training"])
    assert dataset["stats"]["training_matches"] < dataset["stats"]["training_samples"]
    assert {row["l"] for row in dataset["training"]} <= {"game", "series"}
