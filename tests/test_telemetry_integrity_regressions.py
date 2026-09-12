"""Regressions from the 1.3.21 live telemetry audit."""

import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.analyzer._util import _trend_direction
from src.core.analyzer.build_track import build_track
from src.core.analyzer.html_renderer import build_html_template, render_html
from src.core.analyzer.metrics import analyze_suspension
from src.core.analyzer.prompt.context import PromptContext
from src.core.analyzer.prompt.session import build_session_sections
from src.core.telemetry_analyzer import (
    TelemetryAnalyzer,
    _clean_completed_lap_track,
)
from src.core.telemetry_capture import FrameData
from src.models import LapData, SessionData
from src.ui.app import SimLapsApp
from src.ui.services.lap_processing_service import LapProcessingService
from src.utils.config import AppConfig


def _frame(frame_number: int, *, fuel: float, x: float, z: float) -> FrameData:
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=frame_number,
        physics={
            "speed_kmh": 100.0,
            "gear": 3,
            "fuel": fuel,
            "tyre_contact_point": [
                {"x": x - 1.0, "y": 0.0, "z": z - 2.0},
                {"x": x + 1.0, "y": 0.0, "z": z - 2.0},
                {"x": x - 1.0, "y": 0.0, "z": z + 2.0},
                {"x": x + 1.0, "y": 0.0, "z": z + 2.0},
            ],
        },
        graphics={
            "normalized_car_position": frame_number / 10.0,
            "has_authoritative_progress": True,
        },
    )


def _analysis_frame(
    frame_number: int,
    *,
    speed: float,
    position: float,
    current_lap_time_ms: int,
    last_lap_time_ms: int,
    status_name: str = "AC_LIVE",
    is_in_pit_lane: bool = False,
) -> FrameData:
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=frame_number,
        physics={"speed_kmh": speed, "gear": 3, "fuel": 5.0},
        graphics={
            "normalized_car_position": position,
            "has_authoritative_progress": True,
            "current_time_ms": current_lap_time_ms,
            "last_time_ms": last_lap_time_ms,
            "completed_laps": 0,
            "is_valid_lap": True,
            "status_name": status_name,
            "session_phase": "Session",
            "is_in_pit_lane": is_in_pit_lane,
        },
    )


def _corner(frame_offset: int = 0) -> dict:
    return {
        "id": 1,
        "name": "T1",
        "apex_speed": 80.0,
        "entry_speed": 100.0,
        "exit_speed": 90.0,
        "start_frame": frame_offset,
        "end_frame": frame_offset + 20,
        "apex_frame": frame_offset + 10,
        "apex_x": 0.0,
        "apex_z": 0.0,
        "lap_pos": 0.15,
        "segment_time_s": 2.0,
        "confidence": 1.0,
        "confidence_label": "high",
        "entry_state": None,
        "apex_state": None,
        "exit_state": None,
    }


def _lap(
    lap_num: int,
    *,
    valid: bool,
    lap_time: float,
    max_speed: float,
) -> dict:
    corner = _corner(lap_num * 100)
    return {
        "lap_num": lap_num,
        "lap_time_s": lap_time,
        "lap_time_str": f"1:{lap_time - 60:05.2f}",
        "max_speed": max_speed,
        "avg_speed": 100.0,
        "fuel_used": 1.1,
        "is_valid": valid,
        "start_frame": lap_num * 100,
        "end_frame": lap_num * 100 + 100,
        "corners": [corner],
        "track": [],
    }


def _prompt_data(laps: list[dict], *, comparison_lap_num: int | None) -> dict:
    valid_laps = [lap for lap in laps if lap["is_valid"]]
    best = min(valid_laps, key=lambda lap: lap["lap_time_s"])
    ref_corner = best["corners"][0]
    return {
        "hz": 10.0,
        "laps": laps,
        "best_lap_num": best["lap_num"],
        "reference_lap_num": best["lap_num"],
        "comparison_lap_num": comparison_lap_num,
        "comparison_available": comparison_lap_num is not None,
        "valid_lap_nums": [lap["lap_num"] for lap in valid_laps],
        "coaching_lap_nums": [lap["lap_num"] for lap in valid_laps],
        "ref_corners": [ref_corner],
        "profile_corners": [{"id": 1, "name": "T1", "start": 0.1, "end": 0.2}],
        "corner_data": {},
        "corner_speeds": {1: {lap["lap_num"]: 80.0 for lap in valid_laps}},
        "analysis_mode": "full",
        "analysis_confidence": "high",
        "analysis_notes": [],
        "authoritative_progress_ratio": 1.0,
        "plausible_frame_ratio": 1.0,
        "track_label": "Laguna Seca",
        "car": "Mazda MX-5 ND Cup",
    }


