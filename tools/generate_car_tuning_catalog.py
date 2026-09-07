#!/usr/bin/env python3
"""Generate compact car_tuning_catalog.json for use in AI prompt generation.

This script scans an Assetto Corsa Evo car folder, decodes the protobuf-encoded
.carsetup and .carsetuplimits files for each car, and extracts the tunable
parameters along with their number of discrete settings. The output is written
to src/core/data/car_tuning_catalog.json and is consumed by the telemetry AI
prompt generator (src/core/car_tuning_catalog.py).

Running the script
------------------
By default it looks for cars under ../extracted/content/cars relative to this
script. If your car files are elsewhere, pass the folder path as an argument::

    python tools/generate_car_tuning_catalog.py
    python tools/generate_car_tuning_catalog.py "C:/path/to/win-x64/cars/content/cars"

The script will:
1. Find every <car_key>/data/setup/ directory.
2. Try each .carsetup with each .carsetuplimits and keep the combination that
   yields the most tunable parameters (useful for cars with stock/performance
   variants).
3. Write src/core/data/car_tuning_catalog.json.

Detected parameters
-------------------
The current pattern list covers:
- Basic: tyre pressure, camber, toe, fuel
- Aero: ride height, rear wing angle
- Electronics: TC, TC2, ABS, stability control
- Brakes: brake bias, brake torque
- Suspension: anti-roll bars, steer ratio, wheel rate, bumpstops, diff preload
- Dampers: slow/fast bump and rebound

Adding support for a new car
----------------------------
If a car uses new protobuf field numbers for a known parameter, add or adjust
an entry in PARAMETER_PATTERNS. For entirely new parameters, dump the car's
setup/limits with inspect_car_setup.py first to find the relevant field numbers.
"""
import json
import os
import re
import struct
import sys

EXTRACTED_CARS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extracted", "content", "cars")
PREFERRED_SETUP_TOKENS = ("stock", "safe", "base")
AVOID_SETUP_TOKENS = ("wet",)
PREFERRED_LIMITS_TOKENS = ("limits", "lim")
IGNORED_FILE_TOKENS = {
    "carsetup",
    "carsetuplimits",
    "setup",
    "limits",
    "limit",
    "lim",
    "stock",
    "safe",
    "base",
    "wet",
}

