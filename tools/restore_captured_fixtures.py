"""Reproduce the redacted, captured regression fixtures from their Git source.

Run from the repository root. Source bytes stay in memory; only redacted output
is written. This deliberately does not import decoder offsets or regenerate
signals from the implementation under test.
"""

from __future__ import annotations

import hashlib
import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SOURCE = "2fadd7f"
STEAM_ID = "76561198000000000"


def source(name: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{SOURCE}:tests/fixtures/{name}"], cwd=ROOT
    )


def redact_graphics(data: bytes) -> bytes:
    """Replace only populated char[33] human-name fields, including padding."""
    result = bytearray(data)
    for start, replacement in ((3020, b"Fixture Driver"), (3053, b"Fixture Surname")):
        if any(data[start:start + 33]):
            result[start:start + 33] = replacement.ljust(33, b"\0")
    return bytes(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify provenance and redactions without writing files.")
    args = parser.parse_args()
    rows = [json.loads(line) for line in source("sample_telemetry.jsonl").splitlines()]
    names: set[str] = set()
    graphics = bytes.fromhex(source("ac_evo_graphics_frame.txt").decode().strip())
    for data in [graphics, *(bytes.fromhex(row["graphics_raw"]) for row in rows)]:
        for start in (3020, 3053):
            name = data[start:start + 33].split(b"\0")[0].decode("utf-8", "surrogateescape")
            if name.strip():
                names.add(name)

    first_time = datetime.fromisoformat(rows[0]["timestamp"])
    epoch = first_time.replace(year=2000, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    for row in rows:
        row["timestamp"] = (epoch + (datetime.fromisoformat(row["timestamp"]) - first_time)).isoformat()
        row["graphics_raw"] = redact_graphics(bytes.fromhex(row["graphics_raw"])).hex()
    outputs = {
        "sample_telemetry.jsonl": ("\n".join(json.dumps(row) for row in rows) + "\n").encode(),
        "ac_evo_graphics_frame.txt": redact_graphics(graphics).hex().encode() + b"\n",
        "ac_evo_graphics_frame_physics.json": source("ac_evo_graphics_frame_physics.json"),
        "ac_evo_static_frame.txt": source("ac_evo_static_frame.txt"),
    }

    # Surrogate escapes preserve the capture's existing invalid UTF-8 bytes.
    log = source("sample_log.txt").decode("utf-8", "surrogateescape")
    names.update(re.findall(r"\tDriver (.+?) on car ", log))
    names.update(raw.split("|", 1)[0].strip() for raw in re.findall(r"connecting gamecar [a-f0-9-]+ \(([^)]+)\)", log))
    names.update(re.findall(r"(?i)[A-Z]:\\+Users\\+([^\\\r\n]+)", log))
    replacements = {name: f"FixtureDriver{index:02d}" for index, name in enumerate(sorted(name for name in names if name.strip()), 1)}
    for name in sorted(replacements, key=len, reverse=True):
        log = re.sub(r"(?<![\w])" + re.escape(name) + r"(?![\w])", replacements[name], log)

    # Keep numeric identity syntax and all repeated car/driver relationships.
    log = re.sub(r"(?<!\d)7656119\d{10}(?!\d)", STEAM_ID, log)
    identifiers: dict[str, str] = {}

    def replace_identifier(match: re.Match) -> str:
        value = match.group()
        if value not in identifiers:
            index = len(identifiers) + 1
            if len(value) == 33:
                identifiers[value] = f"aaaaaaaaaaaaaaaa-{index:016x}"
            else:
                identifiers[value] = f"aaaaaaaa-aaaa-aaaa-aaaa-{index:012x}"
        return identifiers[value]

    log = re.sub(r"\b(?:[a-fA-F0-9]{16}-[a-fA-F0-9]{16}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})\b", replace_identifier, log)
    log = re.sub(r"(WebSocket handshake message:).*", r"\1 <redacted-handshake>", log)
    # Development source paths are not user directories and remain useful log
    # noise. User-directory prefixes become an explicitly fictional location.
    log = re.sub(r"(?i)[A-Z]:\\+Users\\+[^\\\r\n]+", lambda _: r"C:\Fixtures\User", log)
    stamp = re.compile(r"(?m)^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\]")
    origin = datetime.fromisoformat(stamp.search(log).group(1))
    log = stamp.sub(lambda match: "[" + (datetime(2000, 1, 1) + (datetime.fromisoformat(match.group(1)) - origin)).isoformat(sep=" ", timespec="milliseconds") + "]", log)
    outputs["sample_log.txt"] = log.encode("utf-8", "surrogateescape")

    manifest = {
        "source_commit": subprocess.check_output(["git", "rev-parse", SOURCE], cwd=ROOT, text=True).strip(),
        "source_directory": "tests/fixtures",
        "telemetry_frame_range_inclusive": [0, 1930],
        "log_line_range_inclusive": [1, 14672],
        "graphics_redaction_spans": [[3020, 3053], [3053, 3086]],
        "capture_reported_versions": {
            "sample_telemetry.jsonl": "ACE 0.8.0.1, SHM 1.0 (populated static buffers)",
            "sample_log.txt": "ACE 0.5.4 (Build release line)",
            "ac_evo_static_frame.txt": "ACE 0.6.2, SHM 1.0",
            "ac_evo_graphics_frame.txt": "Unknown: no version encoded in graphics region",
        },
        "timestamp_redaction": {
            "sample_telemetry.jsonl": "One constant shift to 2000-01-01T00:00:00+00:00; original elapsed intervals retained",
            "sample_log.txt": "One constant shift of line-prefix timestamps to 2000-01-01 00:00:00.000; game/build dates unchanged",
        },
        "log_redactions": [
            "Human names from graphics and log identity fields become consistent FixtureDriverNN aliases; whitespace-only fields are not names",
            "Numeric Steam identity becomes 76561198000000000; car/driver UUID relationships use deterministic aaaaaaaa prefixes",
            "Windows user-directory prefixes become C:\\Fixtures\\User; public application/development paths remain",
            "Complete WebSocket handshake payloads become <redacted-handshake>",
            "Original line ordering, mixed UTF-8 bytes, channels, event names, lap times and source line count are preserved",
        ],
        "signal_preservation": "All physics/static bytes unchanged; graphics bytes outside name/surname spans unchanged; no frames removed or synthesized",
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in outputs.items()},
    }
    if args.check:
        for name, data in outputs.items():
            assert (FIXTURES / name).read_bytes() == data, f"Captured fixture differs: {name}"
        assert json.loads((FIXTURES / "captured_provenance.json").read_text()) == manifest
        print("Verified all source signals, redaction spans, timestamp shifts, log transformations and fixture hashes; no files written.")
    else:
        for name, data in outputs.items():
            (FIXTURES / name).write_bytes(data)
        (FIXTURES / "captured_provenance.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
        print("Restored five redacted captured fixtures and provenance manifest.")


if __name__ == "__main__":
    main()
