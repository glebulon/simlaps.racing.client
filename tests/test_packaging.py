"""Smoke-test the files and imports shipped by the built wheel."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _copy_tracked_tree(destination: Path) -> None:
    """Copy the current tracked-file tree to an isolated build directory."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative_path = Path(os.fsdecode(raw_path))
        source = PROJECT_ROOT / relative_path
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


@pytest.mark.packaging
def test_wheel_contains_catalogs_and_importable_console_entrypoint(tmp_path: Path):
    """Build/install a wheel and exercise resources without the source tree."""
    source_tree = tmp_path / "source"
    _copy_tracked_tree(source_tree)

    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            ".",
        ],
        cwd=source_tree,
        check=True,
        timeout=180,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel_path = wheels[0]

    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
    assert "src/core/data/track_catalog.json" in names
    assert "src/core/data/car_tuning_catalog.json" in names
    assert "src/core/analyzer/vendor/chart.umd.min.js" in names
    assert "src/core/analyzer/vendor/chart.js.LICENSE.md" in names
    assert "src/core/analyzer/vendor/chartjs-plugin-annotation.min.js" in names
    assert "src/core/analyzer/vendor/chartjs-plugin-annotation.LICENSE.md" in names

    install_target = tmp_path / "installed"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(install_target),
            str(wheel_path),
        ],
        check=True,
        timeout=180,
    )

    probe = r"""
import importlib
import importlib.metadata
from importlib import resources

track_catalog = importlib.import_module("src.core.track_catalog")
car_catalog = importlib.import_module("src.core.car_tuning_catalog")
assert track_catalog.TRACK_CATALOG
assert car_catalog.get_tuning_params("Porsche 992 GT3 RS")
assert resources.files("src.core").joinpath("data", "track_catalog.json").is_file()
assert resources.files("src.core").joinpath("data", "car_tuning_catalog.json").is_file()

entry_point = next(
    entry
    for entry in importlib.metadata.entry_points(group="console_scripts")
    if entry.name == "simlaps-client"
)
assert entry_point.value == "src.main:main"
assert callable(entry_point.load())
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=install_target,
        env=env,
        check=True,
        timeout=30,
    )
