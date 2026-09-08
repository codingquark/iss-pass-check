#!/usr/bin/env python3
"""
ISS Next Pass Finder

Find the next visible ISS pass over your location with support for:
- Location names (cities, addresses) or coordinates
- ISS magnitude calculation for visibility assessment
- Multiple output formats (table, JSON, simple)
- Configurable search parameters
"""

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Mapping, Optional, Tuple

import requests
from skyfield.api import EarthSatellite, load, wgs84

try:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

MIN_ALTITUDE_DEGREES = 10.0
DEFAULT_MAX_MAGNITUDE = 3.0
ISS_INTRINSIC_MAGNITUDE = -1.8
ISS_STANDARD_DISTANCE = 1000.0
INVISIBLE_MAGNITUDE = 99.0

TWILIGHT_THRESHOLDS = {
    'civil': -6.0, 'nautical': -12.0, 'astronomical': -18.0, 'any': 0.0,
}
DEFAULT_TWILIGHT = 'civil'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EPH_PATH = os.path.join(SCRIPT_DIR, 'de421.bsp')

NOTIFY_CONFIG_PATH = os.path.expanduser('~/.config/iss-pass-notify.json')
NOTIFY_STATE_PATH = os.path.expanduser('~/.local/share/iss-pass-notify/state.json')
NOTIFY_WINDOW_MINUTES = 30
LAUNCHD_LABEL = 'com.iss-pass-notify.checker'
LAUNCHD_PLIST_PATH = os.path.expanduser(f'~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist')


@dataclass
class PassInfo:
    """Information about a single ISS pass."""
    rise_time: datetime
    set_time: datetime
    max_altitude_time: datetime
    duration_seconds: float
    max_altitude_degrees: float
    rise_azimuth: float
    set_azimuth: float
    max_magnitude: float
    rise_magnitude: float
    set_magnitude: float
    is_visible: bool

    def to_dict(self) -> dict:
        return {
            "rise_time": self.rise_time.isoformat() + "Z",
            "set_time": self.set_time.isoformat() + "Z",
            "max_altitude_time": self.max_altitude_time.isoformat() + "Z",
            "duration_seconds": int(self.duration_seconds),
            "max_altitude_degrees": round(self.max_altitude_degrees, 1),
            "rise_azimuth": round(self.rise_azimuth, 1),
            "set_azimuth": round(self.set_azimuth, 1),
            "rise_direction": azimuth_to_direction(self.rise_azimuth),
            "set_direction": azimuth_to_direction(self.set_azimuth),
            "max_magnitude": self.max_magnitude,
            "rise_magnitude": self.rise_magnitude,
            "set_magnitude": self.set_magnitude,
        }


