import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.log_parser import LogParser
from src.core.api_client import APIClient, SubmissionStatus
from src.core.security import GameProcessStatus
from src.models import SessionData, SharedSessionManager


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            encoded.append(byte | 0x80)
        else:
            encoded.append(byte)
            return bytes(encoded)


def _float(field: int, value: float) -> bytes:
    return _varint((field << 3) | 5) + struct.pack("<f", value)


def _message(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _setup_bytes(*, pressure_fl: float = 24.1) -> bytes:
    chassis = _message(1, struct.pack("<ff", 39_000.0, 8_800.0))
    chassis += _float(2, 14.6)
    chassis += _message(3, _float(1, 68.5) + _float(2, 100.0))
    payload = _message(1, chassis)
    for spring in (60_000.0, 60_000.0, 40_000.0, 40_000.0):
        payload += _message(2, _float(1, spring))
    for _ in range(4):
        payload += _message(3, _float(1, 5.0) + _float(3, 7.0))
    for pressure in (pressure_fl, 24.2, 25.1, 25.2):
        payload += _message(4, _float(1, pressure) + _float(2, -2.0))
    payload += _message(6, _float(2, 65.0) + _float(3, 70.0))
    payload += _message(7, _float(1, 18.0))
    payload += _message(
        9,
        b"ks_mazda_mx5_nd_cup_preset_mx5ndcup_mech_1_"
        b"preset_mx5ndcup_visual_1",
    )
    return payload


def _game_started() -> str:
    return (
        "[2026-08-16 15:32:45.000] [gameplay] [info] Game Started! "
        "GameModeType_PRACTICE | Red Bull Ring GP | ks_mazda_mx5_nd_cup | "
        "GameModeSelectionWeatherType_CLEAR"
    )


def _lap_lines(number: int, time_ms: int) -> list[str]:
    minutes, remainder = divmod(time_ms, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return [
        (
            f"[2026-08-16 15:34:{number:02d}.000] [gameplay] [info] "
            "New lap carId abc123-def456: "
            f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
        ),
        (
            "[2026-08-16 15:34:59.010] [network] [info] Relevant onSplit "
            f"for Combo 1@1: laptime {time_ms}, valid true, flags 2, lap {number}"
        ),
    ]


@pytest.mark.asyncio
async def test_parse_file_loads_setup_overlays_changes_and_freezes_each_lap(tmp_path):
    setup_root = tmp_path / "Car Setups"
    setup_path = setup_root / "Mazda MX-5 ND Cup" / "Red Bull Ring" / "Loaded.carsetup"
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(_setup_bytes())
    log_path = tmp_path / "ace.txt"
    lines = [
        (
            "[2026-08-16 15:32:44.000] [gameplay] [info] ACEVO-2629 "
            "onSetPlayerCurrentCarCommand: Set new car abc123-def456 "
            "content\\cars\\ks_mazda_mx5_nd_cup\\presets\\"
            "preset_mx5ndcup_mech_1.mechanicalcarpreset"
        ),
        _game_started(),
        "[2026-08-16 15:32:46.000] [gameface] [info] Load preset Loaded",
        (
            "[2026-08-16 15:32:46.001] [fastStorage] [warning] "
            f'Opening non-packed file "{setup_path}"'
        ),
        *_lap_lines(1, 90_000),
        "[2026-08-16 15:35:00.000] [gameface] [info] KS-SETUP-GROUP pressure_FL 25.4",
        "[2026-08-16 15:35:00.001] [gameface] [info] KS-SETUP-GROUP telemetry_laps_to_record 3",
        *_lap_lines(2, 89_000),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    manager = SharedSessionManager()
    parser = LogParser(
        str(log_path),
        setup_root=str(setup_root),
        session_manager=manager,
    )
    sessions = await parser.parse_file()

    assert len(sessions) == 1
    session = sessions[0]
    assert session.car_configuration_id == "preset_mx5ndcup_mech_1"
    assert session.car_configuration_label == "ND2"
    assert len(session.laps) == 2
    first, second = session.laps
    assert "preset_name Loaded" in first.setup_notes
    assert "pressure_FL 24.1" in first.setup_notes
    assert "pressure_FL 25.4" not in first.setup_notes
    assert "pressure_FL 25.4" in second.setup_notes
    assert "telemetry_laps_to_record" not in second.setup_notes
    assert first.car_configuration_id == "preset_mx5ndcup_mech_1"
    assert first.car_configuration_label == "ND2"


def test_setup_file_configuration_is_fallback_when_set_car_line_is_missing(tmp_path):
    setup_root = tmp_path / "Car Setups"
    setup_path = setup_root / "Mazda MX-5 ND Cup" / "fallback.carsetup"
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(_setup_bytes())
    parser = LogParser(str(tmp_path / "ace.txt"), setup_root=str(setup_root))

    parser._process_line("[gameface] [info] Load preset Fallback")
    parser._process_line(
        f'[fastStorage] [warning] Opening non-packed file "{setup_path}"'
    )

    identification = parser._session_manager.get_player_identification()
    assert identification.car_configuration_id == "preset_mx5ndcup_mech_1"
    assert identification.car_configuration_label == "ND2"


def test_setup_loaded_before_game_start_survives_shared_session_reset(tmp_path):
    setup_root = tmp_path / "Car Setups"
    setup_path = setup_root / "Mazda MX-5 ND Cup" / "prestart.carsetup"
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(_setup_bytes())
    parser = LogParser(str(tmp_path / "ace.txt"), setup_root=str(setup_root))

    parser._process_line("[gameface] [info] Load preset Prestart")
    parser._process_line(
        f'[fastStorage] [warning] Opening non-packed file "{setup_path}"'
    )
    parser._process_line(_game_started())

    assert parser.current_session is not None
    assert "preset_name Prestart" in parser.current_session.setup_notes
    assert "pressure_FL 24.1" in parser.current_session.setup_notes
    assert parser.current_session.car_configuration_label == "ND2"


def test_mismatched_delayed_setup_signal_does_not_replace_selected_car(tmp_path):
    setup_root = tmp_path / "Car Setups"
    setup_path = setup_root / "Mazda MX-5 ND Cup" / "stale.carsetup"
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(_setup_bytes())
    parser = LogParser(str(tmp_path / "ace.txt"), setup_root=str(setup_root))
    parser._process_line(
        "[gameplay] [info] onSetPlayerCurrentCarCommand: Set new car abc123 "
        "content\\cars\\ks_mazda_mx5_nd_cup\\presets\\"
        "preset_mx5ndcup_mech_2.mechanicalcarpreset"
    )

    parser._process_line(
        f'[fastStorage] [warning] Opening non-packed file "{setup_path}"'
    )

    snapshot = parser._session_manager.get_car_setup_snapshot()
    assert snapshot.values == ()
    assert snapshot.car_configuration_id == "preset_mx5ndcup_mech_2"
    assert snapshot.car_configuration_label == "ND1"


def test_explicit_setup_load_can_arrive_before_matching_car_signal(tmp_path):
    setup_root = tmp_path / "Car Setups"
    setup_path = setup_root / "Mazda MX-5 ND Cup" / "reordered.carsetup"
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(_setup_bytes())
    parser = LogParser(str(tmp_path / "ace.txt"), setup_root=str(setup_root))
    parser._session_manager.update_car_configuration(
        "old_car", "preset_old_mech_1", "Old"
    )

    parser._process_line("[gameface] [info] Load preset Reordered")
    parser._process_line(
        f'[fastStorage] [warning] Opening non-packed file "{setup_path}"'
    )
    parser._process_line(
        "[gameplay] [info] onSetPlayerCurrentCarCommand: Set new car abc123 "
        "content\\cars\\ks_mazda_mx5_nd_cup\\presets\\"
        "preset_mx5ndcup_mech_1.mechanicalcarpreset"
    )

    snapshot = parser._session_manager.get_car_setup_snapshot()
    assert snapshot.preset_name == "Reordered"
    assert ("pressure_FL", "24.1") in snapshot.values
    assert snapshot.car_configuration_label == "ND2"


def test_missing_or_outside_setup_file_fails_closed_without_losing_manual_values(tmp_path):
    setup_root = tmp_path / "Car Setups"
    setup_root.mkdir()
    outside = tmp_path / "outside.carsetup"
    outside.write_bytes(_setup_bytes())
    parser = LogParser(str(tmp_path / "ace.txt"), setup_root=str(setup_root))

    parser._process_line("[gameface] [info] KS-SETUP-GROUP pressure_FL 26")
    parser._process_line("[gameface] [info] Load preset Unsafe")
    parser._process_line(
        f'[fastStorage] [warning] Opening non-packed file "{outside}"'
    )

    snapshot = parser._session_manager.get_car_setup_snapshot()
    assert snapshot.values == (("pressure_FL", "26"),)
    assert snapshot.preset_name is None


@pytest.mark.asyncio
async def test_follow_callback_uses_shm_boundary_setup_not_later_edit(tmp_path):
    log_path = tmp_path / "ace.txt"
    log_path.write_text("", encoding="utf-8")
    manager = SharedSessionManager()
    manager.update_car_configuration(
        "ks_mazda_mx5_nd_cup",
        "preset_mx5ndcup_mech_1",
        "ND2",
    )
    manager.replace_car_setup_from_file({"pressure_FL": "24.1"}, "Loaded")
    emitted = []
    lap_emitted = asyncio.Event()

    async def on_lap(_session, lap):
        emitted.append(lap)
        lap_emitted.set()

    parser = LogParser(
        str(log_path),
        on_lap_complete=on_lap,
        session_manager=manager,
    )
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0.02
    parser.current_session = SessionData(
        track="Red Bull Ring GP",
        car="ks_mazda_mx5_nd_cup",
        session_type="PRACTICE",
    )
    follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
    await asyncio.sleep(0.03)
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 89_900,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 50,
            "last_laptime_ms": 90_000,
            "is_valid_lap": True,
        }
    )
    manager.update_car_setup_value("pressure_FL", "25.4")

    try:
        await asyncio.wait_for(lap_emitted.wait(), timeout=1.0)
    finally:
        parser.stop()
        await asyncio.wait_for(follow_task, timeout=1.0)

    assert len(emitted) == 1
    assert "pressure_FL 24.1" in emitted[0].setup_notes
    assert "pressure_FL 25.4" not in emitted[0].setup_notes
    assert emitted[0].car_configuration_label == "ND2"


@pytest.mark.asyncio
async def test_public_submission_uses_lap_snapshot_after_later_setup_change(tmp_path):
    setup_root = tmp_path / "Car Setups"
    setup_path = setup_root / "Mazda MX-5 ND Cup" / "Loaded.carsetup"
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(_setup_bytes())
    log_path = tmp_path / "ace.txt"
    log_path.write_text(
        "\n".join(
            [
                (
                    "[2026-08-16 15:32:44.000] [gameplay] [info] ACEVO-2629 "
                    "onSetPlayerCurrentCarCommand: Set new car abc123-def456 "
                    "content\\cars\\ks_mazda_mx5_nd_cup\\presets\\"
                    "preset_mx5ndcup_mech_1.mechanicalcarpreset"
                ),
                _game_started(),
                "[gameface] [info] Load preset Loaded",
                f'[fastStorage] [warning] Opening non-packed file "{setup_path}"',
                *_lap_lines(1, 90_000),
                "[gameface] [info] KS-SETUP-GROUP pressure_FL 25.4",
                *_lap_lines(2, 89_000),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    manager = SharedSessionManager()
    session = (await LogParser(str(log_path), setup_root=str(setup_root), session_manager=manager).parse_file())[0]
    first, second = session.laps
    session.setup_notes = second.setup_notes

    response = MagicMock(status_code=201)
    response.json.return_value = {"id": "lap-1"}
    client = APIClient(session_manager=manager)
    send = AsyncMock(return_value=response)
    with (
        patch("src.core.api_client.is_secret_configured", return_value=True),
        patch("src.core.api_client.is_game_running", return_value=GameProcessStatus.RUNNING),
        patch("src.core.api_client.sign_payload", side_effect=lambda payload: payload),
        patch.object(client, "_send_submission", new=send),
    ):
        result = await client.submit_lap(
            session,
            first,
            user_id="76561198321627695",
        )

    assert result.status is SubmissionStatus.SUCCESS
    submitted = send.call_args.args[0]
    assert "pressure_FL 24.1" in submitted["setupNotes"]
    assert "pressure_FL 25.4" not in submitted["setupNotes"]
    assert submitted["carConfigurationId"] == "preset_mx5ndcup_mech_1"
