# Test Coverage Improvement Plan

## Current Status

**Overall Coverage: 24%** (4322 statements, 3305 missed)

### Coverage Breakdown

| Module | Current | Target | Priority |
|--------|---------|--------|----------|
| telemetry_analyzer.py | 14% | 70%+ | High |
| telemetry_capture.py | 17% | 70%+ | High |
| log_parser.py | 30% | 60%+ | High |
| security.py | 40% | 70%+ | High |
| api_client.py | 59% | 80%+ | Medium |
| telemetry_decoder.py | 50% | 80%+ | Medium |
| track_catalog.py | 12% | 60%+ | Medium |
| helpers.py | 29% | 70%+ | Low |
| config.py | 0% | 50%+ | Low |

**Not Practical to Test:**
- UI modules (Flet desktop app)
- main.py (entry point)

---

## Priority 1: High Impact Core Modules

### 1. telemetry_analyzer.py (14% → 70%+)

**Missing Coverage Areas:**
- Lap detection algorithms (detect_laps, detect_corners, detect_profiled_corners)
- Track building (build_track)
- Corner detection logic
- Track profile matching
- Game-reported lap boundary handling
- Edge cases (short sessions, incomplete data)

**Test Plan:**
```python
# tests/test_telemetry_analyzer_comprehensive.py

class TestLapDetection:
    """Test lap detection with various scenarios."""
    
    def test_detect_laps_with_velocity_integration()
    def test_detect_laps_with_game_boundaries()
    def test_detect_laps_min_lap_time_filtering()
    def test_detect_laps_short_session()
    def test_detect_laps_no_valid_laps()
    def test_detect_laps_single_lap()
    def test_detect_laps_multiple_laps()

class TestCornerDetection:
    """Test corner detection algorithms."""
    
    def test_detect_corners_with_track_profile()
    def test_detect_corners_auto_detection()
    def test_detect_corners_no_corners_detected()
    def test_detect_corners_with_track_catalog_profile()

class TestTrackBuilding:
    """Test track building from telemetry frames."""
    
    def test_build_track_with_start_idx()
    def test_build_track_velocity_integration()
    def test_build_track_empty_frames()
    def test_build_track_short_session()
```

**Approach:**
- Use mock telemetry data for different scenarios
- Test with and without game-reported boundaries
- Test edge cases (empty data, single frames, etc.)
- Test track profile matching

---

### 2. telemetry_capture.py (17% → 70%+)

**Missing Coverage Areas:**
- Shared memory connection logic
- Region reader initialization
- Capture loop behavior
- Error handling (disconnections, timeouts)
- Lap boundary recording
- Metadata creation
- Raw dump saving

**Test Plan:**
```python
# tests/test_telemetry_capture.py

class TestRegionReader:
    """Test shared memory region reader."""
    
    def test_region_reader_initialization()
    def test_region_reader_open_success()
    def test_region_reader_open_failure()
    def test_region_reader_read_raw()
    def test_region_reader_close()

class TestTelemetryCapture:
    """Test telemetry capture system."""
    
    def test_capture_initialization()
    def test_capture_start_stop()
    def test_capture_frame_decoding()
    def test_capture_lap_boundary_recording()
    def test_capture_metadata_creation()
    def test_capture_raw_dump_saving()
    def test_capture_disconnect_handling()
    def test_capture_timeout_handling()

class TestCaptureIntegration:
    """Test capture with mock shared memory."""
    
    def test_full_capture_session()
    def test_capture_with_no_regions()
    def test_capture_with_partial_regions()
```

**Approach:**
- Mock shared memory handles (kernel32 functions)
- Test connection/disconnection scenarios
- Test timeout and heartbeat logic
- Test frame capture and decoding
- Test metadata and dump saving

---

### 3. log_parser.py (30% → 60%+)

**Missing Coverage Areas:**
- Full parsing flow (not just individual patterns)
- Session state management
- Lap completion detection
- Tyre compound changes
- Stint tracking
- Outlap detection
- Fuel consumption tracking
- Edge cases (malformed logs, missing fields)

**Test Plan:**
```python
# tests/test_log_parser_comprehensive.py

class TestLogParserFlow:
    """Test complete log parsing workflow."""
    
    def test_parse_full_session()
    def test_parse_session_with_multiple_laps()
    def test_parse_session_with_tyre_changes()
    def test_parse_session_with_stints()
    def test_parse_session_with_outlaps()
    def test_parse_empty_log()
    def test_parse_malformed_log()

class TestSessionState:
    """Test session state management."""
    
    def test_session_initialization()
    def test_session_state_updates()
    def test_session_boundary_detection()
    def test_stint_tracking()

class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_missing_track_name()
    def test_missing_car_model()
    def test_missing_steam_id()
    def test_invalid_lap_times()
    def test_duplicate_lap_detection()
```

**Approach:**
- Create comprehensive log fixtures for different scenarios
- Test full session parsing (not just pattern matching)
- Test state transitions and edge cases
- Test error recovery

---

### 4. security.py (40% → 70%+)

**Missing Coverage Areas:**
- Game detection (psutil process checking)
- Steam user extraction
- HMAC signing edge cases
- Secret validation
- Timestamp generation
- Nonce generation

