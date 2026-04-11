# Structured Logging Implementation

## Overview

Implemented a comprehensive structured logging system across the SimLaps client to ensure consistent, organized, and searchable log messages throughout the application.

## What Was Implemented

### 1. Centralized Structured Logger (`src/utils/structured_logger.py`)

**Features:**
- **Component-based tagging**: Each log message is tagged with its source component
- **Log levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL
- **Structured context**: Key-value pairs for additional context
- **Exception handling**: Automatic traceback capture for exceptions
- **Debug logs integration**: All logs automatically flow to the Debug Logs viewer

**Components:**
```python
class Component(Enum):
    APP = "APP"
    TELEMETRY = "TELEMETRY"
    ANALYZER = "ANALYZER"
    CAPTURE = "CAPTURE"
    UI = "UI"
    HOME = "HOME"
    DEBUG_LOGS = "DEBUG_LOGS"
    HISTORY = "HISTORY"
    PB_CACHE = "PB_CACHE"
    PB_VIEWER = "PB_VIEWER"
    LAP_CARD = "LAP_CARD"
    CONFIG = "CONFIG"
    SECURITY = "SECURITY"
    DISCORD = "DISCORD"
    API = "API"
    LOG_PARSER = "LOG_PARSER"
```

**Usage Examples:**
```python
from src.utils.structured_logger import log_info, log_warning, log_error, log_exception, Component

# Basic logging
log_info(Component.TELEMETRY, "Capture started", regions=3, hz=10)

# With structured context
log_warning(Component.ANALYZER, "Lap detection fallback", 
           method="game-boundaries", laps=2)

# Exception handling
log_exception(Component.APP, "Telemetry start error", exception, 
              enabled=True, capture_exists=False)
```

### 2. Updated Modules

#### Telemetry Capture (`src/core/telemetry_capture.py`)
- **Start/stop events**: Capture initialization and termination
- **Progress tracking**: Frame capture progress every 30 seconds
- **Lap boundaries**: When game-reported lap times are recorded
- **Connection status**: Game connection and region discovery
- **Error handling**: Capture failures and exceptions

#### Telemetry Analyzer (`src/core/telemetry_analyzer.py`)
- **Analysis lifecycle**: Start, quality assessment, completion
- **Data quality**: Progress coverage and frame plausibility metrics
- **Lap detection**: Success/failure with method and count
- **Mode determination**: Full vs diagnostic mode selection
- **Results summary**: Lap count, best time, coachable laps

#### Main App (`src/ui/app.py`)
- **Initialization**: App startup and service initialization
- **Configuration**: Loading and validation
- **Telemetry control**: Start/stop from UI triggers
- **Analysis orchestration**: Auto-stop analysis workflow
- **Error handling**: Exception capture and UI feedback

#### UI Components
- **Home Page**: Button clicks and user interactions
- **History Page**: Statistics and entry tracking
- **Debug Logs**: Export operations and status
- **PB Cache**: Cache operations and viewer events

## Log Message Format

**Standard Format:**
```
[timestamp] [COMPONENT] [LEVEL] message | key1=value1 key2=value2
```

**Examples:**
```
[13:08:55] [TELEMETRY] [INFO] Found regions | count=3 regions=['physics', 'graphics', 'static']
[13:08:56] [ANALYZER] [WARNING] Lap detection fallback | method=game-boundaries laps=2
[13:08:57] [APP] [ERROR] Telemetry start error: ValueError: invalid parameter | enabled=True capture_exists=False
```

## Benefits

### 1. **Consistency**
- All logging follows the same format and structure
- Component tagging makes source identification easy
- Standardized log levels for severity filtering

### 2. **Searchability**
- Structured key-value pairs enable precise filtering
- Component tags allow per-component log analysis
- Consistent timestamp format for chronological analysis

### 3. **Debugging**
- Exception logging includes full tracebacks
- Context data helps diagnose issues without additional code
- All logs flow to Debug Logs viewer for real-time monitoring

### 4. **Maintainability**
- Single logging interface reduces code duplication
- Easy to add new components and log types
- Centralized configuration for log behavior

## Migration Notes

### Before (Inconsistent)
```python
print(f"[TELEMETRY] Capture started - waiting for game connection")
add_debug_log(f"[ANALYZER] Starting analysis: {frames} frames, {hz}Hz")
print(f"[APP] Error starting telemetry: {e}")
```

### After (Structured)
```python
log_info(Component.TELEMETRY, "Capture started - waiting for game connection")
log_info(Component.ANALYZER, "Starting analysis", frames=frames, hz=hz)
log_exception(Component.APP, "Telemetry start error", e)
```

## Debug Logs Integration

The structured logger automatically integrates with the existing Debug Logs viewer:

- **Real-time display**: All structured logs appear in Debug Logs immediately
- **Component filtering**: Easy to identify telemetry vs app vs UI events
- **Level highlighting**: ERROR and CRITICAL messages stand out
- **Context preservation**: Key-value pairs are displayed for context

## Future Enhancements

1. **Log rotation**: Implement log file rotation for long-running sessions
2. **Remote logging**: Option to send logs to external monitoring service
3. **Performance metrics**: Add timing and performance data to logs
4. **Filtering UI**: Add component and level filters to Debug Logs viewer
5. **Log analytics**: Built-in analysis of common patterns and issues

## Usage Guidelines

### DO:
- Use appropriate log levels (DEBUG for detailed tracing, INFO for important events)
- Include relevant context data as key-value pairs
- Use component tags consistently
- Log exceptions with `log_exception()` to capture tracebacks

### DON'T:
- Use `print()` statements - use the structured logger instead
- Include sensitive data in log messages
- Log at DEBUG level in production code paths
- Create overly verbose log messages

This structured logging system provides a solid foundation for debugging, monitoring, and maintaining the SimLaps client application.
