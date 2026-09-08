#!/usr/bin/env python3
"""
Comprehensive test suite for ISS Next Pass Finder.

This suite includes:
- Unit tests for core calculation functions
- Unit tests for coordinate validation
- Unit tests for azimuth to direction conversion
- Integration tests with mocked API responses
- Tests for output formatting
"""

import pytest
import json
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
import sys
import os

# Add the parent directory to the path to import iss_next_pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import iss_next_pass as iss


# ============================================================================
# UNIT TESTS - Coordinate Validation
# ============================================================================


class TestCoordinateValidation:
    """Tests for validate_coordinates function."""

    def test_valid_coordinates(self):
        """Test that valid coordinates are accepted."""
        is_valid, msg = iss.validate_coordinates(40.7128, -74.0060)
        assert is_valid is True
        assert msg == ""

    def test_valid_extremes(self):
        """Test boundary values for coordinates."""
        # Test north pole
        is_valid, msg = iss.validate_coordinates(90.0, 0.0)
        assert is_valid is True

        # Test south pole
        is_valid, msg = iss.validate_coordinates(-90.0, 0.0)
        assert is_valid is True

        # Test date line
        is_valid, msg = iss.validate_coordinates(0.0, 180.0)
        assert is_valid is True

        is_valid, msg = iss.validate_coordinates(0.0, -180.0)
        assert is_valid is True

    def test_invalid_latitude_too_high(self):
        """Test that latitude > 90 is rejected."""
        is_valid, msg = iss.validate_coordinates(91.0, 0.0)
        assert is_valid is False
        assert "Latitude" in msg

    def test_invalid_latitude_too_low(self):
        """Test that latitude < -90 is rejected."""
        is_valid, msg = iss.validate_coordinates(-91.0, 0.0)
        assert is_valid is False
        assert "Latitude" in msg

    def test_invalid_longitude_too_high(self):
        """Test that longitude > 180 is rejected."""
        is_valid, msg = iss.validate_coordinates(0.0, 181.0)
        assert is_valid is False
        assert "Longitude" in msg

    def test_invalid_longitude_too_low(self):
        """Test that longitude < -180 is rejected."""
        is_valid, msg = iss.validate_coordinates(0.0, -181.0)
        assert is_valid is False
        assert "Longitude" in msg


# ============================================================================
# UNIT TESTS - Azimuth to Direction Conversion
# ============================================================================


class TestAzimuthToDirection:
    """Tests for azimuth_to_direction function."""

    def test_cardinal_directions(self):
        """Test primary cardinal directions."""
        assert iss.azimuth_to_direction(0) == "N"
        assert iss.azimuth_to_direction(90) == "E"
        assert iss.azimuth_to_direction(180) == "S"
        assert iss.azimuth_to_direction(270) == "W"

    def test_intercardinal_directions(self):
        """Test intercardinal directions."""
        assert iss.azimuth_to_direction(45) == "NE"
        assert iss.azimuth_to_direction(135) == "SE"
        assert iss.azimuth_to_direction(225) == "SW"
        assert iss.azimuth_to_direction(315) == "NW"

    def test_secondary_intercardinal_directions(self):
        """Test secondary intercardinal directions."""
        assert iss.azimuth_to_direction(22.5) == "NNE"
        assert iss.azimuth_to_direction(67.5) == "ENE"
        assert iss.azimuth_to_direction(112.5) == "ESE"
        assert iss.azimuth_to_direction(157.5) == "SSE"

    def test_wraparound(self):
        """Test that azimuth wraps around at 360 degrees."""
        assert iss.azimuth_to_direction(360) == "N"
        assert iss.azimuth_to_direction(361) == iss.azimuth_to_direction(1)

    def test_negative_angles(self):
        """Test handling of negative angles."""
        # -90 should be same as 270 (west)
        assert iss.azimuth_to_direction(-90) == "W"


# ============================================================================
# UNIT TESTS - Magnitude Calculations
# ============================================================================


