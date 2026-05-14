"""Regression tests for version source-of-truth consistency."""

from pathlib import Path
import re

from src import __version__
from src.version import get_version


def _read_version_wiring_from_pyproject(pyproject_path: Path) -> tuple[bool, str | None]:
    in_project_section = False
    in_setuptools_dynamic_section = False
    project_uses_dynamic_version = False
    setuptools_dynamic_attr = None

    for raw_line in pyproject_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("[") and line.endswith("]"):
            in_project_section = line == "[project]"
            in_setuptools_dynamic_section = line == "[tool.setuptools.dynamic]"
            continue

        if in_project_section:
            match = re.match(r'dynamic\s*=\s*\[(.*)\]', line)
            if match:
                dynamic_entries = [
                    entry.strip().strip('"').strip("'")
                    for entry in match.group(1).split(",")
                    if entry.strip()
                ]
                project_uses_dynamic_version = "version" in dynamic_entries

        if in_setuptools_dynamic_section:
            match = re.match(r'version\s*=\s*\{\s*attr\s*=\s*"([^"]+)"\s*\}', line)
            if match:
                setuptools_dynamic_attr = match.group(1)

    return project_uses_dynamic_version, setuptools_dynamic_attr


def test_pyproject_uses_dynamic_version_from_src_version_module() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject_path = repo_root / "pyproject.toml"

    project_uses_dynamic_version, setuptools_dynamic_attr = _read_version_wiring_from_pyproject(pyproject_path)

    assert project_uses_dynamic_version is True
    assert setuptools_dynamic_attr == "src.version.VERSION"


def test_runtime_package_version_alias_matches_single_source() -> None:
    assert __version__ == get_version()
