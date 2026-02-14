# ISS Next Pass Finder

Find the next visible ISS pass over your location from the command line.

## Features

- **Location lookup** -- use city names, addresses, or raw coordinates
- **Magnitude calculation** -- estimates ISS brightness for each pass
- **Twilight modes** -- civil, nautical, astronomical, or any (sunset)
- **Multiple output formats** -- simple text, ASCII table, JSON
- **Signal notifications** -- get a message before each visible pass via signal-cli
- **macOS launchd integration** -- schedule automatic pass checks every 15 minutes

## Requirements

- Python 3.8+
- Core dependencies (installed via `requirements.txt`):
  `requests`, `skyfield`, `sgp4`, `numpy`, `jplephem`
- Optional: `geopy` -- required for `--location` name lookup
- Optional: [signal-cli](https://github.com/AsamK/signal-cli) -- required for Signal notifications
- JPL ephemeris file `de421.bsp` (downloaded automatically on first run, or manually)

## Installation

```sh
git clone https://github.com/codingquark/iss_pass_api_cli.git
cd iss_pass_api_cli
pip install -r requirements.txt
```

Download the ephemeris file (≈17 MB, needed for sun position calculations):

```sh
python -c "from skyfield.api import load; load('de421.bsp')"
```

## Quick Start

```sh
# Auto-detect location from IP
python iss_next_pass.py

# By city name (requires geopy)
python iss_next_pass.py --location "New York City"

# By coordinates
python iss_next_pass.py --lat 40.7128 --lon -74.0060

# Multiple passes in table format
python iss_next_pass.py --location "London" --count 5 --format table

# JSON output, wider search window
python iss_next_pass.py --location "Tokyo" --days 7 --format json
```

## CLI Reference

### Location Options

| Flag | Description |
|------|-------------|
| `--location`, `-l` | Location name (city, address, landmark). Requires geopy. |
| `--lat` | Latitude of the observer (-90 to 90). |
| `--lon` | Longitude of the observer (-180 to 180). |

If neither is given, location is auto-detected from your IP address.

### Visibility Options

| Flag | Default | Description |
|------|---------|-------------|
| `--twilight` | `civil` | Sky darkness threshold: `civil` (-6 deg), `nautical` (-12 deg), `astronomical` (-18 deg), `any` (sunset). |
| `--min-magnitude` | `3.0` | Maximum apparent magnitude (lower = brighter). |
| `--altitude` | `10.0` | Minimum altitude above horizon in degrees. |

### Search Options

| Flag | Default | Description |
|------|---------|-------------|
| `--count`, `-n` | `1` | Number of passes to find. |
| `--days`, `-d` | `2` | Number of days to search ahead. |

### Output Options

| Flag | Default | Description |
|------|---------|-------------|
| `--format`, `-f` | `simple` | Output format: `simple`, `table`, or `json`. |
| `--verbose`, `-v` | off | Show detailed debug output. |

### Notification Options

| Flag | Description |
|------|-------------|
| `--setup-notify` | Interactive setup for Signal pass notifications. |
| `--check-notify` | Check for upcoming passes and send notification (for cron/launchd). |
| `--notify-config` | Path to notification config file (default: `~/.config/iss-pass-notify.json`). |
| `--install-launchd` | Install a macOS LaunchAgent to check every 15 minutes. |
| `--uninstall-launchd` | Remove the macOS LaunchAgent. |

## Notification Setup

1. Install and link [signal-cli](https://github.com/AsamK/signal-cli) to your Signal account.

2. Run the interactive setup:
   ```sh
   python iss_next_pass.py --setup-notify
   ```
   This prompts for your location, twilight preference, Signal sender/recipient
   numbers, and notification window. The config is saved to
   `~/.config/iss-pass-notify.json`.

3. Install the macOS LaunchAgent:
   ```sh
   python iss_next_pass.py --install-launchd
   ```
   This creates a plist at `~/Library/LaunchAgents/com.iss-pass-notify.checker.plist`
   that runs `--check-notify` every 15 minutes.

4. To remove the scheduled job:
   ```sh
   python iss_next_pass.py --uninstall-launchd
   ```

## Configuration Files

| File | Purpose |
|------|---------|
| `~/.config/iss-pass-notify.json` | Notification settings (location, Signal numbers, twilight, window). |
| `~/.local/share/iss-pass-notify/state.json` | Tracks which passes have already been notified (auto-pruned after 48 h). |
| `~/Library/LaunchAgents/com.iss-pass-notify.checker.plist` | macOS LaunchAgent definition (created by `--install-launchd`). |
| `de421.bsp` | JPL planetary ephemeris (sun positions). Downloaded to the script directory. |

## Output Formats

**Simple** (default):
```
Location: New York City
Coordinates: 40.7128 deg, -74.0060 deg

Date: 2025-03-15
Rise: 19:32:10 UTC (WSW, 248 deg)
Set:  19:38:45 UTC (NNE, 22 deg)
Duration: 395 seconds
Max Altitude: 67.3 deg
Brightness: -2.1 mag (peak)
```

**Table** (`--format table`):
```
+------------+----------+----------+----------+---------+---------+-----------+
|    Date    |   Rise   |   Set    | Duration | Max Alt |   Mag   | Direction |
+------------+----------+----------+----------+---------+---------+-----------+
| 2025-03-15 | 19:32:10 | 19:38:45 |     395s |  67 deg |    -2.1 |  WSW-NNE  |
+------------+----------+----------+----------+---------+---------+-----------+
```

**JSON** (`--format json`):
```json
{
  "location": {
    "description": "New York City",
    "latitude": 40.7128,
    "longitude": -74.006
  },
  "passes": [
    {
      "rise_time": "2025-03-15T19:32:10Z",
      "set_time": "2025-03-15T19:38:45Z",
      "duration_seconds": 395,
      "max_altitude_degrees": 67.3,
      "max_magnitude": -2.1
    }
  ]
}
```

## License

MIT -- see [LICENSE](LICENSE).
