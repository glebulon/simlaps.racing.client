"""Regression tests for the repository-rooted build script."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "build.py"


@pytest.fixture
def build_module():
    spec = importlib.util.spec_from_file_location("simlaps_build", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_paths_are_rooted_at_build_script(build_module):
    assert build_module.REPO_ROOT == REPO_ROOT
    for path in (
        build_module.ENTRY_POINT,
        build_module.ICON_PATH,
        build_module.DIST_DIR,
        build_module.BUILD_DIR,
        build_module.OBFUSCATED_DIR,
        build_module.SECURITY_FILE,
    ):
        assert Path(path).is_absolute()
        assert Path(path).is_relative_to(REPO_ROOT)


def test_clean_from_foreign_cwd_does_not_touch_caller_artifacts(tmp_path):
    isolated_project = tmp_path / "project"
    (isolated_project / "src").mkdir(parents=True)
    (isolated_project / "build.py").write_bytes(BUILD_SCRIPT.read_bytes())
    (isolated_project / "src" / "version.py").write_bytes(
        (REPO_ROOT / "src" / "version.py").read_bytes()
    )
    caller_root = tmp_path / "caller"
    caller_root.mkdir()
    caller_build = caller_root / "build"
    caller_dist = caller_root / "dist"
    caller_build.mkdir()
    caller_dist.mkdir()
    build_sentinel = caller_build / "do-not-delete.txt"
    dist_sentinel = caller_dist / "do-not-delete.txt"
    build_sentinel.write_text("keep", encoding="utf-8")
    dist_sentinel.write_text("keep", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(isolated_project / "build.py"), "--clean"],
        cwd=caller_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert build_sentinel.exists()
    assert dist_sentinel.exists()


def test_build_command_uses_repository_cwd_and_absolute_paths(
    tmp_path, build_module, monkeypatch
):
    calls = []

    class FailedBuild:
        returncode = 1
        stderr = "expected test failure"
        stdout = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return FailedBuild()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build_module, "get_venv_executable", lambda _: "pyinstaller")
    monkeypatch.setattr(build_module.subprocess, "run", fake_run)
    stage_dir = tmp_path / "secret_stage"
    stage_dir.mkdir()
    (stage_dir / "_embedded_secret.cp314-win_amd64.pyd").write_bytes(b"test")
    monkeypatch.setattr(build_module, "SECRET_STAGE_DIR", stage_dir)

    assert build_module.build_executable() is False
    command, kwargs = calls[0]
    assert kwargs["cwd"] == build_module.REPO_ROOT
    assert str(build_module.ENTRY_POINT) in command
    assert str(build_module.DIST_DIR) in command
    assert str(build_module.BUILD_DIR) in command
    assert not any(".env" in str(argument) for argument in command)
    assert "_embedded_secret" in command


def test_clean_unlinks_redirected_artifact_without_deleting_target(
    tmp_path, build_module, monkeypatch
):
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    local_venv = fake_repo / "custom-local-env"
    local_venv_cache = local_venv / "Lib" / "__pycache__"
    local_venv_cache.mkdir(parents=True)
    (local_venv / "pyvenv.cfg").write_text("home = python", encoding="utf-8")
    venv_bytecode = local_venv_cache / "keep.pyc"
    venv_bytecode.write_bytes(b"keep")

    redirected_build = fake_repo / "build"
    try:
        redirected_build.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    monkeypatch.setattr(build_module, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(build_module, "BUILD_DIR", redirected_build)
    monkeypatch.setattr(build_module, "OBFUSCATED_DIR", fake_repo / "obfuscated")
    monkeypatch.setattr(build_module, "DIST_DIR", fake_repo / "dist")
    build_module.clean()

    assert not redirected_build.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert venv_bytecode.exists()
