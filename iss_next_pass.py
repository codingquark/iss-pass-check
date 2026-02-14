#!/usr/bin/env python3
"""
ISS Next Pass Finder

Find the next visible ISS pass over your location with support for:
- Location names (cities, addresses) or coordinates
- ISS magnitude calculation for visibility assessment
- Multiple output formats (table, JSON, simple)
- Configurable search parameters
"""

import requests
from skyfield.api import load, EarthSatellite, wgs84
from datetime import datetime, timedelta, timezone
import sys
import os
import subprocess
import argparse
import json
import math
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass, field

# Try to import geopy for geocoding support
try:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False


# Constants
MIN_ALTITUDE_DEGREES = 10.0
DEFAULT_MAX_MAGNITUDE = 3.0  # Visible to naked eye
ISS_INTRINSIC_MAGNITUDE = -1.8  # Base magnitude at 1000km, phase angle 90deg
ISS_STANDARD_DISTANCE = 1000.0  # km

# Twilight thresholds (sun altitude below horizon)
# These are fixed angles - seasons only change WHEN these occur, not the angles
TWILIGHT_THRESHOLDS = {
    'civil': -6.0,        # Horizon still visible, bright sky
    'nautical': -12.0,    # Horizon barely visible at sea
    'astronomical': -18.0, # Sky fully dark for astronomy
    'any': 0.0            # Any time sun is below horizon (includes dusk/dawn)
}
DEFAULT_TWILIGHT = 'civil'  # Good default for casual ISS viewing

# Ephemeris path (absolute so cron works from any cwd)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EPH_PATH = os.path.join(SCRIPT_DIR, 'de421.bsp')

# Notification paths and defaults
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
    max_magnitude: float  # Lower is brighter
    rise_magnitude: float
    set_magnitude: float
    is_visible: bool


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


def load_notify_config(path: str = NOTIFY_CONFIG_PATH) -> Optional['NotifyConfig']:
    """Load notification config from JSON file. Returns None if missing or invalid."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        return NotifyConfig(
            lat=float(data['lat']),
            lon=float(data['lon']),
            location_desc=data.get('location_desc', ''),
            twilight=data.get('twilight', DEFAULT_TWILIGHT),
            min_altitude=float(data.get('min_altitude', MIN_ALTITUDE_DEGREES)),
            max_magnitude=float(data.get('max_magnitude', DEFAULT_MAX_MAGNITUDE)),
            signal_sender=data.get('signal_sender', ''),
            signal_recipient=data.get('signal_recipient', ''),
            notify_window_minutes=int(data.get('notify_window_minutes', NOTIFY_WINDOW_MINUTES)),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_notify_config(config: 'NotifyConfig', path: str = NOTIFY_CONFIG_PATH) -> None:
    """Save notification config to JSON file."""
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        'lat': config.lat,
        'lon': config.lon,
        'location_desc': config.location_desc,
        'twilight': config.twilight,
        'min_altitude': config.min_altitude,
        'max_magnitude': config.max_magnitude,
        'signal_sender': config.signal_sender,
        'signal_recipient': config.signal_recipient,
        'notify_window_minutes': config.notify_window_minutes,
    }
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')


def load_notify_state(path: str = NOTIFY_STATE_PATH) -> dict:
    """Load notification state (notified pass IDs). Returns empty dict if missing."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_notify_state(state: dict, path: str = NOTIFY_STATE_PATH) -> None:
    """Save notification state, pruning entries older than 48 hours."""
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    pruned = {}
    for key, val in state.items():
        try:
            ts = datetime.fromisoformat(key.rstrip('Z')).replace(tzinfo=timezone.utc)
            if ts > cutoff:
                pruned[key] = val
        except (ValueError, TypeError):
            pass
    with open(path, 'w') as f:
        json.dump(pruned, f, indent=2)
        f.write('\n')


