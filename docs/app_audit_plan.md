# SimLaps Client — Full Application Audit Plan

This plan is implementation-ready for an LLM. Each item includes:
- **What is wrong**
- **Where to change** (with concrete file refs)
- **How to change it**
- **How to validate**

## Priority Legend
- **P0** = must-fix correctness/runtime/security bug
- **P1** = user-visible behavior/maintainability issue
- **P2** = code quality/efficiency improvement
- **P3** = longer-term structural cleanup

---

## P0 — Correctness / Runtime Bugs

### 1) Fix missing telemetry startup method call in settings flow
**Problem**
- Saving settings can call a method that does not exist, causing runtime failure when telemetry is enabled from Settings.

**Evidence**
- `self._start_telemetry_on_startup()` is called in `SimLapsApp._save_settings` at @src/ui/app.py#949-955.
- No method with that name exists in `SimLapsApp` (only `_start_telemetry_capture`) at @src/ui/app.py#667-704.

**Implementation steps**
1. In `SimLapsApp._save_settings`, replace `_start_telemetry_on_startup()` with `self.page.run_task(self._start_telemetry_capture)` (or directly `await` only if method is made async end-to-end).
2. Ensure this path is only triggered when telemetry is enabled and capture service exists.
3. Add a regression test that toggles telemetry from disabled→enabled and asserts no exception.

**Validation**
- Manual: open Settings, enable telemetry, save, verify app stays responsive and telemetry status transitions correctly.
- Automated: new test in `tests/test_main_entrypoint.py` or dedicated UI-controller test.

---

### 2) Fix undefined `_ENCODED_SECRET` reference in security status
**Problem**
- `get_security_status()` references `_ENCODED_SECRET`, which is not defined in module scope.

**Evidence**
- Undefined symbol usage at @src/core/security.py#346-351.

**Implementation steps**
1. Replace `'secret_configured': not _ENCODED_SECRET.startswith("PLACEHOLDER")` with a valid check using existing secret source, e.g. `bool(APP_SECRET)`.
2. If placeholder detection is required, explicitly introduce a safe placeholder constant and initialize it consistently.
3. Add unit test to call `get_security_status()` and assert no `NameError`.

**Validation**
- Run security tests + a direct unit test for `get_security_status`.

---

### 3) Fix stale/invalid Discord config key mapping
**Problem**
- `ConfigManager.set_discord_config()` writes `discord_post_invalid`, but `AppConfig` has no such field.

**Evidence**
- `AppConfig` fields listed at @src/utils/config.py#37-83 (no `discord_post_invalid`).
- Invalid assignment key at @src/utils/config.py#258-285.

**Implementation steps**
1. Decide intended behavior:
   - If this should control app-level invalid-lap posting, map it to existing `submit_invalid_laps`.
   - Otherwise add `discord_post_invalid` to `AppConfig` and wire usage in Discord posting logic.
2. Update `set_discord_config` accordingly.
3. Add tests for `set_discord_config(post_invalid=...)` to verify persisted config and effective behavior.

**Validation**
- Config round-trip test passes and invalid-lap Discord behavior matches chosen design.

---

### 4) Resolve version source-of-truth drift
**Problem**
- Different version values are defined in multiple places.

**Evidence**
- `pyproject.toml` version is `1.2.0` at @pyproject.toml#6-8.
- Runtime version constants are `1.3.0` in @src/version.py#14-20.

**Implementation steps**
1. Choose authoritative source (`src/version.py` or `pyproject.toml`).
2. Add a small build-time sync script OR import from one source into the other generation step.
3. Add CI/test assertion that both versions are equal.

**Validation**
- Single version value shown in package metadata and runtime UI/API `User-Agent`.

---

## P1 — User-Visible Behavior / Dead Code

### 5) Implement or remove Retry flow for failed lap cards
**Problem**
- UI exposes a **Retry** button for failed laps, but callback is a no-op.

**Evidence**
- Retry button is rendered in lap card at @src/ui/components/lap_card.py#178-182.
- Handler is empty (`pass`) at @src/ui/pages/home.py#506-509.

**Implementation steps**
1. Add `on_retry_lap` callback to `HomePage` constructor and store it.
2. In `_on_retry_lap`, call parent callback with the lap/session payload.
3. In `SimLapsApp`, wire callback to re-run `_submit_lap` for the matching lap card/history entry.
4. Ensure retry is disabled/hidden for invalid-lap states when `submit_invalid_laps=False`.

