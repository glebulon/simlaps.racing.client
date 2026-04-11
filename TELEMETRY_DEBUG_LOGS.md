# Telemetry Debug Logs Integration

## Overview
Added comprehensive telemetry logging to the Debug Logs system to provide real-time visibility into telemetry capture and analysis operations.

## What's Added

### 1. Telemetry Capture Events
- **Capture Start**: When telemetry capture begins, including connection status
- **First Frame**: When the first valid frame is captured
- **Progress Updates**: Every 30 seconds during active capture
- **Lap Boundaries**: When game-reported lap times are recorded
- **Capture Stop**: When capture ends, including reason and frame count

### 2. Telemetry Analysis Events
- **Analysis Start**: When analysis begins with frame count and parameters
- **Data Quality**: Progress coverage, frame plausibility, and confidence scores
- **Analysis Mode**: Whether full or diagnostic mode is selected
- **Lap Detection**: Number of laps found and detection method used
- **Analysis Complete**: Final results with lap count and best lap time

### 3. App-Level Events
- **UI Triggers**: When user or automatic events start/stop telemetry
- **Errors**: Any failures during capture or analysis
- **Status Changes**: UI state transitions for telemetry

## Debug Logs UI Enhancements

### Improved Log Organization
- **TELEMETRY EVENTS section**: Shows telemetry-specific logs first
- **OTHER LOGS section**: Shows general application logs
- Better separation for easier debugging

## Example Log Output

```
=== TELEMETRY EVENTS ===
[13:05:28] [TELEMETRY] Capture started - waiting for game connection
[13:05:29] [TELEMETRY] Connected to 3 regions: ['physics', 'graphics', 'static']
[13:05:30] [TELEMETRY] First frame captured - telemetry active
[13:06:00] [TELEMETRY] Progress: 300 frames captured
[13:06:30] [TELEMETRY] Lap boundary: frame 295, time 75234ms
[13:07:15] [TELEMETRY] Capture stopped: session_end, 612 frames captured

[13:07:16] [ANALYZER] Starting analysis: 612 frames, 10Hz, track=barcelona
[13:07:16] [ANALYZER] Data quality: progress=87%, frames=92%, confidence=high (0.89)
[13:07:16] [ANALYZER] Analysis mode: full
[13:07:16] [ANALYZER] Lap detection: telemetry-based 3 laps
[13:07:17] [ANALYZER] Analysis complete: 3 laps, best 75.2s

=== OTHER LOGS ===
[13:05:28] [APP] Starting telemetry capture from UI
[13:07:15] [APP] Telemetry auto-stop: session_end
```

## How to Use

1. **Open Debug Logs**: Click the debug logs button in the app
2. **View All Logs**: Default view shows telemetry events first, then other logs
3. **Clear Logs**: Use "Clear Logs" to reset the log buffer
4. **Export**: Use "Export Game Logs" to save full logs to file

## Technical Details

### Log Sources
- `src/core/telemetry_capture.py`: Capture events and frame progress
- `src/core/telemetry_analyzer.py`: Analysis events and quality metrics  
- `src/ui/app.py`: UI triggers and status changes

### Log Organization
- Uses simple string matching for `[TELEMETRY]` and `[ANALYZER]` tags to separate sections
- Maintains chronological order within each section
- Shows last 20 telemetry events and last 30 other logs

### Performance
- Logs are captured asynchronously and don't block telemetry operations
- Log buffer limited to 500 entries total to manage memory
- Telemetry events are prioritized in display for better visibility

## Benefits

1. **Real-time Monitoring**: See telemetry status without leaving the app
2. **Troubleshooting**: Quickly identify capture/analysis issues
3. **Quality Awareness**: Monitor data quality and confidence scores
4. **Performance Tracking**: Track frame rates and lap detection success
5. **User-Friendly**: No need to check external log files

This integration makes telemetry debugging much more accessible and provides immediate feedback on system performance.