def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments with enhanced options.
    
    Returns:
        Parsed arguments namespace.
    """
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
    
    # Location options (mutually exclusive group for clarity)
    location_group = parser.add_argument_group('Location Options')
    location_group.add_argument(
        '--location', '-l', type=str,
        help='Location name (city, address, landmark). Requires geopy library.'
    )
    location_group.add_argument(
        '--lat', type=float,
        help='Latitude of the observer (-90 to 90).'
    )
    location_group.add_argument(
        '--lon', type=float,
        help='Longitude of the observer (-180 to 180).'
    )
    
    # Visibility options
    visibility_group = parser.add_argument_group('Visibility Options')
    visibility_group.add_argument(
        '--twilight', type=str, default=DEFAULT_TWILIGHT,
        choices=list(TWILIGHT_THRESHOLDS.keys()),
        help=f'When sky is dark enough to see ISS. Options: '
             f'civil (-6 deg, dusk/dawn), nautical (-12 deg, darker), '
             f'astronomical (-18 deg, fully dark), any (sunset). Default: {DEFAULT_TWILIGHT}.'
    )
    visibility_group.add_argument(
        '--min-magnitude', type=float, default=DEFAULT_MAX_MAGNITUDE,
        help=f'Maximum magnitude for visibility (lower=brighter, default: {DEFAULT_MAX_MAGNITUDE}).'
    )
    visibility_group.add_argument(
        '--altitude', type=float, default=MIN_ALTITUDE_DEGREES,
        help=f'Minimum altitude above horizon in degrees (default: {MIN_ALTITUDE_DEGREES} deg).'
    )
    
    # Search options
    search_group = parser.add_argument_group('Search Options')
    search_group.add_argument(
        '--count', '-n', type=int, default=1,
        help='Number of passes to find (default: 1).'
    )
    search_group.add_argument(
        '--days', '-d', type=int, default=2,
        help='Number of days to search ahead (default: 2).'
    )
    
    # Output options
    output_group = parser.add_argument_group('Output Options')
    output_group.add_argument(
        '--format', '-f', choices=['simple', 'table', 'json'], default='simple',
        help='Output format (default: simple).'
    )
    output_group.add_argument(
        '--verbose', '-v', action='store_true',
        help='Show detailed information including debug output.'
    )

    # Notification options
    notify_group = parser.add_argument_group('Notification Options')
    notify_group.add_argument(
        '--check-notify', action='store_true',
        help='Check for upcoming passes and send notification (for cron).'
    )
    notify_group.add_argument(
        '--setup-notify', action='store_true',
        help='Interactive setup for pass notifications.'
    )
    notify_group.add_argument(
        '--notify-config', type=str, default=NOTIFY_CONFIG_PATH,
        help=f'Path to notification config file (default: {NOTIFY_CONFIG_PATH}).'
    )
    notify_group.add_argument(
        '--install-launchd', action='store_true',
        help='Install a macOS LaunchAgent to check for passes every 15 minutes.'
    )
    notify_group.add_argument(
        '--uninstall-launchd', action='store_true',
        help='Uninstall the macOS LaunchAgent.'
    )

    return parser.parse_args()


def validate_coordinates(lat: float, lon: float) -> Tuple[bool, str]:
    """
    Validate latitude and longitude values.
    
    Args:
        lat: Latitude value to validate.
        lon: Longitude value to validate.
    
    Returns:
        Tuple of (is_valid, error_message).
    """
    if lat < -90 or lat > 90:
        return False, f"Latitude must be between -90 and 90 degrees (got {lat})."
    if lon < -180 or lon > 180:
        return False, f"Longitude must be between -180 and 180 degrees (got {lon})."
    return True, ""


def geocode_location(location_name: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Convert a location name to coordinates using OpenStreetMap's Nominatim service.
    
    Args:
        location_name: Name of the location (city, address, landmark).
    
    Returns:
        Tuple of (latitude, longitude, display_name) or (None, None, None) on failure.
    """
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
        else:
            print(f"Error: Could not find location '{location_name}'.")
            print("Try a more specific location name or use coordinates directly.")
            return None, None, None
            
    except GeocoderTimedOut:
        print("Error: Geocoding service timed out. Please try again.")
        return None, None, None
    except GeocoderServiceError as e:
        print(f"Error: Geocoding service error: {e}")
        return None, None, None
    except Exception as e:
        print(f"Error geocoding location: {e}")
        return None, None, None


