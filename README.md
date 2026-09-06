# SimLaps Telemetry Client

A desktop application that monitors Assetto Corsa Evo (ACE) game logs and telemetry in real-time and automatically submits lap times to the SimLaps server.

## Features

- **Zero-Friction Setup**: No login required — just run and drive
- **Dual Detection**: Combines log parsing and telemetry capture for reliable lap detection
- **Game-Reported Laps**: Uses authoritative lap boundaries from ACE when available
- **Telemetry Fallback**: Analyzes physics data to detect laps when game boundaries are unavailable
- **Multi-Region Telemetry**: Captures and decodes physics, graphics, and static shared memory regions
- **Telemetry Analysis**: Generates HTML reports, AI coaching prompts, and JSONL exports from captured telemetry
- **Car Tuning Catalog**: Maps car identifiers to available setup parameters for AI coaching context
- **Anti-Cheat Protection**: Only submits when game is running, cryptographically signed payloads
- **Auto-Submit**: Automatically uploads valid lap times to SimLaps
- **Discord Integration**: Optional notifications for lap submissions with PB detection and fuel/tyre details
- **Personal Best Cache**: Tracks and displays your best times per track+car combination
- **Track Catalog**: Built-in profiles for 20+ tracks with corner definitions and per-track min-lap-time filtering
- **Lap Validity Tracking**: Comprehensive lap state classification (valid, track limits, penalties, sector desync, etc.)
- **Modern UI**: Clean, dark-themed interface built with Flet
- **Lap History**: Track all recorded laps locally with submission status
- **Service Architecture**: Modular service layer for lifecycle, processing, submission, monitoring, and telemetry management
- **Portable**: Single executable, no installation needed

## Security Features

The client implements multiple anti-cheat measures:

| Feature | Description |
|---------|-------------|
| **Game Detection** | Only processes logs when ACE is running |
| **Signed Payloads** | HMAC-SHA256 signatures prevent tampering |
| **Replay Prevention** | Unique nonces prevent replay attacks |
| **Timestamp Validation** | Requests expire after 5 minutes |
| **Rate Limiting** | Server-side limits prevent spam |
| **Plausibility Checks** | Server rejects impossibly fast times |

## Requirements

- Windows 10/11
- Assetto Corsa Evo installed
- Internet connection
- Python 3.10+ (for running from source)

## Installation

### Option 1: Download Pre-built Executable

Download the latest `SimLapsClient.exe` from the releases page and run it directly.

