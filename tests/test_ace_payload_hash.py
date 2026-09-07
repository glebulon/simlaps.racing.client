"""Keep the standalone helper free of embedded submission examples."""

import ast
from pathlib import Path
import re
import runpy
import sys
from types import SimpleNamespace

import pytest


HELPER = Path(__file__).resolve().parents[1] / "telemetry" / "ace_payload_hash.py"


def test_helper_source_has_no_embedded_credential_or_personal_sample() -> None:
    source = HELPER.read_text(encoding="utf-8")
    assert re.search(r"eyJ[a-zA-Z0-9_-]+\.", source) is None
    tree = ast.parse(source)
    globals_assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "TOKEN" not in globals_assigned
    assert "payload" not in globals_assigned


def test_running_helper_requires_a_caller_supplied_payload_and_token(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("the standalone example must not submit anything")

    # The preserved optional requests dependency is unnecessary for this check.
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=fail_if_called))
    with pytest.raises(SystemExit, match="your own payload and token"):
        runpy.run_path(str(HELPER), run_name="__main__")