def get_location_from_ip() -> Tuple[float, float]:
    """
    Retrieve the user's current geographic location based on IP address.
    
    Returns:
        Tuple of (latitude, longitude).
    
    Raises:
        SystemExit: If location cannot be retrieved.
    """
    try:
        response = requests.get('https://ipinfo.io/json', timeout=10)
        response.raise_for_status()
        data = response.json()
        loc = data['loc'].split(',')
        lat, lon = float(loc[0]), float(loc[1])
        return lat, lon
    except requests.exceptions.Timeout:
        print("Error: Location service timed out. Please try again or specify location manually.")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Error retrieving location from IP: {e}")
        print("Please specify your location using --location or --lat/--lon.")
        sys.exit(1)
    except (KeyError, ValueError, IndexError) as e:
        print(f"Error parsing location data: {e}")
        sys.exit(1)


def get_location(args: argparse.Namespace) -> Tuple[float, float, str]:
    """
    Get location from arguments or auto-detect.
    
    Args:
        args: Parsed command-line arguments.
    
    Returns:
        Tuple of (latitude, longitude, location_description).
    """
    # Priority: --location > --lat/--lon > IP-based
    
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
    
    # Auto-detect from IP
    lat, lon = get_location_from_ip()
    return lat, lon, "Auto-detected from IP"


def get_iss_tle() -> List[str]:
    """
    Fetch the latest TLE data for the ISS from multiple sources.
    
    Tries CelesTrak first (more reliable), falls back to wheretheiss.at.
    
    Returns:
        List containing the two TLE lines.
    
    Raises:
        SystemExit: If TLE data cannot be retrieved from any source.
    """
    # TLE sources to try in order
    sources = [
        {
            'name': 'CelesTrak',
            'url': 'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE',
            'parser': 'text'
        },
        {
            'name': 'wheretheiss.at',
            'url': 'https://api.wheretheiss.at/v1/satellites/25544/tles',
            'parser': 'json'
        }
    ]
    
    last_error = None
    
    for source in sources:
        try:
            response = requests.get(source['url'], timeout=15)
            response.raise_for_status()
            
            if source['parser'] == 'json':
                tle_data = response.json()
                line1 = tle_data.get('line1')
                line2 = tle_data.get('line2')
            else:  # text format (CelesTrak)
                lines = response.text.strip().split('\n')
                if len(lines) >= 3:
                    # Format: name, line1, line2
                    line1 = lines[1].strip()
                    line2 = lines[2].strip()
                elif len(lines) >= 2:
                    line1 = lines[0].strip()
                    line2 = lines[1].strip()
                else:
                    continue
            
            if line1 and line2 and line1.startswith('1 ') and line2.startswith('2 '):
                return [line1, line2]
                
        except requests.exceptions.Timeout:
            last_error = f"{source['name']} timed out"
        except requests.exceptions.RequestException as e:
            last_error = f"{source['name']}: {e}"
        except Exception as e:
            last_error = f"{source['name']}: {e}"
    
    print(f"Error: Could not retrieve ISS TLE data from any source.")
    if last_error:
        print(f"Last error: {last_error}")
    print("Check your internet connection and try again.")
    sys.exit(1)