### Option 2: Run from Source

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/sim-laps-client.git
   cd sim-laps-client
   ```

2. Create a virtual environment (recommended):
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the application:
   ```bash
   python -m src.main
   ```

## Usage

### Getting Started

1. Launch SimLaps Client
2. The app will wait for ACE to start
3. Start driving in ACE
4. Your lap times are automatically captured and submitted

That's it! No login, no API keys, no configuration needed.

### Configuration (Optional)

Access Settings to customize:

- **Log File Path**: Location of your ACE log directory (default: `Saved Games\ACE\Logs`)
- **Server URL**: SimLaps server address
- **Auto-submit**: Enable/disable automatic lap submission
- **Submit Invalid Laps**: Optionally submit laps flagged as invalid (track limits, penalties)
- **Discord Webhook**: Optional Discord notifications for lap submissions
- **Discord User ID**: For personal-best callouts in Discord notifications
- **Telemetry Output Path**: Directory for HTML reports and JSONL exports
- **Telemetry Enabled**: Enable/disable telemetry capture and analysis
- **Minimize to Tray**: Keep running in background

## Building from Source

### Prerequisites

Install build dependencies:
```bash
pip install -r requirements.txt
pip install pyinstaller pyarmor
```

### Build Commands

**Standard Build (with obfuscation + secret injection):**
```bash
python build.py
```

**Quick Build (no obfuscation, for testing):**
```bash
python build.py --no-obfuscate
```

**Use specific secret:**
```bash
python build.py --secret <64-char-hex-string>
```

**Clean Build Artifacts:**
```bash
python build.py --clean
```

The executable will be created at `dist/SimLapsClient.exe`.

### Build Output

After a successful build, you'll find:
- `dist/SimLapsClient.exe` — The client application
- `dist/SERVER_SECRET.txt` — The secret to add to your server's `.env`

### Server Configuration

After building, add the generated secret to your server's `.env`:

```env
CLIENT_APP_SECRET=<hex-string-from-SERVER_SECRET.txt>
```

Then run the Prisma migration:
```bash
npx prisma migrate dev --name add_used_nonce
```

## Project Structure

```
sim-laps-client/
├── src/
│   ├── __init__.py             # Package root (exports __version__)
│   ├── main.py                 # Application entry point
│   ├── version.py              # Version information (single source of truth)
│   ├── models/                 # Data models
│   │   ├── __init__.py         # Public model exports
│   │   ├── lap.py              # LapData, SessionData, LapState, InProgressLap, StintData
│   │   ├── tyre_state.py       # Tyre compound tracking
│   │   ├── context.py          # LogContext (persistent parsing state)
│   │   ├── constants.py        # Tuning constants and thresholds
│   │   └── shared_session.py   # Thread-safe shared session data store and manager
│   ├── core/
│   │   ├── log_parser.py       # ACE log parsing with lap/session/tyre extraction
│   │   ├── telemetry_capture.py    # Multi-region shared memory telemetry capture
│   │   ├── telemetry_decoder.py     # Raw telemetry decoding (physics, graphics, static)
│   │   ├── telemetry_analyzer.py    # Lap detection and analysis orchestrator
│   │   ├── analyzer/            # Telemetry analysis sub-package
│   │   │   ├── __init__.py      # Re-exports TelemetryAnalyzer
│   │   │   ├── _util.py         # Shared math/utility functions
│   │   │   ├── ai_prompt.py     # AI coaching prompt generation
│   │   │   ├── analysis_result.py   # AnalysisResult dataclass
│   │   │   ├── build_track.py   # Track geometry from telemetry frames
│   │   │   ├── canonical.py     # Canonical lap resampling
│   │   │   ├── corner_detection.py  # Corner identification and profiling
│   │   │   ├── html_renderer.py # HTML telemetry report rendering
│   │   │   ├── lap_detection.py # Lap boundary detection from normalized position
│   │   │   ├── metrics.py       # Corner phase analysis metrics
│   │   │   └── session_summary.py   # Session summary persistence
│   │   ├── track_catalog.py     # Track profiles and corner definitions
│   │   ├── car_tuning_catalog.py    # Car setup parameter catalog
│   │   ├── data/                 # Static data files
│   │   │   ├── track_catalog.json
│   │   │   └── car_tuning_catalog.json
│   │   ├── api_client.py        # Server communication (signed submissions)
│   │   ├── security.py          # HMAC signing, game detection, anti-cheat
│   │   ├── discord_notifier.py  # Discord webhook notifications
│   │   └── pb_cache.py          # Personal Best cache with API preloading
│   ├── ui/
│   │   ├── app.py               # Main app controller (SimLapsApp)
│   │   ├── pages/
│   │   │   ├── __init__.py
│   │   │   ├── home.py          # Dashboard page (status, lap cards, telemetry controls)
│   │   │   ├── settings.py      # Settings page
│   │   │   └── history.py       # Lap history page
│   │   ├── components/
│   │   │   ├── __init__.py
│   │   │   ├── lap_card.py      # Lap display component
│   │   │   ├── status_bar.py    # Connection/game status indicator
│   │   │   ├── telemetry_status.py  # Telemetry capture controls and status
│   │   │   ├── pb_cache_viewer.py   # Personal Best cache viewer dialog
│   │   │   └── debug_logs.py    # Structured debug log viewer
│   │   └── services/
│   │       ├── __init__.py
│   │       ├── app_lifecycle_service.py      # App startup/shutdown orchestration
│   │       ├── lap_processing_service.py      # Lap validation and processing pipeline
│   │       ├── lap_submission_service.py      # Submission queue and retry logic
│   │       ├── monitoring_service.py          # Log file and game state monitoring
│   │       ├── settings_service.py            # Settings CRUD and validation
│   │       ├── telemetry_lifecycle_service.py # Telemetry start/stop/analysis orchestration
│   │       └── user_bootstrap_service.py      # Steam ID detection and user resolution
│   └── utils/
│       ├── config.py            # Settings persistence (AppData/config.json)
│       ├── helpers.py           # Formatting utilities (lap time, car/track names)
│       └── structured_logger.py # Structured logging with component tagging
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Shared test fixtures and configuration
│   ├── fixtures/                # Test data files (sample logs, telemetry dumps)
│   ├── test_api_client.py
│   ├── test_app_lifecycle_service.py
│   ├── test_car_tuning_catalog.py
│   ├── test_config_manager.py
│   ├── test_decode_graphics_evo.py
│   ├── test_decode_static_evo.py
│   ├── test_discord_integration.py
│   ├── test_helpers_utils.py
│   ├── test_history_page.py
│   ├── test_home_page.py
│   ├── test_lap_processing_service.py
│   ├── test_lap_submission_service.py
│   ├── test_log_parser_*.py     # Extensive log parser test suite (12+ files)
│   ├── test_main_entrypoint.py
│   ├── test_monitoring_service.py
│   ├── test_pb_cache_additional.py
│   ├── test_security.py
│   ├── test_security_comprehensive.py
│   ├── test_settings_service.py
│   ├── test_shared_session.py
│   ├── test_status_bar.py
│   ├── test_telemetry_analyzer_*.py     # Analyzer tests (advanced, comprehensive, real data)
│   ├── test_telemetry_capture*.py       # Capture tests (basic, advanced, coverage)
│   ├── test_telemetry_decoder_*.py      # Decoder tests (comprehensive, real data)
│   ├── test_telemetry_lifecycle_service.py
│   ├── test_telemetry_status.py
│   ├── test_track_catalog.py
│   ├── test_tyre_state_model.py
│   ├── test_user_bootstrap_service.py
│   └── test_version_sync.py
├── telemetry/
│   ├── ACE_SharedFileOut_Documentation_v1.md  # ACE shared memory documentation
│   └── ace_payload_hash.py                    # ACE payload hash and opt-in submit helper
├── tools/
│   ├── generate_car_tuning_catalog.py  # Car tuning catalog generator
│   ├── parse_all_logs.py               # Batch log parsing utility
│   └── README.md                       # Tools documentation
├── assets/
│   ├── icon.ico              # Application icon (Windows)
│   └── icon.png              # Application icon (Flet UI)
├── acelogs/                  # Sample ACE log files for development
├── plans/                    # Architecture and design planning documents
├── extract.py                # ACE shared memory structure extraction utility
├── build.py                  # Build script with obfuscation and secret injection
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── pytest.ini
└── README.md
```

## How It Works

### Dual Detection Approach

The client uses two complementary methods for lap detection:

1. **Log Parsing (Primary)**: Reads ACE log files for game-reported lap completions, sector times, tyre compounds, and penalties
2. **Telemetry Capture (Fallback)**: Analyzes shared memory physics data when log parsing provides incomplete information

### Data Flow

```
ACE Game ──→ Log File ──→ Log Parser ──→ Shared Session ──→ API Client ──→ Server
    │                                    │
    └──→ Shared Memory ──→ Telemetry     │
         (Physics/Graphics/Static)       │
              │                          │
              └──→ Telemetry Analyzer ───┘
                   (HTML reports, AI prompts, JSONL)
