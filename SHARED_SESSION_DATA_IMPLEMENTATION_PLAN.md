# Shared Session Data Implementation Plan

## 🎯 Objective
Create a unified session data object that eliminates duplication between telemetry analysis and log parsing, providing both systems with access to definitive timing, validity, and car setup data.

## 📊 Current Architecture Analysis

### Existing Data Models
- **`SessionData`** (src/models/lap.py): Contains session metadata and lap list
- **`LapData`** (src/models/lap.py): Individual lap data with timing and validity
- **`LogContext`** (src/models/context.py): Persistent parsing context
- **`TelemetryAnalyzer`** (src/core/telemetry_analyzer.py): Processes FrameData into analysis
- **`APIClient`** (src/core/api_client.py): Handles lap submissions to server

### Data Flow Issues
1. **Duplication**: Both log parser and telemetry analyzer maintain separate lap timing data
2. **Inconsistent sources**: Different validity flags between logs and telemetry
3. **Missing setup data**: Car setup scattered across different components
4. **No unified interface**: Each system has its own data access patterns

## 🏗️ Proposed Architecture

### New Core Component: `SharedSessionManager`

```python
@dataclass
class SharedSessionData:
    """Unified session data accessible by both telemetry and log parser"""
    
    # === Session Metadata ===
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    game_version: str = "Unknown"
    session_type: str = "Unknown"
    track: str = "Unknown"
    car: str = "Unknown"
    player_name: Optional[str] = None
    player_id: Optional[str] = None
    
    # === Definitive Lap Data (Priority: Logs > Shared Memory > Calculated) ===
    lap_times: Dict[int, float] = field(default_factory=dict)  # lap_num → time_ms
    lap_validity: Dict[int, bool] = field(default_factory=dict)  # lap_num → is_valid
    lap_boundaries: Dict[int, int] = field(default_factory=dict)  # lap_num → frame_idx
    
    # === Car Setup (from Shared Memory) ===
    car_setup: Dict[str, Any] = field(default_factory=dict)
    
    # === Session Summary ===
    best_lap_time: Optional[float] = None
    total_laps: Optional[int] = None
    current_lap: Optional[int] = None
    session_phase: Optional[str] = None
    
    # === Telemetry Summary ===
    fuel_consumption: Dict[int, float] = field(default_factory=dict)
    max_speed: Optional[float] = None
    tyre_compound: str = "Unknown"
    
    # === Source Tracking ===
    data_sources: Dict[str, Set[str]] = field(default_factory=dict)  # field → sources
    
class SharedSessionManager:
    """Thread-safe manager for shared session data"""
    
    def __init__(self):
        self._session_data = SharedSessionData()
        self._lock = threading.RLock()
        self._observers: List[Callable] = []
    
    # === Data Access Methods ===
    def get_lap_time(self, lap_num: int) -> Optional[float]
    def get_lap_validity(self, lap_num: int) -> bool
    def get_car_setup(self) -> Dict[str, Any]
    def get_best_lap_time(self) -> Optional[float]
    
    # === Data Update Methods ===
    def update_from_logs(self, log_session_data: SessionData)
    def update_from_telemetry(self, telemetry_data: Dict)
    def update_from_shared_memory(self, shm_data: Dict)
    
    # === Observer Pattern ===
    def subscribe(self, callback: Callable[[SharedSessionData], None])
    def notify_observers(self)
```

## 📋 Implementation Steps

### Phase 1: Core Infrastructure

#### 1.1 Create Shared Session Models
**File**: `src/models/shared_session.py`
```python
# Implement SharedSessionData and SharedSessionManager
# Include thread-safe access patterns
# Add data source tracking for debugging
```

#### 1.2 Integration Points
**Files to modify**:
- `src/models/__init__.py` - Export new models
- `src/core/log_parser.py` - Import and use SharedSessionManager
- `src/core/telemetry_analyzer.py` - Import and use SharedSessionManager
- `src/core/api_client.py` - Use shared data for submissions

### Phase 2: Log Parser Integration

#### 2.1 Update Log Parser
**File**: `src/core/log_parser.py`
```python
class LogParser:
    def __init__(self, session_manager: Optional[SharedSessionManager] = None):
        self._session_manager = session_manager or SharedSessionManager()
    
    def _finalize_lap(self, lap_data: LapData):
        # Update shared session data instead of internal state
        self._session_manager.update_lap_from_logs(lap_data)
        # Keep existing callback system for compatibility
```

