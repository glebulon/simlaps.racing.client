"""Regression coverage for ACE session track descriptions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.api_client import APIClient, SubmissionStatus
from src.core.log_parser import LapData, LogParser
from src.core.security import GameProcessStatus


def test_clean_track_name_removes_known_session_durations() -> None:
    parser = LogParser()

    assert (
        parser._clean_track_name(
            "Laguna Seca GP Time Attack Practice  5400 seconds "
            "@2014/8/15 11:25:0"
        )
        == "Laguna Seca GP"
    )
    assert (
        parser._clean_track_name("Laguna Seca GP Race Race  3 laps @2014/8/15")
        == "Laguna Seca GP"
    )
    assert parser._clean_track_name("Circuit 3 Laps") == "Circuit 3 Laps"
    assert parser._clean_track_name("Venue 15 Minutes") == "Venue 15 Minutes"
    assert (
        parser._clean_track_name("Laguna Seca GP Practice @2014/8/15")
        == "Laguna Seca GP"
    )
    assert (
        parser._clean_track_name("Laguna Seca GP Qualifying  600 seconds")
        == "Laguna Seca GP"
    )
    assert (
        parser._clean_track_name("Laguna Seca GP Race Race  3 laps")
        == "Laguna Seca GP"
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("configured_app_secret")
async def test_parser_track_description_keeps_api_id_stable_across_track_name_update(
    tmp_path,
) -> None:
    game_started = (
        "[2026-09-07 12:00:00] [gameplay] [info] Game Started! "
        "GameModeType_PRACTICE | Laguna Seca GP Time Attack Practice  5400 seconds "
        "@2014/8/15 11:25:0 | ks_ferrari_296_gt3 | "
        "GameModeSelectionWeatherType_CLEAR"
    )
    connected = (
        "[2026-09-07 12:00:01] [gameplay] [info] "
        "11111111111111111 connected on car ks_ferrari_296_gt3, "
        "with new carId 01234567-89ab-cdef-0123-456789abcdef"
    )
    lap_complete = (
        "[2026-09-07 12:01:31.000] [gameplay] [info] New lap carId "
        "01234567-89ab-cdef-0123-456789abcdef: 1:30.000"
    )

    async def parse_session(*lines: str):
        path = tmp_path / f"session-{len(lines)}.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        sessions = await LogParser(log_path=str(path)).parse_file()
        assert len(sessions) == 1
        return sessions[0]

    before_track_name = await parse_session(game_started, connected, lap_complete)
    after_track_name = await parse_session(
        game_started,
        connected,
        "TRACK NAME laguna_seca gp",
        lap_complete,
    )

    async def intercepted_payload(session):
        captured = {}

        def capture(payload):
            captured.update(payload)
            return {
                **payload,
                "_timestamp": "2026-09-12T10:00:00+00:00",
                "_nonce": "test-nonce",
                "_signature": "test-signature",
            }

        response = MagicMock(status_code=201)
        response.json.return_value = {"id": "test-lap"}
        client = APIClient()
        lap = LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=90_000,
            lap_time_str="1:30.000",
            is_valid=True,
        )
        with (
            patch(
                "src.core.api_client.is_game_running",
                return_value=GameProcessStatus.RUNNING,
            ),
            patch("src.core.api_client.sign_payload", side_effect=capture),
            patch.object(client, "_send_submission", new=AsyncMock(return_value=response)),
        ):
            result = await client.submit_lap(session, lap, user_id="11111111111111111")
        assert result.status is SubmissionStatus.SUCCESS
        return captured

    before_payload = await intercepted_payload(before_track_name)
    after_payload = await intercepted_payload(after_track_name)

    assert before_payload["trackId"] == "laguna_seca"
    assert after_payload["trackId"] == "laguna_seca"
