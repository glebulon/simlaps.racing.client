"""Focused tests for src.models.tyre_state."""

from src.models.tyre_state import TyreState


def test_tyre_state_defaults_and_reset() -> None:
    state = TyreState()

    assert state.compound_name == "Unknown"
    assert state.compound_code == "Unknown"

    state.set_all("S")
    assert state.compound_name == "S"
    assert state.compound_code == "S"

    state.reset()
    assert state.compound_name == "Unknown"
    assert state.compound_code == "Unknown"


def test_tyre_state_mixed_compounds_rendering() -> None:
    state = TyreState()
    state.set(0, "S")
    state.set(1, "M")

    assert state.compound_code == "Mixed"
    assert state.compound_name in {"Mixed (M/S)", "Mixed (S/M)"}


def test_tyre_state_snapshot_is_independent_copy() -> None:
    state = TyreState()
    state.set_all("H")

    snap = state.snapshot()
    state.set(0, "S")

    assert snap.compound_name == "H"
    assert state.compound_name.startswith("Mixed")
