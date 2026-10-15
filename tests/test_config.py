"""Config parsing tests."""
from datetime import time as dtime

import pytest

from tesla_peak_pause.config import ConfigError, load_settings

BASE = {
    "TESLA_CLIENT_ID": "id",
    "TESLA_CLIENT_SECRET": "secret",
    "TPP_VEHICLES": "Daily=VIN1,Second=VIN2",
}


def test_defaults():
    s = load_settings(dict(BASE))
    assert s.peak_start == dtime(16, 0)
    assert s.peak_end == dtime(21, 0)
    assert s.days == set(range(7))
    assert s.grace_minutes == 5
    assert s.boundary_mode == "strict"
    assert s.dry_run is False
    assert [(v.name, v.vin) for v in s.vehicles] == [("Daily", "VIN1"), ("Second", "VIN2")]


def test_peak_window_parsing():
    s = load_settings({**BASE, "TPP_PEAK_START": "17:30", "TPP_PEAK_END": "20:45"})
    assert s.peak_start == dtime(17, 30)
    assert s.peak_end == dtime(20, 45)


def test_bad_time_rejected():
    for bad in ("4pm", "25:00", "16:60", "16", "16:00:00"):
        with pytest.raises(ConfigError):
            load_settings({**BASE, "TPP_PEAK_START": bad})


def test_empty_time_falls_back_to_default():
    s = load_settings({**BASE, "TPP_PEAK_START": ""})
    assert s.peak_start == dtime(16, 0)


def test_days_parsing():
    s = load_settings({**BASE, "TPP_DAYS": "mon,tue,wed,thu,fri"})
    assert s.days == {0, 1, 2, 3, 4}
    s = load_settings({**BASE, "TPP_DAYS": "sat,sun"})
    assert s.days == {5, 6}


def test_bad_day_rejected():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TPP_DAYS": "mon,funday"})


def test_bad_boundary_mode_rejected():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TPP_BOUNDARY_MODE": "chaos"})


def test_window_mode_accepted():
    s = load_settings({**BASE, "TPP_BOUNDARY_MODE": "window"})
    assert s.boundary_mode == "window"


def test_grace_bounds():
    s = load_settings({**BASE, "TPP_GRACE_MINUTES": "0"})
    assert s.grace_minutes == 0
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TPP_GRACE_MINUTES": "61"})
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TPP_GRACE_MINUTES": "five"})


def test_missing_vehicles_rejected():
    env = dict(BASE)
    del env["TPP_VEHICLES"]
    with pytest.raises(ConfigError):
        load_settings(env)


def test_simulate_needs_no_config():
    s = load_settings({}, require_auth=False)
    assert s.vehicles == []
    assert s.peak_start == dtime(16, 0)
