#!/usr/bin/env python3
"""
Build Script for SimLaps Telemetry Client

Creates an obfuscated, packaged Windows executable with embedded secret.

Usage:
    python build.py              # Build with PyArmor obfuscation
    python build.py --no-obfuscate   # Build without obfuscation (faster, for testing)
    python build.py --clean      # Clean build artifacts
"""

import os
import sys
import shutil
import secrets
import subprocess
import argparse
from pathlib import Path

from dotenv import dotenv_values


REPO_ROOT = Path(__file__).resolve().parent


def get_venv_executable(name: str) -> str:
    """Get the path to an executable in the current venv."""
    # Check if we're in a venv
    venv_path = sys.prefix
    
    # Try Scripts (Windows) or bin (Unix)
    for scripts_dir in ["Scripts", "bin"]:
        exe_path = os.path.join(venv_path, scripts_dir, name)
        if os.path.exists(exe_path):
            return exe_path
        # Try with .exe extension on Windows
        exe_path_win = exe_path + ".exe"
        if os.path.exists(exe_path_win):
            return exe_path_win
    
    # Fallback to just the command name (rely on PATH)
    return name


# Import version from source
sys.path.insert(0, str(REPO_ROOT / "src"))
from version import VERSION

# Configuration
APP_NAME = "SimLapsClient"
APP_VERSION = VERSION
# Build paths are deliberately rooted at this file's repository.  The build
# script is commonly invoked from IDEs and release shells whose CWD is not the
# repository, and relative artifact paths in that case are unsafe.
ENTRY_POINT = REPO_ROOT / "src" / "main.py"
ICON_PATH = REPO_ROOT / "assets" / "icon.ico"
DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "build"
OBFUSCATED_DIR = REPO_ROOT / "obfuscated"
SECURITY_FILE = REPO_ROOT / "src" / "core" / "security.py"

# Build-time embedded secret: generated Cython module compiled to a native
# extension so release artifacts carry no plaintext credential.
SECRET_STAGE_DIR = BUILD_DIR / "secret_stage"
EMBEDDED_SECRET_MODULE = "_embedded_secret"
PLACEHOLDER_SECRETS = frozenset({"blahtopsecret"})


def _validate_cleanup_target(path: Path, allowed_root: Path) -> Path:
    """Validate a cleanup target before touching it.

    A symlink is safe to unlink, but never safe to recurse into.  For regular
    paths, both the lexical path and its resolved destination must remain
    below the intended repository tree.
    """
    candidate = Path(path)
    root = Path(allowed_root).resolve()
    lexical = Path(os.path.abspath(os.fspath(candidate)))
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing to clean path outside {root}: {candidate}"
        ) from exc

    if candidate.is_symlink():
        return candidate

    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing to clean redirected path outside {root}: {candidate}"
        ) from exc
    return candidate


def _remove_path(path: Path, allowed_root: Path) -> None:
    """Remove one validated file, symlink, or directory without following links."""
    target = _validate_cleanup_target(path, allowed_root)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def _is_virtual_environment(path: Path) -> bool:
    """Return whether *path* looks like a Python virtual environment."""
    return (
        (path / "pyvenv.cfg").is_file()
        or (path / "Scripts" / "python.exe").is_file()
        or (path / "bin" / "python").is_file()
    )


def _clean_cached_files() -> None:
    """Remove project bytecode without traversing environments or artifacts."""
    skipped_names = {
        ".git",
        ".pyarmor",
        ".venv",
        "build",
        "dist",
        "env",
        "env.bak",
        "obfuscated",
        "venv",
        "venv-sim-laps-client",
        "venv.bak",
    }
    for current_root, dir_names, file_names in os.walk(
        REPO_ROOT, topdown=True, followlinks=False
    ):
        current = Path(current_root)
        retained_dirs = []
        for name in dir_names:
            child = current / name
            relative_child = child.relative_to(REPO_ROOT)
            if (
                name in skipped_names
                or relative_child == Path("tests") / "output"
            ):
                continue
            if child.is_symlink() or _is_virtual_environment(child):
                continue
            retained_dirs.append(name)
        dir_names[:] = retained_dirs

        for name in file_names:
            if not name.endswith(".pyc"):
                continue
            target = current / name
            if target.is_symlink():
                continue
            _remove_path(target, REPO_ROOT)

        for name in list(dir_names):
            if name != "__pycache__":
                continue
            target = current / name
            _remove_path(target, REPO_ROOT)
            dir_names.remove(name)


