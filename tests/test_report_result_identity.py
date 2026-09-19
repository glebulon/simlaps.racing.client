"""Regression coverage for capture-result identity and telemetry trust."""

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from unittest.mock import AsyncMock

import pytest

from src.core.analyzer.lap_detection import _detect_laps_by_timing_state
from src.core.analyzer.prompt.context import PromptContext
from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.core.telemetry_capture import FrameData, LapBoundary, TelemetryCapture
from src.models import LapData, LapState, SharedSessionManager


def _frame(index: int, timer: int, *, last: int = 0) -> FrameData:
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=index,
        physics={"speed_kmh": 100.0, "fuel": 20.0 - index * 0.001},
        graphics={
            "normalized_car_position": (index % 200) / 200.0,
            "has_authoritative_progress": True,
            "current_time_ms": timer,
            "last_time_ms": last,
            "completed_laps": 0,
            "is_valid_lap": True,
        },
    )


def test_timer_reset_while_paused_does_not_create_shm_boundary():
    assert _detect_laps_by_timing_state(
        [
            {"frame": 0, "lap_time_ms": 5_000, "last_lap_time_ms": 90_000, "status_name": "AC_LIVE"},
            {"frame": 10, "lap_time_ms": 0, "last_lap_time_ms": 90_000, "status_name": "AC_PAUSE"},
        ],
        hz=10.0,
    ) in (None, [])


def test_late_result_update_keeps_original_capture_frame_boundary():
    capture = TelemetryCapture(hz=10.0)
    capture._frames = [FrameData("2026-01-01T00:00:00Z", i, {"speed_kmh": 100.0}) for i in range(50)]
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=20_000,
        lap_time_str="0:20.000",
    )

    capture.record_lap_boundary(20_000, 1, "VALID")
    assert capture.bind_lap_boundary("session-a", lap)

    lap.lap_number = 4
    lap.lap_time_ms = 19_500
    lap.lap_type = LapState.INVALID_GAME.value
    lap.is_valid = False
    assert capture.reconcile_lap_boundary("session-a", lap)

    boundary = capture.get_lap_boundaries()[0]
    assert boundary.frame_index == 49
    assert boundary.lap_number == 4
    assert boundary.original_lap_number == 1
    assert boundary.lap_type == "INVALID_GAME"
    assert boundary.result_id == lap.result_id


@pytest.mark.asyncio
async def test_identity_boundary_does_not_use_stale_display_number_maps(tmp_path):
    manager = SharedSessionManager()
    manager._session_data.lap_timing[4] = type("Timing", (), {"completed_lap_time": 99_999.0})()
    manager._session_data.lap_validity[4] = type("Validity", (), {"is_valid": False})()
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    analyzer._generate_html = AsyncMock(return_value="report.html")
    analyzer._generate_ai_prompt = AsyncMock(return_value="prompt.txt")

    frames = [_frame(i, i * 100) for i in range(200)]
    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[
            LapBoundary(199, 20_000, 4, "VALID", "session-a", "result-a", 1),
        ],
        output_prefix="identity",
    )

    data = analyzer._generate_html.await_args.args[0]
    assert result.laps_detected == 1
    assert data["laps"][0]["lap_num"] == 4
    assert data["laps"][0]["source_lap_num"] == 1
    assert data["laps"][0]["lap_time_s"] == pytest.approx(20.0)
    assert data["laps"][0]["is_valid"] is True


@pytest.mark.asyncio
async def test_closed_report_cannot_update_new_session_telemetry_summary(tmp_path):
    manager = SharedSessionManager()
    manager._session_data.session_metadata.session_id = "new-session"
    manager._session_data.max_speed = 42.0
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)

    result = await analyzer.analyze(
        [_frame(index, index * 100) for index in range(100)],
        hz=10.0,
        car_name="Unknown Car",
        game_lap_boundaries=[
            LapBoundary(99, 10_000, 1, "VALID", "old-session", "old-result", 1),
        ],
        output_prefix="closed_summary",
    )

    assert result.laps_detected == 1
    assert manager._session_data.max_speed == pytest.approx(42.0)