PARAMETER_PATTERNS = [
    # Tyres / alignment  (limits top_field=4, 4 occurrences: FL FR RL RR)
    {"label": "Front tyre pressure", "top_field": 4, "occurrence_kind": "front_axle", "setup_field": 1, "limits_child_fields": [1], "validator": "pressure", "category": "basic"},
    {"label": "Rear tyre pressure", "top_field": 4, "occurrence_kind": "rear_axle", "setup_field": 1, "limits_child_fields": [1], "validator": "pressure", "category": "basic"},
    {"label": "Front camber", "top_field": 4, "occurrence_kind": "front_axle", "setup_field": 2, "limits_child_fields": [2, 5], "validator": "camber", "max_zero": True, "category": "basic"},
    {"label": "Rear camber", "top_field": 4, "occurrence_kind": "rear_axle", "setup_field": 2, "limits_child_fields": [2, 5], "validator": "camber", "max_zero": True, "category": "basic"},
    {"label": "Front toe", "top_field": 4, "occurrence_kind": "front_axle", "setup_field": 3, "limits_child_fields": [3], "validator": "toe", "category": "basic"},
    {"label": "Rear toe", "top_field": 4, "occurrence_kind": "rear_axle", "setup_field": 3, "limits_child_fields": [3], "validator": "toe", "category": "basic"},
    # Ride height / aero  (limits top_field=6, single occurrence)
    {"label": "Front ride height", "top_field": 6, "occurrence_kind": "first", "setup_field": 2, "limits_child_fields": [2], "validator": "ride_height", "category": "aero"},
    {"label": "Rear ride height", "top_field": 6, "occurrence_kind": "first", "setup_field": 3, "limits_child_fields": [3], "validator": "ride_height", "category": "aero"},
    {"label": "Rear wing angle", "top_field": 6, "occurrence_kind": "first", "setup_field": 5, "limits_child_fields": [5], "validator": "wing_angle", "min_zero": True, "require_enabled": True, "category": "aero"},
    # Fuel  (limits top_field=7)
    {"label": "Fuel load", "top_field": 7, "occurrence_kind": "first", "setup_field": 1, "limits_child_fields": [1], "validator": "fuel", "category": "basic"},
    # Electronics  (limits top_field=5, single occurrence, zero-based)
    {"label": "Traction control", "top_field": 5, "occurrence_kind": "first", "setup_field": 1, "limits_child_fields": [1], "validator": "electronics", "min_zero": True, "require_enabled": True, "category": "electronics"},
    {"label": "Traction control 2", "top_field": 5, "occurrence_kind": "first", "setup_field": 2, "limits_child_fields": [2], "validator": "electronics", "min_zero": True, "require_enabled": True, "category": "electronics"},
    {"label": "ABS", "top_field": 5, "occurrence_kind": "first", "setup_field": 3, "limits_child_fields": [3], "validator": "electronics", "min_zero": True, "require_enabled": True, "category": "electronics"},
    {"label": "Stability control", "top_field": 5, "occurrence_kind": "first", "limits_child_fields": [6], "validator": "electronics", "min_zero": True, "limits_only": True, "require_enabled": True, "category": "electronics"},
    # Suspension  (limits top_field=1, single occurrence)
    {"label": "Front anti-roll bar", "top_field": 1, "occurrence_kind": "first", "limits_child_fields": [1], "limits_occurrence": 0, "limits_only": True, "require_enabled": True, "validator": "arb", "category": "suspension"},
    {"label": "Rear anti-roll bar", "top_field": 1, "occurrence_kind": "first", "limits_child_fields": [1], "limits_occurrence": 1, "limits_only": True, "require_enabled": True, "validator": "arb", "category": "suspension"},
    {"label": "Steer ratio", "top_field": 1, "occurrence_kind": "first", "limits_child_fields": [2], "limits_only": True, "require_enabled": True, "validator": "steer_ratio", "category": "suspension"},
    {"label": "Brake bias", "top_field": 1, "occurrence_kind": "first", "limits_child_fields": [3], "limits_nested": [1], "limits_only": True, "require_enabled": True, "validator": "brake_bias", "category": "brakes"},
    {"label": "Brake torque", "top_field": 1, "occurrence_kind": "first", "limits_child_fields": [3], "limits_nested": [2], "limits_only": True, "require_enabled": True, "validator": "brake_torque", "category": "brakes"},
    {"label": "Differential preload", "top_field": 1, "occurrence_kind": "first", "limits_child_fields": [4], "limits_nested": [3], "limits_only": True, "require_enabled": True, "validator": "diff_preload", "category": "suspension"},
    # Springs  (limits top_field=2, 4 occurrences: FL FR RL RR)
    {"label": "Front wheel rate", "top_field": 2, "occurrence_kind": "front_axle", "setup_field": 1, "limits_child_fields": [1], "require_enabled": True, "validator": "wheel_rate", "category": "suspension"},
    {"label": "Rear wheel rate", "top_field": 2, "occurrence_kind": "rear_axle", "setup_field": 1, "limits_child_fields": [1], "require_enabled": True, "validator": "wheel_rate", "category": "suspension"},
    {"label": "Front bumpstop rate", "top_field": 2, "occurrence_kind": "front_axle", "limits_child_fields": [2], "limits_nested": [2], "limits_only": True, "require_enabled": True, "validator": "bumpstop_rate", "category": "suspension"},
    {"label": "Rear bumpstop rate", "top_field": 2, "occurrence_kind": "rear_axle", "limits_child_fields": [2], "limits_nested": [2], "limits_only": True, "require_enabled": True, "validator": "bumpstop_rate", "category": "suspension"},
    {"label": "Front bumpstop range", "top_field": 2, "occurrence_kind": "front_axle", "limits_child_fields": [2], "limits_nested": [1], "limits_only": True, "min_zero": True, "require_enabled": True, "validator": "bumpstop_range", "category": "suspension"},
    {"label": "Rear bumpstop range", "top_field": 2, "occurrence_kind": "rear_axle", "limits_child_fields": [2], "limits_nested": [1], "limits_only": True, "min_zero": True, "require_enabled": True, "validator": "bumpstop_range", "category": "suspension"},
    # Dampers  (limits top_field=3, 4 occurrences: FL FR RL RR)
    {"label": "Front slow bump", "top_field": 3, "occurrence_kind": "front_axle", "setup_field": 1, "limits_child_fields": [1], "require_enabled": True, "validator": "slow_damper", "category": "dampers"},
    {"label": "Rear slow bump", "top_field": 3, "occurrence_kind": "rear_axle", "setup_field": 1, "limits_child_fields": [1], "require_enabled": True, "validator": "slow_damper", "category": "dampers"},
    {"label": "Front fast bump", "top_field": 3, "occurrence_kind": "front_axle", "setup_field": 2, "limits_child_fields": [2], "require_enabled": True, "validator": "fast_damper", "category": "dampers"},
    {"label": "Rear fast bump", "top_field": 3, "occurrence_kind": "rear_axle", "setup_field": 2, "limits_child_fields": [2], "require_enabled": True, "validator": "fast_damper", "category": "dampers"},
    {"label": "Front slow rebound", "top_field": 3, "occurrence_kind": "front_axle", "setup_field": 3, "limits_child_fields": [3], "require_enabled": True, "validator": "slow_damper", "category": "dampers"},
    {"label": "Rear slow rebound", "top_field": 3, "occurrence_kind": "rear_axle", "setup_field": 3, "limits_child_fields": [3], "require_enabled": True, "validator": "slow_damper", "category": "dampers"},
    {"label": "Front fast rebound", "top_field": 3, "occurrence_kind": "front_axle", "setup_field": 4, "limits_child_fields": [4], "require_enabled": True, "validator": "fast_damper", "category": "dampers"},
    {"label": "Rear fast rebound", "top_field": 3, "occurrence_kind": "rear_axle", "setup_field": 4, "limits_child_fields": [4], "require_enabled": True, "validator": "fast_damper", "category": "dampers"},
]


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
    raise ValueError("unterminated varint")


