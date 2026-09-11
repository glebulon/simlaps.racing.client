"""Runtime dotenv compatibility without bundling a plaintext credential."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv as real_load_dotenv

from src.core import security


def test_source_runtime_dotenv_uses_discovery_without_override(monkeypatch):
    calls = []
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(
        security,
        "load_dotenv",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    security._load_runtime_dotenv()

    assert calls == [((), {"override": False})]


def test_frozen_runtime_dotenv_uses_external_sidecar(tmp_path, monkeypatch):
    executable = tmp_path / "SimLapsClient.exe"
    executable.touch()
    env_path = tmp_path / ".env"
    env_path.write_text("APP_SECRET=file-value\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(
        security,
        "load_dotenv",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    security._load_runtime_dotenv()

    assert calls == [((str(env_path),), {"override": False})]


def test_frozen_runtime_dotenv_never_reads_pyinstaller_extraction_dir(tmp_path, monkeypatch):
    executable = tmp_path / "SimLapsClient.exe"
    executable.touch()
    meipass = tmp_path / "_MEIPASS"
    meipass.mkdir()
    (meipass / ".env").write_text("APP_SECRET=extraction-value\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(
        security,
        "load_dotenv",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    security._load_runtime_dotenv()

    assert calls == []


def test_process_environment_wins_over_frozen_sidecar(tmp_path, monkeypatch):
    executable = tmp_path / "SimLapsClient.exe"
    executable.touch()
    env_path = tmp_path / ".env"
    env_path.write_text("APP_SECRET=file-value\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv("APP_SECRET", "process-value")
    monkeypatch.setattr(security, "load_dotenv", real_load_dotenv)

    security._load_runtime_dotenv()

    assert security.os.environ["APP_SECRET"] == "process-value"  # noqa: S105


def test_isolated_sidecar_value_resolves_when_process_value_is_absent(tmp_path, monkeypatch):
    executable = tmp_path / "SimLapsClient.exe"
    executable.touch()
    (tmp_path / ".env").write_text("APP_SECRET=sidecar-value\n", encoding="utf-8")
    monkeypatch.delenv("APP_SECRET", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    security._load_runtime_dotenv()

    assert security._resolve_secret(security.os.environ.get("APP_SECRET"), "embedded") == ("sidecar-value")


def test_source_import_loads_only_isolated_dotenv_and_process_wins(tmp_path):
    project = tmp_path / "project"
    core = project / "src" / "core"
    utils = project / "src" / "utils"
    core.mkdir(parents=True)
    utils.mkdir(parents=True)
    for package_dir in (project / "src", core, utils):
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(Path(security.__file__), core / "security.py")
    logger_source = Path(security.__file__).parents[1] / "utils" / "structured_logger.py"
    shutil.copy2(logger_source, utils / "structured_logger.py")
    (project / ".env").write_text("APP_SECRET=isolated-file-value\n", encoding="utf-8")

    command = [
        sys.executable,
        "-c",
        "from src.core import security; print(security.APP_SECRET)",
    ]
    clean_env = os.environ.copy()
    clean_env.pop("APP_SECRET", None)
    sidecar = subprocess.run(  # noqa: S603
        command,
        cwd=project,
        env=clean_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert sidecar.stdout.strip() == "isolated-file-value"

    process_env = clean_env | {"APP_SECRET": "isolated-process-value"}
    process = subprocess.run(  # noqa: S603
        command,
        cwd=project,
        env=process_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert process.stdout.strip() == "isolated-process-value"