def test_build_track_uses_contact_centroid_and_preserves_fuel():
    track = build_track(
        [
        _frame(0, fuel=5.0, x=-306.0, z=-206.0),
        _frame(1, fuel=4.9, x=-300.0, z=-200.0),
        ],
        hz=10.0,
    )

    assert [(point["x"], point["z"]) for point in track] == [
        (-306.0, -206.0),
        (-300.0, -200.0),
    ]
    assert [point["fuel"] for point in track] == [5.0, 4.9]


def test_build_track_prefers_contact_centroid_over_velocity():
    frame = _frame(0, fuel=5.0, x=-306.0, z=-206.0)
    frame.physics["velocity"] = {"x": 10.0, "y": 0.0, "z": 5.0}

    track = build_track([frame], hz=10.0)

    assert (track[0]["x"], track[0]["z"]) == (-306.0, -206.0)


def test_non_monotonic_series_is_not_labeled_rising_from_endpoints():
    assert _trend_direction([10.7, 86.4, 52.6, 13.0], threshold=0.15) == "FLAT"


def test_opposite_sign_equal_magnitude_camber_is_not_a_mismatch():
    corner = _corner()
    lap = {
        "lap_num": 1,
        "corners": [corner],
        "track": [
            {
            "frame": 10,
            "sus_fl": 0.05,
            "sus_fr": 0.05,
            "sus_rl": 0.05,
            "sus_rr": 0.05,
            "camber_fl": -0.02943,
            "camber_fr": 0.03020,
            }
        ],
    }

    result = analyze_suspension(
        [lap],
        [{"id": 1, "name": "T1"}],
        lap_corner_map={1: {1: corner}},
    )

    assert result["camber_notes"] == []


@pytest.mark.asyncio
async def test_ai_prompt_excludes_invalid_lap_from_coaching_aggregates(tmp_path):
    invalid = _lap(1, valid=False, lap_time=61.0, max_speed=333.0)
    valid_best = _lap(2, valid=True, lap_time=65.0, max_speed=178.0)
    valid_compare = _lap(3, valid=True, lap_time=70.0, max_speed=170.0)
    data = _prompt_data(
        [invalid, valid_best, valid_compare],
        comparison_lap_num=3,
    )

    path = await TelemetryAnalyzer(str(tmp_path))._generate_ai_prompt(
        data,
        output_prefix="invalid_exclusion",
    )
    prompt = (tmp_path / "telemetry_invalid_exclusion_ai_prompt.txt").read_text(encoding="utf-8")

    assert path == str(tmp_path / "telemetry_invalid_exclusion_ai_prompt.txt")
    assert "Top speed: 178.0 km/h" in prompt
    assert "333.0 km/h" not in prompt
    assert "Lap 1: 1:01.00 [INVALID]" in prompt


@pytest.mark.asyncio
async def test_one_valid_lap_has_no_self_comparison_coaching(tmp_path):
    invalid = _lap(1, valid=False, lap_time=61.0, max_speed=333.0)
    valid = _lap(2, valid=True, lap_time=65.0, max_speed=178.0)
    data = _prompt_data([invalid, valid], comparison_lap_num=None)

    await TelemetryAnalyzer(str(tmp_path))._generate_ai_prompt(
        data,
        output_prefix="one_valid",
    )
    prompt = (tmp_path / "telemetry_one_valid_ai_prompt.txt").read_text(encoding="utf-8")

    assert "COMPARATIVE COACHING UNAVAILABLE" in prompt
    assert "TIME LOSS RANKING" not in prompt
    assert "Compare lap:" not in prompt
    assert "+0.00s" not in prompt


@pytest.mark.asyncio
async def test_all_invalid_prompt_does_not_blame_good_progress_coverage(tmp_path):
    invalid = _lap(2, valid=False, lap_time=65.0, max_speed=178.0)
    data = _prompt_data(
        [_lap(1, valid=True, lap_time=64.0, max_speed=177.0)],
        comparison_lap_num=None,
    )
    data.update(
        laps=[invalid],
        analysis_mode="diagnostic",
        authoritative_progress_ratio=1.0,
        plausible_frame_ratio=1.0,
        ref_corners=[],
        best_lap_num=None,
        reference_lap_num=None,
        comparison_lap_num=None,
        comparison_available=False,
        valid_lap_nums=[],
        coaching_lap_nums=[],
    )

    await TelemetryAnalyzer(str(tmp_path))._generate_ai_prompt(
        data,
        output_prefix="all_invalid",
    )
    prompt = (tmp_path / "telemetry_all_invalid_ai_prompt.txt").read_text(encoding="utf-8")

    assert "no valid completed lap is available" in prompt
    assert "record at least one valid lap for coaching" in prompt
    assert "until graphics-based progress coverage is reliable" not in prompt