class TestISSMagnitudeCalculation:
    """Tests for calculate_iss_magnitude function."""

    def test_base_magnitude_at_standard_distance(self):
        """Test magnitude at standard distance and optimal phase angle."""
        # At standard distance (1000 km) and optimal phase angle (0 deg, fully illuminated)
        magnitude = iss.calculate_iss_magnitude(1000.0, 0.0)
        # Should be close to intrinsic magnitude
        assert magnitude < 0  # ISS should be bright
        assert magnitude > -4  # But not unrealistically bright

    def test_magnitude_increases_with_distance(self):
        """Test that magnitude increases (gets dimmer) with distance."""
        mag_close = iss.calculate_iss_magnitude(500.0, 90.0)
        mag_far = iss.calculate_iss_magnitude(2000.0, 90.0)
        assert mag_far > mag_close  # Larger magnitude = dimmer

    def test_magnitude_changes_with_phase_angle(self):
        """Test that magnitude varies with phase angle."""
        mag_forward = iss.calculate_iss_magnitude(1000.0, 0.0)  # Fully illuminated
        mag_perpendicular = iss.calculate_iss_magnitude(1000.0, 90.0)  # Side angle
        mag_backlit = iss.calculate_iss_magnitude(1000.0, 179.0)  # Nearly backlit

        # Forward lighting should be brightest
        assert mag_forward < mag_perpendicular
        assert mag_perpendicular < mag_backlit

    def test_magnitude_with_zero_distance(self):
        """Test handling of zero distance (shouldn't crash)."""
        magnitude = iss.calculate_iss_magnitude(0.0, 90.0)
        assert isinstance(magnitude, float)
        assert not math.isnan(magnitude)

    def test_magnitude_is_reasonable(self):
        """Test that calculated magnitudes are in reasonable range."""
        # Test various realistic scenarios
        test_cases = [
            (1000.0, 0.0),  # Standard distance, optimal angle
            (1500.0, 45.0),  # Farther, worse angle
            (800.0, 120.0),  # Close, bad angle
            (2500.0, 60.0),  # Very far, moderate angle
        ]

        for distance, phase_angle in test_cases:
            magnitude = iss.calculate_iss_magnitude(distance, phase_angle)
            # ISS magnitudes should generally be between -4 and +6
            assert -5 < magnitude < 10, (
                f"Unrealistic magnitude {magnitude} for distance={distance}, phase={phase_angle}"
            )


# ============================================================================
# UNIT TESTS - Argument Parsing and Validation
# ============================================================================


class TestArgumentParsing:
    """Tests for command-line argument parsing."""

    def test_parse_location_argument(self):
        """Test parsing --location argument."""
        with patch("sys.argv", ["iss_next_pass.py", "--location", "New York"]):
            args = iss.parse_arguments()
            assert args.location == "New York"

    def test_parse_coordinates(self):
        """Test parsing --lat and --lon arguments."""
        with patch(
            "sys.argv", ["iss_next_pass.py", "--lat", "40.7128", "--lon", "-74.0060"]
        ):
            args = iss.parse_arguments()
            assert args.lat == 40.7128
            assert args.lon == -74.0060

    def test_parse_twilight_option(self):
        """Test parsing --twilight argument."""
        with patch("sys.argv", ["iss_next_pass.py", "--twilight", "nautical"]):
            args = iss.parse_arguments()
            assert args.twilight == "nautical"

    def test_default_twilight(self):
        """Test that default twilight is civil."""
        with patch("sys.argv", ["iss_next_pass.py"]):
            args = iss.parse_arguments()
            assert args.twilight == iss.DEFAULT_TWILIGHT

    def test_parse_count(self):
        """Test parsing --count argument."""
        with patch("sys.argv", ["iss_next_pass.py", "--count", "5"]):
            args = iss.parse_arguments()
            assert args.count == 5

    def test_parse_days(self):
        """Test parsing --days argument."""
        with patch("sys.argv", ["iss_next_pass.py", "--days", "7"]):
            args = iss.parse_arguments()
            assert args.days == 7

    def test_parse_format(self):
        """Test parsing --format argument."""
        with patch("sys.argv", ["iss_next_pass.py", "--format", "json"]):
            args = iss.parse_arguments()
            assert args.format == "json"

    def test_parse_verbose(self):
        """Test parsing --verbose flag."""
        with patch("sys.argv", ["iss_next_pass.py", "--verbose"]):
            args = iss.parse_arguments()
            assert args.verbose is True