def clean():
    """Remove build artifacts."""
    print("Cleaning build artifacts...")

    # These are the only top-level directories the build owns.  Validate each
    # before removing it so a redirected symlink or modified constant cannot
    # turn cleanup into deletion outside the repository.
    for artifact_dir in (BUILD_DIR, OBFUSCATED_DIR, REPO_ROOT / ".pyarmor"):
        if artifact_dir.exists() or artifact_dir.is_symlink():
            _remove_path(artifact_dir, REPO_ROOT)
            print(f"  Removing {artifact_dir}/")

    # Clean dist/ and remove credential artifacts left by older builds.
    if DIST_DIR.exists() or DIST_DIR.is_symlink():
        if DIST_DIR.is_symlink():
            _remove_path(DIST_DIR, REPO_ROOT)
        else:
            _validate_cleanup_target(DIST_DIR, REPO_ROOT)
            for item in DIST_DIR.iterdir():
                is_directory = item.is_dir()
                _remove_path(item, DIST_DIR)
                print(f"  Removing {item}{'/' if is_directory else ''}")

    _clean_cached_files()
    
    print("Clean complete!")


def check_dependencies():
    """Check if required build tools are installed."""
    print("Checking build dependencies...")
    
    # Map package names to their import names
    required = {
        "pyinstaller": "PyInstaller",
        "pyarmor": "pyarmor",
        "cython": "Cython",
    }
    missing = []
    
    for package, import_name in required.items():
        try:
            if package == "pyarmor":
                # PyArmor 9.x installs versioned CLI launchers (pyarmor-7.exe,
                # pyarmor-8.exe) that may not work if the venv path changed.
                # Invoke via python -m for reliability.
                result = subprocess.run(
                    [sys.executable, "-m", "pyarmor.cli", "--version"],
                    capture_output=True, text=True, cwd=REPO_ROOT,
                )
                if result.returncode != 0:
                    missing.append(package)
            else:
                __import__(import_name)
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        print("Install with: pip install " + " ".join(missing))
        return False
    
    print("  All dependencies found!")
    return True


def get_build_secret() -> str | None:
    """Resolve APP_SECRET from the build environment or repository .env.

    The file is read only while staging the compiled extension and is never
    passed to PyInstaller.  Process environment values take precedence over
    the local file, matching runtime dotenv behavior.
    """
    secret = os.environ.get("APP_SECRET")
    if not secret:
        env_path = REPO_ROOT / ".env"
        if env_path.is_file():
            secret = dotenv_values(env_path).get("APP_SECRET")
    if not secret or secret.strip() in PLACEHOLDER_SECRETS:
        return None
    return secret.strip()


def generate_secret_module_source(secret: str) -> str:
    """Generate Cython source that reconstructs *secret* from XOR pads."""
    data = secret.encode("utf-8")
    pad_a = secrets.token_bytes(len(data))
    pad_b = bytes(a ^ b for a, b in zip(pad_a, data))

    def fmt(raw: bytes) -> str:
        return "".join(f"\\x{byte:02x}" for byte in raw)

    return (
        "# Auto-generated at build time by build.py. Never commit this file.\n"
        f'_PA = b"{fmt(pad_a)}"\n'
        f'_PB = b"{fmt(pad_b)}"\n'
        "\n"
        "def get_secret() -> bytes:\n"
        "    return bytes(a ^ b for a, b in zip(_PA, _PB))\n"
    )


