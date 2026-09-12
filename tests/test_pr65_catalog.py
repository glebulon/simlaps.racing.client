"""PR65 catalog provenance and optional metadata validation."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.core.telemetry_capture import FrameData
from src.core.track_catalog import TRACK_CATALOG, _validate_catalog, select_track_profile


def _minimal_catalog(calibration=None):
    config = {"name": "Full", "aliases": [], "corners": []}
    if calibration is not None:
        config["_calibration"] = calibration
    return {
        "example": {
            "name": "Example",
            "aliases": ["example"],
            "default_config": "full",
            "configs": {"full": config},
        }
    }


def test_laguna_calibration_documents_sanitized_geometry_crosscheck() -> None:
    calibration = TRACK_CATALOG["laguna_seca"]["configs"]["full"]["_calibration"]

    assert "BMW" not in calibration["method"]
    assert "sanitized" in calibration["method"].casefold()
    assert "raw shared-memory replay" in calibration["uncertainty"].casefold()
    assert calibration["evidence_reference"] == "telemetry/Laguna_Seca_Calibration.md"
    assert all(isinstance(key, str) and isinstance(value, str) for key, value in calibration.items())


def test_calibration_rejects_non_string_metadata() -> None:
    with pytest.raises(ValueError, match="Calibration fields"):
        _validate_catalog(_minimal_catalog({"method": ["wrong"]}))


def test_catalog_without_calibration_metadata_remains_valid() -> None:
    # Older catalog rows did not carry provenance metadata and remain valid.
    _validate_catalog(_minimal_catalog())


@pytest.mark.parametrize(
    ("track_name", "config_name"),
    [
        ("Laguna Seca", "GP"),
        ("laguna_seca gp", None),
    ],
)
def test_laguna_gp_identity_selects_estimated_eleven_corner_profile(
    track_name: str, config_name: str | None
) -> None:
    """ACE's Laguna GP labels must select the named full profile."""
    track_key, profile = select_track_profile(
        track_name=track_name, config_name=config_name
    )

    assert track_key == "laguna_seca"
    assert profile is not None
    assert profile["config_key"] == "full"
    assert profile["config_name"] == "Full"
    assert len(profile["corners"]) == 11
    assert profile["confidence"] == "estimated"


@pytest.mark.asyncio
async def test_full_analyzer_callback_uses_laguna_gp_estimated_profile(tmp_path) -> None:
    """A log-style Laguna GP label reaches the full analyzer profile data."""
    frames = [
        FrameData(
            timestamp=datetime.now(timezone.utc).isoformat(),
            frame_number=frame_number,
            physics={"speed_kmh": 100.0, "gear": 3, "heading": 0.0},
            graphics={
                "normalized_car_position": (frame_number % 100) / 99.0,
                "has_authoritative_progress": True,
            },
        )
        for frame_number in range(300)
    ]

    analyzer = TelemetryAnalyzer(output_dir=str(tmp_path))
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        result = await analyzer.analyze(
            frames,
            hz=10.0,
            track_name="laguna_seca gp",
            game_lap_boundaries=[100, 200, 300],
        )

    report_data = html_spy.await_args.args[0]
    assert result.track_name == "Laguna Seca (Full)"
    assert report_data["track_key"] == "laguna_seca"
    assert report_data["config_key"] == "full"
    assert report_data["config_name"] == "Full"
    assert len(report_data["profile_corners"]) == 11
    assert any("estimated" in note.casefold() for note in report_data["analysis_notes"])