# ============================================================================
# UNIT TESTS - Output Formatting
# ============================================================================


class TestOutputFormatting:
    """Tests for output formatting functions."""

    @pytest.fixture
    def sample_passes(self):
        """Create sample PassInfo objects for testing."""
        return [
            iss.PassInfo(
                rise_time=datetime(2024, 2, 15, 10, 30, 0, tzinfo=timezone.utc),
                set_time=datetime(2024, 2, 15, 10, 45, 0, tzinfo=timezone.utc),
                max_altitude_time=datetime(
                    2024, 2, 15, 10, 37, 30, tzinfo=timezone.utc
                ),
                duration_seconds=900.0,
                max_altitude_degrees=45.5,
                rise_azimuth=280.0,
                set_azimuth=100.0,
                max_magnitude=-1.5,
                rise_magnitude=-0.5,
                set_magnitude=0.5,
                is_visible=True,
            ),
            iss.PassInfo(
                rise_time=datetime(2024, 2, 16, 12, 15, 0, tzinfo=timezone.utc),
                set_time=datetime(2024, 2, 16, 12, 25, 0, tzinfo=timezone.utc),
                max_altitude_time=datetime(2024, 2, 16, 12, 20, 0, tzinfo=timezone.utc),
                duration_seconds=600.0,
                max_altitude_degrees=30.0,
                rise_azimuth=270.0,
                set_azimuth=90.0,
                max_magnitude=0.0,
                rise_magnitude=0.5,
                set_magnitude=1.0,
                is_visible=True,
            ),
        ]

    def test_format_simple_with_passes(self, sample_passes):
        """Test simple text formatting with passes."""
        output = iss.format_simple(sample_passes, "New York", 40.7128, -74.0060)

        assert "Location: New York" in output
        assert "40.7128" in output
        assert "-74.0060" in output
        assert "2024-02-15" in output
        assert "2024-02-16" in output
        assert "900 seconds" in output or "15 minute" in output.lower()

    def test_format_simple_no_passes(self):
        """Test simple text formatting with no passes."""
        output = iss.format_simple([], "Tokyo", 35.6762, 139.6503)

        assert "No visible ISS passes" in output
        assert "Location: Tokyo" in output

    def test_format_table_with_passes(self, sample_passes):
        """Test table formatting with passes."""
        output = iss.format_table(sample_passes, "London", 51.5074, -0.1278)

        assert "Location: London" in output
        assert "2024-02-15" in output
        assert "2024-02-16" in output
        assert "|" in output  # Table format should have pipes
        assert "+" in output  # Table format should have box drawing

    def test_format_table_no_passes(self):
        """Test table formatting with no passes."""
        output = iss.format_table([], "Mumbai", 19.0760, 72.8777)

        assert "No visible ISS passes" in output
        assert "Location: Mumbai" in output

    def test_format_json_with_passes(self, sample_passes):
        """Test JSON formatting with passes."""
        output = iss.format_json(sample_passes, "Sydney", -33.8688, 151.2093)

        # Should be valid JSON
        data = json.loads(output)

        assert data["location"]["description"] == "Sydney"
        assert data["location"]["latitude"] == -33.8688
        assert data["location"]["longitude"] == 151.2093
        assert len(data["passes"]) == 2

        # Check first pass structure
        first_pass = data["passes"][0]
        assert "rise_time" in first_pass
        assert "set_time" in first_pass
        assert "duration_seconds" in first_pass
        assert "max_altitude_degrees" in first_pass
        assert "max_magnitude" in first_pass
        assert "rise_direction" in first_pass
        assert "set_direction" in first_pass

    def test_format_json_empty(self):
        """Test JSON formatting with no passes."""
        output = iss.format_json([], "Paris", 48.8566, 2.3522)

        # Should be valid JSON
        data = json.loads(output)

        assert data["location"]["description"] == "Paris"
        assert data["passes"] == []


# ============================================================================
# INTEGRATION TESTS - Location Services
# ============================================================================