@pytest.mark.asyncio
async def test_html_report_is_self_contained_and_handles_no_valid_best(tmp_path):
    invalid = _lap(1, valid=False, lap_time=61.0, max_speed=333.0)
    invalid["canonical_track"] = None
    data = {
        "meta": {},
        "hz": 10.0,
        "track_key": "laguna_seca",
        "track_name": "Laguna Seca",
        "config_key": "gp",
        "config_name": "Full",
        "track_label": "Laguna Seca (Full)",
        "laps": [invalid],
        "best_lap_num": None,
        "reference_lap_num": None,
        "comparison_lap_num": None,
        "comparison_available": False,
        "valid_lap_nums": [],
        "ref_corners": [],
        "corner_data": {},
        "corner_speeds": {},
        "analysis_mode": "diagnostic",
        "analysis_confidence": "high",
        "analysis_notes": ["No valid completed laps were available."],
    }

    path = await render_html(data, str(tmp_path), "offline")
    html = (tmp_path / "telemetry_offline.html").read_text(encoding="utf-8")

    assert path == str(tmp_path / "telemetry_offline.html")
    assert "https://cdnjs.cloudflare.com" not in html
    assert "https://cdn.jsdelivr.net" not in html
    assert "Best Lap', value: bestLap ? bestLap.lap_time_str : 'N/A'" in html


@pytest.mark.asyncio
async def test_html_report_escapes_hostile_data_and_renders_it_as_text(tmp_path):
    hostile = "</script><script>alert('x')</script> & < > \u2028\u2029"
    corner = _corner()
    corner["name"] = hostile
    lap = _lap(1, valid=True, lap_time=61.0, max_speed=123.0)
    lap["corners"] = [corner]
    data = {
        "meta": {"driver": hostile},
        "hz": 10.0,
        "track_key": "hostile-track",
        "track_name": hostile,
        "config_key": "gp",
        "config_name": hostile,
        "track_label": hostile,
        "laps": [lap],
        "best_lap_num": 1,
        "reference_lap_num": 1,
        "comparison_lap_num": None,
        "comparison_available": False,
        "valid_lap_nums": [1],
        "ref_corners": [corner],
        "corner_data": {1: {1: {"apex": 80.0}}},
        "corner_speeds": {1: {1: 80.0}},
        "analysis_mode": "full",
        "analysis_confidence": "high",
        "analysis_notes": [hostile],
    }

    await render_html(data, str(tmp_path), "hostile")
    html = (tmp_path / "telemetry_hostile.html").read_text(encoding="utf-8")

    class ScriptCounter(HTMLParser):
        def __init__(self):
            super().__init__()
            self.script_count = 0

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self.script_count += 1

    parser = ScriptCounter()
    parser.feed(html)
    assert parser.script_count == 3
    assert hostile not in html
    assert r"\u003c/script\u003e" in html
    assert r"\u0026" in html
    assert r"\u003e" in html
    assert r"\u2028" in html
    assert r"\u2029" in html
    assert "innerHTML" not in html

    data_match = re.search(r"const DATA = (.*);\nconst LAP_COLORS", html)
    assert data_match is not None
    recovered = json.loads(data_match.group(1))
    assert recovered["meta"]["driver"] == hostile
    assert recovered["track_name"] == hostile
    assert recovered["analysis_notes"] == [hostile]
    assert recovered["ref_corners"][0]["name"] == hostile