@pytest.mark.asyncio
async def test_identified_result_without_session_id_cannot_update_summary(tmp_path):
    manager = SharedSessionManager()
    manager._session_data.max_speed = 42.0
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)

    await analyzer.analyze(
        [_frame(index, index * 100) for index in range(100)],
        hz=10.0,
        car_name="Unknown Car",
        game_lap_boundaries=[
            LapBoundary(99, 10_000, 1, "VALID", result_id="identified-result"),
        ],
        output_prefix="identified_without_session",
    )

    assert manager._session_data.max_speed == pytest.approx(42.0)


@pytest.mark.asyncio
async def test_mixed_owned_and_missing_session_results_cannot_update_summary(tmp_path):
    manager = SharedSessionManager()
    manager._session_data.session_metadata.session_id = "owned-session"
    manager._session_data.max_speed = 42.0
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    frames = [_frame(index, index * 100) for index in range(100)]
    frames.extend(_frame(index, (index - 100) * 100) for index in range(100, 200))

    result = await analyzer.analyze(
        frames,
        hz=10.0,
        car_name="Unknown Car",
        game_lap_boundaries=[
            LapBoundary(99, 10_000, 1, "VALID", "owned-session", "r1", 1),
            LapBoundary(199, 10_000, 2, "VALID", None, "r2", 2),
        ],
        output_prefix="mixed_session_summary",
    )

    assert result.laps_detected == 2
    assert manager._session_data.max_speed == pytest.approx(42.0)


def test_summary_session_check_and_write_share_manager_lock():
    manager = SharedSessionManager()
    manager._session_data.session_metadata.session_id = "owned-session"
    entered = Event()
    reset_attempted = Event()
    release = Event()
    original_mark_source = manager._mark_source

    def blocked_mark_source(field_name, source):
        entered.set()
        assert release.wait(timeout=2.0)
        original_mark_source(field_name, source)

    manager._mark_source = blocked_mark_source
    update_result = []
    update_thread = Thread(
        target=lambda: update_result.append(
            manager.update_from_telemetry(
                {"max_speed": 321.0},
                expected_session_id="owned-session",
            )
        )
    )
    def reset_with_marker():
        reset_attempted.set()
        manager.reset()

    reset_thread = Thread(target=reset_with_marker)
    update_thread.start()
    assert entered.wait(timeout=2.0)
    reset_thread.start()
    assert reset_attempted.wait(timeout=2.0)
    release.set()
    update_thread.join(timeout=2.0)
    reset_thread.join(timeout=2.0)

    assert not update_thread.is_alive()
    assert not reset_thread.is_alive()
    assert update_result == [True]
    assert manager._session_data.max_speed is None


@pytest.mark.asyncio
async def test_identity_callback_late_frame_keeps_shm_boundary_and_result_metadata(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    frames = []
    for index in range(260):
        current = index * 100 if index < 200 else (index - 200) * 100
        last = 19_998 if index == 200 else 0
        frames.append(_frame(index, current, last=last))

    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[
            # The parser callback arrived five samples after the SHM finish.
            LapBoundary(205, 20_000, 7, "INVALID_GAME", "session-a", "result-late", 3),
        ],
        output_prefix="late_identity",
    )

    data = json.loads(
        re.search(
            r"const DATA = (.*);\nconst LAP_COLORS",
            (tmp_path / "telemetry_late_identity.html").read_text(encoding="utf-8"),
        ).group(1)
    )
    lap = data["laps"][0]
    assert result.laps_detected == 1
    assert lap["end_frame"] == 200
    assert lap["lap_num"] == 7
    assert lap["original_lap_number"] == 3
    assert lap["result_id"] == "result-late"
    assert lap["is_valid"] is False
    assert lap["derived_metrics_trustworthy"] is True


