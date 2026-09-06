# Contributing to SimLaps Telemetry Client

Thanks for your interest in contributing! This document covers how to set up the project locally, run tests, and submit changes.

## How to contribute

This repository is public, but only maintainers have write access. To make changes:

1. **Fork the repository** on GitHub. This creates your own copy under your account.
2. **Clone your fork** locally:
   ```powershell
   git clone https://github.com/YOUR_USERNAME/simlaps.racing.client.git
   cd sim-laps-client
   ```
3. **Add the upstream remote** so you can pull updates:
   ```powershell
   git remote add upstream https://github.com/glebulon/simlaps.racing.client.git
   ```
4. **Create a feature branch** in your fork:
   ```powershell
   git checkout -b feature/my-change
   ```
5. **Make your changes**, commit them, and push to your fork:
   ```powershell
   git push origin feature/my-change
   ```
6. **Open a Pull Request** from `YOUR_USERNAME/simlaps.racing.client:feature/my-change` to `glebulon/simlaps.racing.client:main`.

A maintainer will review your PR. Please keep PRs focused on a single issue or feature, and include tests when possible.

## Development setup

### Requirements

- Windows 10/11 (Assetto Corsa Evo is Windows-only, so most contributors test on Windows)
- Python 3.10+
- `git`

### Install

It is recommended to use the existing project virtual environment, but you can also create your own:

```powershell
# Option A: use the bundled project venv
venv-sim-laps-client\Scripts\python.exe -m pip install -r requirements.txt
venv-sim-laps-client\Scripts\python.exe -m pip install -r requirements-dev.txt

# Option B: create your own venv
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Run the app locally

No `APP_SECRET` is required for local development or testing. If `APP_SECRET` is not set, the client runs in offline mode and will not attempt to submit laps to the server.

```powershell
venv-sim-laps-client\Scripts\python.exe -m src.main
```

If you want to test real submissions, copy `.env.example` to `.env` and set a real `APP_SECRET` provided by a project maintainer. **Never commit `.env` or any real secret.**

## Testing

All changes should keep the existing test suite green. Default local runs exclude
the slower wheel build/install check:

```powershell
venv-sim-laps-client\Scripts\python.exe -m pytest tests/ -q
```

Before opening a PR or preparing a release, also run the opt-in packaging check
and verify the environment dependencies:

```powershell
venv-sim-laps-client\Scripts\python.exe -m pytest tests/test_packaging.py -q -m packaging
venv-sim-laps-client\Scripts\python.exe -m pip check
```

The explicit `-m packaging` overrides the default marker selection. This check
builds and installs the wheel in an isolated temporary directory and verifies its
resources and console entry point.

For a coverage report:

```powershell
venv-sim-laps-client\Scripts\python.exe -m pytest tests/ --cov=src --cov-report=term-missing
```

The project uses `pytest` with `pytest-asyncio` for async tests. Tests cover the log parser, telemetry capture/decoder/analyzer, UI services, API client, and security module.

## Code style

- Follow **PEP 8**.
- Keep functions focused and modules cohesive.
- Add or update tests for any behavior changes.
- Use existing patterns in `src/` rather than introducing new conventions.

You can format the code with `black`:

```powershell
venv-sim-laps-client\Scripts\python.exe -m black src/ tests/
```

Type checking with `mypy` is available but not strictly enforced:

```powershell
venv-sim-laps-client\Scripts\python.exe -m mypy src/
```

## Things to avoid

- **Do not commit secrets.** `APP_SECRET`, Discord webhook URLs, and other sensitive values are handled through `.env` (which is ignored). GitHub will reject PRs containing secrets if secret scanning is enabled, and they can never be fully removed from public history.
- **Do not commit build artifacts.** `dist/`, `build/`, `obfuscated/`, `__pycache__/`, `*.pyc`, and telemetry dumps are all `.gitignore`d.
- **Do not commit generated config files.** `config.json` and `SERVER_SECRET.txt` are user-specific and ignored.
- **Do not modify `.env.example` to include real values.** It should remain a template.

## Building (maintainers / release only)

The packaged Windows executable is produced with:

```powershell
python build.py
```

This requires `pyinstaller` and `pyarmor` and an `APP_SECRET` in `.env`. Contributors do not normally need to run this.

## License

By contributing, you agree that your contributions will be licensed under the same license as the project. See the `LICENSE` file for details.

## Questions?

Open a GitHub issue or join the SimLaps Discord community for support.