def calculate_iss_magnitude(distance_km: float, phase_angle_deg: float) -> float:
    """
    Calculate the apparent magnitude of the ISS.
    
    The ISS magnitude depends on:
    - Distance from observer
    - Phase angle (sun-satellite-observer angle)
    - Solar panel orientation (approximated)
    
    Args:
        distance_km: Distance from observer to ISS in kilometers.
        phase_angle_deg: Phase angle in degrees (0 deg = fully illuminated, 180 deg = backlit).
    
    Returns:
        Apparent magnitude (lower is brighter, negative is very bright).
    """
    # Base magnitude adjustment for distance
    # Magnitude changes by 5 for every factor of 100 in brightness
    # Brightness falls off as 1/r^2 so magnitude changes as 5*log10(r/r_std)
    distance_factor = 5.0 * math.log10(distance_km / ISS_STANDARD_DISTANCE) if distance_km > 0 else 0
    
    # Phase angle correction
    # At 0 deg (full illumination), ISS is brightest
    # At 90 deg, use base value
    # At 180 deg (backlit), much dimmer
    phase_rad = math.radians(phase_angle_deg)
    # Simplified phase function - realistic for diffuse + specular reflection
    phase_correction = -2.5 * math.log10(max(0.01, (1 + math.cos(phase_rad)) / 2))
    
    magnitude = ISS_INTRINSIC_MAGNITUDE + distance_factor + phase_correction
    
    return round(magnitude, 1)