@dataclass
class NotifyConfig:
    """Configuration for ISS pass notifications."""
    lat: float
    lon: float
    location_desc: str = ""
    twilight: str = DEFAULT_TWILIGHT
    min_altitude: float = MIN_ALTITUDE_DEGREES
    max_magnitude: float = DEFAULT_MAX_MAGNITUDE
    signal_sender: str = ""
    signal_recipient: str = ""
    notify_window_minutes: int = NOTIFY_WINDOW_MINUTES

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'NotifyConfig':
        return cls(
            lat=float(data['lat']),
            lon=float(data['lon']),
            location_desc=str(data.get('location_desc') or ''),
            twilight=str(data.get('twilight') or DEFAULT_TWILIGHT),
            min_altitude=float(data.get('min_altitude', MIN_ALTITUDE_DEGREES)),
            max_magnitude=float(data.get('max_magnitude', DEFAULT_MAX_MAGNITUDE)),
            signal_sender=str(data.get('signal_sender') or ''),
            signal_recipient=str(data.get('signal_recipient') or ''),
            notify_window_minutes=int(data.get('notify_window_minutes', NOTIFY_WINDOW_MINUTES)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _write_json(path: str, data: dict) -> None:
    """Write a dict as JSON to path, creating parent dirs as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')


def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def load_notify_config(path: str = NOTIFY_CONFIG_PATH) -> Optional['NotifyConfig']:
    """Load notification config from JSON file. Returns None if missing or invalid."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return None
    data = _load_json(path)
    if data is None:
        return None
    try:
        return NotifyConfig.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


def save_notify_config(config: 'NotifyConfig', path: str = NOTIFY_CONFIG_PATH) -> None:
    """Save notification config to JSON file."""
    _write_json(os.path.expanduser(path), config.to_dict())


def load_notify_state(path: str = NOTIFY_STATE_PATH) -> dict:
    """Load notification state (notified pass IDs). Returns empty dict if missing."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return {}
    data = _load_json(path)
    return data or {}


def _parse_state_timestamp(key: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(key.rstrip('Z')).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def save_notify_state(state: dict, path: str = NOTIFY_STATE_PATH) -> None:
    """Save notification state, pruning entries older than 48 hours."""
    path = os.path.expanduser(path)
    refs = [ts for key in state.keys() if (ts := _parse_state_timestamp(key)) is not None]
    ref_time = max(refs) if refs else datetime.now(timezone.utc)
    cutoff = ref_time - timedelta(hours=48)
    pruned = {
        key: val
        for key, val in state.items()
        if (ts := _parse_state_timestamp(key)) is not None and ts > cutoff
    }
    _write_json(path, pruned)


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Find the next visible ISS pass over your location.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --location "New York City"
  %(prog)s --lat 40.7128 --lon -74.0060
  %(prog)s --location "London" --count 5 --format table
  %(prog)s --location "Tokyo" --twilight nautical --days 7
  %(prog)s --location "Mumbai" --twilight any --count 10 --format json

Notification examples:
  %(prog)s --setup-notify
  %(prog)s --check-notify
  %(prog)s --install-launchd
        """
    )

    location_group = parser.add_argument_group('Location Options')
    location_group.add_argument('--location', '-l', type=str,
        help='Location name (city, address, landmark). Requires geopy library.')
    location_group.add_argument('--lat', type=float,
        help='Latitude of the observer (-90 to 90).')
    location_group.add_argument('--lon', type=float,
        help='Longitude of the observer (-180 to 180).')

    visibility_group = parser.add_argument_group('Visibility Options')
    visibility_group.add_argument('--twilight', type=str, default=DEFAULT_TWILIGHT,
        choices=list(TWILIGHT_THRESHOLDS.keys()),
        help=f'When sky is dark enough to see ISS. Options: '
             f'civil (-6 deg, dusk/dawn), nautical (-12 deg, darker), '
             f'astronomical (-18 deg, fully dark), any (sunset). Default: {DEFAULT_TWILIGHT}.')
    visibility_group.add_argument('--min-magnitude', type=float, default=DEFAULT_MAX_MAGNITUDE,
        help=f'Maximum magnitude for visibility (lower=brighter, default: {DEFAULT_MAX_MAGNITUDE}).')
    visibility_group.add_argument('--altitude', type=float, default=MIN_ALTITUDE_DEGREES,
        help=f'Minimum altitude above horizon in degrees (default: {MIN_ALTITUDE_DEGREES} deg).')

    search_group = parser.add_argument_group('Search Options')
    search_group.add_argument('--count', '-n', type=int, default=1,
        help='Number of passes to find (default: 1).')
    search_group.add_argument('--days', '-d', type=int, default=2,
        help='Number of days to search ahead (default: 2).')

    output_group = parser.add_argument_group('Output Options')
    output_group.add_argument('--format', '-f', choices=['simple', 'table', 'json'],
        default='simple', help='Output format (default: simple).')
    output_group.add_argument('--verbose', '-v', action='store_true',
        help='Show detailed information including debug output.')

    notify_group = parser.add_argument_group('Notification Options')
    notify_group.add_argument('--check-notify', action='store_true',
        help='Check for upcoming passes and send notification (for cron).')
    notify_group.add_argument('--setup-notify', action='store_true',
        help='Interactive setup for pass notifications.')
    notify_group.add_argument('--notify-config', type=str, default=NOTIFY_CONFIG_PATH,
        help=f'Path to notification config file (default: {NOTIFY_CONFIG_PATH}).')
    notify_group.add_argument('--install-launchd', action='store_true',
        help='Install a macOS LaunchAgent to check for passes every 15 minutes.')
    notify_group.add_argument('--uninstall-launchd', action='store_true',
        help='Uninstall the macOS LaunchAgent.')

    return parser.parse_args()


def validate_coordinates(lat: float, lon: float) -> Tuple[bool, str]:
    """Validate latitude and longitude values."""
    if not -90 <= lat <= 90:
        return False, f"Latitude must be between -90 and 90 degrees (got {lat})."
    if not -180 <= lon <= 180:
        return False, f"Longitude must be between -180 and 180 degrees (got {lon})."
    return True, ""


def _is_valid_tle_lines(line1: Optional[str], line2: Optional[str]) -> bool:
    """Validate that two lines look like a TLE pair."""
    return bool(line1 and line2 and line1.startswith('1 ') and line2.startswith('2 '))


def geocode_location(location_name: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Convert a location name to coordinates. Returns (lat, lon, display_name) or (None, None, None)."""
    if not GEOPY_AVAILABLE:
        print("Error: geopy library is not installed.")
        print("Install it with: pip install geopy")
        print("Or use --lat and --lon to specify coordinates directly.")
        return None, None, None

    try:
        geolocator = Nominatim(user_agent="iss_pass_finder/1.0")
        location = geolocator.geocode(location_name, timeout=10)
        if location:
            return location.latitude, location.longitude, location.address
        print(f"Error: Could not find location '{location_name}'.")
        print("Try a more specific location name or use coordinates directly.")
    except (GeocoderTimedOut, GeocoderServiceError):
        print("Error: Geocoding service timed out. Please try again.")
    except Exception as e:
        print(f"Error geocoding location: {e}")
    return None, None, None


def get_location_from_ip() -> Tuple[float, float]:
    """Retrieve the user's location based on IP address. Exits on failure."""
    try:
        response = requests.get('https://ipinfo.io/json', timeout=10)
        response.raise_for_status()
        data = response.json()
        loc = data['loc'].split(',')
        return float(loc[0]), float(loc[1])
    except requests.exceptions.Timeout:
        print("Error: Location service timed out. Please try again or specify location manually.")
    except requests.exceptions.RequestException as e:
        print(f"Error retrieving location from IP: {e}")
        print("Please specify your location using --location or --lat/--lon.")
    except (KeyError, ValueError, IndexError) as e:
        print(f"Error parsing location data: {e}")
    sys.exit(1)


def get_location(args: argparse.Namespace) -> Tuple[float, float, str]:
    """Get location from arguments or auto-detect."""
    if args.location:
        lat, lon, address = geocode_location(args.location)
        if lat is None:
            sys.exit(1)
        return lat, lon, address or args.location

    if args.lat is not None and args.lon is not None:
        is_valid, error_msg = validate_coordinates(args.lat, args.lon)
        if not is_valid:
            print(f"Error: {error_msg}")
            sys.exit(1)
        return args.lat, args.lon, f"Coordinates ({args.lat}, {args.lon})"

    if args.lat is not None or args.lon is not None:
        print("Error: Both --lat and --lon must be specified together.")
        sys.exit(1)

    lat, lon = get_location_from_ip()
    return lat, lon, "Auto-detected from IP"


def get_iss_tle() -> List[str]:
    """Fetch the latest TLE data for the ISS from multiple sources."""
    sources = [
        ('CelesTrak', 'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE', 'text'),
        ('wheretheiss.at', 'https://api.wheretheiss.at/v1/satellites/25544/tles', 'json'),
    ]
    last_error = None

    for name, url, fmt in sources:
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()

            if fmt == 'json':
                tle_data = response.json()
                line1, line2 = tle_data.get('line1'), tle_data.get('line2')
            else:
                lines = response.text.strip().split('\n')
                if len(lines) < 2:
                    continue
                start = 1 if len(lines) >= 3 else 0
                line1, line2 = lines[start].strip(), lines[start + 1].strip()

            if _is_valid_tle_lines(line1, line2):
                return [line1, line2]
        except Exception as e:
            last_error = f"{name}: {e}"

    print("Error: Could not retrieve ISS TLE data from any source.")
    if last_error:
        print(f"Last error: {last_error}")
    print("Check your internet connection and try again.")
    sys.exit(1)


def calculate_iss_magnitude(distance_km: float, phase_angle_deg: float) -> float:
    """Calculate apparent magnitude of the ISS based on distance and phase angle."""
    distance_factor = 5.0 * math.log10(distance_km / ISS_STANDARD_DISTANCE) if distance_km > 0 else 0
    phase_correction = -2.5 * math.log10(max(0.01, (1 + math.cos(math.radians(phase_angle_deg))) / 2))
    return round(ISS_INTRINSIC_MAGNITUDE + distance_factor + phase_correction, 1)


def find_visible_passes(
    lat: float, lon: float, tle: List[str],
    twilight_threshold: float, min_altitude: float, max_magnitude: float,
    days: int, count: int, verbose: bool = False
) -> List[PassInfo]:
    """Find visible ISS passes over the given location."""
    ts = load.timescale()
    satellite = EarthSatellite(tle[0], tle[1], 'ISS (ZARYA)', ts)
    observer = wgs84.latlon(lat, lon)
    t0 = ts.now()
    t1 = ts.utc(t0.utc_datetime() + timedelta(days=days))

    eph = load(EPH_PATH)
    sun, earth = eph['sun'], eph['earth']
    obs_pos = earth + observer
    passes = []

    def iss_info(t) -> Tuple[float, float, float, float]:
        """Get ISS altitude, azimuth, distance, and phase angle at time t."""
        topocentric = (satellite - observer).at(t)
        alt, az, distance = topocentric.altaz()
        sun_vector = obs_pos.at(t).observe(sun).apparent().position.km
        iss_vector = topocentric.position.km
        dot = sum(a * b for a, b in zip(iss_vector, sun_vector))
        mag_iss = math.sqrt(sum(x**2 for x in iss_vector))
        mag_sun = math.sqrt(sum(x**2 for x in sun_vector))
        if mag_iss > 0 and mag_sun > 0:
            cos_phase = max(-1, min(1, dot / (mag_iss * mag_sun)))
            phase_angle = math.degrees(math.acos(cos_phase))
        else:
            phase_angle = 90.0
        return alt.degrees, az.degrees, distance.km, phase_angle

    def visibility(t) -> Tuple[bool, float]:
        """Check if ISS is visible and return magnitude."""
        if not satellite.at(t).is_sunlit(eph):
            return False, INVISIBLE_MAGNITUDE
        if obs_pos.at(t).observe(sun).apparent().altaz()[0].degrees >= twilight_threshold:
            return False, INVISIBLE_MAGNITUDE
        _, _, distance, phase_angle = iss_info(t)
        magnitude = calculate_iss_magnitude(distance, phase_angle)
        return magnitude <= max_magnitude, magnitude

    t_events, events = satellite.find_events(observer, t0, t1, altitude_degrees=min_altitude)

    if verbose:
        print(f"Found {len(events)} events in {days} day search window")

    i = 0
    while i < len(events) and len(passes) < count:
        if events[i] == 0:  # Rise event
            rise_time = t_events[i]
            _, rise_az, _, _ = iss_info(rise_time)

            max_time, max_alt, set_time = None, 0, None
            for j in range(i + 1, len(events)):
                if events[j] == 1:
                    max_time = t_events[j]
                    max_alt, _, _, _ = iss_info(max_time)
                elif events[j] == 2:
                    set_time = t_events[j]
                    break

            if set_time is not None:
                _, set_az, _, _ = iss_info(set_time)
                check_times = [rise_time] + ([max_time] if max_time is not None else []) + [set_time]
                results = [visibility(t) for t in check_times]
                best_magnitude = min((mag for vis, mag in results if vis), default=INVISIBLE_MAGNITUDE)

                if best_magnitude < INVISIBLE_MAGNITUDE:
                    _, rise_mag = visibility(rise_time)
                    _, set_mag = visibility(set_time)
                    duration = (set_time.utc_datetime() - rise_time.utc_datetime()).total_seconds()

                    passes.append(PassInfo(
                        rise_time=rise_time.utc_datetime(),
                        set_time=set_time.utc_datetime(),
                        max_altitude_time=(max_time if max_time is not None else rise_time).utc_datetime(),
                        duration_seconds=duration,
                        max_altitude_degrees=max_alt,
                        rise_azimuth=rise_az,
                        set_azimuth=set_az,
                        max_magnitude=best_magnitude,
                        rise_magnitude=rise_mag if rise_mag < INVISIBLE_MAGNITUDE else best_magnitude,
                        set_magnitude=set_mag if set_mag < INVISIBLE_MAGNITUDE else best_magnitude,
                        is_visible=True,
                    ))

                    if verbose:
                        print(f"Found visible pass: {passes[-1].rise_time} (mag: {best_magnitude})")
        i += 1

    return passes


def azimuth_to_direction(azimuth: float) -> str:
    """Convert azimuth in degrees to compass direction."""
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    return directions[round(azimuth / 22.5) % 16]


def _location_header(location_desc: str, lat: float, lon: float) -> List[str]:
    """Return the standard location header lines."""
    return [f"Location: {location_desc}", f"Coordinates: {lat:.4f} deg, {lon:.4f} deg", ""]


def format_simple(passes: List[PassInfo], location_desc: str, lat: float, lon: float) -> str:
    """Format passes in simple text format."""
    lines = _location_header(location_desc, lat, lon)
    if not passes:
        lines.append("No visible ISS passes found in the search window.")
        return "\n".join(lines)

    for i, p in enumerate(passes, 1):
        if len(passes) > 1:
            lines.append(f"--- Pass {i} of {len(passes)} ---")
        lines += [
            f"Date: {p.rise_time.strftime('%Y-%m-%d')}",
            f"Rise: {p.rise_time.strftime('%H:%M:%S UTC')} ({azimuth_to_direction(p.rise_azimuth)}, {p.rise_azimuth:.0f} deg)",
            f"Set:  {p.set_time.strftime('%H:%M:%S UTC')} ({azimuth_to_direction(p.set_azimuth)}, {p.set_azimuth:.0f} deg)",
            f"Duration: {int(p.duration_seconds)} seconds",
            f"Max Altitude: {p.max_altitude_degrees:.1f} deg",
            f"Brightness: {p.max_magnitude:+.1f} mag (peak)",
            "",
        ]
    return "\n".join(lines)


def format_table(passes: List[PassInfo], location_desc: str, lat: float, lon: float) -> str:
    """Format passes in a table format."""
    lines = _location_header(location_desc, lat, lon)
    if not passes:
        lines.append("No visible ISS passes found in the search window.")
        return "\n".join(lines)

    sep = "+------------+----------+----------+----------+---------+---------+-----------+"
    titles = "|    Date    |   Rise   |   Set    | Duration | Max Alt |   Mag   | Direction |"
    lines += [sep, titles, sep]

    for p in passes:
        date = p.rise_time.strftime('%Y-%m-%d')
        rise = p.rise_time.strftime('%H:%M:%S')
        set_t = p.set_time.strftime('%H:%M:%S')
        duration = f"{int(p.duration_seconds)}s"
        max_alt = f"{p.max_altitude_degrees:.0f} deg"
        mag = f"{p.max_magnitude:+.1f}"
        direction = f"{azimuth_to_direction(p.rise_azimuth)}-{azimuth_to_direction(p.set_azimuth)}"
        lines.append(f"| {date} | {rise} | {set_t} | {duration:>8} | {max_alt:>7} | {mag:>7} | {direction:^9} |")

    lines += [sep, "", "Mag = Apparent magnitude (lower/negative is brighter)"]
    return "\n".join(lines)


def format_json(passes: List[PassInfo], location_desc: str, lat: float, lon: float) -> str:
    """Format passes as JSON."""
    data = {
        "location": {"description": location_desc, "latitude": lat, "longitude": lon},
        "passes": [p.to_dict() for p in passes],
    }
    return json.dumps(data, indent=2)


def format_notification_message(p: PassInfo) -> str:
    """Format a PassInfo into a short notification message."""
    now = datetime.now(timezone.utc)
    minutes_until = int((p.rise_time - now).total_seconds() / 60)
    ist = timezone(timedelta(hours=5, minutes=30))
    rise_str = p.rise_time.replace(tzinfo=timezone.utc).astimezone(ist).strftime('%H:%M IST')
    return (
        f"ISS visible in ~{minutes_until} min ({rise_str})\n"
        f"Duration: {int(p.duration_seconds / 60)} min | Max alt: {p.max_altitude_degrees:.0f} deg | "
        f"Brightness: {p.max_magnitude:+.1f} mag\n"
        f"Direction: {azimuth_to_direction(p.rise_azimuth)} -> {azimuth_to_direction(p.set_azimuth)}"
    )


def send_signal_notification(message: str, config: NotifyConfig) -> bool:
    """Send a notification via signal-cli. Returns True on success, never raises."""
    try:
        result = subprocess.run(
            ["signal-cli", "-u", config.signal_sender, "send",
             "-m", message, config.signal_recipient],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def send_notification(message: str, config: NotifyConfig) -> bool:
    """Dispatch notification via configured backend (signal for now)."""
    return send_signal_notification(message, config)


def _pass_state_key(rise_time: datetime) -> str:
    """Create a minute-precision ISO key for dedup state."""
    dt = rise_time.replace(second=0, microsecond=0, tzinfo=None)
    return dt.isoformat() + 'Z'


def _require_notify_config(config_path: str) -> NotifyConfig:
    config = load_notify_config(config_path)
    if config is None:
        print("Error: No notification config found. Run --setup-notify first.")
        sys.exit(1)
    return config


def run_check_notify(config_path: str = NOTIFY_CONFIG_PATH) -> None:
    """Check for upcoming passes and send a notification if one is imminent."""
    config = _require_notify_config(config_path)

    state = load_notify_state(NOTIFY_STATE_PATH)
    tle = get_iss_tle()
    twilight_threshold = TWILIGHT_THRESHOLDS.get(config.twilight, TWILIGHT_THRESHOLDS[DEFAULT_TWILIGHT])
    search_days = max(1, (config.notify_window_minutes // (24 * 60)) + 1)
    passes = find_visible_passes(
        lat=config.lat, lon=config.lon, tle=tle,
        twilight_threshold=twilight_threshold,
        min_altitude=config.min_altitude, max_magnitude=config.max_magnitude,
        days=search_days, count=5,
    )

    now = datetime.now(timezone.utc)
    window = config.notify_window_minutes

    for p in passes:
        minutes_until = (p.rise_time - now).total_seconds() / 60
        if not (0 <= minutes_until <= window):
            continue

        key = _pass_state_key(p.rise_time)
        if key in state:
            continue

        message = format_notification_message(p)
        if send_notification(message, config):
            state[key] = {'notified_at': now.isoformat() + 'Z'}
            save_notify_state(state, NOTIFY_STATE_PATH)
            print(f"Notification sent for pass at {p.rise_time.strftime('%H:%M UTC')}")
        else:
            print(f"Failed to send notification for pass at {p.rise_time.strftime('%H:%M UTC')}")
        break
    else:
        print("No upcoming passes in notification window.")


def run_setup_notify(config_path: str = NOTIFY_CONFIG_PATH) -> None:
    """Interactive setup for notification config."""
    print("ISS Pass Notification Setup")
    print("=" * 40)
    print()

    location_input = input("Location name (e.g. 'Ahmedabad') or leave blank for coords: ").strip()
    if location_input:
        lat_val, lon_val, address = geocode_location(location_input)
        if lat_val is None:
            sys.exit(1)
        location_desc = address or location_input
    else:
        try:
            lat_val = float(input("Latitude: ").strip())
            lon_val = float(input("Longitude: ").strip())
        except ValueError:
            print("Error: Invalid coordinate.")
            sys.exit(1)
        is_valid, err = validate_coordinates(lat_val, lon_val)
        if not is_valid:
            print(f"Error: {err}")
            sys.exit(1)
        location_desc = f"Coordinates ({lat_val}, {lon_val})"

    twilight = input(f"Twilight setting [{DEFAULT_TWILIGHT}] (civil/nautical/astronomical/any): ").strip()
    if not twilight:
        twilight = DEFAULT_TWILIGHT
    if twilight not in TWILIGHT_THRESHOLDS:
        print(f"Error: Invalid twilight '{twilight}'.")
        sys.exit(1)

    print("\nSignal Messenger Configuration")
    print("(Requires signal-cli to be installed and linked)")
    signal_sender = input("Your Signal phone number (sender, e.g. +1234567890): ").strip()
    signal_recipient = input("Recipient phone number (e.g. +0987654321): ").strip()

    if not signal_sender or not signal_recipient:
        print("Error: Both sender and recipient numbers are required.")
        sys.exit(1)

    window_input = input(f"Notify how many minutes before pass? [{NOTIFY_WINDOW_MINUTES}]: ").strip()
    try:
        notify_window = int(window_input) if window_input else NOTIFY_WINDOW_MINUTES
    except ValueError:
        print("Error: Invalid number.")
        sys.exit(1)

    config = NotifyConfig(
        lat=lat_val, lon=lon_val, location_desc=location_desc,
        twilight=twilight, signal_sender=signal_sender,
        signal_recipient=signal_recipient, notify_window_minutes=notify_window,
    )
    save_notify_config(config, config_path)

    print(f"\nConfig saved to: {config_path}\n")
    print("Next step — install the LaunchAgent to check every 15 minutes:")
    print(f"  python3 {os.path.abspath(__file__)} --install-launchd")
    print()


def _generate_plist(config_path: str) -> str:
    """Generate launchd plist XML content."""
    import plistlib
    log_dir = os.path.expanduser('~/.local/share/iss-pass-notify')
    return plistlib.dumps({
        'Label': LAUNCHD_LABEL,
        'ProgramArguments': [sys.executable, os.path.abspath(__file__),
                             '--check-notify', '--notify-config', config_path],
        'StartInterval': 900,
        'StandardOutPath': os.path.join(log_dir, 'launchd.log'),
        'StandardErrorPath': os.path.join(log_dir, 'launchd.err'),
        'RunAtLoad': False,
    }).decode('utf-8')


def install_launchd(config_path: str = NOTIFY_CONFIG_PATH) -> None:
    """Install a macOS LaunchAgent for periodic pass checks."""
    _require_notify_config(config_path)

    log_dir = os.path.expanduser('~/.local/share/iss-pass-notify')
    os.makedirs(log_dir, exist_ok=True)

    if os.path.exists(LAUNCHD_PLIST_PATH):
        subprocess.run(['launchctl', 'unload', LAUNCHD_PLIST_PATH], capture_output=True)

    plist_content = _generate_plist(config_path)
    os.makedirs(os.path.dirname(LAUNCHD_PLIST_PATH), exist_ok=True)
    with open(LAUNCHD_PLIST_PATH, 'w') as f:
        f.write(plist_content)

    result = subprocess.run(['launchctl', 'load', LAUNCHD_PLIST_PATH],
                             capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error loading LaunchAgent: {result.stderr.strip()}")
        sys.exit(1)

    print("LaunchAgent installed and loaded.")
    print(f"  Plist: {LAUNCHD_PLIST_PATH}")
    print(f"  Logs:  {log_dir}/launchd.log")
    print("  Runs every 15 minutes. To check manually:")
    print(f"    python3 {os.path.abspath(__file__)} --check-notify")
    print("\nTo uninstall:")
    print(f"  python3 {os.path.abspath(__file__)} --uninstall-launchd")


def uninstall_launchd() -> None:
    """Uninstall the macOS LaunchAgent."""
    if not os.path.exists(LAUNCHD_PLIST_PATH):
        print("LaunchAgent is not installed.")
        return
    subprocess.run(['launchctl', 'unload', LAUNCHD_PLIST_PATH], capture_output=True)
    os.remove(LAUNCHD_PLIST_PATH)
    print("LaunchAgent unloaded and removed.")


def main():
    """Main function that orchestrates ISS pass finding and display."""
    args = parse_arguments()

    if args.setup_notify:
        return run_setup_notify(args.notify_config)
    if args.check_notify:
        return run_check_notify(args.notify_config)
    if args.install_launchd:
        return install_launchd(args.notify_config)
    if args.uninstall_launchd:
        return uninstall_launchd()

    lat, lon, location_desc = get_location(args)
    twilight_threshold = TWILIGHT_THRESHOLDS[args.twilight]

    if args.verbose:
        print(f"Location: {location_desc}")
        print(f"Coordinates: {lat:.4f} deg, {lon:.4f} deg")
        print(f"Twilight: {args.twilight} ({twilight_threshold} deg)")
        print(f"Min altitude: {args.altitude} deg")
        print(f"Max magnitude: {args.min_magnitude}")
        print(f"Search window: {args.days} days")
        print(f"Looking for {args.count} pass(es)...")
        print()

    tle = get_iss_tle()
    passes = find_visible_passes(
        lat=lat, lon=lon, tle=tle,
        twilight_threshold=twilight_threshold,
        min_altitude=args.altitude, max_magnitude=args.min_magnitude,
        days=args.days, count=args.count, verbose=args.verbose,
    )

    formatters = {'json': format_json, 'table': format_table, 'simple': format_simple}
    print(formatters[args.format](passes, location_desc, lat, lon))


if __name__ == "__main__":
    main()