@pytest.mark.asyncio
async def test_html_corner_annotations_follow_rendered_track_positions(tmp_path):
    lap = _lap(1, valid=True, lap_time=61.0, max_speed=123.0)
    lap["corners"][0]["start_frame"] = 300
    lap["corners"][0]["end_frame"] = 700
    lap["corners"][0]["apex_frame"] = 450
    lap["canonical_track"] = [
        {
            "frame": frame,
            "x": 0.0,
            "z": 0.0,
            "speed": 100.0,
            "brake": 0.0,
            "gas": 0.0,
            "gear": 3,
            "steer": 0.0,
            "yaw_rate": 0.0,
            "acc_g_x": 0.0,
            "acc_g_z": 0.0,
        }
        for frame in (100, 200, 300, 500, 700, 900, 1100)
    ]
    data = {
        "meta": {},
        "hz": 10.0,
        "track_key": "road_atlanta",
        "track_name": "Road Atlanta",
        "config_key": "gp",
        "config_name": "Full",
        "track_label": "Road Atlanta (Full)",
        "laps": [lap],
        "best_lap_num": 1,
        "reference_lap_num": 1,
        "comparison_lap_num": None,
        "comparison_available": False,
        "valid_lap_nums": [1],
        "ref_corners": lap["corners"],
        "corner_data": {},
        "corner_speeds": {},
        "analysis_mode": "full",
        "analysis_confidence": "high",
        "analysis_notes": [],
    }

    await render_html(data, str(tmp_path), "canonical")
    html = (tmp_path / "telemetry_canonical.html").read_text(encoding="utf-8")
    payload = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", html).group(1))
    rendered_corner = payload["laps"][0]["corners"][0]
    annotation_block = html.split("const annotations = {};")[1].split("speedChart =", 1)[0]

    assert rendered_corner["start_pos"] == pytest.approx(2 / 6, abs=0.0001)
    assert rendered_corner["end_pos"] == pytest.approx(4 / 6, abs=0.0001)
    assert rendered_corner["apex_pos"] == pytest.approx(2.75 / 6, abs=0.0001)
    assert "const bounds = cornerAxisBounds(bestLap, c);" in annotation_block
    assert "xMin: s, xMax: e" in annotation_block
    assert "const s = c.start_pos * 100;" not in annotation_block
    assert "const e = c.end_pos * 100;" not in annotation_block
    assert "i / Math.max(lap.track.length - 1, 1) * 100" not in html
    assert "const apexIndex = Math.round(c.apex_pos * Math.max(pts.length - 1, 1));" in html


@pytest.mark.parametrize(
    "reference_positions, candidate_pos",
    [((0.202, 0.606), 0.606), ((0.20, 0.30), 0.30)],
)
def test_inferred_prompt_corners_match_by_progress_without_reusing_candidates(
    reference_positions, candidate_pos
):
    reference = _lap(1, valid=True, lap_time=61.0, max_speed=123.0)
    reference["corners"] = [
        {**_corner(), "id": 1, "lap_pos": reference_positions[0]},
        {**_corner(), "id": 2, "lap_pos": reference_positions[1]},
    ]
    comparison = _lap(2, valid=True, lap_time=62.0, max_speed=123.0)
    comparison["corners"] = [{**_corner(), "id": 9, "lap_pos": candidate_pos}]
    data = _prompt_data([reference, comparison], comparison_lap_num=2)
    data["profile_corners"] = []
    data["ref_corners"] = reference["corners"]

    _, corner_map = build_session_sections(PromptContext.from_data(data))

    assert corner_map[2].get(1) is None
    assert corner_map[2][2] is comparison["corners"][0]


def test_html_template_substitutes_trusted_scripts_before_report_data():
    html = build_html_template(
        '{"value":"__CHART_JS__"}',
        chart_js="trusted-chart __DATA__",
        annotation_js="trusted-annotation __CHART_JS__",
    )

    assert "<script>trusted-chart __DATA__</script>" in html
    assert "<script>trusted-annotation __CHART_JS__</script>" in html
    assert 'const DATA = {"value":"__CHART_JS__"};' in html
    assert html.count("trusted-chart") == 1


@pytest.mark.asyncio
async def test_analyzer_trims_mid_session_pit_prefix_to_completed_lap_duration(
    tmp_path,
):
    """A pit/restart prefix must not become part of the next completed lap."""
    frames = []
    for frame_number in range(260):
        if frame_number < 60:
            current = frame_number * 100
            last = 0
            position = frame_number / 60
            speed = 100.0
            in_pit_lane = False
        elif frame_number < 180:
            current = (frame_number - 60) * 100
            last = 6_000
            if frame_number < 118:
                position = 0.95
                speed = 5.0
                in_pit_lane = True
            else:
                position = (frame_number - 118) / 62
                speed = 100.0
                in_pit_lane = False
        elif frame_number < 240:
            current = (frame_number - 180) * 100
            last = 6_100
            position = (frame_number - 180) / 60
            speed = 100.0
            in_pit_lane = False
        else:
            current = (frame_number - 240) * 100
            last = 6_200
            position = (frame_number - 240) / 60
            speed = 100.0
            in_pit_lane = False
        frames.append(
            _analysis_frame(
                frame_number,
                speed=speed,
                position=position,
                current_lap_time_ms=current,
                last_lap_time_ms=last,
                is_in_pit_lane=in_pit_lane,
            )
        )

    analyzer = TelemetryAnalyzer(str(tmp_path))
    with (
        patch.object(
            analyzer,
            "_generate_html",
            new=AsyncMock(return_value="report.html"),
        ) as html_spy,
        patch.object(
            analyzer,
            "_generate_ai_prompt",
            new=AsyncMock(return_value="prompt.txt"),
        ),
    ):
        await analyzer.analyze(
            frames,
            hz=10.0,
            game_lap_boundaries=[
                (60, 6_000, 1, "OUTLAP"),
                (180, 6_100, 2, "INVALID_GAME"),
                (240, 6_200, 3, "VALID"),
            ],
            output_prefix="pit_prefix",
        )

    data = html_spy.await_args.args[0]
    pit_lap = next(lap for lap in data["laps"] if lap["lap_num"] == 2)
    assert pit_lap["start_frame"] == 118
    assert len(pit_lap["track"]) == 62
    assert not any(point["is_in_pit_lane"] for point in pit_lap["track"])
    assert pit_lap["avg_speed"] == pytest.approx(100.0)
    assert any("authoritative lap duration" in note for note in data["analysis_notes"])