**Test Plan:**
```python
# tests/test_security_comprehensive.py

class TestGameDetection:
    """Test game process detection."""
    
    def test_detect_game_running()
    def test_detect_game_not_running()
    def test_detect_game_multiple_processes()
    def test_detect_game_by_name()

class TestSteamUser:
    """Test Steam user extraction."""
    
    def test_get_steam_user_from_log()
    def test_get_steam_user_missing()
    def test_get_steam_user_invalid_format()

class TestSigning:
    """Test payload signing."""
    
    def test_sign_payload_valid()
    def test_sign_payload_missing_secret()
    def test_sign_payload_empty_payload()
    def test_verify_signature()

class TestNonceAndTimestamp:
    """Test nonce and timestamp generation."""
    
    def test_generate_nonce_unique()
    def test_generate_timestamp_current()
    def test_timestamp_validation()
```

**Approach:**
- Mock psutil for process detection
- Test with various Steam ID formats
- Test signing with valid/invalid secrets
- Test nonce uniqueness

---

## Priority 2: Medium Priority Modules

### 5. api_client.py (59% → 80%+)

**Missing Coverage Areas:**
- Error handling (network errors, server errors)
- Retry logic
- Rate limiting
- Invalid responses
- Timeout handling

**Test Plan:**
```python
# tests/test_api_client_edge_cases.py

class TestAPIErrorHandling:
    """Test API client error scenarios."""
    
    def test_network_error_retry()
    def test_server_error_500()
    def test_server_error_401()
    def test_timeout_handling()
    def test_invalid_json_response()
    def test_rate_limit_handling()

class TestSubmissionStatus:
    """Test submission status handling."""
    
    def test_status_success()
    def test_status_failure()
    def test_status_rate_limited()
    def test_status_invalid_lap()
```

---

### 6. telemetry_decoder.py (50% → 80%+)

**Missing Coverage Areas:**
- Fallback decoder paths
- Invalid data handling
- Different physics formats
- Error recovery

**Test Plan:**
```python
# tests/test_telemetry_decoder_edge_cases.py

class TestDecoderEdgeCases:
    """Test decoder with edge cases."""
    
    def test_decode_invalid_length()
    def test_decode_corrupted_data()
    def test_decode_empty_data()
    def test_fallback_decoder()
    def test_decoder_error_handling()
```

---

### 7. track_catalog.py (12% → 60%+)

**Missing Coverage Areas:**
- Track profile lookup
- Corner definitions
- Track matching logic
- Missing track handling

**Test Plan:**
```python
# tests/test_track_catalog.py

class TestTrackCatalog:
    """Test track catalog functionality."""
    
    def test_get_track_profile()
    def test_get_track_profile_missing()
    def test_track_corner_definitions()
    def test_track_matching()
    def test_all_tracks_have_profiles()
```

---

## Priority 3: Low Priority

### 8. helpers.py (29% → 70%+)

**Test Plan:**
```python
# tests/test_helpers.py

class TestHelperFunctions:
    """Test utility helper functions."""
    
    def test_all_helper_functions()
    def test_edge_cases()
```

---

### 9. config.py (0% → 50%+)

**Test Plan:**
```python
# tests/test_config.py

class TestConfigManager:
    """Test configuration management."""
    
    def test_load_default_config()
    def test_load_custom_config()
    def test_save_config()
    def test_config_file_not_found()
    def test_invalid_config_json()
```

---

## Implementation Strategy

### Phase 1: High Priority (Week 1)
1. telemetry_analyzer.py comprehensive tests
2. telemetry_capture.py comprehensive tests
3. Run coverage check - target: 40% overall

### Phase 2: High Priority (Week 2)
4. log_parser.py comprehensive tests
5. security.py comprehensive tests
6. Run coverage check - target: 50% overall

### Phase 3: Medium Priority (Week 3)
7. api_client.py edge case tests
8. telemetry_decoder.py edge case tests
9. track_catalog.py tests
10. Run coverage check - target: 60% overall

### Phase 4: Low Priority (Week 4)
11. helpers.py tests
12. config.py tests
13. Final coverage check - target: 65% overall

---

## Testing Approach

### Mock Strategy
- Use `unittest.mock` for external dependencies (psutil, kernel32, httpx)
- Create mock telemetry data for consistent testing
- Use pytest fixtures for common test data

### Test Data
- Create comprehensive fixtures in `tests/fixtures/`:
  - `sample_log_full_session.txt` - Complete session log
  - `sample_log_tyre_changes.txt` - Session with tyre changes
  - `sample_log_short_session.txt` - Short session
  - `sample_telemetry_full.jsonl` - Complete telemetry session
  - `sample_telemetry_short.jsonl` - Short telemetry session

### Test Organization
- Keep existing tests (they're good)
- Add new comprehensive test files
- Separate edge case tests from happy path tests

---

## Success Criteria

- Overall coverage: 65%+ (from 24%)
- Core modules (analyzer, capture, parser, security): 60%+ each
- All tests passing
- No test flakiness
- Test execution time < 30 seconds

---

## Notes

- UI modules (Flet) will remain at 0% coverage - not practical to test
- main.py will remain at 0% coverage - entry point, hard to test
- Focus on business logic, not infrastructure
- Tests should be fast, reliable, and maintainable