def stage_embedded_secret() -> bool:
    """Compile the build credential into a native extension under build/."""
    print("Embedding APP_SECRET as compiled native module...")
    secret = get_build_secret()
    if not secret:
        print("\nERROR: no usable APP_SECRET found in the process environment or repository .env!")
        print("Set APP_SECRET before building (see .env.example).")
        return False

    stage_dir = Path(SECRET_STAGE_DIR)
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / f"{EMBEDDED_SECRET_MODULE}.pyx").write_text(
        generate_secret_module_source(secret), encoding="utf-8"
    )
    (stage_dir / "setup.py").write_text(
        "from setuptools import Extension, setup\n"
        "from Cython.Build import cythonize\n"
        "setup(ext_modules=cythonize(\n"
        f'    [Extension("{EMBEDDED_SECRET_MODULE}", ["{EMBEDDED_SECRET_MODULE}.pyx"])],\n'
        "    language_level=3,\n"
        "))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "setup.py", "build_ext", "--inplace"],
        cwd=stage_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Cython compile failed: {result.stderr}")
        print("  Ensure Cython is installed and MSVC Build Tools are present.")
        return False
    compiled = list(stage_dir.glob(f"{EMBEDDED_SECRET_MODULE}.*.pyd"))
    if not compiled:
        print("  Cython compile produced no .pyd output")
        return False
    print(f"  Embedded secret module: {compiled[0]}")
    return True


def obfuscate_source():
    """Obfuscate source code with PyArmor."""
    print("Obfuscating source code with PyArmor...")

    # Only obfuscate sensitive files to stay within trial limits
    files_to_obfuscate = [
        "src/core/security.py",
        "src/core/api_client.py",
    ]

    # PyArmor obfuscation command (using free features only)
    # Invoke via python -m pyarmor.cli for venv-path resilience
    cmd = [
        sys.executable, "-m", "pyarmor.cli",
        "gen",
        "--output", OBFUSCATED_DIR,
        "--obf-code", "1",  # Obfuscate each function code object (free tier)
        "--obf-module", "1",  # Obfuscate whole module code (free tier)
        *(REPO_ROOT / path for path in files_to_obfuscate),
    ]

    print(f"  Running: pyarmor gen --output {OBFUSCATED_DIR} ...")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)

    if result.returncode != 0:
        print(f"  PyArmor error: {result.stderr}")
        print(f"  stdout: {result.stdout}")

        # Check if it's a license issue
        if "out of license" in result.stderr or "trial" in result.stdout.lower():
            print("  WARNING: PyArmor trial limitation detected")
            print("  Falling back to basic obfuscation...")

            # Try with minimal arguments
            simple_cmd = [
                sys.executable, "-m", "pyarmor.cli",
                "gen",
                "--output", OBFUSCATED_DIR,
                *(REPO_ROOT / path for path in files_to_obfuscate),
            ]

            print(f"  Running: pyarmor gen --output {OBFUSCATED_DIR} ...")
            result = subprocess.run(
                simple_cmd, capture_output=True, text=True, cwd=REPO_ROOT
            )

            if result.returncode != 0:
                print(f"  Simple PyArmor also failed: {result.stderr}")
                return False

    # Verify obfuscated output
    if OBFUSCATED_DIR.exists():
        print(f"  Obfuscation complete: {OBFUSCATED_DIR}/")
        return True
    else:
        print("  Obfuscation failed: output directory not found")
        return False


def build_executable():
    """Build the executable with PyInstaller."""
    print("Building executable with PyInstaller...")
    
    pyinstaller_exe = get_venv_executable("pyinstaller")
    
    # Use obfuscated source if available
    src_dir = REPO_ROOT
    entry = ENTRY_POINT
    
    print(f"  Using source: {src_dir}")
    
    # PyInstaller arguments
    cmd = [
        pyinstaller_exe,
        "--onefile",
        "--windowed",  # No console window (GUI app)
        "--name", APP_NAME,
        "--clean",
        "--noconfirm",
    ]
    
    # Add icon if exists
    if os.path.exists(ICON_PATH):
        cmd.extend(["--icon", str(ICON_PATH)])
        # Include icon.ico as data file for window icon at runtime
        cmd.extend(["--add-data", f"{ICON_PATH};assets"])
    
    # Also include icon.png for ft.Image in the UI
    icon_png_path = REPO_ROOT / "assets" / "icon.png"
    if icon_png_path.exists():
        cmd.extend(["--add-data", f"{icon_png_path};assets"])

    # Saved telemetry reports are self-contained and load these pinned chart
    # libraries from the frozen application's extraction directory.
    analyzer_vendor_path = REPO_ROOT / "src" / "core" / "analyzer" / "vendor"
    if analyzer_vendor_path.is_dir():
        cmd.extend([
            "--add-data",
            f"{analyzer_vendor_path};src/core/analyzer/vendor",
        ])
    
    # Never bundle .env as data. The credential is supplied by the compiled
    # native extension staged by main().
    stage_dir = Path(SECRET_STAGE_DIR)
    if list(stage_dir.glob(f"{EMBEDDED_SECRET_MODULE}.*.pyd")):
        cmd.extend(["--paths", str(stage_dir)])
        hidden_imports_extra = [EMBEDDED_SECRET_MODULE]
    else:
        print("  WARNING: no embedded secret module - release will run in offline mode")
        hidden_imports_extra = []
    
    # Add hidden imports for Flet and psutil
    hidden_imports = [
        "flet",
        "flet_core",
        "flet_runtime",
        "flet_desktop",
        "httpx",
        "httpcore",
        "anyio",
        "sniffio",
        "h11",
        "certifi",
        "idna",
        "psutil",
        "src",
        "src.version",
        "src.core",
        "src.core.track_catalog",
        "src.core.track_catalog:select_track_profile",
        "src.core.track_catalog:build_track_profile",
        "src.core.telemetry_capture",
        "src.core.telemetry_capture:CaptureMetadata",
        "src.core.telemetry_capture:FrameData",
        "src.core.telemetry_decoder",
        "src.ui",
        "src.ui.app",
        "src.ui.pages",
        "src.ui.pages.settings",
        "src.ui.pages.telemetry",
        "src.ui.components",
        "src.utils",
    ]
    
    for imp in [*hidden_imports, *hidden_imports_extra]:
        cmd.extend(["--hidden-import", imp])
    
    # Add data files for Flet
    # Flet requires its runtime files and desktop app to be included
    cmd.extend([
        "--collect-all", "flet",
        "--collect-all", "flet_core",
        "--collect-all", "flet_runtime",
        "--collect-all", "flet_desktop",
        "--collect-binaries", "flet",
        "--collect-binaries", "flet_runtime",
        "--collect-binaries", "flet_desktop",
        "--collect-data", "flet",
        "--collect-data", "flet_runtime",
        "--collect-data", "flet_desktop",
        "--collect-all", "src",
        "--collect-all", "src.core",
        "--collect-all", "src.ui",
        "--collect-all", "src.utils",
    ])
    
    # Add obfuscated src directory first so it shadows the plain sources.
    if OBFUSCATED_DIR.exists():
        cmd.extend(["--paths", str(OBFUSCATED_DIR)])

    # Add the source and artifact paths so imports and outputs remain rooted
    # at the repository even when called from another CWD.
    cmd.extend([
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(REPO_ROOT),
        "--paths", str(src_dir),
    ])
    
    # Add entry point
    cmd.append(str(entry))
    
    print(f"  Running: {' '.join(cmd[:10])}...")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    
    if result.returncode != 0:
        print(f"  PyInstaller error: {result.stderr}")
        print(f"  stdout: {result.stdout}")
        return False
    
    # Verify output
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if exe_path.exists():
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"  Build complete: {exe_path} ({size_mb:.1f} MB)")
        return True
    else:
        print("  Build failed: executable not found")
        return False


