"""Regression tests for ``decode_static_evo``.

Validates the AC Evo ``SPageFileStaticEvo`` decoder against a real captured
static SHM frame (Brands Hatch Indy practice session). The fixture is the
hex-encoded 2048-byte buffer dumped by the live capture path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.telemetry_decoder import (
    STATIC_EVO_MIN_SIZE,
    decode_static,
    decode_static_evo,
)


FIXTURE = Path(__file__).parent / "fixtures" / "ac_evo_static_frame.txt"


@pytest.fixture(scope="module")
def static_bytes() -> bytes:
    """Captured static SHM bytes (Brands Hatch Indy practice)."""
    hex_str = FIXTURE.read_text(encoding="utf-8").strip()
    return bytes.fromhex(hex_str)


class TestStaticEvoBasics:
    def test_buffer_is_full_static_region(self, static_bytes):
        """Region is the standard 2 KiB static SHM region."""
        assert len(static_bytes) == 2048

    def test_decoder_returns_payload(self, static_bytes):
        result = decode_static_evo(static_bytes)
        assert result is not None
        assert result["_decoder"] == "ac_evo_static"
        assert result["buffer_size"] == 2048

    def test_too_small_buffer_returns_none(self):
        assert decode_static_evo(b"\x00" * (STATIC_EVO_MIN_SIZE - 1)) is None

    def test_zero_buffer_returns_none(self):
        """Buffer of all zeros (game not started yet) is rejected."""
        assert decode_static_evo(b"\x00" * 2048) is None

    def test_dispatch_routes_evo_first(self, static_bytes):
        """``decode_static`` prefers the evo decoder."""
        result = decode_static(static_bytes)
        assert result["_decoder"] == "ac_evo_static"


class TestStaticEvoFields:
    def test_versions_present(self, static_bytes):
        result = decode_static_evo(static_bytes)
        assert result["sm_version"] == "1.0"
        assert result["ac_evo_version"] == "0.6.2"
        # legacy alias
        assert result["ac_version"] == result["ac_evo_version"]

    def test_session_metadata(self, static_bytes):
        result = decode_static_evo(static_bytes)
        assert result["session_name"] == "Practice"
        # session enum 0 maps to PRACTICE in our lookup
        assert result["session"] == 0
        assert result["session_name_enum"] == "PRACTICE"
        assert result["number_of_sessions"] == 1
        assert result["event_id"] == 0
        assert result["session_id"] == 0

    def test_geography(self, static_bytes):
        result = decode_static_evo(static_bytes)
        assert result["nation"] == "GBR"
        # Brands Hatch geographic hints are not always populated; the
        # decode must still surface the float fields without raising.
        assert isinstance(result["longitude"], float)
        assert isinstance(result["latitude"], float)

    def test_track_identity(self, static_bytes):
        result = decode_static_evo(static_bytes)
        assert result["track"] == "Brands Hatch"
        assert result["track_configuration"] == "Indy"

    def test_track_length_brands_hatch_indy(self, static_bytes):
        """Brands Hatch Indy is 1.944 km in real life; SHM reports 1944 m."""
        result = decode_static_evo(static_bytes)
        assert result["track_length_m"] == pytest.approx(1944.0, abs=1.0)
        assert result["track_length_km"] == pytest.approx(1.944, abs=0.001)
        # legacy ACC alias
        assert result["track_spline_length"] == result["track_length_m"]

    def test_session_flags_have_expected_types(self, static_bytes):
        result = decode_static_evo(static_bytes)
        assert isinstance(result["is_static_weather"], bool)
        assert isinstance(result["is_timed_race"], bool)
        assert isinstance(result["is_online"], bool)
        # Practice on a single track config -> not online, not timed
        assert result["is_timed_race"] is False
        assert result["is_online"] is False

    def test_starting_grip_enum_named(self, static_bytes):
        result = decode_static_evo(static_bytes)
        # Whatever the int is, the lookup table either yields a known
        # name or stringifies the int. Either way it must be a str.
        assert isinstance(result["starting_grip_name"], str)


class TestStaticEvoSanityGates:
    def test_implausible_track_length_rejected(self, static_bytes):
        """If track_length_m lands outside the [1, 30 000] window the decoder
        bails so a wrong offset can't silently surface junk."""
        # Splice an obviously-bad track_length_m (NaN) into the buffer.
        import struct

        mutated = bytearray(static_bytes)
        struct.pack_into("<f", mutated, 204, float("nan"))
        assert decode_static_evo(bytes(mutated)) is None

    def test_zero_track_length_still_decodes(self, static_bytes):
        """track_length_m == 0.0 is allowed (early-load frame). The decoder
        must still return the rest of the populated payload rather than
        rejecting the whole frame."""
        import struct

        mutated = bytearray(static_bytes)
        struct.pack_into("<f", mutated, 204, 0.0)
        result = decode_static_evo(bytes(mutated))
        assert result is not None
        assert result["track_length_m"] == 0.0
        assert result["track_length_km"] == 0.0
        # Other fields still decoded cleanly.
        assert result["track"] == "Brands Hatch"