@pytest.mark.asyncio
async def test_analyzer_selects_single_timer_epoch_after_outlap_reset_and_preserves_pb(
    tmp_path,
):
    """A reset-separated timed lap gets metrics only from its timed suffix."""
    frames = []
    for frame_number in range(1182):
        if frame_number < 600:
            current = frame_number * 100
            position = frame_number / 600
        else:
            current = (frame_number - 600) * 100
            position = (frame_number - 600) / 582
        frames.append(
            _analysis_frame(
                frame_number,
                speed=100.0,
                position=position,
                current_lap_time_ms=current,
                last_lap_time_ms=0,
            )
        )
    # Static SHM may expose only the parent track while the session-owned log
    # identity carries the concrete layout.
    frames[0].static = {"track": "Suzuka", "track_configuration": None}

    analyzer = TelemetryAnalyzer(str(tmp_path))
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        result = await analyzer.analyze(
            frames,
            hz=10.0,
            track_name="Suzuka East",
            game_lap_boundaries=[(1182, 58_281, 1, "VALID")],
            output_prefix="reset_suffix",
        )

    data = html_spy.await_args.args[0]
    lap = data["laps"][0]
    assert result.best_lap_time == pytest.approx(58.281)
    assert lap["start_frame"] == 600
    assert len(lap["track"]) == 582
    assert lap["derived_metrics_trustworthy"] is True
    assert lap["avg_speed"] == pytest.approx(100.0)
    assert data["track_key"] == "suzuka_east"
    assert data["config_key"] == "east"
    summary = json.loads((tmp_path / "session_history.jsonl").read_text().strip())
    assert summary["best_lap_time_s"] == pytest.approx(58.281)
    assert summary["top_speed"] == pytest.approx(100.0)


def test_cleaner_accepts_reset_epoch_with_paused_samples():
    points = []
    for frame_number in range(1202):
        active_index = frame_number - 600
        if frame_number < 600:
            timer = frame_number * 100
        elif frame_number < 920:
            timer = active_index * 100
        elif frame_number < 940:
            timer = 31_900
        else:
            timer = (active_index - 20) * 100
        points.append(
            {
                "frame": frame_number,
                "lap_time_ms": timer,
                "status_name": "AC_PAUSE" if 920 <= frame_number < 940 else "AC_LIVE",
            }
        )

    cleaned, prefix_removed, pause_removed, trustworthy = _clean_completed_lap_track(
        points,
        58_281,
        hz=10.0,
    )

    assert trustworthy is True
    assert prefix_removed == 600
    assert pause_removed == 20
    assert len(cleaned) == 582


@pytest.mark.asyncio
async def test_analyzer_preserves_static_profileless_identity_for_history(tmp_path):
    frames = [
        _analysis_frame(
            frame_number,
            speed=100.0,
            position=frame_number / 100,
            current_lap_time_ms=frame_number * 650,
            last_lap_time_ms=0,
        )
        for frame_number in range(100)
    ]
    frames[0].static = {"track": "Road Atlanta", "track_configuration": "GP"}

    analyzer = TelemetryAnalyzer(str(tmp_path))
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        await analyzer.analyze(
            frames,
            hz=10.0,
            track_name="road_atlanta gp",
            game_lap_boundaries=[(100, 65_000, 1, "VALID")],
            output_prefix="road_atlanta_identity",
        )

    data = html_spy.await_args.args[0]
    assert data["track_label"] == "Road Atlanta"
    summary = json.loads((tmp_path / "session_history.jsonl").read_text().strip())
    assert summary["track"] == "Road Atlanta"