def create_spec_file():
    """Create a PyInstaller spec file for more control."""
    entry_point = repr(str(ENTRY_POINT))
    icon_path = repr(str(ICON_PATH)) if ICON_PATH.exists() else "None"
    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    [{entry_point}],
    pathex=[{str(REPO_ROOT)!r}],
    binaries=[],
    datas=[],
    hiddenimports=[
        'flet',
        'flet_core', 
        'flet_runtime',
        'httpx',
        'httpcore',
        'anyio',
        'sniffio',
        'h11',
        'certifi',
        'idna',
        'psutil',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='{APP_NAME}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={icon_path},
)
'''
    
    spec_path = REPO_ROOT / f"{APP_NAME}.spec"
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_content)
    
    print(f"Created {spec_path}")
    return spec_path


def main():
    """Main build process."""
    parser = argparse.ArgumentParser(description="Build SimLaps Client")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts")
    parser.add_argument("--spec", action="store_true", help="Create spec file only")
    parser.add_argument("--no-obfuscate", action="store_true", help="Build without PyArmor obfuscation (faster, for testing)")
    
    args = parser.parse_args()
    
    print(f"SimLaps Client Build Script v{APP_VERSION}")
    print("=" * 50)
    
    if args.clean:
        clean()
        return 0
    
    if args.spec:
        create_spec_file()
        return 0
    
    # Check dependencies
    if not check_dependencies():
        return 1
    
    # Clean previous build and stage the authorized credential as a compiled
    # extension. The source .env is never copied into the release artifact.
    clean()
    if not stage_embedded_secret():
        return 1
    
    # Obfuscate source unless disabled
    if not args.no_obfuscate:
        if not obfuscate_source():
            print("\nOBFUSCATION FAILED!")
            return 1
    else:
        print("Skipping obfuscation (building from source)")

    # Build executable
    if not build_executable():
        print("\nBuild FAILED!")
        return 1
    
    print("\n" + "=" * 50)
    print("BUILD SUCCESSFUL!")
    print("=" * 50)
    print(f"\nExecutable: {DIST_DIR}/{APP_NAME}.exe")
    if not args.no_obfuscate:
        print("Source code obfuscated with PyArmor")
    print("APP_SECRET embedded as compiled native module (no plaintext .env in artifact)")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
