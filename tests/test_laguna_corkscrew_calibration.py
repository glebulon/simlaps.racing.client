"""Regression coverage for the Laguna Seca Corkscrew and Rainey windows."""

from __future__ import annotations

import pytest

from src.core.analyzer.canonical import _build_canonical_lap, _canonical_bins_for_profile
from src.core.analyzer.corner_detection import (
    _detect_profiled_corners_canonical,
    detect_profiled_corners,
)
from src.core.track_catalog import build_track_profile

# These are coarse, independently measured landmarks from controlled ACE
# 0.9.1+release.6 driving observations. They describe the geometry and speed
# troughs; they are intentionally not copied from the catalog bounds.
_CORKSCREW_APEX = 0.684
_RAINEY_APEX = 0.758
_CORKSCREW_LEFT = 0.682
_CORKSCREW_RIGHT = 0.695

# Coarse world-space landmarks are deliberately separate from the catalog
# windows. The heading change and yaw sign pair capture the observed
# left/right direction change, while x/z follow a nonlinear world-space path.
_GEOMETRY_LANDMARKS = (
    (0.000, (0.0, 0.0, 0.0, 0.0)),
    (0.612, (65.0, 0.0, 3.14, -0.03)),
    (0.645, (67.0, -116.0, -3.10, 0.00)),
    (0.666, (74.0, -191.0, -2.98, -0.05)),
    (_CORKSCREW_LEFT, (79.0, -244.0, 2.87, 0.59)),
    (_CORKSCREW_RIGHT, (42.0, -271.0, 2.36, -0.59)),
    (0.715, (28.0, -336.0, -3.11, -0.11)),
    (0.752, (0.0, -459.0, 2.30, 0.45)),
    (_RAINEY_APEX, (-19.0, -465.0, 2.03, 0.47)),
    (0.764, (-38.0, -478.0, 1.73, 0.48)),
    (0.780, (-92.0, -474.0, 1.28, 0.24)),
    (1.000, (-20.0, -300.0, 0.00, 0.00)),
)


def _geometry_at(progress: float) -> tuple[float, float, float, float]:
    for left, right in zip(_GEOMETRY_LANDMARKS, _GEOMETRY_LANDMARKS[1:], strict=False):
        if left[0] <= progress <= right[0]:
            span = right[0] - left[0]
            ratio = (progress - left[0]) / span if span else 0.0
            return tuple(
                left[1][index] + (right[1][index] - left[1][index]) * ratio for index in range(4)
            )
    return _GEOMETRY_LANDMARKS[-1][1]


def _laguna_geometry_trace(speed_scale: float = 1.0) -> list[dict]:
    """Build a coarse progress trace with a wrapped tail after the finish."""

    progress_values = {round(index / 500.0, 3) for index in range(501)}
    # Explicit probes straddle the expected catalog boundaries. Keeping them
    # in the fixture makes inclusive-start/exclusive-end behavior reviewable.
    progress_values.update(
        {
            0.644,
            0.645,
            0.666,
            _CORKSCREW_LEFT,
            _CORKSCREW_APEX,
            0.686,
            _CORKSCREW_RIGHT,
            0.713,
            0.715,
            0.752,
            _RAINEY_APEX,
            0.764,
            0.789,
            0.79,
        }
    )
    trailing_wrap = [0.002, 0.009]

    def speed_at(progress: float) -> float:
        # Synthetic speed troughs make apex selection deterministic; they are
        # not claimed as captured BMW M4 GT3 speeds.
        if 0.674 <= progress <= 0.708:
            return (96.0 + abs(progress - _CORKSCREW_APEX) * 900.0) * speed_scale
        if 0.744 <= progress <= 0.778:
            return (122.0 + abs(progress - _RAINEY_APEX) * 420.0) * speed_scale
        return 180.0 * speed_scale

    trace = []
    for frame, progress in enumerate([*sorted(progress_values), *trailing_wrap]):
        x, z, heading, yaw_rate = _geometry_at(progress)
        trace.append(
            {
                "frame": frame,
                "lap_progress": progress,
                "norm_pos": progress,
                "time_s": frame / 10.0,
                "speed": speed_at(progress),
                "x": x,
                "z": z,
                "heading": heading,
                "yaw_rate": yaw_rate,
                "brake": 0.2 if progress in {0.645, 0.715} else 0.0,
                "steer": 0.1 if 0.645 <= progress < 0.79 else 0.0,
                "gas": 0.8 if progress > 0.70 else 0.0,
            }
        )
    return trace