@pytest.mark.asyncio
async def test_oversized_timer_without_pit_evidence_stays_untrusted(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    frames = [_frame(index, index * 100) for index in range(301)]
    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[LapBoundary(300, 20_000, 1, "VALID", "session-a", "result-a", 1)],
        output_prefix="un evidenced_prefix",
    )

    assert result.laps_detected == 1
    html = (tmp_path / "telemetry_un evidenced_prefix.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", html).group(1))
    assert data["laps"][0]["derived_metrics_trustworthy"] is False
    assert data["laps"][0]["max_speed"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("frame_count", [0, 1, 10])
async def test_missing_telemetry_retains_captured_official_result(tmp_path, frame_count):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    result = await analyzer.analyze(
        [_frame(index, index * 100) for index in range(frame_count)],
        hz=10.0,
        game_lap_boundaries=[LapBoundary(max(frame_count - 1, 0), 20_000, 1, "VALID", "session-a", "result-a", 1)],
        output_prefix=f"missing_{frame_count}",
    )

    assert result.laps_detected == 1
    assert result.best_lap_time == pytest.approx(20.0)
    if frame_count == 0:
        html = (tmp_path / "telemetry_missing_0.html").read_text(encoding="utf-8")
        assert "Telemetry map unavailable" in html


@pytest.mark.asyncio
async def test_missing_official_duration_keeps_result_but_suppresses_derived_metrics(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    result = await analyzer.analyze(
        [_frame(index, index * 100) for index in range(100)],
        hz=10.0,
        game_lap_boundaries=[LapBoundary(99, None, 1, "VALID", "session-a", "result-a", 1)],
        output_prefix="missing_duration",
    )

    assert result.laps_detected == 1
    html = (tmp_path / "telemetry_missing_duration.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", html).group(1))
    assert data["laps"][0]["derived_metrics_trustworthy"] is False
    assert data["laps"][0]["max_speed"] is None


@pytest.mark.asyncio
async def test_unique_completed_timer_epoch_is_selected_between_resets(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    frames = []
    for index in range(255):
        if index < 50:
            timer = index * 100
        elif index < 250:
            timer = (index - 50) * 100
        else:
            timer = (index - 250) * 100
        frames.append(_frame(index, timer))

    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[LapBoundary(254, 20_000, 1, "VALID", "session-a", "result-a", 1)],
        output_prefix="epoch_selection",
    )

    data = json.loads(
        re.search(
            r"const DATA = (.*);\nconst LAP_COLORS",
            (tmp_path / "telemetry_epoch_selection.html").read_text(encoding="utf-8"),
        ).group(1)
    )
    lap = data["laps"][0]
    assert result.laps_detected == 1
    assert lap["start_frame"] == 50
    assert lap["derived_metrics_trustworthy"] is True


@pytest.mark.asyncio
async def test_equal_time_outlap_does_not_claim_timed_result(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    frames = []
    for index in range(170):
        if index < 60:
            timer = index * 100
        elif index < 160:
            timer = (index - 60) * 100
        else:
            timer = (index - 160) * 100
        frames.append(_frame(index, timer, last=10_000 if index == 160 else 0))

    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[
            LapBoundary(60, 10_000, 1, "OUTLAP", "session-a", "outlap", 1),
            LapBoundary(165, 10_000, 2, "INVALID_GAME", "session-a", "timed", 2),
        ],
        output_prefix="equal_time_outlap",
    )

    data = json.loads(
        re.search(
            r"const DATA = (.*);\nconst LAP_COLORS",
            (tmp_path / "telemetry_equal_time_outlap.html").read_text(encoding="utf-8"),
        ).group(1)
    )
    assert result.laps_detected == 1
    assert data["laps"][0]["lap_num"] == 2
    assert data["laps"][0]["result_id"] == "timed"
    assert data["laps"][0]["derived_metrics_trustworthy"] is True


@pytest.mark.asyncio
async def test_equal_time_results_use_original_numbers_when_callbacks_are_reordered(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    frames = []
    for index in range(260):
        if index < 100:
            timer = index * 100
        elif index < 200:
            timer = (index - 100) * 100
        else:
            timer = (index - 200) * 100
        frames.append(_frame(index, timer, last=10_000 if index in (100, 200) else 0))

    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[
            # Arrival order is reversed, but original callback numbers bind
            # each equal-time result to its physical timer epoch.
            LapBoundary(205, 10_000, 2, "VALID", "session-a", "second", 2),
            LapBoundary(210, 10_000, 1, "INVALID_GAME", "session-a", "first", 1),
        ],
        output_prefix="equal_time_reordered",
    )

    data = json.loads(
        re.search(
            r"const DATA = (.*);\nconst LAP_COLORS",
            (tmp_path / "telemetry_equal_time_reordered.html").read_text(encoding="utf-8"),
        ).group(1)
    )
    assert result.laps_detected == 2
    assert [(lap["result_id"], lap["lap_num"], lap["is_valid"]) for lap in data["laps"]] == [
        ("first", 1, False),
        ("second", 2, True),
    ]
    assert all(lap["derived_metrics_trustworthy"] for lap in data["laps"])


@pytest.mark.asyncio
async def test_duplicate_display_numbers_use_result_key_for_official_best(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    frames = []
    for index in range(311):
        if index < 200:
            timer = index * 100
        else:
            timer = (index - 200) * 100
        frames.append(_frame(index, timer, last=20_000 if index == 200 else (10_000 if index == 300 else 0)))

    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[
            LapBoundary(205, 20_000, 1, "VALID", "session-a", "result-a", 1),
            LapBoundary(305, 10_000, 1, "VALID", "session-a", "result-b", 2),
        ],
        output_prefix="duplicate_display",
    )

    data = json.loads(
        re.search(
            r"const DATA = (.*);\nconst LAP_COLORS",
            (tmp_path / "telemetry_duplicate_display.html").read_text(encoding="utf-8"),
        ).group(1)
    )
    assert result.best_lap_time == pytest.approx(10.0)
    assert [lap["lap_num"] for lap in data["laps"]] == [1, 1]
    assert data["best_lap_key"] == data["laps"][1]["result_key"]
    assert data["best_lap_key"] != data["laps"][0]["result_key"]


@pytest.mark.asyncio
async def test_prompt_keeps_faster_untrusted_best_out_of_coaching_reference(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    path = await analyzer._generate_ai_prompt(
        {
            "laps": [
                {
                    "lap_num": 1,
                    "lap_time_s": 5.0,
                    "lap_time_str": "0:05.00",
                    "max_speed": None,
                    "avg_speed": None,
                    "fuel_used": None,
                    "is_valid": True,
                    "derived_metrics_trustworthy": False,
                },
                {
                    "lap_num": 2,
                    "lap_time_s": 10.0,
                    "lap_time_str": "0:10.00",
                    "max_speed": 100.0,
                    "avg_speed": 90.0,
                    "fuel_used": 0.1,
                    "is_valid": False,
                    "derived_metrics_trustworthy": True,
                },
            ],
            "best_lap_num": 1,
            "analysis_mode": "full",
            "analysis_confidence": "high",
            "analysis_notes": [],
            "authoritative_progress_ratio": 1.0,
            "plausible_frame_ratio": 1.0,
            "ref_corners": [{"id": 1, "name": "T1"}],
            "comparison_available": False,
            "reference_lap_num": 2,
            "comparison_lap_num": None,
            "corner_data": {},
            "corner_speeds": {},
            "track_label": "Test Track",
            "track_name": "Test Track",
            "car": "Test Car",
            "hz": 10.0,
        },
        output_prefix="untrusted_best_prompt",
    )

    prompt = Path(path).read_text(encoding="utf-8")
    assert "Official best result: #1  0:05.00" in prompt
    assert "Coaching reference:   #2  0:10.00" in prompt
    assert "- Top speed:  100.0 km/h" in prompt


@pytest.mark.asyncio
async def test_prompt_does_not_coach_untrusted_invalid_result(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    path = await analyzer._generate_ai_prompt(
        {
            "laps": [
                {
                    "lap_num": 1,
                    "lap_time_s": 5.0,
                    "lap_time_str": "0:05.00",
                    "max_speed": 100.0,
                    "avg_speed": 90.0,
                    "fuel_used": 0.1,
                    "is_valid": True,
                    "derived_metrics_trustworthy": True,
                    "corners": [],
                    "track": [],
                },
                {
                    "lap_num": 2,
                    "lap_time_s": 10.0,
                    "lap_time_str": "0:10.00",
                    "max_speed": 95.0,
                    "avg_speed": 85.0,
                    "fuel_used": 0.2,
                    "is_valid": False,
                    "derived_metrics_trustworthy": True,
                    "corners": [],
                    "track": [],
                },
                {
                    "lap_num": 3,
                    "lap_time_s": 3.0,
                    "lap_time_str": "0:03.00",
                    "max_speed": None,
                    "avg_speed": None,
                    "fuel_used": None,
                    "is_valid": False,
                    "derived_metrics_trustworthy": False,
                    "corners": [],
                    "track": [],
                },
            ],
            "best_lap_num": 3,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "comparison_available": True,
            "analysis_mode": "full",
            "analysis_confidence": "high",
            "analysis_notes": [],
            "authoritative_progress_ratio": 1.0,
            "plausible_frame_ratio": 1.0,
            "ref_corners": [{"id": 1, "name": "T1"}],
            "corner_data": {},
            "corner_speeds": {},
            "track_label": "Test Track",
            "track_name": "Test Track",
            "car": "Test Car",
            "hz": 10.0,
        },
        output_prefix="untrusted_invalid_prompt",
    )

    prompt = Path(path).read_text(encoding="utf-8")
    assert "INVALID LAPS (coached anyway; treat deltas with care):" in prompt
    assert "Lap 2: 0:10.00 [INVALID]" in prompt
    assert "- Lap 3: 0:03.00 [INVALID]" not in prompt


@pytest.mark.asyncio
async def test_keyed_two_lap_prompt_preserves_display_labels_and_coaching_content(tmp_path):
    fixture = json.loads(Path("tests/fixtures/ai_prompt_full_input.json").read_text(encoding="utf-8"))
    for lap_index, lap in enumerate(fixture["laps"]):
        lap["track"] = [
            {
                "frame": frame,
                "drs_available": True,
                "drs_enabled": lap_index == 0,
                "brake": 0.0,
                "steer": 0.0,
                "gas": 0.0,
                "gas_percent": 0.0,
                "acc_g_z": 0.0,
                "gear": 3 + lap_index,
                "gear_rpm_window": 0.95,
            }
            for frame in range(101)
        ]
        lap["start_frame"] = 0
    legacy = deepcopy(fixture)
    legacy["corner_speeds"] = {1: {1: 80.0, 2: 85.0}, 2: {1: 70.0, 2: 75.0}}
    legacy["comparison_available"] = True
    legacy_path = await TelemetryAnalyzer(str(tmp_path))._generate_ai_prompt(
        legacy,
        output_prefix="legacy_prompt_identity",
    )

    keyed_equivalent = deepcopy(fixture)
    for index, lap in enumerate(keyed_equivalent["laps"], start=1):
        lap["result_key"] = f"result:{index}"
    keyed_equivalent["best_lap_result_key"] = "result:1"
    keyed_equivalent["reference_lap_result_key"] = "result:1"
    keyed_equivalent["comparison_lap_result_key"] = "result:2"
    keyed_equivalent["comparison_available"] = True
    keyed_equivalent["corner_speeds"] = {
        1: {"result:1": 80.0, "result:2": 85.0},
        2: {"result:1": 70.0, "result:2": 75.0},
    }
    equivalent_path = await TelemetryAnalyzer(str(tmp_path))._generate_ai_prompt(
        keyed_equivalent,
        output_prefix="equivalent_prompt_identity",
    )

    keyed = deepcopy(keyed_equivalent)
    for lap in keyed["laps"]:
        lap["lap_num"] = 1
    keyed["best_lap_num"] = 1
    keyed["reference_lap_num"] = 1
    keyed["comparison_lap_num"] = 1
    keyed_path = await TelemetryAnalyzer(str(tmp_path))._generate_ai_prompt(
        keyed,
        output_prefix="keyed_prompt_identity",
    )

    legacy_prompt = Path(legacy_path).read_text(encoding="utf-8")
    equivalent_prompt = Path(equivalent_path).read_text(encoding="utf-8")
    keyed_prompt = Path(keyed_path).read_text(encoding="utf-8")
    assert equivalent_prompt == legacy_prompt
    assert "result:1" not in keyed_prompt
    assert "result:2" not in keyed_prompt
    assert keyed_prompt.count("Lap 1") >= 2
    assert "Apex speeds" in legacy_prompt and "Apex speeds" in keyed_prompt
    assert "T1" in legacy_prompt and "T1" in keyed_prompt
    assert "INCONSISTENT DRS USAGE" in keyed_prompt
    assert keyed_prompt.count("Lap 1: DRS used 100.0%") == 1
    assert keyed_prompt.count("Lap 1: DRS used 0.0%") == 1
    assert keyed_prompt.count("Lap 1 (entry): Gear 3") >= 1
    assert keyed_prompt.count("Lap 1 (entry): Gear 4") >= 1
    assert "Gear changes mid-corner" not in keyed_prompt
    coast_section = keyed_prompt.split("COAST TIME AGGREGATION", 1)[1].split("\n\n", 1)[0]
    assert coast_section.count("<- BEST") == 1
    for section in ("STRAIGHT/SECTOR ANALYSIS", "BRAKING & TIMING ANALYSIS", "GRIP UTILIZATION ANALYSIS"):
        assert section in legacy_prompt
        assert section in keyed_prompt


def test_prompt_context_does_not_use_ambiguous_display_number_after_key_miss():
    data = {
        "laps": [
            {"result_key": "result:1", "lap_num": 1, "lap_time_s": 20.0},
            {"result_key": "result:2", "lap_num": 1, "lap_time_s": 10.0},
        ],
        "best_lap_result_key": "missing-result",
        "best_lap_num": 1,
        "reference_lap_result_key": "missing-result",
        "reference_lap_num": 1,
        "comparison_lap_result_key": "missing-result",
        "comparison_lap_num": 1,
    }

    context = PromptContext.from_data(data)

    assert context.best_lap["result_key"] == "result:2"
    assert context.coaching_reference_lap["result_key"] == "result:2"
    assert context.comparison_lap_key == "missing-result"


@pytest.mark.asyncio
async def test_partial_timer_coverage_keeps_official_result_without_metrics(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    frames = [_frame(i, i * 100) for i in range(100)]

    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[LapBoundary(99, 20_000, 1, "VALID", "session-a", "result-a", 1)],
        output_prefix="partial",
    )

    assert result.html_path is not None
    assert result.ai_prompt_path is not None
    html = (tmp_path / "telemetry_partial.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", html).group(1))
    lap = data["laps"][0]
    assert result.best_lap_time == pytest.approx(20.0)
    assert lap["derived_metrics_trustworthy"] is False
    assert lap["max_speed"] is None
    assert lap["avg_speed"] is None
    assert lap["fuel_used"] is None
    assert data["laps"][0]["derived_metrics_trustworthy"] is False
    prompt = (tmp_path / "telemetry_partial_ai_prompt.txt").read_text(encoding="utf-8")
    assert "filter(v => Number.isFinite(v))" in html
    assert "Top Speed" in html
    assert "Lap 1: 0:20.00 [VALID] top speed N/A km/h" in prompt


@pytest.mark.asyncio
async def test_rendered_outputs_keep_trustworthy_and_official_results_separate(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    frames = []
    for index in range(300):
        # The first segment's official duration exceeds its sampled coverage;
        # the second segment has matching timer and sampled durations.
        if index < 100:
            timer = index * 100
        elif index < 200:
            timer = (index - 100) * 100
        else:
            timer = (index - 200) * 100
        last = 19_998 if index == 100 else (9_998 if index == 200 else 0)
        frame = _frame(index, timer, last=last)
        frame.physics["quality_score"] = 0.1 if index < 100 else 1.0
        frame.graphics["quality_score"] = 0.1 if index < 100 else 1.0
        if index < 100:
            frame.graphics["has_authoritative_progress"] = False
        frames.append(frame)
    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[
            LapBoundary(105, 20_000, 1, "VALID", "session-a", "result-a", 1),
            LapBoundary(205, 10_000, 2, "INVALID_GAME", "session-a", "result-b", 2),
        ],
        output_prefix="mixed",
    )

    assert result.laps_detected == 2
    html = (tmp_path / "telemetry_mixed.html").read_text(encoding="utf-8")
    prompt = (tmp_path / "telemetry_mixed_ai_prompt.txt").read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", html).group(1))
    assert [lap["result_id"] for lap in data["laps"]] == ["result-a", "result-b"]
    assert [lap["derived_metrics_trustworthy"] for lap in data["laps"]] == [False, True]
    # Capture-level quality is recomputed from the trustworthy segment, even
    # though this synthetic input has no track profile and remains diagnostic.
    assert data["authoritative_progress_ratio"] == pytest.approx(1.0)
    assert "Lap 1: 0:20.00 [VALID] top speed N/A km/h" in prompt
    assert "Lap 2: 0:10.00 [INVALID] top speed 100.0 km/h" in prompt