def find_visible_passes(
    lat: float,
    lon: float,
    tle: List[str],
    twilight_threshold: float,
    min_altitude: float,
    max_magnitude: float,
    days: int,
    count: int,
    verbose: bool = False
) -> List[PassInfo]:
    """
    Find visible ISS passes over the given location.
    
    Args:
        lat: Observer's latitude.
        lon: Observer's longitude.
        tle: TLE data for ISS.
        twilight_threshold: Sun altitude threshold for darkness.
        min_altitude: Minimum altitude above horizon for a pass.
        max_magnitude: Maximum magnitude (brightness threshold).
        days: Number of days to search ahead.
        count: Maximum number of passes to find.
        verbose: Whether to print debug information.
    
    Returns:
        List of PassInfo objects for visible passes.
    """
    ts = load.timescale()
    satellite = EarthSatellite(tle[0], tle[1], 'ISS (ZARYA)', ts)
    observer = wgs84.latlon(lat, lon)
    t0 = ts.now()
    t1 = ts.utc(t0.utc_datetime() + timedelta(days=days))
    
    eph = load(EPH_PATH)
    sun = eph['sun']
    earth = eph['earth']
    
    passes = []
    
    def get_sun_altitude(t) -> float:
        """Get sun altitude at time t."""
        sun_alt = (earth + observer).at(t).observe(sun).apparent().altaz()[0].degrees
        return sun_alt
    
    def get_iss_info(t) -> Tuple[float, float, float, float]:
        """Get ISS altitude, azimuth, distance, and phase angle at time t."""
        difference = satellite - observer
        topocentric = difference.at(t)
        alt, az, distance = topocentric.altaz()
        
        # Calculate phase angle (sun-satellite-observer)
        iss_pos = satellite.at(t)
        sun_pos = (earth + observer).at(t).observe(sun).apparent()
        
        # Get vectors for phase angle calculation
        iss_vector = topocentric.position.km
        sun_vector = sun_pos.position.km
        
        # Phase angle from dot product
        dot = sum(a * b for a, b in zip(iss_vector, sun_vector))
        mag_iss = math.sqrt(sum(x**2 for x in iss_vector))
        mag_sun = math.sqrt(sum(x**2 for x in sun_vector))
        
        if mag_iss > 0 and mag_sun > 0:
            cos_phase = dot / (mag_iss * mag_sun)
            cos_phase = max(-1, min(1, cos_phase))  # Clamp to valid range
            phase_angle = math.degrees(math.acos(cos_phase))
        else:
            phase_angle = 90.0
        
        return alt.degrees, az.degrees, distance.km, phase_angle
    
    def is_pass_visible(t) -> Tuple[bool, float]:
        """Check if ISS is visible and return magnitude."""
        # Check if ISS is sunlit
        if not satellite.at(t).is_sunlit(eph):
            return False, 99.0
        
        # Check if observer is in darkness
        sun_alt = get_sun_altitude(t)
        if sun_alt >= twilight_threshold:
            return False, 99.0
        
        # Calculate magnitude
        _, _, distance, phase_angle = get_iss_info(t)
        magnitude = calculate_iss_magnitude(distance, phase_angle)
        
        return magnitude <= max_magnitude, magnitude
    
    # Find all satellite events
    t_events, events = satellite.find_events(observer, t0, t1, altitude_degrees=min_altitude)
    
    if verbose:
        print(f"Found {len(events)} events in {days} day search window")
    
    i = 0
    while i < len(events) and len(passes) < count:
        if events[i] == 0:  # Rise event
            rise_time = t_events[i]
            rise_alt, rise_az, rise_dist, rise_phase = get_iss_info(rise_time)
            
            # Find culmination and set times
            max_time = None
            max_alt = 0
            set_time = None
            
            for j in range(i + 1, len(events)):
                if events[j] == 1:  # Culmination
                    max_time = t_events[j]
                    max_alt, _, _, _ = get_iss_info(max_time)
                elif events[j] == 2:  # Set
                    set_time = t_events[j]
                    break
            
            if set_time is not None:
                set_alt, set_az, set_dist, set_phase = get_iss_info(set_time)
                
                # Check visibility at multiple points during the pass
                check_times = [rise_time]
                if max_time is not None:
                    check_times.append(max_time)
                check_times.append(set_time)
                
                # Find best visibility during pass
                best_visible = False
                best_magnitude = 99.0
                
                for check_t in check_times:
                    visible, mag = is_pass_visible(check_t)
                    if visible and mag < best_magnitude:
                        best_visible = True
                        best_magnitude = mag
                
                if best_visible:
                    # Calculate magnitudes at key points
                    _, rise_mag = is_pass_visible(rise_time)
                    _, set_mag = is_pass_visible(set_time)
                    
                    duration = (set_time.utc_datetime() - rise_time.utc_datetime()).total_seconds()
                    
                    pass_info = PassInfo(
                        rise_time=rise_time.utc_datetime(),
                        set_time=set_time.utc_datetime(),
                        max_altitude_time=max_time.utc_datetime() if max_time is not None else rise_time.utc_datetime(),
                        duration_seconds=duration,
                        max_altitude_degrees=max_alt,
                        rise_azimuth=rise_az,
                        set_azimuth=set_az,
                        max_magnitude=best_magnitude,
                        rise_magnitude=rise_mag if rise_mag < 99 else best_magnitude,
                        set_magnitude=set_mag if set_mag < 99 else best_magnitude,
                        is_visible=True
                    )
                    passes.append(pass_info)
                    
                    if verbose:
                        print(f"Found visible pass: {pass_info.rise_time} (mag: {best_magnitude})")
        i += 1
    
    return passes


def azimuth_to_direction(azimuth: float) -> str:
    """Convert azimuth in degrees to compass direction."""
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    index = round(azimuth / 22.5) % 16
    return directions[index]


