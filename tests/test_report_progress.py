"""Regression tests for telemetry report trace coordinates."""

import json
import re

import pytest

from src.core.analyzer.canonical import _build_canonical_lap
from src.core.analyzer.html_renderer import build_html_template, render_html


def _point(frame, *, progress=None, authoritative=False, timer_ms=None, speed=100.0):
    point = {
        "frame": frame,
        "x": float(frame),
        "z": 0.0,
        "speed": speed,
        "brake": 0.0,
        "gas": 0.5,
        "gear": 3,
        "steer": 0.0,
        "yaw_rate": 0.0,
        "acc_g_x": 0.0,
        "acc_g_z": 0.0,
        "norm_pos": progress,
        "has_authoritative_progress": authoritative,
    }
    if timer_ms is not None:
        point["lap_time_ms"] = timer_ms
    return point


def _lap(lap_num, track, *, start_frame=0, canonical_track=None):
    return {
        "lap_num": lap_num,
        "start_frame": start_frame,
        "end_frame": start_frame + 100,
        "lap_time_s": 10.0,
        "lap_time_str": "0:10.00",
        "max_speed": 100.0,
        "avg_speed": 100.0,
        "fuel_used": None,
        "is_valid": True,
        "track": track,
        "canonical_track": canonical_track,
        "corners": [],
    }


def _data(laps):
    return {
        "meta": {},
        "hz": 10.0,
        "track_key": "test-track",
        "track_name": "Test Track",
        "config_key": "gp",
        "config_name": "Full",
        "track_label": "Test Track (Full)",
        "laps": laps,
        "best_lap_num": 1,
        "reference_lap_num": 1,
        "comparison_lap_num": None,
        "comparison_available": False,
        "valid_lap_nums": [1],
        "analysis_mode": "full",
        "analysis_confidence": "high",
        "analysis_notes": [],
        "ref_corners": [],
        "corner_data": {},
        "corner_speeds": {},
    }


def _payload(html):
    match = re.search(r"const DATA = (.*);\nconst LAP_COLORS", html)
    assert match is not None
    return json.loads(match.group(1))


@pytest.mark.asyncio
async def test_report_keeps_raw_and_canonical_progress_aligned_to_source_frames(tmp_path):
    raw = [
        _point(100, progress=0.1, authoritative=True),
        _point(110, progress=0.2, authoritative=False),
        _point(120, progress=0.3, authoritative=True),
    ]
    canonical = [
        {**_point(frame), "lap_progress": progress}
        for frame, progress in ((200, 0.0), (250, 0.5), (300, 1.0))
    ]
    data = _data([
        _lap(1, raw, start_frame=100),
        _lap(2, [_point(200)], start_frame=200, canonical_track=canonical),
    ])

    await render_html(data, str(tmp_path), "coordinates")
    payload = _payload((tmp_path / "telemetry_coordinates.html").read_text(encoding="utf-8"))

    assert [point["lap_progress"] for point in payload["laps"][0]["track"]] == [0.1, None, 0.3]
    assert [point["elapsed_s"] for point in payload["laps"][0]["track"]] == [0.0, 1.0, 2.0]
    assert [point["lap_progress"] for point in payload["laps"][1]["track"]] == [0.0, 0.5, 1.0]
    assert [point["elapsed_s"] for point in payload["laps"][1]["track"]] == [0.0, 5.0, 10.0]