#### 2.2 Data Source Priority Implementation
```python
def update_lap_from_logs(self, lap_data: LapData):
    """Update lap data from authoritative game logs"""
    with self._lock:
        # Logs are highest priority for timing and validity
        self._session_data.lap_times[lap_data.lap_number] = lap_data.lap_time_ms
        self._session_data.lap_validity[lap_data.lap_number] = lap_data.is_valid
        self._session_data.data_sources["lap_times"].add("logs")
        self._session_data.data_sources["lap_validity"].add("logs")
        
        # Update session metadata
        self._session_data.session_type = self.current_session.session_type
        self._session_data.track = self.context.current_track
        self._session_data.car = self.context.current_car
        
        self.notify_observers()
```

### Phase 3: Telemetry Analyzer Integration

#### 3.1 Update Telemetry Analyzer
**File**: `src/core/telemetry_analyzer.py`
```python
class TelemetryAnalyzer:
    def __init__(self, output_dir: str, session_manager: Optional[SharedSessionManager] = None):
        self._output_dir = output_dir
        self._track_catalog = track_catalog
        self._session_manager = session_manager or SharedSessionManager()
    
    async def analyze(self, frames: List[FrameData], ...):
        # Use shared session data instead of internal calculations
        lap_times = self._session_manager.get_all_lap_times()
        lap_validity = self._session_manager.get_all_lap_validity()
        
        # Update shared data with telemetry insights
        self._session_manager.update_from_telemetry(telemetry_summary)
```

#### 3.2 Car Setup Integration
```python
def update_from_shared_memory(self, shm_data: Dict):
    """Update car setup from shared memory graphics/session state"""
    with self._lock:
        self._session_data.car_setup.update({
            "tire_pressures": shm_data.get("tyre_pressures", {}),
            "tire_compound": shm_data.get("tyre_compound", "Unknown"),
            "fuel_level": shm_data.get("fuel", 0.0),
            "brake_bias": shm_data.get("brake_bias", 0.5),
            "drs_available": shm_data.get("drs_available", False),
            "session_temp": shm_data.get("air_temp", 20.0),
            "ride_heights": shm_data.get("ride_heights", {}),
            "wing_angles": shm_data.get("wing_angles", {}),
        })
        self._session_data.data_sources["car_setup"].add("shared_memory")
        self.notify_observers()
```

### Phase 4: API Client Integration

#### 4.1 Update API Client
**File**: `src/core/api_client.py`
```python
class APIClient:
    def __init__(self, session_manager: Optional[SharedSessionManager] = None):
        self._session_manager = session_manager or SharedSessionManager()
    
    async def submit_lap(self, lap_data: LapData) -> SubmissionResult:
        # Use shared session data for consistent submissions
        session_data = self._session_manager.get_session_data()
        
        payload = {
            "lap": lap_data.to_dict(),
            "session": session_data.to_dict(),
            "car_setup": self._session_manager.get_car_setup(),
            "data_sources": session_data.data_sources,
        }
```

### Phase 5: Data Source Priority Logic

#### 5.1 Unified Data Access
```python
def get_lap_time(self, lap_num: int) -> Optional[float]:
    """Get lap time with definitive source priority"""
    with self._lock:
        # 1st priority: Game logs (most authoritative)
        if lap_num in self._session_data.lap_times:
            if "logs" in self._session_data.data_sources.get("lap_times", set()):
                return self._session_data.lap_times[lap_num]
        
        # 2nd priority: Shared memory timing state
        if lap_num in self._session_data.shm_lap_times:
            return self._session_data.shm_lap_times[lap_num]
        
        # 3rd priority: Calculated from telemetry
        return self._session_data.calc_lap_times.get(lap_num)
```

#### 5.2 Data Source Validation
```python
def validate_data_consistency(self) -> Dict[str, List[str]]:
    """Check for inconsistencies between data sources"""
    issues = []
    
    for lap_num in self._session_data.lap_times:
        log_time = self._session_data.lap_times.get(lap_num)
        shm_time = self._session_data.shm_lap_times.get(lap_num)
        calc_time = self._session_data.calc_lap_times.get(lap_num)
        
        if log_time and shm_time and abs(log_time - shm_time) > 100:
            issues.append(f"Lap {lap_num}: Log time {log_time}ms vs SHM time {shm_time}ms")
    
    return {"inconsistencies": issues}
```