@pytest.mark.asyncio
async def test_analyzer_keeps_ambiguous_reset_capture_diagnostic_without_bad_history_metrics(
    tmp_path,
):
    """An unresolved timer epoch retains the authoritative lap but suppresses metrics."""
    frames = []
    for frame_number in range(1000):
        if frame_number < 500:
            current = frame_number * 100
        elif frame_number < 800:
            current = (frame_number - 500) * 100
        elif frame_number == 800:
            current = 0
        else:
            current = (frame_number - 800) * 100
        frames.append(
            _analysis_frame(
                frame_number,
                speed=100.0,
                position=(frame_number % 500) / 500,
                current_lap_time_ms=current,
                last_lap_time_ms=0,
            )
        )

    analyzer = TelemetryAnalyzer(str(tmp_path))
    result = await analyzer.analyze(
        frames,
        hz=10.0,
        track_name="Suzuka East",
        game_lap_boundaries=[(1000, 49_800, 1, "VALID")],
        output_prefix="ambiguous_reset",
    )

    report = (tmp_path / "telemetry_ambiguous_reset.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", report).group(1))
    lap = data["laps"][0]
    assert result.best_lap_time == pytest.approx(49.8)
    assert lap["derived_metrics_trustworthy"] is False
    assert lap["max_speed"] is None
    assert lap["avg_speed"] is None
    assert lap["fuel_used"] is None
    assert lap["corners"] == []
    assert data["analysis_mode"] == "diagnostic"
    assert '"max_speed": null' in report
    prompt = (tmp_path / "telemetry_ambiguous_reset_ai_prompt.txt").read_text(encoding="utf-8")
    assert "Derived metrics: N/A for lap(s) 1" in prompt
    summary = json.loads((tmp_path / "session_history.jsonl").read_text().strip())
    assert summary["best_lap_time_s"] == pytest.approx(49.8)
    assert summary["top_speed"] is None
    assert summary["avg_fuel_per_lap"] is None


@pytest.mark.asyncio
async def test_analyzer_mixed_trusted_and_ambiguous_laps_render_safely(tmp_path):
    frames = []
    for frame_number in range(1000):
        if frame_number < 500:
            current = frame_number * 100
        elif frame_number < 800:
            current = (frame_number - 500) * 100
        elif frame_number == 800:
            current = 0
        else:
            current = (frame_number - 800) * 100
        frames.append(
            _analysis_frame(
                frame_number,
                speed=100.0,
                position=(frame_number % 500) / 500,
                current_lap_time_ms=current,
                last_lap_time_ms=0,
            )
        )

    result = await TelemetryAnalyzer(str(tmp_path)).analyze(
        frames,
        hz=10.0,
        track_name="Suzuka East",
        game_lap_boundaries=[
            (500, 49_800, 1, "VALID"),
            (1000, 49_800, 2, "VALID"),
        ],
        output_prefix="mixed_reset",
    )

    assert result.laps_detected == 2
    report = (tmp_path / "telemetry_mixed_reset.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", report).group(1))
    assert [lap["derived_metrics_trustworthy"] for lap in data["laps"]] == [True, False]
    prompt = (tmp_path / "telemetry_mixed_reset_ai_prompt.txt").read_text(encoding="utf-8")
    assert "Suppressed derived metrics for 1 valid lap segment(s)" in prompt
    assert (tmp_path / "session_history.jsonl").exists()


@pytest.mark.asyncio
async def test_ambiguous_invalid_lap_does_not_disable_valid_coaching_output(tmp_path):
    frames = []
    for frame_number in range(2000):
        if frame_number < 1000:
            timer = (frame_number % 500) * 100
        elif frame_number < 1500:
            timer = (frame_number - 1000) * 100
        elif frame_number < 1800:
            timer = (frame_number - 1500) * 100
        elif frame_number == 1800:
            timer = 0
        else:
            timer = (frame_number - 1800) * 100
        frames.append(
            _analysis_frame(
                frame_number,
                speed=100.0,
                position=(frame_number % 500) / 500,
                current_lap_time_ms=timer,
                last_lap_time_ms=0,
            )
        )

    result = await TelemetryAnalyzer(str(tmp_path)).analyze(
        frames,
        hz=10.0,
        track_name="Suzuka East",
        game_lap_boundaries=[
            (500, 49_800, 1, "VALID"),
            (1000, 49_800, 2, "VALID"),
            (2000, 49_800, 3, "INVALID_GAME"),
        ],
        output_prefix="invalid_untrusted",
    )

    report = (tmp_path / "telemetry_invalid_untrusted.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", report).group(1))
    prompt = (tmp_path / "telemetry_invalid_untrusted_ai_prompt.txt").read_text(encoding="utf-8")
    assert result.laps_detected == 3
    assert data["analysis_mode"] == "full"
    assert data["comparison_available"] is True
    assert data["valid_lap_nums"] == [1, 2]
    assert [lap["derived_metrics_trustworthy"] for lap in data["laps"]] == [True, True, False]
    assert "Telemetry coaching is running in DIAGNOSTIC mode." not in prompt
    assert "LAP-BY-LAP SUMMARY:" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("timer_mode", ["positive_underspan", "zero_timer"])