def decode_message(data: bytes, start: int = 0, end: int | None = None) -> list[dict]:
    if end is None:
        end = len(data)
    pos = start
    items = []
    while pos < end:
        tag_pos = pos
        tag, pos = read_varint(data, pos)
        field = tag >> 3
        wire = tag & 7
        entry = {"field": field, "wire": wire, "offset": tag_pos}
        if wire == 0:
            value, pos = read_varint(data, pos)
            entry["value"] = value
        elif wire == 2:
            size, pos = read_varint(data, pos)
            raw = data[pos:pos + size]
            if len(raw) < size:
                raise ValueError("truncated length-delimited field")
            pos += size
            entry["size"] = size
            try:
                entry["children"] = decode_message(raw, 0, len(raw))
            except ValueError:
                entry["raw_hex"] = raw.hex()
        elif wire == 5:
            raw = data[pos:pos + 4]
            if len(raw) < 4:
                raise ValueError("truncated 32-bit field")
            pos += 4
            entry["value"] = struct.unpack("<f", raw)[0]
            entry["raw_hex"] = raw.hex()
        else:
            break
        items.append(entry)
    return items


def decode_file(path: str) -> list[dict]:
    with open(path, "rb") as f:
        return decode_message(f.read())


def tokenize_filename(name: str) -> list[str]:
    stem = os.path.splitext(name)[0].lower()
    parts = re.split(r"[^a-z0-9]+", stem)
    return [part for part in parts if part and part not in IGNORED_FILE_TOKENS]


def choose_setup_file(files: list[str]) -> str:
    dry_files = [name for name in files if not any(token in name.lower() for token in AVOID_SETUP_TOKENS)]
    candidates = dry_files or files

    def score(name: str) -> tuple[int, int, int]:
        lowered = name.lower()
        preferred = sum(token in lowered for token in PREFERRED_SETUP_TOKENS)
        avoided = sum(token in lowered for token in AVOID_SETUP_TOKENS)
        return preferred, -avoided, -len(lowered)

    return sorted(candidates, key=score, reverse=True)[0]


def choose_limits_file(setup_name: str, files: list[str]) -> str:
    setup_tokens = set(tokenize_filename(setup_name))

    def score(name: str) -> tuple[int, int, int, int]:
        lowered = name.lower()
        file_tokens = set(tokenize_filename(name))
        overlap = len(setup_tokens & file_tokens)
        preferred = sum(token in lowered for token in PREFERRED_LIMITS_TOKENS)
        avoided = sum(token in lowered for token in AVOID_SETUP_TOKENS)
        return overlap, preferred, -avoided, -len(lowered)

    return sorted(files, key=score, reverse=True)[0]


