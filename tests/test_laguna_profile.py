"""Regression coverage for the Laguna Seca normalized-progress profile."""

import math

import pytest

from src.core.analyzer.canonical import _build_canonical_lap, _canonical_bins_for_profile
from src.core.telemetry_analyzer import _detect_profiled_corners_canonical
from src.core.track_catalog import build_track_profile

# These landmarks are deliberately independent of the catalog windows. They
# represent the locally observed geometry used to validate that the windows
# select the right sections of the lap.
_GEOMETRY_LANDMARKS = {
    1: 0.057,
    2: 0.126,
    3: 0.214,
    4: 0.289,
    5: 0.428,
    6: 0.538,
    7: 0.613,
    8: 0.688,
    9: 0.752,
    10: 0.833,
    11: 0.915,
}
_CORKSCREW_HEADING_PAIR = ((0.682, -27.0), (0.695, 19.0))


def _synthetic_geometry_track(speed_scale: float) -> list[dict]:
    """Build one canonical lap from geometry landmarks, without profile data."""
    track = []
    x = 0.0
    z = 0.0
    for frame in range(1001):
        progress = frame / 1000.0
        speed = 220.0
        for landmark in _GEOMETRY_LANDMARKS.values():
            # The speed trough is derived from the geometry landmark rather
            # than from any catalog start/end value.
            speed -= 75.0 * math.exp(-((progress - landmark) / 0.004) ** 2)

        heading = 0.0
        for corner_id, landmark in _GEOMETRY_LANDMARKS.items():
            if corner_id == 8:
                if progress < _CORKSCREW_HEADING_PAIR[0][0]:
                    continue
                if progress <= landmark + 0.001:
                    turn, center = _CORKSCREW_HEADING_PAIR[0][1], _CORKSCREW_HEADING_PAIR[0][0]
                else:
                    turn, center = _CORKSCREW_HEADING_PAIR[1][1], _CORKSCREW_HEADING_PAIR[1][0]
            else:
                turn = (-1.0 if corner_id % 2 else 1.0) * (8.0 + corner_id)
                center = landmark
            heading += math.radians(turn) * math.exp(-((progress - center) / 0.006) ** 2)

        if frame:
            x += math.cos(heading) * 0.001
            z += math.sin(heading) * 0.001

        track.append({
            "frame": frame,
            "norm_pos": progress,
            "lap_progress": progress,
            "time_s": progress * 100.0,
            "speed": speed * speed_scale,
            "heading": heading,
            "brake": 0.0,
            "gas": 0.5,
            "steer": 0.0,
            "x": x,
            "z": z,
        })
    return track


def test_laguna_profiled_detection_follows_geometry_at_multiple_speed_scales() -> None:
    profile = build_track_profile("laguna_seca", "full")
    baseline = _detect_profiled_corners_canonical(
        _synthetic_geometry_track(1.0), profile, hz=10.0, authoritative_progress=True
    )
    scaled = _detect_profiled_corners_canonical(
        _synthetic_geometry_track(1.25), profile, hz=10.0, authoritative_progress=True
    )

    assert [corner["id"] for corner in baseline] == list(_GEOMETRY_LANDMARKS)
    assert [corner["lap_pos"] for corner in scaled] == pytest.approx(
        [corner["lap_pos"] for corner in baseline]
    )
    for corner in baseline:
        assert corner["lap_pos"] == pytest.approx(_GEOMETRY_LANDMARKS[corner["id"]], abs=0.006)

    corkscrew = next(corner for corner in baseline if corner["id"] == 8)
    assert 0.682 <= corkscrew["lap_pos"] <= 0.695
    heading_trace = _synthetic_geometry_track(1.0)
    for progress, expected_heading in _CORKSCREW_HEADING_PAIR:
        point = heading_trace[round(progress * 1000)]
        assert math.degrees(point["heading"]) == pytest.approx(expected_heading, abs=0.1)


def test_laguna_profile_survives_canonical_progress_wrap_without_phase_shift() -> None:
    profile = build_track_profile("laguna_seca", "full")
    source = _synthetic_geometry_track(1.0)
    # A capture buffer may contain a few samples from the next lap after the
    # boundary. Canonical preparation must retain the completed lap's absolute
    # [0, 1] progress rather than phase shifting it around the trailing wrap.
    source.extend(
        {
            **point,
            "frame": point["frame"] + 1001,
            "lap_progress": point["lap_progress"],
        }
        for point in source[:8]
    )

    canonical = _build_canonical_lap(
        source,
        lap_start_frame=0,
        hz=10.0,
        bins=_canonical_bins_for_profile(profile),
    )

    assert canonical is not None
    assert canonical["progress_start"] == pytest.approx(0.0)
    assert canonical["progress_end"] == pytest.approx(1.0)
    corners = _detect_profiled_corners_canonical(
        canonical["samples"], profile, hz=10.0, authoritative_progress=True
    )
    corkscrew = next(corner for corner in corners if corner["id"] == 8)
    assert corkscrew["lap_pos"] == pytest.approx(_GEOMETRY_LANDMARKS[8], abs=0.006)