**Validation**
- Manual: force submission failure, click Retry, verify status transitions `FAILED -> SUBMITTING -> SUBMITTED/FAILED`.
- Automated: unit test around retry callback dispatch.

---

### 6) Make update banner actionable or remove dead handler
**Problem**
- Update banner appears, but open-update helper is unused and no button is wired.

**Evidence**
- Banner creation at @src/ui/pages/home.py#258-273.
- Update check toggles visibility at @src/ui/pages/home.py#365-377.
- Dead helper `_open_update_url` at @src/ui/pages/home.py#384-387 (not wired).

**Implementation steps**
1. Add a clear CTA (`TextButton`/`IconButton`) on banner and wire `on_click=self._open_update_url`.
2. Or remove `_open_update_url` if intentionally using only selectable text.
3. Add a test for banner action callback wiring.

**Validation**
- Clicking update CTA opens expected download URL.

---

### 7) Replace bare `except:` in History page timestamp parsing
**Problem**
- Bare except masks non-date errors.

**Evidence**
- Bare exception at @src/ui/pages/history.py#146-152.

**Implementation steps**
1. Replace `except:` with explicit exception types (`ValueError`, `TypeError`).
2. Log/debug unexpected exceptions separately when not date-format related.

**Validation**
- History still renders with malformed timestamps; unexpected exceptions surface in logs.

---

### 8) Remove or actually use `_telemetry_session_incomplete`
**Problem**
- Flag is set/reset but does not drive behavior.

**Evidence**
- Defined at @src/ui/app.py#84.
- Set at @src/ui/app.py#368-373.
- Reset at @src/ui/app.py#699-701.
- No decision logic consumes it.

**Implementation steps**
1. Either remove the flag entirely, or use it to annotate analysis notes/UI warning (e.g., “capture started late, lap 1 incomplete”).
2. If retained, expose this state to telemetry analyzer metadata.

**Validation**
- No dead-state variable remains; behavior is explicit.

---

### 9) Stop calling private telemetry capture method from app layer
**Problem**
- App calls `_make_output_prefix()` (private method), creating brittle coupling.

**Evidence**
- Private method call at @src/ui/app.py#685-688.

**Implementation steps**
1. Add/consume a public method on `TelemetryCapture` (e.g., `prepare_new_session_prefix()` or return prefix from `start_capture`).
2. Replace direct private call in `SimLapsApp`.

**Validation**
- App compiles/runs without private method access; telemetry prefix still generated correctly.

---

## P2 — Logging, Error Handling, Efficiency

### 10) Replace high-volume `print()` debugging with structured logger in app controller
**Problem**
- `SimLapsApp` has heavy direct console printing despite existing structured logger utilities.

**Evidence**
- Extensive print usage throughout @src/ui/app.py#87-1094.
- Logger utilities are already imported at @src/ui/app.py#31.

**Implementation steps**
1. Replace repetitive `print` with `log_debug/log_info/log_warning/log_error/log_exception`.
2. Keep user-facing snackbars for UX, but keep console output minimal.
3. Add component and context fields to logs (lap number, track, reason, prefix).

**Validation**
- Console noise reduced significantly; debug logs still fully traceable.

---

### 11) Reduce analyzer console noise and move diagnostics behind log levels
**Problem**
- Analyzer prints many per-run messages, creating noisy output and performance overhead in longer sessions.

**Evidence**
- Multiple prints in @src/core/telemetry_analyzer.py#417-423, @src/core/telemetry_analyzer.py#706-717, @src/core/telemetry_analyzer.py#1493-1505, @src/core/telemetry_analyzer.py#1823-1839, @src/core/telemetry_analyzer.py#1949-1950, @src/core/telemetry_analyzer.py#3235-3236.

**Implementation steps**
1. Convert debug prints to structured logger calls.
2. Keep only high-level summary logs at INFO; move frame-level diagnostics to DEBUG.
3. Add a configurable verbosity flag if needed.

**Validation**
- Analyzer output remains informative but concise under default settings.

---

### 12) Tighten exception handling and avoid silent swallow patterns
**Problem**
- Several broad exception handlers swallow errors (`pass`) without context.

**Evidence**
- Examples in @src/utils/debug_logger.py#58-71 and @src/ui/components/debug_logs.py#228-269.

**Implementation steps**
1. Replace silent `pass` with scoped exceptions and optional debug logging.
2. For expected failures (e.g., stream closed), document intent in comments.
3. For unexpected failures, add low-noise structured logs.