def discover_car_variants(cars_dir: str) -> list[tuple[str, str, str]]:
    discovered = []
    for car_key in sorted(os.listdir(cars_dir)):
        setup_dir = os.path.join(cars_dir, car_key, "data", "setup")
        if not os.path.isdir(setup_dir):
            continue

        setup_files = sorted(name for name in os.listdir(setup_dir) if name.endswith(".carsetup"))
        limits_files = sorted(name for name in os.listdir(setup_dir) if name.endswith(".carsetuplimits"))
        if not setup_files or not limits_files:
            continue

        setup_name = choose_setup_file(setup_files)
        limits_name = choose_limits_file(setup_name, limits_files)
        discovered.append(
            (
                car_key,
                os.path.join(setup_dir, setup_name),
                os.path.join(setup_dir, limits_name),
            )
        )
    return discovered


def discover_car_files(cars_dir: str) -> dict[str, list[tuple[str, str, str]]]:
    variants: dict[str, list[tuple[str, str, str]]] = {}
    for car_key in sorted(os.listdir(cars_dir)):
        setup_dir = os.path.join(cars_dir, car_key, "data", "setup")
        if not os.path.isdir(setup_dir):
            continue

        setup_files = sorted(name for name in os.listdir(setup_dir) if name.endswith(".carsetup"))
        limits_files = sorted(name for name in os.listdir(setup_dir) if name.endswith(".carsetuplimits"))
        if not setup_files or not limits_files:
            continue

        variants[car_key] = []
        for setup_name in setup_files:
            for limits_name in limits_files:
                variants[car_key].append(
                    (
                        car_key,
                        os.path.join(setup_dir, setup_name),
                        os.path.join(setup_dir, limits_name),
                    )
                )
    return variants


def get_top_level_entry(decoded: list[dict], field: int, occurrence: int) -> dict | None:
    matches = [entry for entry in decoded if entry.get("field") == field]
    if occurrence >= len(matches):
        return None
    return matches[occurrence]


def get_top_level_entries(decoded: list[dict], field: int) -> list[dict]:
    return [entry for entry in decoded if entry.get("field") == field]


def get_child_entry(entry: dict | None, field: int) -> dict | None:
    if not entry:
        return None
    for child in entry.get("children", []):
        if child.get("field") == field:
            return child
    return None


def get_float_value(entry: dict | None, field: int) -> float | None:
    child = get_child_entry(entry, field)
    if child is None:
        return None
    value = child.get("value")
    return float(value) if value is not None else None


def has_enabled_flag(entry: dict | None) -> bool:
    if not entry:
        return False
    for child in entry.get("children", []):
        if child.get("field") == 4 and child.get("wire") == 0 and child.get("value") == 1:
            return True
    return False


def get_range(entry: dict | None, min_zero: bool = False, max_zero: bool = False) -> tuple[float, float, float] | None:
    if not entry:
        return None
    step = get_float_value(entry, 1)
    minimum = get_float_value(entry, 2)
    maximum = get_float_value(entry, 3)
    if step is None:
        return None
    if minimum is None and min_zero:
        minimum = 0.0
    if minimum is None and maximum is not None:
        minimum = step
    if minimum is None:
        return None
    if maximum is None and max_zero:
        maximum = 0.0
    elif maximum is None:
        maximum = minimum
    return step, minimum, maximum


def settings_count(step: float, minimum: float, maximum: float) -> int:
    if step <= 0:
        return 1
    span = maximum - minimum
    if span <= 0:
        return 1
    return int(round(span / step)) + 1


def value_in_range(value: float, minimum: float, maximum: float, step: float) -> bool:
    tolerance = max(abs(step) * 0.51, 1e-5)
    return (minimum - tolerance) <= value <= (maximum + tolerance)


def is_pressure_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 15.0 <= value <= 40.0 and 10.0 <= minimum <= 40.0 and minimum <= maximum <= 45.0 and 0.009 <= step <= 5.0 and 1 <= count <= 200


def is_camber_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return -8.0 <= value <= 1.0 and -10.0 <= minimum <= 1.0 and minimum <= maximum <= 2.0 and 0.001 <= step <= 1.0 and 1 <= count <= 100