def format_simple(passes: List[PassInfo], location_desc: str, lat: float, lon: float) -> str:
    """Format passes in simple text format."""
    lines = []
    lines.append(f"Location: {location_desc}")
    lines.append(f"Coordinates: {lat:.4f} deg, {lon:.4f} deg")
    lines.append("")
    
    if not passes:
        lines.append("No visible ISS passes found in the search window.")
        return "\n".join(lines)
    
    for i, p in enumerate(passes, 1):
        if len(passes) > 1:
            lines.append(f"--- Pass {i} of {len(passes)} ---")
        
        lines.append(f"Date: {p.rise_time.strftime('%Y-%m-%d')}")
        lines.append(f"Rise: {p.rise_time.strftime('%H:%M:%S UTC')} ({azimuth_to_direction(p.rise_azimuth)}, {p.rise_azimuth:.0f} deg)")
        lines.append(f"Set:  {p.set_time.strftime('%H:%M:%S UTC')} ({azimuth_to_direction(p.set_azimuth)}, {p.set_azimuth:.0f} deg)")
        lines.append(f"Duration: {int(p.duration_seconds)} seconds")
        lines.append(f"Max Altitude: {p.max_altitude_degrees:.1f} deg")
        lines.append(f"Brightness: {p.max_magnitude:+.1f} mag (peak)")
        lines.append("")
    
    return "\n".join(lines)


def format_table(passes: List[PassInfo], location_desc: str, lat: float, lon: float) -> str:
    """Format passes in a table format."""
    lines = []
    lines.append(f"Location: {location_desc}")
    lines.append(f"Coordinates: {lat:.4f} deg, {lon:.4f} deg")
    lines.append("")
    
    if not passes:
        lines.append("No visible ISS passes found in the search window.")
        return "\n".join(lines)
    
    # Table header
    header = "+------------+----------+----------+----------+---------+---------+-----------+"
    titles = "|    Date    |   Rise   |   Set    | Duration | Max Alt |   Mag   | Direction |"
    sep =    "+------------+----------+----------+----------+---------+---------+-----------+"
    footer = "+------------+----------+----------+----------+---------+---------+-----------+"
    
    lines.append(header)
    lines.append(titles)
    lines.append(sep)
    
    for p in passes:
        date = p.rise_time.strftime('%Y-%m-%d')
        rise = p.rise_time.strftime('%H:%M:%S')
        set_t = p.set_time.strftime('%H:%M:%S')
        duration = f"{int(p.duration_seconds)}s"
        max_alt = f"{p.max_altitude_degrees:.0f} deg"
        mag = f"{p.max_magnitude:+.1f}"
        direction = f"{azimuth_to_direction(p.rise_azimuth)}-{azimuth_to_direction(p.set_azimuth)}"
        
        row = f"| {date} | {rise} | {set_t} | {duration:>8} | {max_alt:>7} | {mag:>7} | {direction:^9} |"
        lines.append(row)
    
    lines.append(footer)
    lines.append("")
    lines.append("Mag = Apparent magnitude (lower/negative is brighter)")
    
    return "\n".join(lines)


def format_json(passes: List[PassInfo], location_desc: str, lat: float, lon: float) -> str:
    """Format passes as JSON."""
    data = {
        "location": {
            "description": location_desc,
            "latitude": lat,
            "longitude": lon
        },
        "passes": []
    }
    
    for p in passes:
        pass_data = {
            "rise_time": p.rise_time.isoformat() + "Z",
            "set_time": p.set_time.isoformat() + "Z",
            "max_altitude_time": p.max_altitude_time.isoformat() + "Z",
            "duration_seconds": int(p.duration_seconds),
            "max_altitude_degrees": round(p.max_altitude_degrees, 1),
            "rise_azimuth": round(p.rise_azimuth, 1),
            "set_azimuth": round(p.set_azimuth, 1),
            "rise_direction": azimuth_to_direction(p.rise_azimuth),
            "set_direction": azimuth_to_direction(p.set_azimuth),
            "max_magnitude": p.max_magnitude,
            "rise_magnitude": p.rise_magnitude,
            "set_magnitude": p.set_magnitude
        }
        data["passes"].append(pass_data)
    
    return json.dumps(data, indent=2)