class TestLocationServices:
    """Integration tests for location detection services."""

    @patch("requests.get")
    def test_get_location_from_ip_success(self, mock_get):
        """Test successful IP-based location detection."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "loc": "40.7128,-74.0060",
            "city": "New York",
        }
        mock_get.return_value = mock_response

        lat, lon = iss.get_location_from_ip()

        assert lat == 40.7128
        assert lon == -74.0060
        mock_get.assert_called_once()

    @patch("requests.get")
    def test_get_location_from_ip_timeout(self, mock_get):
        """Test timeout handling in IP location."""
        mock_get.side_effect = iss.requests.exceptions.Timeout()

        with pytest.raises(SystemExit):
            iss.get_location_from_ip()

    @patch("requests.get")
    def test_get_location_from_ip_request_error(self, mock_get):
        """Test request error handling in IP location."""
        mock_get.side_effect = iss.requests.exceptions.RequestException("Network error")

        with pytest.raises(SystemExit):
            iss.get_location_from_ip()


# ============================================================================
# INTEGRATION TESTS - TLE Fetching
# ============================================================================


class TestTLEFetching:
    """Integration tests for TLE data fetching."""

    @patch("requests.get")
    def test_get_iss_tle_from_celestrak(self, mock_get):
        """Test successful TLE fetch from CelesTrak."""
        # Sample TLE data
        tle_data = "ISS (ZARYA)\n1 25544U 98067A   21001.00000000  .00002182  00000-0  41420-4 0  9990\n2 25544  51.6461 339.8014 0002571  34.5857 120.4689 15.48919393282286"

        mock_response = MagicMock()
        mock_response.text = tle_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        tle = iss.get_iss_tle()

        assert len(tle) == 2
        assert tle[0].startswith("1 ")
        assert tle[1].startswith("2 ")

    @patch("requests.get")
    def test_get_iss_tle_from_wheretheiss(self, mock_get):
        """Test TLE fetch fallback to wheretheiss.at."""
        # First call fails (CelesTrak), second succeeds (wheretheiss)
        json_response = {
            "line1": "1 25544U 98067A   21001.00000000  .00002182  00000-0  41420-4 0  9990",
            "line2": "2 25544  51.6461 339.8014 0002571  34.5857 120.4689 15.48919393282286",
        }

        mock_response_fail = MagicMock()
        mock_response_fail.raise_for_status.side_effect = (
            iss.requests.exceptions.RequestException()
        )

        mock_response_success = MagicMock()
        mock_response_success.json.return_value = json_response
        mock_response_success.raise_for_status.return_value = None

        mock_get.side_effect = [mock_response_fail, mock_response_success]

        tle = iss.get_iss_tle()

        assert len(tle) == 2
        assert tle[0].startswith("1 ")
        assert tle[1].startswith("2 ")

    @patch("requests.get")
    def test_get_iss_tle_all_sources_fail(self, mock_get):
        """Test that SystemExit is raised when all sources fail."""
        mock_get.side_effect = iss.requests.exceptions.RequestException("Network error")

        with pytest.raises(SystemExit):
            iss.get_iss_tle()


# ============================================================================
# INTEGRATION TESTS - PassInfo Dataclass
# ============================================================================


class TestPassInfoDataclass:
    """Tests for PassInfo dataclass."""

    def test_passinfo_creation(self):
        """Test creating a PassInfo instance."""
        rise_time = datetime(2024, 2, 15, 10, 30, 0, tzinfo=timezone.utc)
        set_time = datetime(2024, 2, 15, 10, 45, 0, tzinfo=timezone.utc)

        pass_info = iss.PassInfo(
            rise_time=rise_time,
            set_time=set_time,
            max_altitude_time=rise_time + timedelta(minutes=7),
            duration_seconds=900.0,
            max_altitude_degrees=45.5,
            rise_azimuth=280.0,
            set_azimuth=100.0,
            max_magnitude=-1.5,
            rise_magnitude=-0.5,
            set_magnitude=0.5,
            is_visible=True,
        )

        assert pass_info.rise_time == rise_time
        assert pass_info.set_time == set_time
        assert pass_info.duration_seconds == 900.0
        assert pass_info.max_altitude_degrees == 45.5
        assert pass_info.is_visible is True

    def test_passinfo_duration_calculation(self):
        """Test that duration is correctly set."""
        rise = datetime(2024, 2, 15, 10, 30, 0, tzinfo=timezone.utc)
        set_t = datetime(2024, 2, 15, 10, 45, 30, tzinfo=timezone.utc)

        pass_info = iss.PassInfo(
            rise_time=rise,
            set_time=set_t,
            max_altitude_time=rise + timedelta(minutes=7),
            duration_seconds=930.0,
            max_altitude_degrees=45.5,
            rise_azimuth=280.0,
            set_azimuth=100.0,
            max_magnitude=-1.5,
            rise_magnitude=-0.5,
            set_magnitude=0.5,
            is_visible=True,
        )

        # Duration should be 15 minutes 30 seconds = 930 seconds
        assert pass_info.duration_seconds == 930.0


# ============================================================================
# EDGE CASES AND SPECIAL SCENARIOS
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_magnitude_with_extreme_distance(self):
        """Test magnitude calculation with extreme distances."""
        # Very close
        mag_close = iss.calculate_iss_magnitude(10.0, 90.0)
        # Very far
        mag_far = iss.calculate_iss_magnitude(10000.0, 90.0)

        assert mag_far > mag_close

    def test_azimuth_360_wraps_to_north(self):
        """Test that 360 degrees wraps to north."""
        result_360 = iss.azimuth_to_direction(360.0)
        result_0 = iss.azimuth_to_direction(0.0)
        assert result_360 == result_0 == "N"

    def test_format_functions_handle_empty_list(self):
        """Test that format functions handle empty pass lists gracefully."""
        output_simple = iss.format_simple([], "Test", 0.0, 0.0)
        output_table = iss.format_table([], "Test", 0.0, 0.0)
        output_json = iss.format_json([], "Test", 0.0, 0.0)

        assert "No visible ISS passes" in output_simple
        assert "No visible ISS passes" in output_table
        assert json.loads(output_json)["passes"] == []

    def test_twilight_thresholds_defined(self):
        """Test that all expected twilight thresholds exist."""
        expected_thresholds = ["civil", "nautical", "astronomical", "any"]
        for threshold in expected_thresholds:
            assert threshold in iss.TWILIGHT_THRESHOLDS

    def test_magnitude_constants_valid(self):
        """Test that magnitude constants are reasonable."""
        assert iss.ISS_INTRINSIC_MAGNITUDE < 0  # ISS is bright
        assert iss.ISS_STANDARD_DISTANCE > 0
        assert iss.DEFAULT_MAX_MAGNITUDE > -5 and iss.DEFAULT_MAX_MAGNITUDE < 10


# ============================================================================
# Constants Validation Tests
# ============================================================================


class TestConstants:
    """Tests for verifying constants are correctly defined."""

    def test_twilight_thresholds_order(self):
        """Test that twilight thresholds increase in darkness."""
        # Should go from least dark to most dark
        assert iss.TWILIGHT_THRESHOLDS["any"] > iss.TWILIGHT_THRESHOLDS["civil"]
        assert iss.TWILIGHT_THRESHOLDS["civil"] > iss.TWILIGHT_THRESHOLDS["nautical"]
        assert (
            iss.TWILIGHT_THRESHOLDS["nautical"]
            > iss.TWILIGHT_THRESHOLDS["astronomical"]
        )

    def test_min_altitude_is_positive(self):
        """Test that minimum altitude threshold is positive."""
        assert iss.MIN_ALTITUDE_DEGREES > 0

    def test_default_max_magnitude_visible(self):
        """Test that default magnitude threshold is for naked eye visibility."""
        # Magnitude 6 is approximately the limit of naked eye visibility
        assert iss.DEFAULT_MAX_MAGNITUDE <= 6.0


# ============================================================================
# NOTIFICATION TESTS - Config Loading
# ============================================================================


class TestNotifyConfigLoading:
    """Tests for notification config I/O."""

    def test_load_missing_config(self, tmp_path):
        """Test that loading a missing config returns None."""
        result = iss.load_notify_config(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_load_valid_config(self, tmp_path):
        """Test loading a valid config file."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "lat": 23.02,
            "lon": 72.57,
            "location_desc": "Ahmedabad",
            "twilight": "civil",
            "min_altitude": 10.0,
            "max_magnitude": 3.0,
            "signal_sender": "+1234567890",
            "signal_recipient": "+0987654321",
            "notify_window_minutes": 30,
        }))
        config = iss.load_notify_config(str(config_file))
        assert config is not None
        assert config.lat == 23.02
        assert config.lon == 72.57
        assert config.location_desc == "Ahmedabad"
        assert config.signal_sender == "+1234567890"
        assert config.signal_recipient == "+0987654321"
        assert config.notify_window_minutes == 30

    def test_load_config_bad_json(self, tmp_path):
        """Test that invalid JSON returns None."""
        config_file = tmp_path / "bad.json"
        config_file.write_text("not json{{{")
        result = iss.load_notify_config(str(config_file))
        assert result is None

    def test_load_config_non_object_json(self, tmp_path):
        """Test that non-object JSON returns None."""
        config_file = tmp_path / "list.json"
        config_file.write_text(json.dumps(["not", "a", "config"]))
        result = iss.load_notify_config(str(config_file))
        assert result is None

    def test_load_config_missing_required_field(self, tmp_path):
        """Test that missing required fields returns None."""
        config_file = tmp_path / "incomplete.json"
        config_file.write_text(json.dumps({"lat": 23.02}))  # missing lon
        result = iss.load_notify_config(str(config_file))
        assert result is None

    def test_save_and_load_roundtrip(self, tmp_path):
        """Test that saving and loading config preserves values."""
        config_path = str(tmp_path / "config.json")
        config = iss.NotifyConfig(
            lat=51.5, lon=-0.12, location_desc="London",
            signal_sender="+111", signal_recipient="+222",
        )
        iss.save_notify_config(config, config_path)
        loaded = iss.load_notify_config(config_path)
        assert loaded is not None
        assert loaded.lat == 51.5
        assert loaded.lon == -0.12
        assert loaded.signal_sender == "+111"