```

### Data Extracted from Logs

| Data | Source Pattern |
|------|---------------|
| Steam ID | `{steamId} connected on car {car}` |
| Track | `TRACK NAME {name}` or `Game Started!` line |
| Car | Connection line or session start |
| Lap Time | `New lap carId {id}: {time}` |
| Sectors | `On Split ... splittime {ms}` |
| Tyre Compound | `setCompound Tyre: {n} compound name: {name}` |
| Game Version | `Build release {version}` |
| Invalid Laps | `PENALTY_ADDED_KEY`, `tyres out → 4` track limit violations |
| Lap Boundaries | Game-reported lap completion events |
| Session Type | `SESSION TYPE {type}` |
| Fuel | Per-lap fuel consumption from telemetry |
| Track Limits | `tyres out → 4` with inside distance |

### Telemetry Data

- **Multi-Region Capture**: Captures physics (speed, position, fuel, inputs), graphics (normalized car position, lap validity), and static (session metadata, car info, track layout) shared memory regions
- **Decode Pipeline**: Decodes all three regions with AC/ACC structure matching and Evo-specific fallback pattern detection
- **Lap Detection**: Uses normalized car position from graphics region when available, with fallback to velocity integration
- **Track Catalog**: Built-in profiles for 20+ tracks with corner definitions, sector positions, and per-track minimum lap times
- **Analysis Output**: Generates HTML telemetry reports with corner-by-corner metrics, AI coaching prompts with car tuning context, and JSONL structured data exports
- **Session Summaries**: Persists and compares session-to-session metrics for trend analysis

### API Submission Format

Laps are submitted to `/api/submit` with signed payloads:

```json
{
  "userId": "76561198321627695",
  "trackId": "spa_francorchamps",
  "carId": "ks_porsche_992_gt3_cup",
  "time": 138456,
  "sector1": 45000,
  "sector2": 48000,
  "sector3": 45456,
  "gameVersion": "1.0.0",
  "tires": "S",
  "fuelUsedLiters": 2.85,
  "_timestamp": 1706054400000,
  "_nonce": "550e8400-e29b-41d4-a716-446655440000",
  "_signature": "a1b2c3..."
}
```

The standalone ACE payload helper does not contain credentials or a sample
submission. To use it, provide a payload JSON file and set
`ACE_CAREER_MODE_TOKEN` in the environment before running
`python -m telemetry.ace_payload_hash payload.json`.

## Troubleshooting

### "Waiting for game..."

The client is ready but ACE isn't running. Start the game.

### "Log file not found"

1. Ensure ACE has been run at least once
2. Check the log path in Settings
3. Default location: `C:\Users\{username}\Saved Games\ACE\Logs`

### Laps not being detected

1. Ensure ACE is running (green indicator in app)
2. Complete a full lap (cross the finish line)
3. Check if the status bar shows "Monitoring active"
4. Verify the log path is correct

### "Signature verification failed"

Your client version doesn't match the server. Download the latest version.

### "Rate limit exceeded"

Wait 30 seconds between lap submissions. This is normal during intense sessions.

## Configuration Storage

Settings are stored at:
- Windows: `%APPDATA%\SimLapsClient\config.json`

## Development

### Running Tests

```bash
# Using project venv Python (recommended)
venv-sim-laps-client\Scripts\python.exe -m pytest tests/