async def test_partial_monotonic_timer_suppresses_derived_metrics_and_history(
    tmp_path, timer_mode
):
    frames = [
        FrameData(
            timestamp=datetime.now(timezone.utc).isoformat(),
            frame_number=frame_number,
            physics={
                "speed_kmh": 250.0,
                "gear": 5,
                "fuel": 5.0 - frame_number / 1000,
            },
            graphics={
                "normalized_car_position": frame_number / 199,
                "has_authoritative_progress": True,
                "current_time_ms": (
                    frame_number * 100 if timer_mode == "positive_underspan" else 0
                ),
                "last_time_ms": 0,
                "completed_laps": 0,
                "is_valid_lap": True,
                "status_name": "AC_LIVE",
                "session_phase": "Session",
                "is_in_pit_lane": False,
            },
        )
        for frame_number in range(200)
    ]

    result = await TelemetryAnalyzer(str(tmp_path)).analyze(
        frames,
        hz=10.0,
        track_name="Suzuka East",
        game_lap_boundaries=[(200, 90_000, 1, "VALID")],
        output_prefix=f"partial_duration_{timer_mode}",
    )

    report = (tmp_path / f"telemetry_partial_duration_{timer_mode}.html").read_text(
        encoding="utf-8"
    )
    data = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", report).group(1))
    lap = data["laps"][0]
    history = json.loads((tmp_path / "session_history.jsonl").read_text(encoding="utf-8"))
    assert result.best_lap_time == pytest.approx(90.0)
    assert lap["derived_metrics_trustworthy"] is False
    assert lap["max_speed"] is None
    assert lap["avg_speed"] is None
    assert lap["fuel_used"] is None
    assert history["top_speed"] is None


@pytest.mark.asyncio
async def test_conflicting_static_layout_does_not_write_history(tmp_path):
    frames = [
        FrameData(
            timestamp=datetime.now(timezone.utc).isoformat(),
            frame_number=frame_number,
            physics={"speed_kmh": 100.0, "gear": 3, "fuel": 5.0},
            graphics={
                "normalized_car_position": frame_number / 649,
                "has_authoritative_progress": True,
                "current_time_ms": frame_number * 100,
                "last_time_ms": 0,
                "completed_laps": 0,
                "is_valid_lap": True,
                "status_name": "AC_LIVE",
                "session_phase": "Session",
                "is_in_pit_lane": False,
            },
            static=(
                {"track": "Suzuka East", "track_configuration": "Full"}
                if frame_number == 0
                else {}
            ),
        )
        for frame_number in range(650)
    ]

    result = await TelemetryAnalyzer(str(tmp_path)).analyze(
        frames,
        hz=10.0,
        track_name="Suzuka East Full",
        capture_track_name="Suzuka East Full",
        game_lap_boundaries=[(650, 65_000, 1, "VALID")],
        output_prefix="conflicting_layout",
    )

    assert result.best_lap_time == pytest.approx(65.0)
    assert not (tmp_path / "session_history.jsonl").exists()