### Phase 6: Migration Strategy

#### 6.1 Backward Compatibility
```python
# Keep existing interfaces working during transition
class LegacySessionDataWrapper:
    """Wrapper to maintain compatibility with existing code"""
    
    def __init__(self, shared_manager: SharedSessionManager):
        self._shared = shared_manager
    
    @property
    def laps(self) -> List[LapData]:
        # Convert shared data back to legacy format
        return self._convert_to_legacy_laps()
```

#### 6.2 Gradual Migration
1. **Week 1**: Implement SharedSessionManager, integrate with log parser
2. **Week 2**: Integrate with telemetry analyzer, maintain dual data paths
3. **Week 3**: Update API client to use shared data
4. **Week 4**: Remove legacy data structures, complete migration

### Phase 7: Testing Strategy

#### 7.1 Unit Tests
**File**: `tests/test_shared_session_manager.py`
```python
def test_data_source_priority()
def test_thread_safety()
def test_car_setup_integration()
def test_lap_timing_consistency()
def test_observer_pattern()
```

#### 7.2 Integration Tests
**File**: `tests/test_shared_session_integration.py`
```python
def test_log_parser_to_shared_data()
def test_telemetry_to_shared_data()
def test_api_client_shared_data()
def test_data_consistency_validation()
```

#### 7.3 Performance Tests
```python
def test_concurrent_access_performance()
def test_memory_usage_optimization()
def test_large_session_handling()
```

## 🎯 Benefits

### Immediate Benefits
- **Eliminated duplication**: Single source of truth for session data
- **Definitive timing**: Always uses most authoritative source
- **Rich setup data**: Car setup from shared memory available everywhere
- **Thread safety**: Safe concurrent access from multiple components

### Long-term Benefits
- **Maintainability**: Easier to add new data sources
- **Debugging**: Clear data source tracking
- **Performance**: Reduced memory usage and computation
- **Extensibility**: Observer pattern for future components

## 📈 Success Metrics

### Quantitative Metrics
- **Memory usage**: Reduce by 30% (eliminated duplicate data structures)
- **Data consistency**: 100% consistency between components
- **Setup data availability**: Car setup accessible in all components

### Qualitative Metrics
- **Developer experience**: Single API for session data access
- **Debugging**: Clear data source provenance
- **Maintainability**: Easier to add new features

## 🔧 Implementation Timeline

| Phase | Duration | Dependencies | Deliverables |
|-------|----------|-------------|-------------|
| 1. Core Infrastructure | 1 week | None | SharedSessionManager, basic models |
| 2. Log Parser Integration | 1 week | Phase 1 | Log parser using shared data |
| 3. Telemetry Integration | 1 week | Phase 2 | Analyzer using shared data |
| 4. API Client Integration | 0.5 week | Phase 3 | Unified submission data |
| 5. Data Source Logic | 0.5 week | Phase 4 | Priority system, validation |
| 6. Migration | 1 week | Phase 5 | Backward compatibility, cleanup |
| 7. Testing | 1 week | Phase 6 | Comprehensive test suite |

**Total Estimated Time**: 6 weeks

## 🚀 Risk Mitigation

### Technical Risks
- **Thread safety**: Use RLock and comprehensive testing
- **Performance**: Profile and optimize concurrent access
- **Data loss**: Implement backup/restore mechanisms

### Migration Risks
- **Backward compatibility**: Maintain wrapper classes during transition
- **Data corruption**: Implement validation and consistency checks
- **Rollback plan**: Keep legacy code available during transition

## 📚 File Structure Changes

### New Files
```
src/models/shared_session.py          # Core shared session models
tests/test_shared_session_manager.py   # Unit tests
tests/test_shared_session_integration.py # Integration tests
```

### Modified Files
```
src/models/__init__.py                # Export new models
src/core/log_parser.py                 # Use SharedSessionManager
src/core/telemetry_analyzer.py        # Use SharedSessionManager  
src/core/api_client.py                 # Use shared data for submissions
```

### Files to Remove (Post-Migration)
```
# Legacy data structures (after migration complete)
src/models/context.py (merge functionality)
# Duplicate session handling in individual components
```

This implementation plan provides a comprehensive roadmap for creating a unified session data system that eliminates duplication while maintaining data integrity and performance.