def _corners_by_id(corners: list[dict]) -> dict[int, dict]:
    return {corner["id"]: corner for corner in corners}


@pytest.mark.parametrize("speed_scale", [0.8, 1.2])
def test_laguna_direct_progress_tracks_measured_corkscrew_and_rainey_geometry(speed_scale):
    """Direct progress detection finds synthetic troughs at observed landmarks."""

    profile = build_track_profile("laguna_seca", "full")
    trace = _laguna_geometry_trace(speed_scale)
    corners = _corners_by_id(detect_profiled_corners(trace, 0, len(trace), profile, hz=10.0))

    assert set(corners) >= {8, 9}
    assert corners[8]["lap_pos"] == pytest.approx(_CORKSCREW_APEX, abs=0.004)
    assert corners[8]["apex_x"] == pytest.approx(73.5, abs=8.0)
    assert corners[8]["apex_z"] == pytest.approx(-248.0, abs=8.0)
    assert corners[9]["lap_pos"] == pytest.approx(_RAINEY_APEX, abs=0.004)
    assert corners[9]["apex_x"] == pytest.approx(-19.0, abs=10.0)
    assert corners[9]["apex_z"] == pytest.approx(-465.0, abs=10.0)
    assert corners[8]["apex_speed"] < corners[9]["apex_speed"] < 180.0 * speed_scale

    c8_points = [point for point in trace if corners[8]["start_frame"] <= point["frame"] <= corners[8]["end_frame"]]
    assert any(point["yaw_rate"] > 0.0 for point in c8_points)
    assert any(point["yaw_rate"] < 0.0 for point in c8_points)

    # The samples at the shared boundary belong to C8 before it and C9 from
    # it, while the end of each half-open window is excluded.
    progress_by_frame = {point["frame"]: point["norm_pos"] for point in trace}
    assert progress_by_frame[corners[8]["start_frame"]] == pytest.approx(0.645)
    assert progress_by_frame[corners[8]["end_frame"]] == pytest.approx(0.714)
    assert progress_by_frame[corners[9]["start_frame"]] == pytest.approx(0.715)
    assert progress_by_frame[corners[9]["end_frame"]] == pytest.approx(0.789)


def test_laguna_canonical_progress_preserves_geometry_through_trailing_wrap():
    """Canonical resampling ignores the wrapped tail and preserves both landmarks."""

    profile = build_track_profile("laguna_seca", "full")
    source_trace = _laguna_geometry_trace()
    canonical = _build_canonical_lap(
        source_trace,
        lap_start_frame=0,
        hz=10.0,
        bins=_canonical_bins_for_profile(profile),
    )

    assert canonical is not None
    assert canonical["progress_end"] == pytest.approx(1.0)
    assert canonical["samples"][-1]["lap_progress"] == pytest.approx(1.0)

    corners = _corners_by_id(
        _detect_profiled_corners_canonical(
            canonical["samples"],
            profile,
            hz=10.0,
            authoritative_progress=True,
        )
    )

    assert set(corners) >= {8, 9}
    assert corners[8]["lap_pos"] == pytest.approx(_CORKSCREW_APEX, abs=0.01)
    assert corners[8]["apex_x"] == pytest.approx(73.5, abs=8.0)
    assert corners[8]["apex_z"] == pytest.approx(-248.0, abs=8.0)
    assert corners[8]["apex_speed"] < corners[9]["apex_speed"]
    assert corners[9]["lap_pos"] == pytest.approx(_RAINEY_APEX, abs=0.01)
    assert corners[9]["apex_x"] == pytest.approx(-19.0, abs=10.0)
    assert corners[9]["apex_z"] == pytest.approx(-465.0, abs=10.0)

    c8_samples = [
        point
        for point in canonical["samples"]
        if profile["corners"][7]["start"] <= point["lap_progress"] < profile["corners"][7]["end"]
    ]
    assert any(point["yaw_rate"] > 0.0 for point in c8_samples)
    assert any(point["yaw_rate"] < 0.0 for point in c8_samples)