@pytest.mark.asyncio
async def test_analyzer_excludes_paused_samples_from_completed_lap(tmp_path):
    """Wall-clock pause frames must not dilute a lap's telemetry metrics."""
    frames = []
    for frame_number in range(180):
        if frame_number < 60:
            current = frame_number * 100
            last = 0
            position = frame_number / 60
            speed = 100.0
            status = "AC_LIVE"
        elif frame_number < 100:
            current = (frame_number - 60) * 100
            last = 6_000
            position = (frame_number - 60) / 80
            speed = 100.0
            status = "AC_LIVE"
        elif frame_number < 120:
            current = 3_900
            last = 6_000
            position = 0.5
            speed = 0.0
            status = "AC_PAUSE"
        elif frame_number < 160:
            current = (frame_number - 80) * 100
            last = 6_000
            position = (frame_number - 80) / 80
            speed = 100.0
            status = "AC_LIVE"
        else:
            current = (frame_number - 160) * 100
            last = 8_000
            position = (frame_number - 160) / 80
            speed = 100.0
            status = "AC_LIVE"
        frames.append(
            _analysis_frame(
                frame_number,
                speed=speed,
                position=position,
                current_lap_time_ms=current,
                last_lap_time_ms=last,
                status_name=status,
            )
        )

    analyzer = TelemetryAnalyzer(str(tmp_path))
    with (
        patch.object(
            analyzer,
            "_generate_html",
            new=AsyncMock(return_value="report.html"),
        ) as html_spy,
        patch.object(
            analyzer,
            "_generate_ai_prompt",
            new=AsyncMock(return_value="prompt.txt"),
        ),
    ):
        await analyzer.analyze(
            frames,
            hz=10.0,
            game_lap_boundaries=[
                (60, 6_000, 1, "OUTLAP"),
                (160, 8_000, 2, "VALID"),
            ],
            output_prefix="paused_lap",
        )

    data = html_spy.await_args.args[0]
    lap = data["laps"][0]
    assert len(lap["track"]) == 80
    assert all(point["status_name"] != "AC_PAUSE" for point in lap["track"])
    assert [point["frame"] for point in lap["track"]] == list(range(60, 140))
    assert lap["track"][40]["source_frame"] == 120
    assert lap["avg_speed"] == pytest.approx(100.0)
    assert any("paused telemetry samples" in note for note in data["analysis_notes"])


@pytest.mark.asyncio
async def test_lap_processing_schedules_submission_without_awaiting_network():
    service = LapProcessingService()
    config = AppConfig(auto_submit=True, submit_invalid_laps=False)
    home_page = MagicMock()
    home_page._lap_count = 1
    home_page.add_lap.return_value = MagicMock()
    history_entries = []
    schedule_submission = MagicMock()
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=65_000,
        lap_time_str="1:05.000",
        is_valid=True,
        timestamp="2026-08-18T10:00:00+00:00",
    )

    await service.handle_lap_complete(
        session=SessionData(track="Laguna Seca", car="Mazda MX-5 ND Cup"),
        lap=lap,
        home_page=home_page,
        telemetry_capture=None,
        config=config,
        session_manager=MagicMock(),
        pb_cache=MagicMock(),
        history_entries=history_entries,
        schedule_submission=schedule_submission,
        create_history_entry=lambda **kwargs: MagicMock(**kwargs),
    )

    schedule_submission.assert_called_once()


@pytest.mark.asyncio
async def test_app_submission_handoff_uses_log_enriched_lap_without_duplicate():
    """Exercise the public app callbacks used by LogParser in their real order."""
    app = SimLapsApp.__new__(SimLapsApp)
    app.page = MagicMock()
    app._config = AppConfig(auto_submit=True, submit_invalid_laps=False)
    app._session_manager = MagicMock()
    app._pb_cache = MagicMock()
    app._pb_cache.check_and_update_pb.return_value = False
    app._telemetry_capture = None
    app._history_entries = []
    app._lap_processing_service = LapProcessingService()
    app._lap_submission_service = AsyncMock()
    app._api_client = MagicMock()
    app._discord_notifier = None
    app._current_track_name = None
    app._home_page = MagicMock()
    app._home_page._lap_count = 1
    card = MagicMock()
    app._home_page.add_lap.return_value = card

    session = SessionData(track="Laguna Seca", car="Mazda MX-5 ND Cup")
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=65_000,
        lap_time_str="1:05.000",
        is_valid=True,
        timestamp="2026-08-18T10:00:00+00:00",
    )

    await app._on_lap_complete(session, lap)

    app.page.run_task.assert_called_once()
    scheduled = app.page.run_task.call_args.args
    assert scheduled[0] == app._submit_lap
    app._lap_submission_service.submit_lap.assert_not_awaited()

    lap.sector1_ms = 20_000
    lap.sector2_ms = 21_000
    lap.sector3_ms = 24_000
    await app._on_lap_update(session, lap)
    await scheduled[0](*scheduled[1:])

    app._lap_submission_service.submit_lap.assert_awaited_once()
    submitted_lap = app._lap_submission_service.submit_lap.await_args.kwargs["lap"]
    assert submitted_lap is lap
    assert (
        submitted_lap.sector1_ms,
        submitted_lap.sector2_ms,
        submitted_lap.sector3_ms,
    ) == (20_000, 21_000, 24_000)