@pytest.mark.asyncio
async def test_report_uses_one_elapsed_axis_when_any_lap_lacks_progress(tmp_path):
    laps = [
        _lap(1, [_point(0, progress=0.0, authoritative=True), _point(10, progress=0.5, authoritative=True)]),
        _lap(2, [_point(20, progress=0.25, authoritative=True), _point(30, authoritative=False)]),
    ]
    data = _data(laps)

    await render_html(data, str(tmp_path), "elapsed")
    html = (tmp_path / "telemetry_elapsed.html").read_text(encoding="utf-8")

    assert "const chartAxis = progressReady ? 'progress' : 'elapsed';" in html
    assert "function tracePoints(lap, valueFn)" in html
    assert "data: tracePoints(lap" in html
    assert "i / Math.max(lap.track.length - 1, 1)" not in html
    assert "spanGaps: false" in html
    assert "parsing: false" not in html
    assert "interaction: { mode: 'nearest', intersect: false }" in html
    assert "Shared x-axis: lap progress" not in html
    assert "Shared x-axis: elapsed seconds" in html


def _canonical_input(progresses, *, timers=None, frames=None, authoritative=True):
    timers = timers if timers is not None else [index * 40 for index in range(len(progresses))]
    frames = frames if frames is not None else list(range(len(progresses)))
    return [
        _point(
            frame,
            progress=progress,
            authoritative=authoritative,
            timer_ms=timer,
        )
        for frame, progress, timer in zip(frames, progresses, timers, strict=False)
    ]


def test_canonical_normalizes_only_a_proven_leading_timer_prefix():
    track = _canonical_input(
        [0.999926, 0.001] + [index / 10 for index in range(1, 11)],
    )

    result = _build_canonical_lap(track, lap_start_frame=0, hz=10.0, bins=40)

    assert result is not None
    assert result["progress_start"] == pytest.approx(0.0)
    assert result["source_samples"] == len(track)
    assert result["samples"][0]["lap_progress"] == pytest.approx(0.0)
    assert result["samples"][-1]["lap_progress"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timers": [None] * 12},
        {"authoritative": False},
        {"timers": [50, 25] + [index * 40 for index in range(2, 12)]},
        {"timers": [50_000 + index * 100 for index in range(12)]},
        {"frames": [0, 10] + list(range(100, 110))},
    ],
)
def test_canonical_does_not_normalize_without_the_full_boundary_proof(kwargs):
    progresses = [0.999926, 0.001] + [index / 10 for index in range(1, 11)]
    track = _canonical_input(progresses, **kwargs)

    assert _build_canonical_lap(track, lap_start_frame=0, hz=10.0, bins=40) is None


def test_canonical_does_not_normalize_out_of_range_leading_progress():
    progresses = [1.001, 0.001] + [index / 10 for index in range(1, 11)]
    track = _canonical_input(progresses)

    result = _build_canonical_lap(track, lap_start_frame=0, hz=10.0, bins=40)

    assert result is not None
    assert result["progress_start"] == pytest.approx(0.001)


def test_canonical_does_not_skip_a_missing_initial_authoritative_point():
    progresses = [0.999926, 0.001] + [index / 10 for index in range(1, 11)]
    track = _canonical_input(progresses)
    track[0]["has_authoritative_progress"] = False

    result = _build_canonical_lap(track, lap_start_frame=0, hz=10.0, bins=40)

    assert result is None


def test_canonical_keeps_existing_trailing_wrap_filtering_and_small_reversal_contract():
    progresses = [index / 20 for index in range(21)] + [0.0, 0.01]
    progresses.insert(7, 0.295)
    result = _build_canonical_lap(
        _canonical_input(progresses, timers=[index * 100 for index in range(len(progresses))]),
        lap_start_frame=0,
        hz=10.0,
        bins=40,
    )

    assert result is not None
    assert result["progress_start"] == pytest.approx(0.0)
    assert result["progress_end"] == pytest.approx(1.0)


def test_template_keeps_map_fractions_separate_from_chart_corner_bounds():
    html = build_html_template(json.dumps(_data([])))

    assert "function cornerAxisBounds(lap, corner)" in html
    assert "const bounds = cornerAxisBounds(bestLap, c);" in html
    assert "const apexIndex = Math.round(c.apex_pos * Math.max(pts.length - 1, 1));" in html
    assert "const s = c.start_pos * 100;" not in html
    assert "const e = c.end_pos * 100;" not in html
