"""Tests for the build-time embedded secret module (build.py + security.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build
from src.core import security

TEST_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


class TestGenerateSecretModuleSource:
    def test_roundtrip_reconstructs_secret(self):
        namespace = {}
        exec(build.generate_secret_module_source(TEST_SECRET), namespace)

        assert namespace["get_secret"]() == TEST_SECRET.encode("utf-8")

    def test_secret_never_appears_as_plaintext(self):
        source = build.generate_secret_module_source(TEST_SECRET)

        assert TEST_SECRET not in source

    def test_pads_are_random_per_generation(self):
        first = build.generate_secret_module_source(TEST_SECRET)
        second = build.generate_secret_module_source(TEST_SECRET)

        assert first != second


class TestGetBuildSecret:
    def test_reads_from_process_environment(self, monkeypatch):
        monkeypatch.setenv("APP_SECRET", TEST_SECRET)

        assert build.get_build_secret() == TEST_SECRET

    def test_reads_from_dotenv_when_env_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("APP_SECRET", raising=False)
        (tmp_path / ".env").write_text(f"APP_SECRET={TEST_SECRET}\n", encoding="utf-8")
        monkeypatch.setattr(build, "REPO_ROOT", tmp_path)

        assert build.get_build_secret() == TEST_SECRET

    def test_rejects_placeholder(self, monkeypatch):
        monkeypatch.setenv("APP_SECRET", "blahtopsecret")

        assert build.get_build_secret() is None

    def test_rejects_placeholder_from_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.delenv("APP_SECRET", raising=False)
        (tmp_path / ".env").write_text("APP_SECRET=blahtopsecret\n", encoding="utf-8")
        monkeypatch.setattr(build, "REPO_ROOT", tmp_path)

        assert build.get_build_secret() is None

    def test_missing_everywhere_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("APP_SECRET", raising=False)
        monkeypatch.setattr(build, "REPO_ROOT", tmp_path)

        assert build.get_build_secret() is None


class TestResolveSecret:
    def test_env_wins_over_embedded(self):
        assert security._resolve_secret("env-secret", "embedded-secret") == "env-secret"

    def test_placeholder_env_falls_through_to_embedded(self):
        assert security._resolve_secret("blahtopsecret", "embedded-secret") == "embedded-secret"

    def test_embedded_used_when_env_missing(self):
        assert security._resolve_secret(None, "embedded-secret") == "embedded-secret"

    def test_both_unusable_returns_none(self):
        assert security._resolve_secret("blahtopsecret", None) is None
        assert security._resolve_secret(None, None) is None
        assert security._resolve_secret("", "") is None


class TestLoadEmbeddedSecret:
    def test_returns_none_when_module_absent(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "_embedded_secret", raising=False)

        assert security._load_embedded_secret() is None

    def test_decodes_secret_from_native_module(self, monkeypatch):
        class FakeModule:
            @staticmethod
            def get_secret() -> bytes:
                return TEST_SECRET.encode("utf-8")

        monkeypatch.setitem(sys.modules, "_embedded_secret", FakeModule)

        assert security._load_embedded_secret() == TEST_SECRET

    def test_returns_none_on_module_error(self, monkeypatch):
        class BrokenModule:
            @staticmethod
            def get_secret() -> bytes:
                raise RuntimeError("corrupt")

        monkeypatch.setitem(sys.modules, "_embedded_secret", BrokenModule)

        assert security._load_embedded_secret() is None


class TestStageEmbeddedSecret:
    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Produces a Windows .pyd; native extension is .so on other platforms",
    )
    def test_compiles_native_module(self, tmp_path, monkeypatch):
        pytest.importorskip("Cython")
        monkeypatch.setenv("APP_SECRET", TEST_SECRET)
        stage_dir = tmp_path / "secret_stage"
        monkeypatch.setattr(build, "SECRET_STAGE_DIR", str(stage_dir))

        assert build.stage_embedded_secret() is True

        compiled = list(stage_dir.glob("_embedded_secret.*.pyd"))
        assert compiled, "expected a compiled .pyd in the staging dir"

        sys.path.insert(0, str(stage_dir))
        try:
            sys.modules.pop("_embedded_secret", None)
            import _embedded_secret

            assert _embedded_secret.get_secret() == TEST_SECRET.encode("utf-8")
        finally:
            sys.path.remove(str(stage_dir))
            sys.modules.pop("_embedded_secret", None)

    def test_fails_without_secret(self, tmp_path, monkeypatch):
        monkeypatch.delenv("APP_SECRET", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(build, "SECRET_STAGE_DIR", str(tmp_path / "secret_stage"))

        assert build.stage_embedded_secret() is False
