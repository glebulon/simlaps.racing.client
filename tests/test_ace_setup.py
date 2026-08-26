import os
import struct

import pytest

from src.core.ace_setup import (
    CarSetupDecodeError,
    decode_car_setup,
    load_car_setup_file,
    parse_car_configuration_string,
)


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


def make_setup_bytes(
    *,
    pressure_fl: float = 24.1,
    configuration: str = (
        "ks_mazda_mx5_nd_cup_preset_mx5ndcup_mech_1_"
        "preset_mx5ndcup_visual_1"
    ),
) -> bytes:
    chassis = b"".join(
        (
            _message(1, struct.pack("<ff", 39_000.0, 8_800.0)),
            _float(2, 14.6),
            _message(3, _float(1, 68.5) + _float(2, 100.0)),
        )
    )
    springs = [60_000.0, 60_000.0, 40_000.0, 40_000.0]
    dampers = [(5.0, 7.0), (5.0, 7.0), (6.0, 8.0), (6.0, 8.0)]
    wheels = [
        (pressure_fl, -2.0, -0.02),
        (24.2, -2.0, -0.02),
        (25.1, -1.5, 0.01),
        (25.2, -1.5, 0.01),
    ]

    payload = _message(1, chassis)
    for spring in springs:
        payload += _message(2, _float(1, spring))
    for bump, rebound in dampers:
        payload += _message(3, _float(1, bump) + _float(3, rebound))
    for pressure, camber, toe in wheels:
        payload += _message(
            4,
            _float(1, pressure) + _float(2, camber) + _float(3, toe),
        )
    payload += _message(5, _float(1, 2.0) + _float(2, 3.0) + _float(3, 4.0) + _float(5, 5.0))
    payload += _message(6, _float(2, 65.0) + _float(3, 70.0))
    payload += _message(7, _float(1, 18.0))
    payload += _message(9, configuration.encode("utf-8"))
    return payload


def test_decode_car_setup_returns_full_snapshot_and_configuration():
    setup = decode_car_setup(make_setup_bytes())

    assert setup.values["arbs0"] == "39000"
    assert setup.values["arbs1"] == "8800"
    assert setup.values["brakesfront_bias"] == "68.5"
    assert setup.values["spring_FL"] == "60000"
    assert setup.values["damper_bump_RL"] == "6"
    assert setup.values["damper_rebound_RR"] == "8"
    assert setup.values["pressure_FL"] == "24.1"
    assert setup.values["camber_FR"] == "-2"
    assert setup.values["toe_RR"] == "0.01"
    assert setup.values["ride_height_front"] == "65"
    assert setup.values["fuel"] == "18"
    assert setup.car_id == "ks_mazda_mx5_nd_cup"
    assert setup.car_configuration_id == "preset_mx5ndcup_mech_1"
    assert setup.visual_configuration_id == "preset_mx5ndcup_visual_1"


def test_load_car_setup_file_is_limited_to_setup_root(tmp_path):
    setup_root = tmp_path / "Car Setups"
    allowed = setup_root / "Mazda MX-5 ND Cup" / "test.carsetup"
    allowed.parent.mkdir(parents=True)
    allowed.write_bytes(make_setup_bytes())

    assert load_car_setup_file(allowed, setup_root).values["fuel"] == "18"

    outside = tmp_path / "outside.carsetup"
    outside.write_bytes(make_setup_bytes())
    with pytest.raises(CarSetupDecodeError, match="outside ACE setup root"):
        load_car_setup_file(outside, setup_root)

    sibling = tmp_path / "Car Setups sibling" / "sibling.carsetup"
    sibling.parent.mkdir()
    sibling.write_bytes(make_setup_bytes())
    with pytest.raises(CarSetupDecodeError, match="outside ACE setup root"):
        load_car_setup_file(sibling, setup_root)

    traversal = setup_root / ".." / "outside.carsetup"
    with pytest.raises(CarSetupDecodeError, match="outside ACE setup root"):
        load_car_setup_file(traversal, setup_root)


def test_load_car_setup_file_rejects_symlink_escape(tmp_path):
    setup_root = tmp_path / "Car Setups"
    setup_root.mkdir()
    outside = tmp_path / "outside.carsetup"
    outside.write_bytes(make_setup_bytes())
    link = setup_root / "link.carsetup"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(CarSetupDecodeError, match="outside ACE setup root"):
        load_car_setup_file(link, setup_root)


def test_load_car_setup_file_rejects_wrong_extension_and_oversized_file(tmp_path):
    setup_root = tmp_path / "Car Setups"
    setup_root.mkdir()
    wrong_extension = setup_root / "setup.bin"
    wrong_extension.write_bytes(make_setup_bytes())
    with pytest.raises(CarSetupDecodeError, match="extension"):
        load_car_setup_file(wrong_extension, setup_root)

    oversized = setup_root / "huge.carsetup"
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(CarSetupDecodeError, match="too large"):
        load_car_setup_file(oversized, setup_root)


def test_parse_configuration_string_rejects_unrelated_text():
    assert parse_car_configuration_string("not-a-configuration") == (None, None, None)


def test_decode_car_setup_rejects_truncated_wire_data():
    with pytest.raises(CarSetupDecodeError):
        decode_car_setup(b"\x0a\x08\x00")