# ============================================================================
# NOTIFICATION TESTS - State Management
# ============================================================================


class TestNotifyState:
    """Tests for notification state I/O and dedup."""

    def test_load_missing_state(self, tmp_path):
        """Test loading state from a missing file returns empty dict."""
        result = iss.load_notify_state(str(tmp_path / "missing.json"))
        assert result == {}

    def test_load_state_non_object_json(self, tmp_path):
        """Test that non-object JSON state is treated as empty."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(["not", "state"]))
        result = iss.load_notify_state(str(state_file))
        assert result == {}

    def test_dedup_check(self, tmp_path):
        """Test that a pass ID in state prevents re-notification."""
        state_path = str(tmp_path / "state.json")
        key = "2026-02-15T03:42:00Z"
        state = {key: {"notified_at": "2026-02-15T03:12:00Z"}}
        iss.save_notify_state(state, state_path)
        loaded = iss.load_notify_state(state_path)
        assert key in loaded

    def test_state_prunes_old_entries(self, tmp_path):
        """Test that entries older than 48h are pruned on save."""
        state_path = str(tmp_path / "state.json")
        old_key = "2020-01-01T00:00:00Z"
        recent_key = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(
            second=0, microsecond=0, tzinfo=None
        ).isoformat() + "Z"
        state = {
            old_key: {"notified_at": "2020-01-01T00:00:00Z"},
            recent_key: {"notified_at": recent_key},
        }
        iss.save_notify_state(state, state_path)
        loaded = iss.load_notify_state(state_path)
        assert old_key not in loaded
        assert recent_key in loaded


# ============================================================================
# NOTIFICATION TESTS - Message Formatting
# ============================================================================


class TestNotificationMessage:
    """Tests for notification message formatting."""

    def test_message_contains_key_info(self):
        """Test that the notification message contains expected fields."""
        now = datetime.now(timezone.utc)
        rise = now + timedelta(minutes=28)
        p = iss.PassInfo(
            rise_time=rise,
            set_time=rise + timedelta(minutes=4),
            max_altitude_time=rise + timedelta(minutes=2),
            duration_seconds=240.0,
            max_altitude_degrees=62.0,
            rise_azimuth=22.5,  # NNE
            set_azimuth=157.5,  # SSE
            max_magnitude=-1.2,
            rise_magnitude=-0.5,
            set_magnitude=0.5,
            is_visible=True,
        )
        msg = iss.format_notification_message(p)
        assert "ISS visible in" in msg
        assert "62 deg" in msg
        assert "-1.2 mag" in msg
        assert "NNE" in msg
        assert "SSE" in msg
        assert "4 min" in msg
        assert "IST" in msg


# ============================================================================
# NOTIFICATION TESTS - Signal Sending
# ============================================================================


class TestSendSignalNotification:
    """Tests for send_signal_notification with mocked subprocess."""

    def _make_config(self):
        return iss.NotifyConfig(
            lat=23.02, lon=72.57,
            signal_sender="+111", signal_recipient="+222",
        )

    @patch("iss_next_pass.subprocess.run")
    def test_success(self, mock_run):
        """Test successful signal-cli call returns True."""
        mock_run.return_value = MagicMock(returncode=0)
        result = iss.send_signal_notification("hello", self._make_config())
        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "signal-cli" in args
        assert "+111" in args
        assert "+222" in args

    @patch("iss_next_pass.subprocess.run")
    def test_failure_nonzero_exit(self, mock_run):
        """Test that non-zero exit code returns False."""
        mock_run.return_value = MagicMock(returncode=1)
        result = iss.send_signal_notification("hello", self._make_config())
        assert result is False

    @patch("iss_next_pass.subprocess.run", side_effect=OSError("not found"))
    def test_failure_oserror(self, mock_run):
        """Test that OSError (missing binary) returns False."""
        result = iss.send_signal_notification("hello", self._make_config())
        assert result is False


# ============================================================================
# NOTIFICATION TESTS - run_check_notify Integration
# ============================================================================


class TestRunCheckNotify:
    """Tests for run_check_notify with mocked dependencies."""

    def _write_config(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        config = iss.NotifyConfig(
            lat=23.02, lon=72.57, location_desc="Ahmedabad",
            signal_sender="+111", signal_recipient="+222",
            notify_window_minutes=30,
        )
        iss.save_notify_config(config, config_path)
        return config_path

    @patch("iss_next_pass.NOTIFY_STATE_PATH", new_callable=lambda: property(lambda self: ""))
    @patch("iss_next_pass.send_notification", return_value=True)
    @patch("iss_next_pass.find_visible_passes")
    @patch("iss_next_pass.get_iss_tle", return_value=["1 dummy", "2 dummy"])
    def test_sends_notification_for_imminent_pass(
        self, mock_tle, mock_find, mock_send, mock_state_path, tmp_path
    ):
        """Test that a pass within the window triggers a notification."""
        now = datetime.now(timezone.utc)
        rise = now + timedelta(minutes=20)
        mock_find.return_value = [
            iss.PassInfo(
                rise_time=rise,
                set_time=rise + timedelta(minutes=4),
                max_altitude_time=rise + timedelta(minutes=2),
                duration_seconds=240.0,
                max_altitude_degrees=45.0,
                rise_azimuth=0.0,
                set_azimuth=180.0,
                max_magnitude=-1.0,
                rise_magnitude=-0.5,
                set_magnitude=0.5,
                is_visible=True,
            )
        ]
        config_path = self._write_config(tmp_path)
        state_path = str(tmp_path / "state.json")

        with patch("iss_next_pass.NOTIFY_STATE_PATH", state_path):
            iss.run_check_notify(config_path)

        mock_send.assert_called_once()

    @patch("iss_next_pass.send_notification")
    @patch("iss_next_pass.find_visible_passes")
    @patch("iss_next_pass.get_iss_tle", return_value=["1 dummy", "2 dummy"])
    def test_skips_already_notified_pass(self, mock_tle, mock_find, mock_send, tmp_path):
        """Test that an already-notified pass is skipped."""
        now = datetime.now(timezone.utc)
        rise = now + timedelta(minutes=20)
        rise_key = rise.replace(second=0, microsecond=0, tzinfo=None).isoformat() + "Z"
        mock_find.return_value = [
            iss.PassInfo(
                rise_time=rise,
                set_time=rise + timedelta(minutes=4),
                max_altitude_time=rise + timedelta(minutes=2),
                duration_seconds=240.0,
                max_altitude_degrees=45.0,
                rise_azimuth=0.0,
                set_azimuth=180.0,
                max_magnitude=-1.0,
                rise_magnitude=-0.5,
                set_magnitude=0.5,
                is_visible=True,
            )
        ]
        config_path = self._write_config(tmp_path)
        state_path = str(tmp_path / "state.json")
        # Pre-populate state with this pass
        iss.save_notify_state({rise_key: {"notified_at": now.isoformat() + "Z"}}, state_path)

        with patch("iss_next_pass.NOTIFY_STATE_PATH", state_path):
            iss.run_check_notify(config_path)

        mock_send.assert_not_called()

    @patch("iss_next_pass.send_notification")
    @patch("iss_next_pass.find_visible_passes", return_value=[])
    @patch("iss_next_pass.get_iss_tle", return_value=["1 dummy", "2 dummy"])
    def test_no_passes_no_notification(self, mock_tle, mock_find, mock_send, tmp_path):
        """Test that no passes means no notification."""
        config_path = self._write_config(tmp_path)
        state_path = str(tmp_path / "state.json")

        with patch("iss_next_pass.NOTIFY_STATE_PATH", state_path):
            iss.run_check_notify(config_path)

        mock_send.assert_not_called()

    @patch("iss_next_pass.send_notification")
    @patch("iss_next_pass.find_visible_passes")
    @patch("iss_next_pass.get_iss_tle", return_value=["1 dummy", "2 dummy"])
    def test_skips_pass_too_far_out(self, mock_tle, mock_find, mock_send, tmp_path):
        """Test that a pass beyond the window is not notified."""
        now = datetime.now(timezone.utc)
        rise = now + timedelta(minutes=60)  # beyond 30 min window
        mock_find.return_value = [
            iss.PassInfo(
                rise_time=rise,
                set_time=rise + timedelta(minutes=4),
                max_altitude_time=rise + timedelta(minutes=2),
                duration_seconds=240.0,
                max_altitude_degrees=45.0,
                rise_azimuth=0.0,
                set_azimuth=180.0,
                max_magnitude=-1.0,
                rise_magnitude=-0.5,
                set_magnitude=0.5,
                is_visible=True,
            )
        ]
        config_path = self._write_config(tmp_path)
        state_path = str(tmp_path / "state.json")

        with patch("iss_next_pass.NOTIFY_STATE_PATH", state_path):
            iss.run_check_notify(config_path)

        mock_send.assert_not_called()


# ============================================================================
# NOTIFICATION TESTS - CLI Arguments
# ============================================================================


class TestNotifyCLIArgs:
    """Tests for notification CLI argument parsing."""

    def test_check_notify_flag(self):
        """Test parsing --check-notify flag."""
        with patch("sys.argv", ["iss_next_pass.py", "--check-notify"]):
            args = iss.parse_arguments()
            assert args.check_notify is True

    def test_setup_notify_flag(self):
        """Test parsing --setup-notify flag."""
        with patch("sys.argv", ["iss_next_pass.py", "--setup-notify"]):
            args = iss.parse_arguments()
            assert args.setup_notify is True

    def test_notify_config_path(self):
        """Test parsing --notify-config with custom path."""
        with patch("sys.argv", ["iss_next_pass.py", "--notify-config", "/tmp/test.json"]):
            args = iss.parse_arguments()
            assert args.notify_config == "/tmp/test.json"

    def test_notify_config_default(self):
        """Test default notify-config path."""
        with patch("sys.argv", ["iss_next_pass.py"]):
            args = iss.parse_arguments()
            assert args.notify_config == iss.NOTIFY_CONFIG_PATH


# ============================================================================
# Main Test Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
