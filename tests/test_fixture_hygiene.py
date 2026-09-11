"""Keep captured and synthetic fixtures free of credentials and personal data."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# A synthetic Steam ID is acceptable when a protocol test needs to exercise
# the ID-shaped field. Keep the exception explicit and visibly deterministic.
SYNTHETIC_STEAM_ID64 = b"76561198000000000"

SENSITIVE_PATTERNS = {
    "JWT": re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "Discord webhook": re.compile(rb"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+", re.I),
    "Windows user path": re.compile(rb"[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+", re.I),
    "Unix user path": re.compile(rb"/Users/[A-Za-z0-9._-]+", re.I),
    "Unredacted handshake": re.compile(rb"WebSocket handshake message: (?!<redacted-handshake>)"),
}
PERSONAL_ALIASES = (b"gleb", b"glebulon")


def _fixture_files() -> list[Path]:
    """Return tracked fixtures, with a source-tree fallback for sdists."""
    repo_root = FIXTURES_DIR.parents[1]
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--", "tests/fixtures"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return sorted(repo_root / line for line in result.stdout.splitlines())
    return sorted(path for path in FIXTURES_DIR.rglob("*") if path.is_file())


def _payloads(path: Path):
    """Inspect binary contents of hex fixtures as well as their text envelope."""
    data = path.read_bytes()
    yield data
    if path.name.endswith("_frame.txt"):
        yield bytes.fromhex(data.decode().strip())
    elif path.suffix == ".jsonl":
        for line in data.splitlines():
            for key, value in json.loads(line).items():
                if key.endswith("_raw"):
                    yield bytes.fromhex(value)


def test_tracked_fixtures_are_redacted() -> None:
    findings: list[str] = []
    for path in _fixture_files():
        for data in _payloads(path):
            # ASCII/UTF-8 and UTF-16LE human strings occur in binary mappings.
            for candidate in (data, data.replace(b"\x00", b"")):
                lowered = candidate.lower()
                for alias in PERSONAL_ALIASES:
                    if alias in lowered:
                        findings.append(f"{path}: personal alias")
                for label, pattern in SENSITIVE_PATTERNS.items():
                    if pattern.search(candidate):
                        findings.append(f"{path}: {label}")

    assert not findings, "Fixture hygiene violations:\n" + "\n".join(findings)


def test_captured_fixture_provenance_and_identity_fields() -> None:
    manifest = json.loads((FIXTURES_DIR / "captured_provenance.json").read_text())
    for name, expected in manifest["files"].items():
        data = (FIXTURES_DIR / name).read_bytes()
        assert len(data) == expected["bytes"], name
        assert hashlib.sha256(data).hexdigest() == expected["sha256"], name
    frames = [json.loads(line) for line in (FIXTURES_DIR / "sample_telemetry.jsonl").read_text().splitlines()]
    assert [frame["frame_number"] for frame in frames] == list(range(1931))
    for frame in frames:
        for key, size in (("physics_raw", 1024), ("graphics_raw", 4096), ("static_raw", 2048)):
            assert len(bytes.fromhex(frame[key])) == size
        graphics = bytes.fromhex(frame["graphics_raw"])
        for start, allowed in ((3020, b"Fixture Driver"), (3053, b"Fixture Surname")):
            assert graphics[start:start + 33] in (b"\0" * 33, allowed.ljust(33, b"\0"))
    log = (FIXTURES_DIR / "sample_log.txt").read_bytes()
    assert len(log.splitlines()) == 14672
    # IDs and timestamp syntax are not inherently sensitive. Check the actual
    # identity field of this redacted capture against its declared replacement.
    ids = re.findall(rb"\b(\d+) connected(?: \([^)]+\))? on car", log)
    assert ids and set(ids) == {SYNTHETIC_STEAM_ID64}


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda path: path.name)
def test_fixture_files_exist_under_fixture_root(path: Path) -> None:
    """Keep the scanner's parametrized inventory limited to fixture files."""
    assert path.is_relative_to(FIXTURES_DIR)
