"""Decode ACE ``.carsetup`` snapshots and car mechanical configurations.

ACE logs the path of a loaded setup but does not repeat its values.  The file
is a compact protobuf-wire message, so this module implements the small,
dependency-free subset needed by the client.  File access is deliberately
limited to ACE's user setup directory.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import struct
from typing import Any, Optional


MAX_SETUP_FILE_SIZE = 1024 * 1024
DEFAULT_SETUP_ROOT = Path.home() / "Saved Games" / "ACE" / "Car Setups"

_CONFIGURATION_RE = re.compile(
    r"^(?P<car>.+?)_"
    r"(?P<mechanical>preset_.+?_mech_\d+)_"
    r"(?P<visual>preset_.+?_visual_\d+)$",
    re.IGNORECASE,
)


class CarSetupDecodeError(ValueError):
    """Raised when a setup file is unsafe, malformed, or unsupported."""


@dataclass(frozen=True)
class DecodedCarSetup:
    """A decoded setup and the configuration embedded in that setup."""

    values: dict[str, str]
    car_id: Optional[str] = None
    car_configuration_id: Optional[str] = None
    visual_configuration_id: Optional[str] = None


@dataclass(frozen=True)
class _WireField:
    field: int
    wire: int
    value: Optional[int | float] = None
    raw: bytes = b""
    children: tuple["_WireField", ...] = ()


def _read_varint(data: bytes, position: int, end: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while position < end and shift < 70:
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
        shift += 7
    raise CarSetupDecodeError("unterminated protobuf varint")


def _decode_message(data: bytes, *, depth: int = 0) -> tuple[_WireField, ...]:
    if depth > 12:
        raise CarSetupDecodeError("setup nesting is too deep")
    position = 0
    end = len(data)
    fields: list[_WireField] = []
    while position < end:
        tag, position = _read_varint(data, position, end)
        number = tag >> 3
        wire = tag & 7
        if number <= 0:
            raise CarSetupDecodeError("invalid protobuf field number")
        if wire == 0:
            value, position = _read_varint(data, position, end)
            fields.append(_WireField(number, wire, value=value))
        elif wire == 1:
            if position + 8 > end:
                raise CarSetupDecodeError("truncated 64-bit setup field")
            raw = data[position : position + 8]
            position += 8
            fields.append(_WireField(number, wire, raw=raw))
        elif wire == 2:
            size, position = _read_varint(data, position, end)
            if size < 0 or position + size > end:
                raise CarSetupDecodeError("truncated length-delimited setup field")
            raw = data[position : position + size]
            position += size
            try:
                children = _decode_message(raw, depth=depth + 1) if raw else ()
            except CarSetupDecodeError:
                children = ()
            fields.append(
                _WireField(number, wire, raw=raw, children=children)
            )
        elif wire == 5:
            if position + 4 > end:
                raise CarSetupDecodeError("truncated 32-bit setup field")
            raw = data[position : position + 4]
            position += 4
            fields.append(
                _WireField(number, wire, value=struct.unpack("<f", raw)[0], raw=raw)
            )
        else:
            raise CarSetupDecodeError(f"unsupported protobuf wire type {wire}")
    return tuple(fields)


def _top(fields: tuple[_WireField, ...], number: int) -> list[_WireField]:
    return [entry for entry in fields if entry.field == number]


def _child(parent: Optional[_WireField], number: int) -> Optional[_WireField]:
    if parent is None:
        return None
    return next((entry for entry in parent.children if entry.field == number), None)


def _float_value(parent: Optional[_WireField], number: int) -> Optional[float]:
    entry = _child(parent, number)
    if entry is None or not isinstance(entry.value, (int, float)):
        return None
    value = float(entry.value)
    return value if math.isfinite(value) else None


def _format_value(value: float) -> str:
    if abs(value) < 0.0000005:
        value = 0.0
    if abs(value - round(value)) < 0.00001:
        return str(int(round(value)))
    return f"{value:.5f}".rstrip("0").rstrip(".")


def _put(values: dict[str, str], key: str, value: Optional[float]) -> None:
    if value is not None and math.isfinite(value):
        values[key] = _format_value(value)


def parse_car_configuration_string(
    raw: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return ``(car, mechanical preset, visual preset)`` from setup metadata."""

    match = _CONFIGURATION_RE.fullmatch(raw.strip())
    if not match:
        return None, None, None
    return (
        match.group("car").lower(),
        match.group("mechanical").lower(),
        match.group("visual").lower(),
    )


