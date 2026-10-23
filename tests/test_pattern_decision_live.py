"""Offline regression checks for the cloud-side pattern decision refresh."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import update_pattern_decision_live as live  # noqa: E402


def market(slug: str, question: str, sports_type: str) -> dict:
    return {
        "slug": slug,
        "question": question,
        "groupItemTitle": "Match Winner" if sports_type == "moneyline" else question,
        "sportsMarketType": sports_type,
        "closed": False,
        "outcomes": json.dumps(["Alpha", "Beta"]),
        "outcomePrices": json.dumps(["0.55", "0.45"]),
        "clobTokenIds": json.dumps(["11", "22"]),
        "gameStartTime": "2026-10-22 12:00:00+00",
    }


def test_target_games_keeps_series_and_game_winners_only():
    event = {
        "slug": "lol-alpha-beta-2026-10-22",
        "markets": [
            market("lol-alpha-beta-2026-10-22", "Alpha vs Beta Match Winner", "moneyline"),
            market("lol-alpha-beta-2026-10-22-game1", "Game 1 Winner", "child_moneyline"),
            market("lol-alpha-beta-2026-10-22-game2", "Game 2 Winner", "child_moneyline"),
            market("lol-alpha-beta-2026-10-22-total-games-2pt5", "Games Total O/U 2.5", "totals"),
        ],
    }
    series, games = live.target_games(event)
    assert series and series["slug"].endswith("lol-alpha-beta-2026-10-22")
    assert sorted(games) == [1, 2]


def test_watchlist_filter_uses_registered_leagues_and_worlds():
    watchlist = {
        "games": {
            "lol": {
                "include_keywords": ["League of Legends"],
                "league_keywords": ["LCK", "Worlds"],
            },
            "cs2": {
                "include_keywords": ["Counter-Strike"],
                "league_keywords": ["BLAST"],
            },
        }
    }
    assert live.watchlist_match({"title": "League of Legends: A vs B - Worlds Swiss", "slug": "x"}, watchlist) == "lol"
    assert live.watchlist_match({"title": "Counter-Strike: A vs B - BLAST Open", "slug": "x"}, watchlist) == "cs2"
    assert live.watchlist_match({"title": "Counter-Strike: A vs B - DACH Masters", "slug": "x"}, watchlist) is None


def test_live_window_keeps_future_space_for_candidate_path():
    floor = live.parse_ts("2026-10-22 12:00:00+00")
    now = floor + 20 * 60
    sides = {
        0: [(floor + index * 60, 0.55 + index * 0.002) for index in range(21)],
        1: [(floor + index * 60, 0.45 - index * 0.002) for index in range(21)],
    }
    event = {"slug": "lol-alpha-beta-2026-10-22", "title": "League of Legends: Alpha vs Beta (BO3) - LCK"}
    winner = market("lol-alpha-beta-2026-10-22-game1", "Game 1 Winner", "child_moneyline")
    payload = live.market_payload(
        event=event,
        sport="lol",
        level="game",
        game=1,
        market=winner,
        sides=sides,
        floor=floor,
        now=now,
        phase="live",
    )
    assert payload is not None
    assert payload["status"] == "盘面进行中"
    assert 35 <= payload["observed_max"] <= 75
    assert payload["last_ts"] == now


def test_prematch_can_use_current_outcome_price_without_fake_history():
    floor = live.parse_ts("2026-10-22 12:00:00+00")
    now = floor - 3600
    winner = market("lol-alpha-beta-2026-10-22", "Alpha vs Beta Match Winner", "moneyline")
    floor_floor = floor
    event = {"slug": "lol-alpha-beta-2026-10-22", "title": "League of Legends: Alpha vs Beta (BO3) - LCK"}
    payload = live.market_payload(
        event=event,
        sport="lol",
        level="series",
        game=None,
        market=winner,
        sides={0: [(now, 0.55)], 1: [(now, 0.45)]},
        floor=floor_floor,
        now=now,
        phase="prematch",
    )
    assert payload is not None
    assert payload["status"] == "赛前待走势"
    assert all(len(path) == 1 for path in payload["paths"].values())


def test_watchlist_time_window_drives_future_scan_range():
    watchlist = {"time_window": {"recent_started_days": 1.5, "upcoming_days": 2}}
    assert live.time_window_days(watchlist) == (1.5, 2.0)
    assert live.time_window_days({}) == (2.0, 2.0)


def test_stake_market_flattening_and_devig_probability():
    winner = {
        "id": "winner-1",
        "name": "Match Winner - Twoway",
        "status": "active",
        "updatedAt": 1_792_633_237_312,
        "outcomes": [
            {"name": "Aurora Gaming", "odds": 1.22, "active": True},
            {"name": "The MongolZ", "odds": 3.8, "active": True},
        ],
    }
    groups = [
        {"name": "main", "markets": [[winner]]},
        {"name": "winner", "markets": [[winner]]},
        {"name": "maps", "markets": [[
            {
                "id": "map-1",
                "name": "Map 1 Winner - Twoway",
                "status": "active",
                "outcomes": [{"odds": 1.4}, {"odds": 2.8}],
            }
        ]]},
    ]
    markets = live.stake_markets({"groups": groups})
    assert len(markets) == 2
    parsed = live.stake_match_winner(markets)
    assert parsed is not None
    assert [item["name"] for item in parsed["outcomes"]] == ["Aurora Gaming", "The MongolZ"]
    assert abs(sum(item["fair_probability"] for item in parsed["outcomes"]) - 1.0) < 1e-6
    assert live.stake_game_numbers(markets) == [1]


def test_schedule_links_to_matching_polymarket_match():
    schedule = {
        "sport": "lol",
        "teams": ["Anyone's Legend", "Karmine Corp"],
        "start_ts": 1_792_785_600,
    }
    match = {
        "slug": "lol-al-kc-2026-10-23",
        "sport": "lol",
        "teams": ["Anyone's Legend", "Karmine Corp"],
        "start_ts": 1_792_785_900,
        "status": "赛前待走势",
    }
    assert live.stake_matches_polymarket(schedule, match) is True
    match["teams"] = ["Karmine Corp", "Anyone's Legend"]
    assert live.stake_matches_polymarket(schedule, match) is True
    match["teams"] = ["T1", "Gen.G"]
    assert live.stake_matches_polymarket(schedule, match) is False


def test_schedule_team_aliases_handle_short_abbreviations():
    base = {"sport": "lol", "start_ts": 1_792_792_800}
    pm = {"sport": "lol", "start_ts": 1_792_792_900, "teams": ["LOS", "T1"]}
    assert live.stake_matches_polymarket({**base, "teams": ["Los Grandes", "T1 Esports"]}, pm) is True


def test_merge_replaces_previous_cloud_matches_and_preserves_training():
    base = {
        "generated_at": "old",
        "training": [{"slug": "history-1", "s": "lol", "l": "game"}],
        "matches": [
            {"slug": "cloud-old", "status": "盘面进行中", "source": "live_polymarket_cloud", "date": "2026-10-21"},
            {"slug": "history-1", "status": "已结束", "date": "2026-10-01"},
        ],
        "stats": {"training_samples": 1, "training_matches": 1},
        "source_reports": ["history.json"],
    }
    live_match = {
        "slug": "cloud-new",
        "status": "盘面进行中",
        "source": "live_polymarket_cloud",
        "date": "2026-10-22",
        "markets": {"series": {}},
    }
    merged = live.merge_dataset(base, [live_match], {"built_matches": 1}, "live source")
    assert [item["slug"] for item in merged["matches"]] == ["cloud-new", "history-1"]
    assert merged["training"] == base["training"]
    assert merged["stats"]["live_matches"] == 1
    assert "live source" in merged["source_reports"]


def test_merge_adds_future_schedule_with_polymarket_coverage():
    base = {"generated_at": "old", "training": [{"slug": "history"}], "matches": [], "stats": {}}
    match = {
        "slug": "lol-alpha-beta-2026-10-23",
        "sport": "lol",
        "teams": ["Alpha", "Beta"],
        "start_ts": 1_792_800_000,
        "status": "赛前待走势",
        "source": "live_polymarket_cloud",
        "date": "2026-10-23",
        "markets": {"series": {}},
    }
    schedule = [{
        "sport": "lol",
        "teams": ["Alpha", "Beta"],
        "start_ts": 1_792_800_100,
        "status": "待开赛",
        "polymarket_slug": "",
    }]
    merged = live.merge_dataset(base, [match], {}, "live", schedule)
    assert merged["upcoming_schedule"][0]["polymarket_slug"] == "lol-alpha-beta-2026-10-23"
    assert merged["stats"]["upcoming_schedule"] == 1


def test_template_uses_explicit_prematch_prior():
    template = (ROOT / "tools" / "pattern_decision_workspace.template.html").read_text(encoding="utf-8")
    assert "status==='赛前待走势'" in template
    assert "赛前等待走势" in template
    assert "points.length<2" in template
    assert "prematch?pool.filter" in template
    assert "赛前先验 · 前 10% 片段" in template
    assert "未来赛程" in template
    assert "upcoming_schedule" in template
    assert "PM 待开盘" in template
    assert "Stake 十进制赔率与 Polymarket 0–1 价格不混画" in template


def test_template_charts_both_sides_and_projection_layers():
    template = (ROOT / "tools" / "pattern_decision_workspace.template.html").read_text(encoding="utf-8")
    assert "双方价格路径 × 候选延续" in template
    assert "粗蓝实线＝" in template
    assert "浅橙细线＝" in template
    assert "50:50 · 中枢" in template
    assert "#ff9f0a" in template
    assert "function candidateChart" in template
    assert "同类历史分钟路径" in template
    assert "中位线与区间由同层真实分钟路径计算" in template
    assert "Draw both real series last" in template


def test_template_navigation_points_to_related_pattern_pages():
    template = (ROOT / "tools" / "pattern_decision_workspace.template.html").read_text(encoding="utf-8")
    assert 'href="pattern_structure_audit_2026-10-22.html">新版形态审计' in template
    assert 'href="pattern_library.html">形态总库' in template
    assert 'href="pattern_playbook.html">形态速查' in template
    assert 'href="pattern_taxonomy.html">形态分类' in template
    assert 'href="pattern_library.html#charts_lol">分类图表' in template
    assert 'href="pattern_frequency_index.html">频率排行' in template


def test_prematch_prior_uses_one_minute_history_without_mislabelling_current_data():
    template = (ROOT / "tools" / "pattern_decision_workspace.template.html").read_text(encoding="utf-8")
    assert "function poolGranularity()" in template
    assert "currentMarket.status==='赛前待走势'&&currentMarket.granularity==='数据不足'" in template
    assert "t.gr===poolGranularity()" in template
    assert "历史 '+pool+'先验" in template
    assert "当前行情粒度" in template
    assert "历史匹配粒度" in template


def test_candidate_curves_are_complete_and_smoothed_for_wide_laptops():
    template = (ROOT / "tools" / "pattern_decision_workspace.template.html").read_text(encoding="utf-8")
    assert "function smoothSvgPath" in template
    assert "Catmull-Rom" in template
    assert "全部原始点位参与平滑连接" in template
    assert "futureDistribution(records,invert,steps=180)" in template
    assert "const w=920,h=500" in template
    assert "@media(max-width:1360px){.candidate-workbench{grid-template-columns:1fr}" in template


def test_candidate_projections_use_the_matched_team_side_coordinate():
    template = (ROOT / "tools" / "pattern_decision_workspace.template.html").read_text(encoding="utf-8")
    assert "futureDistribution(g.records,false)" in template
    assert "representativePaths(g.records,false,5)" in template
    assert "matched record's future is already in the current-side analog" in template
    assert "观察方价格坐标" in template
    assert "不是双方同时到 100c" in template


def test_cached_labels_are_refreshed_to_current_chinese_names():
    dataset = {
        "labels": {
            "A3_折价修复": {
                "title": "A3 折价修复",
                "family": "反转",
                "summary": "不是 B4 阴跌，而是 A3_折价修复。",
            }
        }
    }
    refreshed = live.normalise_display_labels(dataset)
    label = refreshed["labels"]["A3_折价修复"]
    assert label["title"] == "高位回撤修复"
    assert label["family"] == "反转结构"
    assert "A3" not in label["summary"]
    assert "B4" not in label["summary"]


def test_atomic_publish_is_readable_by_web_server(tmp_path):
    target = tmp_path / "decision.html"
    live.atomic_write(target, "<html>ok</html>")
    assert target.read_text(encoding="utf-8") == "<html>ok</html>"
    assert target.stat().st_mode & 0o777 == 0o644