def is_toe_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return -2.0 <= value <= 2.0 and -2.5 <= minimum <= 2.0 and -2.5 <= maximum <= 2.5 and 0.00001 <= step <= 0.5 and 1 <= count <= 200


def is_ride_height_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    metric_scaled = 0.02 <= value <= 0.2 and 0.02 <= minimum <= 0.2 and minimum <= maximum <= 0.25 and 0.0001 <= step <= 0.02
    millimetre_scaled = 20.0 <= value <= 250.0 and 20.0 <= minimum <= 250.0 and minimum <= maximum <= 250.0 and 1.0 <= step <= 20.0
    return (metric_scaled or millimetre_scaled) and 1 <= count <= 100


def is_fuel_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 0.0 <= value <= 250.0 and 0.0 <= minimum <= 250.0 and 10.0 <= maximum <= 250.0 and 0.5 <= step <= 10.0 and 1 <= count <= 300


def is_electronics_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 0.0 <= minimum <= 1.0 and 1.0 <= maximum <= 20.0 and 0.5 <= step <= 2.0 and 2 <= count <= 25


def is_arb_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 5000.0 <= minimum <= 100000.0 and minimum <= maximum <= 200000.0 and 100.0 <= step <= 10000.0 and 2 <= count <= 200


def is_steer_ratio_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 8.0 <= minimum <= 20.0 and minimum <= maximum <= 25.0 and 0.1 <= step <= 2.0 and 2 <= count <= 50


def is_brake_bias_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 40.0 <= minimum <= 70.0 and minimum <= maximum <= 90.0 and 0.1 <= step <= 2.0 and 2 <= count <= 200


def is_brake_torque_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 50.0 <= minimum <= 100.0 and minimum <= maximum <= 110.0 and 0.5 <= step <= 5.0 and 2 <= count <= 50


def is_diff_preload_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 5.0 <= minimum <= 100.0 and minimum <= maximum <= 500.0 and 1.0 <= step <= 50.0 and 2 <= count <= 200


def is_wheel_rate_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 30000.0 <= minimum <= 300000.0 and minimum <= maximum <= 500000.0 and 100.0 <= step <= 20000.0 and 2 <= count <= 200


def is_bumpstop_rate_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 100.0 <= minimum <= 5000.0 and minimum <= maximum <= 30000.0 and 10.0 <= step <= 500.0 and 2 <= count <= 200


def is_bumpstop_range_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 0.0 <= minimum <= 5.0 and 0.001 <= maximum <= 0.1 and 0.0001 <= step <= 0.01 and 2 <= count <= 30


def is_slow_damper_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 0.0 <= minimum <= 5.0 and 1.0 <= maximum <= 20.0 and 0.5 <= step <= 2.0 and 2 <= count <= 25


def is_fast_damper_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 500.0 <= minimum <= 5000.0 and minimum <= maximum <= 30000.0 and 100.0 <= step <= 2000.0 and 2 <= count <= 100


def is_wing_angle_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 0.0 <= minimum <= 5.0 and 1.0 <= maximum <= 20.0 and 0.5 <= step <= 2.0 and 2 <= count <= 25


VALIDATORS = {
    "pressure": is_pressure_candidate,
    "camber": is_camber_candidate,
    "toe": is_toe_candidate,
    "ride_height": is_ride_height_candidate,
    "fuel": is_fuel_candidate,
    "electronics": is_electronics_candidate,
    "arb": is_arb_candidate,
    "steer_ratio": is_steer_ratio_candidate,
    "brake_bias": is_brake_bias_candidate,
    "brake_torque": is_brake_torque_candidate,
    "diff_preload": is_diff_preload_candidate,
    "wheel_rate": is_wheel_rate_candidate,
    "bumpstop_rate": is_bumpstop_rate_candidate,
    "bumpstop_range": is_bumpstop_range_candidate,
    "slow_damper": is_slow_damper_candidate,
    "fast_damper": is_fast_damper_candidate,
    "wing_angle": is_wing_angle_candidate,
}


def get_preferred_occurrence(decoded: list[dict], field: int, occurrence_kind: str) -> int:
    matches = get_top_level_entries(decoded, field)
    if occurrence_kind == "rear_axle":
        if len(matches) >= 4:
            return 2
        if len(matches) >= 2:
            return 1
    return 0


