# SimLaps Telemetry Client

A desktop application that monitors Assetto Corsa Evo (ACE) game logs and telemetry in real-time and automatically submits lap times to the SimLaps server.

## Features

- **Zero-Friction Setup**: No login required - just run and drive
- **Dual Detection**: Combines log parsing and telemetry capture for reliable lap detection
- **Game-Reported Laps**: Uses authoritative lap boundaries from ACE when available
- **Telemetry Fallback**: Analyzes physics data to detect laps when game boundaries unavailable
- **Anti-Cheat Protection**: Only submits when game is running, cryptographically signed payloads
- **Auto-Submit**: Automatically upload valid lap times to SimLaps
- **Discord Integration**: Optional notifications for lap submissions
- **Personal Best Cache**: Tracks and displays your best times
- **Track Catalog**: Built-in track profiles for accurate analysis
- **Modern UI**: Clean, dark-themed interface built with Flet
- **Lap History**: Track all your recorded laps locally
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

- **Log File Path**: Location of your ACE log file (default: `Saved Games\ACE\log.txt`)
- **Server URL**: SimLaps server address
- **Auto-submit**: Enable/disable automatic lap submission
- **Discord Webhook**: Optional Discord notifications for lap submissions
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
- `dist/SimLapsClient.exe` - The client application
- `dist/SERVER_SECRET.txt` - The secret to add to your server's `.env`

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
│   ├── main.py              # Application entry point
│   ├── version.py           # Version information
│   ├── models/              # Data models
│   │   ├── __init__.py
│   │   ├── lap.py           # LapData, SessionData, LapState, StintData
│   │   ├── tyre_state.py    # Tyre compound tracking
│   │   ├── context.py       # LogContext (persistent parsing state)
│   │   └── constants.py     # Tuning constants and thresholds
│   ├── core/
│   │   ├── log_parser.py    # ACE log parsing
│   │   ├── telemetry_capture.py  # Shared memory telemetry capture
│   │   ├── telemetry_decoder.py   # Raw telemetry decoding
│   │   ├── telemetry_analyzer.py  # Lap detection from telemetry
│   │   ├── track_catalog.py  # Track profiles and corner definitions
│   │   ├── api_client.py    # Server communication
│   │   ├── security.py      # Signing & game detection
│   │   ├── discord_notifier.py  # Discord notifications
│   │   └── pb_cache.py      # Personal Best cache
│   ├── ui/
│   │   ├── app.py           # Main app controller
│   │   ├── pages/
│   │   │   ├── home.py      # Dashboard page
│   │   │   ├── settings.py  # Settings page
│   │   │   └── history.py   # Lap history page
│   │   └── components/
│   │       ├── lap_card.py  # Lap display component
│   │       ├── status_bar.py
│   │       └── telemetry_status.py
│   └── utils/
│       ├── config.py        # Settings management
│       └── structured_logger.py # Structured logging
├── tests/
│   ├── fixtures/           # Test data files
│   ├── test_api_client.py
│   ├── test_discord_integration.py
│   ├── test_log_parser_real_data.py
│   ├── test_log_parser_tyre_compounds.py
│   ├── test_security.py
│   ├── test_telemetry_analyzer_real_data.py
│   └── test_telemetry_decoder_real_data.py
├── assets/
│   └── icon.ico             # Application icon
├── build.py                 # Build script with .env bundling
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
└── README.md
```

## How It Works

### Dual Detection Approach

The client uses two complementary methods for lap detection:

1. **Log Parsing (Primary)**: Reads ACE log files for game-reported lap completions
2. **Telemetry Capture (Fallback)**: Analyzes shared memory physics data when log parsing fails

### Data Flow

```
ACE Game → Log File → SimLaps Client → Server
           ↓            ↓
       Shared Memory   Parse logs for
       (Physics Only)  lap boundaries
                      ↓
              Sign payload with
              embedded secret
```

### Data Extracted from Logs

| Data | Source Pattern |
|------|----------------|
| Steam ID | `{steamId} connected on car {car}` |
| Track | `TRACK NAME {name}` or `Game Started!` line |
| Car | Connection line or session start |
| Lap Time | `New lap carId {id}: {time}` |
| Sectors | `On Split ... splittime {ms}` |
| Tyre Compound | `setCompound Tyre: {n} compound name: {name}` |
| Game Version | `Build release {version}` |
| Invalid Laps | `PENALTY_ADDED_KEY` |
| Lap Boundaries | Game-reported lap completion events |

### Telemetry Data

- **Multi-Region Capture**: Captures physics, graphics, and static shared memory regions
- **Decode Pipeline**: Decodes physics (speed, position, fuel), graphics (normalized car position), and static (session metadata) from shared memory
- **Lap Detection**: Uses normalized car position from graphics region when available, with fallback to velocity integration
- **Track Catalog**: Built-in profiles for 20+ tracks with corner definitions
- **Min Lap Time**: Configurable per-track to filter spurious detections
- **Output Formats**: Generates HTML telemetry reports, AI coaching prompts, and JSONL exports

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
  "_timestamp": 1706054400000,
  "_nonce": "550e8400-e29b-41d4-a716-446655440000",
  "_signature": "a1b2c3..."
}
```

## Troubleshooting

### "Waiting for game..."

The client is ready but ACE isn't running. Start the game.

### "Log file not found"

1. Ensure ACE has been run at least once
2. Check the log path in Settings
3. Default location: `C:\Users\{username}\Saved Games\ACE\log.txt`

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

- Log parser tests with real game log data
- Telemetry decoder tests with real telemetry dumps
- Telemetry analyzer tests with real physics data
- API client integration tests
- Discord integration tests
- Security and signing tests

### Code Style

The project follows PEP 8 guidelines. Format with:
```bash
pip install black
black src/
```

### Version Management

The client version has a single source of truth: `src/version.py`.

- Update `VERSION_MAJOR`, `VERSION_MINOR`, and `VERSION_PATCH` in `src/version.py`.
- Packaging metadata in `pyproject.toml` is populated automatically via:
  - `[project] dynamic = ["version"]`
  - `[tool.setuptools.dynamic] version = {attr = "src.version.VERSION"}`
- Runtime aliases also resolve from the same source (`src.__version__` -> `src.version.VERSION`).

## Version

- Added telemetry capture and analysis
- Added game-reported lap boundary support
- Added track catalog with 20+ track profiles
- Added Discord notification support
- Added Personal Best cache
- Physics-only telemetry capture (graphics/static not supported)

## License

MIT License - see LICENSE file for details.

## Support

- Report issues on GitHub
- Join the SimLaps Discord community