def decode_car_setup(data: bytes) -> DecodedCarSetup:
    """Decode the garage values stored in one ACE ``.carsetup`` message."""

    if not data:
        raise CarSetupDecodeError("setup file is empty")
    fields = _decode_message(data)
    values: dict[str, str] = {}

    chassis = next(iter(_top(fields, 1)), None)
    arbs = _child(chassis, 1)
    if arbs is not None and len(arbs.raw) == 8:
        arb_front, arb_rear = struct.unpack("<ff", arbs.raw)
        _put(values, "arbs0", arb_front)
        _put(values, "arbs1", arb_rear)
    _put(values, "steering_ratio", _float_value(chassis, 2))
    brakes = _child(chassis, 3)
    _put(values, "brakesfront_bias", _float_value(brakes, 1))
    _put(values, "brake_power", _float_value(brakes, 2))

    corners = ("FL", "FR", "RL", "RR")
    for corner, spring in zip(corners, _top(fields, 2)):
        _put(values, f"spring_{corner}", _float_value(spring, 1))
        bump_stop = _child(spring, 2)
        _put(values, f"bumpstop_range_{corner}", _float_value(bump_stop, 1))
        _put(values, f"bumpstop_rate_{corner}", _float_value(bump_stop, 2))
        packer = _child(spring, 3)
        _put(values, f"packer_range_{corner}", _float_value(packer, 1))
        _put(values, f"packer_rate_{corner}", _float_value(packer, 2))

    for corner, damper in zip(corners, _top(fields, 3)):
        _put(values, f"damper_bump_{corner}", _float_value(damper, 1))
        _put(values, f"damper_rebound_{corner}", _float_value(damper, 3))

    for corner, wheel in zip(corners, _top(fields, 4)):
        _put(values, f"pressure_{corner}", _float_value(wheel, 1))
        _put(values, f"camber_{corner}", _float_value(wheel, 2))
        # Protobuf omits scalar zeroes.  A present wheel with no field 3 means
        # the garage toe setting is exactly zero, not unknown.
        toe = _float_value(wheel, 3)
        _put(values, f"toe_{corner}", 0.0 if toe is None else toe)

    aero = next(iter(_top(fields, 5)), None)
    for number in (1, 2, 3, 5):
        _put(values, f"aero_{number}", _float_value(aero, number))

    ride_height = next(iter(_top(fields, 6)), None)
    _put(values, "ride_height_front", _float_value(ride_height, 2))
    _put(values, "ride_height_rear", _float_value(ride_height, 3))
    for number in (4, 5):
        _put(values, f"ride_height_aux_{number}", _float_value(ride_height, number))

    fuel = next(iter(_top(fields, 7)), None)
    _put(values, "fuel", _float_value(fuel, 1))

    car_id = configuration_id = visual_id = None
    configuration = next(iter(_top(fields, 9)), None)
    if configuration is not None:
        try:
            raw_configuration = configuration.raw.decode("utf-8")
        except UnicodeDecodeError:
            raw_configuration = ""
        car_id, configuration_id, visual_id = parse_car_configuration_string(
            raw_configuration
        )

    if not values:
        raise CarSetupDecodeError("setup contains no supported garage values")
    return DecodedCarSetup(
        values=values,
        car_id=car_id,
        car_configuration_id=configuration_id,
        visual_configuration_id=visual_id,
    )


def _resolved_setup_path(path: str | Path, setup_root: str | Path) -> Path:
    candidate = Path(str(path).replace("\\\\", "\\"))
    root = Path(setup_root).resolve()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CarSetupDecodeError("setup file is unavailable") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CarSetupDecodeError("setup file is outside ACE setup root") from exc
    if resolved.suffix.lower() != ".carsetup":
        raise CarSetupDecodeError("setup file has an invalid extension")
    return resolved


def load_car_setup_file(
    path: str | Path,
    setup_root: str | Path = DEFAULT_SETUP_ROOT,
) -> DecodedCarSetup:
    """Safely read and decode a setup from ACE's user setup directory."""

    resolved = _resolved_setup_path(path, setup_root)
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise CarSetupDecodeError("setup file is unavailable") from exc
    if size > MAX_SETUP_FILE_SIZE:
        raise CarSetupDecodeError("setup file is too large")
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise CarSetupDecodeError("setup file is unavailable") from exc
    return decode_car_setup(data)


def _load_configuration_catalog() -> dict[str, dict[str, str]]:
    path = Path(__file__).with_name("data") / "car_configuration_catalog.json"
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(car).lower(): {
            str(configuration).lower(): str(label)
            for configuration, label in configurations.items()
            if isinstance(configurations, dict) and isinstance(label, str)
        }
        for car, configurations in raw.items()
        if isinstance(configurations, dict)
    }


_CONFIGURATION_CATALOG = _load_configuration_catalog()


def get_car_configuration_label(car_id: str, configuration_id: str) -> str:
    """Return a friendly label, retaining the stable raw ID as fallback."""

    return _CONFIGURATION_CATALOG.get(car_id.lower(), {}).get(
        configuration_id.lower(), configuration_id
    )