# Or using system pytest
pytest tests/
```

### Test Coverage

- Log parser tests with real game log data (12+ test files covering core methods, edge cases, follow mode, lap completion, tyre compounds, shared session integration)
- Telemetry decoder tests with real telemetry dumps (comprehensive + real data)
- Telemetry analyzer tests with real physics data (advanced, comprehensive, real data)
- Telemetry capture tests (basic, advanced, coverage)
- API client integration tests
- Discord integration tests
- Security and signing tests (basic + comprehensive)
- UI component tests (home page, history page, status bar, telemetry status)
- Service layer tests (app lifecycle, lap processing, lap submission, monitoring, settings, telemetry lifecycle, user bootstrap)
- Model tests (shared session, tyre state)
- Utility tests (helpers, config manager, car tuning catalog, track catalog)
- Version sync test

### Code Style

The project follows PEP 8 guidelines. Format with:
```bash
pip install black
black src/
```

### Version Management

The client version has a single source of truth: [`src/version.py`](src/version.py).

- Update `VERSION_MAJOR`, `VERSION_MINOR`, and `VERSION_PATCH` in [`src/version.py`](src/version.py).
- Packaging metadata in [`pyproject.toml`](pyproject.toml) is populated automatically via:
  - `[project] dynamic = ["version"]`
  - `[tool.setuptools.dynamic] version = {attr = "src.version.VERSION"}`
- Runtime aliases also resolve from the same source (`src.__version__` → `src.version.VERSION`).

## License

MIT License — see LICENSE file for details.

## Support

- Report issues on GitHub
- Join the SimLaps Discord community