**Validation**
- Non-critical failures remain non-fatal; debugging visibility improves.

---

### 13) Remove fail-open anti-cheat behavior on process-detection errors
**Problem**
- `is_game_running()` returns `True` when `psutil` is unavailable or throws unexpected errors.

**Evidence**
- Fail-open return path at @src/core/security.py#84-87 and @src/core/security.py#97-99.

**Implementation steps**
1. Change fallback behavior to configurable policy (default fail-closed for submission path).
2. Differentiate “unknown” vs “confirmed running” states.
3. Ensure submission gating handles “unknown” conservatively (or prompts user with clear message).
4. Add tests covering psutil missing/error scenarios.

**Validation**
- Submission is not allowed on unverifiable process state unless explicitly configured.

---

### 14) Consolidate telemetry logging path (`_log`) to avoid duplicate console I/O
**Problem**
- Telemetry capture logs to console and file repeatedly; high-frequency events can produce noisy/expensive output.

**Evidence**
- `_log` always prints at @src/core/telemetry_capture.py#113-121.

**Implementation steps**
1. Route `_log` through structured logger with log-level controls.
2. Keep diagnostic file writes behind `telemetry_debug_logs` guard.
3. Throttle repetitive messages (e.g., reconnect attempts, no-region warnings).

**Validation**
- Capture loop remains responsive with less console I/O overhead.

---

## P3 — Maintainability / Architecture

### 15) Decompose `SimLapsApp` monolith into smaller controllers/services
**Problem**
- `SimLapsApp` mixes UI navigation, telemetry orchestration, submission, Discord posting, config apply, and parser lifecycle in one large class.

**Evidence**
- `SimLapsApp` spans most of @src/ui/app.py#42-1100.
- Large multi-responsibility methods: lap handling @src/ui/app.py#346-462, submission @src/ui/app.py#463-527, discord @src/ui/app.py#529-610.

**Implementation steps**
1. Extract `LapSubmissionService` (submit + status mapping).
2. Extract `TelemetrySessionController` (start/stop/analyze/session events).
3. Keep `SimLapsApp` as wiring + page routing only.
4. Inject dependencies explicitly for testability.

**Validation**
- Class/file size reduced; targeted unit tests can cover each service independently.

---

### 16) Remove legacy auth fields/API that are no longer used
**Problem**
- Config still contains old API-key auth fields despite app flow being “no login required”.

**Evidence**
- Legacy auth fields/methods in @src/utils/config.py#41-45, @src/utils/config.py#96-99, @src/utils/config.py#214-240.

**Implementation steps**
1. Remove `steam_id`, `steam_name`, `api_key`, `is_authenticated`, `set_auth`, `clear_auth` if truly obsolete.
2. Add migration logic in `from_dict`/load to ignore these keys safely from old config files.
3. Update tests and docs to reflect authless architecture.

**Validation**
- Existing user configs still load; no runtime references to removed fields remain.

---

### 17) Externalize large static track catalog from Python module to data file(s)
**Problem**
- Massive in-code dict reduces readability and raises merge-conflict risk.

**Evidence**
- Large hardcoded `TRACK_CATALOG` in @src/core/track_catalog.py#6-220 (continues extensively).

**Implementation steps**
1. Move catalog to JSON/YAML (`src/core/data/track_catalog.json`).
2. Keep `track_catalog.py` focused on lookup/normalization logic.
3. Add schema validation for catalog entries on load.

**Validation**
- Catalog updates require data changes only; lookup behavior remains identical.

---

### 18) Clarify debug logging strategy (dead/duplicated mechanisms)
**Problem**
- `DebugLogger` is globally instantiated but effectively disabled with `ENABLE_DEBUG=False`; app also has separate debug log capture path.

**Evidence**
- Global instance and disabled flag in @src/utils/debug_logger.py#38-40 and @src/utils/debug_logger.py#77-79.
- Separate stream capture in @src/ui/components/debug_logs.py#256-283.

**Implementation steps**
1. Pick one logging pipeline (structured logger + optional file sink recommended).
2. Remove or fully integrate `DebugLogger`.
3. Ensure UI logs viewer reads from unified source.

**Validation**
- Single coherent logging path; no dormant logger code.

---

## Cross-Cutting Documentation / Test Follow-ups

### 19) Update docs to match current telemetry capabilities and startup expectations
**Problem**
- README says telemetry is physics-only while code now handles graphics/static decode paths.

**Evidence**
- Outdated telemetry section at @README.md#235-237.

