#!/usr/bin/env python3

import requests
from skyfield.api import load, EarthSatellite, wgs84, N, E, wgs84
from skyfield.almanac import dark_twilight_day
from datetime import datetime, timedelta
import sys
import argparse

def parse_arguments():
    parser = argparse.ArgumentParser(description='Find the next visible ISS pass over your location.')
    parser.add_argument('--lat', type=float, help='Latitude of the observer.')
    parser.add_argument('--lon', type=float, help='Longitude of the observer.')
    parser.add_argument('--twilight', type=float, default=-18.0,
                        help='Sun altitude threshold for darkness (default: -18 for astronomical twilight).')
    return parser.parse_args()

def get_location():
    """
    Retrieves the user's current geographic location (latitude and longitude)
    based on their IP address using the ipinfo.io service.
    """
    try:
        response = requests.get('https://ipinfo.io/json')
        response.raise_for_status()
        data = response.json()
        loc = data['loc'].split(',')
        lat, lon = float(loc[0]), float(loc[1])
        return lat, lon
    except Exception as e:
        print(f"Error retrieving location: {e}")
        sys.exit(1)

def get_iss_tle():
    """
    Fetches the latest Two-Line Element (TLE) data for the ISS (International Space Station)
    from the wheretheiss.at API.
    """
    try:
        response = requests.get('https://api.wheretheiss.at/v1/satellites/25544/tles')
        response.raise_for_status()
        tle_data = response.json()
        
        # Extract line1 and line2 from the response
        line1 = tle_data.get('line1')
        line2 = tle_data.get('line2')
        
        if not line1 or not line2:
            print("Error: TLE data is incomplete.")
            sys.exit(1)
        
        return [line1, line2]
    except Exception as e:
        print(f"Error retrieving ISS TLE data: {e}")
        sys.exit(1)

def next_visible_pass(lat, lon, tle, twilight_threshold):
    """
    Calculates the next visible ISS pass over the user's location using the provided TLE data.
    
    Parameters:
    - lat (float): Latitude of the observer.
    - lon (float): Longitude of the observer.
    - tle (list): List containing the two TLE lines.
    - twilight_threshold (float): Sun altitude threshold for darkness.
    
    Returns:
    - risetime (datetime): The UTC datetime when the ISS will rise above the horizon and is visible.
    - duration (float): Duration of the pass in seconds.
    """
    try:
        ts = load.timescale()
        satellite = EarthSatellite(tle[0], tle[1], 'ISS (ZARYA)', ts)
        observer = wgs84.latlon(lat, lon)
        t0 = ts.now()
        t1 = ts.utc(t0.utc_datetime() + timedelta(days=2))  # Look ahead 48 hours to find a visible pass

        eph = load('de421.bsp')  # Ephemeris data
        sun = eph['sun']
        earth = eph['earth']

        # Function to determine if the ISS is illuminated and observer is in darkness
        def is_visible(t):
            # Compute the position of the ISS at time t
            iss = satellite.at(t)
            # Determine if ISS is illuminated by the Sun
            illuminated = satellite.at(t).is_sunlit(eph)
            if not illuminated:
                return False

            # Determine if the observer is in darkness based on twilight_threshold
            sun_alt = (earth + observer).at(t).observe(sun).apparent().altaz()[0].degrees
            return sun_alt < twilight_threshold

        # Search for events where the ISS rises above 10 degrees altitude
        t, events = satellite.find_events(observer, t0, t1, altitude_degrees=10.0)

        risetime = None
        duration = None

        for ti, event in zip(t, events):
            if event == 0:  # Rise
                # Check if the pass is visible
                if is_visible(ti):
                    risetime = ti.utc_datetime()
                    # Find the corresponding set event to calculate duration
                    # We'll search forward for the set event
                    for tj, eventj in zip(t, events):
                        if tj > ti and eventj == 2:  # Set
                            settime = tj.utc_datetime()
                            duration = (settime - risetime).total_seconds()
                            return risetime, duration
        return risetime, duration
    except Exception as e:
        print(f"Error calculating next visible pass: {e}")
        sys.exit(1)

def main():
    """
    Main function that orchestrates the retrieval of location, TLE data,
    calculation of the next visible ISS pass, and displays the results.
    """
    args = parse_arguments()
    if args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    else:
        lat, lon = get_location()
    twilight_threshold = args.twilight

    print(f"Your current location:")
    print(f"Latitude: {lat}")
    print(f"Longitude: {lon}")
    print(f"Twilight threshold: {twilight_threshold} degrees\n")

    tle = get_iss_tle()
    risetime, duration = next_visible_pass(lat, lon, tle, twilight_threshold)

    if risetime and duration:
        print(f"The next visible ISS pass will occur at: {risetime.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Duration of the pass: {int(duration)} seconds")
    else:
        print("No visible ISS pass found in the next 48 hours.")

if __name__ == "__main__":
    main()