def format_notification_message(p: PassInfo) -> str:
    """Format a PassInfo into a short notification message."""
    now = datetime.now(timezone.utc)
    minutes_until = int((p.rise_time - now).total_seconds() / 60)
    rise_dir = azimuth_to_direction(p.rise_azimuth)
    set_dir = azimuth_to_direction(p.set_azimuth)
    duration_min = int(p.duration_seconds / 60)
    ist = timezone(timedelta(hours=5, minutes=30))
    rise_str = p.rise_time.replace(tzinfo=timezone.utc).astimezone(ist).strftime('%H:%M IST')
    return (
        f"ISS visible in ~{minutes_until} min ({rise_str})\n"
        f"Duration: {duration_min} min | Max alt: {p.max_altitude_degrees:.0f} deg | "
        f"Brightness: {p.max_magnitude:+.1f} mag\n"
        f"Direction: {rise_dir} -> {set_dir}"
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


def run_check_notify(config_path: str = NOTIFY_CONFIG_PATH) -> None:
    """Check for upcoming passes and send a notification if one is imminent."""
    config = load_notify_config(config_path)
    if config is None:
        print("Error: No notification config found. Run --setup-notify first.")
        sys.exit(1)

    state_path = NOTIFY_STATE_PATH
    state = load_notify_state(state_path)

    tle = get_iss_tle()
    twilight_threshold = TWILIGHT_THRESHOLDS.get(config.twilight, TWILIGHT_THRESHOLDS[DEFAULT_TWILIGHT])

    search_days = max(1, (config.notify_window_minutes // (24 * 60)) + 1)
    passes = find_visible_passes(
        lat=config.lat,
        lon=config.lon,
        tle=tle,
        twilight_threshold=twilight_threshold,
        min_altitude=config.min_altitude,
        max_magnitude=config.max_magnitude,
        days=search_days,
        count=5,
    )

    now = datetime.now(timezone.utc)
    window = config.notify_window_minutes

    for p in passes:
        minutes_until = (p.rise_time - now).total_seconds() / 60
        if minutes_until < 0:
            continue
        if minutes_until > window:
            continue

        key = _pass_state_key(p.rise_time)
        if key in state:
            continue

        message = format_notification_message(p)
        if send_notification(message, config):
            state[key] = {'notified_at': now.isoformat() + 'Z'}
            save_notify_state(state, state_path)
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

    # Location
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

    # Twilight
    twilight = input(f"Twilight setting [{DEFAULT_TWILIGHT}] (civil/nautical/astronomical/any): ").strip()
    if not twilight:
        twilight = DEFAULT_TWILIGHT
    if twilight not in TWILIGHT_THRESHOLDS:
        print(f"Error: Invalid twilight '{twilight}'.")
        sys.exit(1)

    # Signal settings
    print()
    print("Signal Messenger Configuration")
    print("(Requires signal-cli to be installed and linked)")
    signal_sender = input("Your Signal phone number (sender, e.g. +1234567890): ").strip()
    signal_recipient = input("Recipient phone number (e.g. +0987654321): ").strip()

    if not signal_sender or not signal_recipient:
        print("Error: Both sender and recipient numbers are required.")
        sys.exit(1)

    # Window
    window_input = input(f"Notify how many minutes before pass? [{NOTIFY_WINDOW_MINUTES}]: ").strip()
    try:
        notify_window = int(window_input) if window_input else NOTIFY_WINDOW_MINUTES
    except ValueError:
        print("Error: Invalid number.")
        sys.exit(1)

    config = NotifyConfig(
        lat=lat_val,
        lon=lon_val,
        location_desc=location_desc,
        twilight=twilight,
        signal_sender=signal_sender,
        signal_recipient=signal_recipient,
        notify_window_minutes=notify_window,
    )
    save_notify_config(config, config_path)

    print()
    print(f"Config saved to: {config_path}")
    print()
    print("Next step — install the LaunchAgent to check every 15 minutes:")
    print(f"  python3 {os.path.abspath(__file__)} --install-launchd")
    print()


def _generate_plist(config_path: str) -> str:
    """Generate launchd plist XML content."""
    import plistlib
    python_path = sys.executable
    script_path = os.path.abspath(__file__)
    log_dir = os.path.expanduser('~/.local/share/iss-pass-notify')
    plist = {
        'Label': LAUNCHD_LABEL,
        'ProgramArguments': [python_path, script_path, '--check-notify', '--notify-config', config_path],
        'StartInterval': 900,  # 15 minutes
        'StandardOutPath': os.path.join(log_dir, 'launchd.log'),
        'StandardErrorPath': os.path.join(log_dir, 'launchd.err'),
        'RunAtLoad': False,
    }
    return plistlib.dumps(plist).decode('utf-8')


def install_launchd(config_path: str = NOTIFY_CONFIG_PATH) -> None:
    """Install a macOS LaunchAgent for periodic pass checks."""
    config = load_notify_config(config_path)
    if config is None:
        print("Error: No notification config found. Run --setup-notify first.")
        sys.exit(1)

    # Ensure log directory exists
    log_dir = os.path.expanduser('~/.local/share/iss-pass-notify')
    os.makedirs(log_dir, exist_ok=True)

    # Unload existing agent if present
    if os.path.exists(LAUNCHD_PLIST_PATH):
        subprocess.run(['launchctl', 'unload', LAUNCHD_PLIST_PATH],
                        capture_output=True)

    plist_content = _generate_plist(config_path)
    os.makedirs(os.path.dirname(LAUNCHD_PLIST_PATH), exist_ok=True)
    with open(LAUNCHD_PLIST_PATH, 'w') as f:
        f.write(plist_content)

    result = subprocess.run(['launchctl', 'load', LAUNCHD_PLIST_PATH],
                             capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error loading LaunchAgent: {result.stderr.strip()}")
        sys.exit(1)

    print(f"LaunchAgent installed and loaded.")
    print(f"  Plist: {LAUNCHD_PLIST_PATH}")
    print(f"  Logs:  {log_dir}/launchd.log")
    print(f"  Runs every 15 minutes. To check manually:")
    print(f"    python3 {os.path.abspath(__file__)} --check-notify")
    print()
    print("To uninstall:")
    print(f"  python3 {os.path.abspath(__file__)} --uninstall-launchd")


def uninstall_launchd() -> None:
    """Uninstall the macOS LaunchAgent."""
    if not os.path.exists(LAUNCHD_PLIST_PATH):
        print("LaunchAgent is not installed.")
        return

    subprocess.run(['launchctl', 'unload', LAUNCHD_PLIST_PATH],
                    capture_output=True)
    os.remove(LAUNCHD_PLIST_PATH)
    print("LaunchAgent unloaded and removed.")


def main():
    """
    Main function that orchestrates ISS pass finding and display.
    """
    args = parse_arguments()

    # Notification dispatch (short-circuit before normal flow)
    if args.setup_notify:
        run_setup_notify(args.notify_config)
        return
    if args.check_notify:
        run_check_notify(args.notify_config)
        return
    if args.install_launchd:
        install_launchd(args.notify_config)
        return
    if args.uninstall_launchd:
        uninstall_launchd()
        return
    
    # Get location
    lat, lon, location_desc = get_location(args)
    
    # Convert twilight name to angle
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
    
    # Get TLE data
    tle = get_iss_tle()
    
    # Find passes
    passes = find_visible_passes(
        lat=lat,
        lon=lon,
        tle=tle,
        twilight_threshold=twilight_threshold,
        min_altitude=args.altitude,
        max_magnitude=args.min_magnitude,
        days=args.days,
        count=args.count,
        verbose=args.verbose
    )
    
    # Format and print output
    if args.format == 'json':
        output = format_json(passes, location_desc, lat, lon)
    elif args.format == 'table':
        output = format_table(passes, location_desc, lat, lon)
    else:
        output = format_simple(passes, location_desc, lat, lon)
    
    print(output)


if __name__ == "__main__":
    main()