**Implementation steps**
1. Update README telemetry architecture section to reflect current capture/decode pipeline.
2. Document recommended test command using project venv Python.
3. Ensure project version docs reference single source of truth.

**Validation**
- New developer can run and understand current architecture from docs alone.

---

### 20) Add regression tests for all P0 issues
**Scope**
- Missing telemetry startup method call.
- Undefined security status symbol.
- Invalid discord config key mapping.
- Version sync assertion.

**Implementation steps**
1. Add targeted tests in:
   - `tests/test_security.py`
   - `tests/test_main_entrypoint.py` or app-controller tests
   - `tests/test_helpers_utils.py` or new `tests/test_config_manager.py`
2. Add one consistency test that compares `src/version.py` vs `pyproject.toml`.

**Validation**
- CI fails immediately if these regressions reappear.

---

## Additional Dead-Code Candidates (Dual Index Scan: AST + text references)

### 21) Remove or wire unused in-memory log-viewer capture toggles
**Problem**
- `SimpleLogCapture.enable_capture()` / `disable_capture()` exist but are never called from runtime code.
- This is separate from the Settings toggle **"Save Debug Logs"**, which is actively used for telemetry artifact persistence.

**Evidence**
- Method definitions at @src/ui/components/debug_logs.py#23-29.
- No in-repo runtime call sites beyond definitions (text-reference scan over `src/`).
- Settings "Save Debug Logs" wiring is active: @src/ui/pages/settings.py#99-103, @src/ui/pages/settings.py#213-217, @src/ui/pages/settings.py#380-383, @src/ui/app.py#209-213, and gating in @src/core/telemetry_capture.py#243-254 and @src/core/telemetry_capture.py#559-570.

**Implementation steps**
1. Decide whether log capture should be runtime-toggleable.
2. If yes, add explicit UI controls that call these methods.
3. If no, remove the toggle methods and keep capture behavior explicit and static.

**Validation**
- Debug log viewer still captures logs correctly.
- No dead methods remain in `SimpleLogCapture`.

---

### 22) Collapse unused status-bar convenience wrappers
**Problem**
- `StatusBar` has wrapper methods that are not used (`set_connected`, `set_disconnected`, `set_connecting`, `set_error`).

**Evidence**
- Wrapper methods at @src/ui/components/status_bar.py#120-134.
- Current call path uses `set_status(...)` via @src/ui/pages/home.py#444-446.

**Implementation steps**
1. Remove unused wrapper methods, or migrate call sites to use them consistently.
2. Keep one canonical status update API to reduce surface area.

**Validation**
- Home page status changes still render correctly for all connection states.
- No references remain to removed wrapper methods.

---

### 23) Prune stale Steam utility helpers if legacy auth is removed
**Problem**
- Helpers for Steam profile URL / Steam ID validation appear unused in runtime paths.

**Evidence**
- Helper definitions at @src/utils/helpers.py#151-187.
- No in-repo runtime call sites beyond definitions (text-reference scan over `src/`).

**Implementation steps**
1. If legacy Steam-auth flow is fully removed (see item 16), remove these helpers.
2. If future use is intended, add explicit runtime usage and tests.

**Validation**
- No dead helper utilities remain, or usage is explicit and test-covered.

---

### 24) Trim unused config convenience wrappers (if not part of public API contract)
**Problem**
- Module-level and manager-level convenience wrappers appear unused in runtime code.

**Evidence**
- Module wrappers: @src/utils/config.py#305-312 (`get_config`, `save_config`).
- Manager convenience methods: @src/utils/config.py#242-252 and @src/utils/config.py#287-290 (`get_log_path`, `set_log_path`, `get_server_url`, `is_discord_configured`).

**Implementation steps**
1. Confirm no external dependency relies on these helpers.
2. Remove unused wrappers or standardize call sites to use them (choose one pattern).
3. Keep `ConfigManager` API minimal and intentional.

**Validation**
- Config flows (load/save/settings updates) still pass tests.
- No orphan convenience methods remain without call sites.

---

## Suggested Execution Order
1. Complete all **P0** items first.
2. Implement **P1** user-visible fixes (retry, update banner action).
3. Perform **P2** logging/exception hardening.
4. Execute **P3** architecture refactor in small, test-backed slices.
5. Finish with docs updates and regression suite expansion.

## Definition of Done (Global)
- All P0/P1 items implemented with tests.
- Full test suite passes.
- No dead references to removed code paths.
- Logging is structured and controllable by level.
- Docs reflect actual architecture and run/test commands.