def find_matching_limits_child(
    limits_decoded: list[dict],
    top_field: int,
    preferred_occurrence: int,
    child_fields: list[int],
    default_value: float | None,
    validator_name: str,
    min_zero: bool = False,
    max_zero: bool = False,
    require_enabled: bool = False,
    limits_only: bool = False,
    limits_nested: list[int] | None = None,
    limits_occurrence: int | None = None,
) -> tuple[dict | None, int | None]:
    parents = get_top_level_entries(limits_decoded, top_field)
    if not parents:
        return None, None

    ordered_indices = [idx for idx in range(preferred_occurrence, len(parents))]
    ordered_indices.extend(idx for idx in range(0, preferred_occurrence))
    validator = VALIDATORS[validator_name]

    for idx in ordered_indices:
        for child_field in child_fields:
            if limits_occurrence is not None:
                children_for_field = [c for c in parents[idx].get("children", []) if c.get("field") == child_field]
                child = children_for_field[limits_occurrence] if limits_occurrence < len(children_for_field) else None
            else:
                child = get_child_entry(parents[idx], child_field)
            if limits_nested:
                for nested_field in limits_nested:
                    child = get_child_entry(child, nested_field)
            if require_enabled and not has_enabled_flag(child):
                continue
            range_info = get_range(child, min_zero=min_zero, max_zero=max_zero)
            if range_info is None:
                continue
            step, minimum, maximum = range_info
            count = settings_count(step, minimum, maximum)
            probe = default_value if default_value is not None else (minimum + maximum) / 2.0
            if (limits_only or value_in_range(probe, minimum, maximum, step)) and validator(probe, minimum, maximum, step, count):
                return child, count
    return None, None


def append_detected_param(params: list[dict], label: str, settings: int, category: str | None = None) -> None:
    if settings <= 1:
        return
    if any(existing["label"] == label for existing in params):
        return
    entry = {"label": label, "settings_count": settings}
    if category:
        entry["category"] = category
    params.append(entry)


def build_car_params(setup_decoded: list[dict], limits_decoded: list[dict]) -> list[dict]:
    params = []
    for pattern in PARAMETER_PATTERNS:
        limits_only = pattern.get("limits_only", False)
        preferred_occurrence = get_preferred_occurrence(setup_decoded, pattern["top_field"], pattern["occurrence_kind"])

        default_value = None
        if not limits_only:
            setup_parent = get_top_level_entry(setup_decoded, pattern["top_field"], preferred_occurrence)
            default_value = get_float_value(setup_parent, pattern["setup_field"])
            if default_value is None:
                continue

        limits_child, count = find_matching_limits_child(
            limits_decoded,
            pattern["top_field"],
            preferred_occurrence,
            pattern["limits_child_fields"],
            default_value,
            pattern["validator"],
            min_zero=pattern.get("min_zero", False),
            max_zero=pattern.get("max_zero", False),
            require_enabled=pattern.get("require_enabled", False),
            limits_only=limits_only,
            limits_nested=pattern.get("limits_nested"),
            limits_occurrence=pattern.get("limits_occurrence"),
        )
        if limits_child is None or count is None:
            continue

        append_detected_param(params, pattern["label"], count, pattern.get("category"))

    return params


def build_catalog(cars_dir: str | None = None) -> dict:
    catalog = {}
    variants = discover_car_files(cars_dir or EXTRACTED_CARS_DIR)
    for car_key, pairs in variants.items():
        best_params = []
        for _, setup_path, limits_path in pairs:
            setup_decoded = decode_file(setup_path)
            limits_decoded = decode_file(limits_path)
            params = build_car_params(setup_decoded, limits_decoded)
            if len(params) > len(best_params):
                best_params = params

        if best_params:
            catalog[car_key] = best_params

    return catalog


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    dest_dir = os.path.join(here, "..", "src", "core", "data")
    dest = os.path.join(dest_dir, "car_tuning_catalog.json")

    os.makedirs(dest_dir, exist_ok=True)

    cars_dir = sys.argv[1] if len(sys.argv) > 1 else None
    catalog = build_catalog(cars_dir)

    with open(dest, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)

    print(f"Generated {len(catalog)} car entries -> {dest}")
    sample_key = "ks_porsche_992_gt3_rs"
    if sample_key in catalog:
        print(f"\nSample ({sample_key}):")
        for p in catalog[sample_key]:
            print(f"  - {p['label']}  ({p['settings_count']} settings)")


if __name__ == "__main__":
    main()
