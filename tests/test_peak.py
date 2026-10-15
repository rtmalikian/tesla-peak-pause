"""Decision-logic tests for Tesla Peak Pause, with edge cases."""
from datetime import datetime, time as dtime, timedelta

import pytest

from tesla_peak_pause.config import Settings, VehicleConfig
from tesla_peak_pause.peak import (
    ChargeStart, ChargeStop, PeakDecider, VehicleChargeState,
)


def make_settings(**over):
    kw = dict(
        vehicles=[VehicleConfig(name="Model Y", vin="VIN1")],
        peak_start=dtime(16, 0), peak_end=dtime(21, 0),
        days=set(range(7)), grace_minutes=5, boundary_mode="strict",
    )
    kw.update(over)
    return Settings(**kw)


def charging(vin="VIN1", reachable=True, charging=True, plugged=True):
    return VehicleChargeState(vin=vin, name="Car", reachable=reachable,
                              charging=charging, plugged=plugged, soc=62)


def mon(dt_h, dt_m, day=12):  # 2026-10-12 is a Monday
    return datetime(2026, 10, day, dt_h, dt_m).astimezone()


def test_stop_at_peak_start_when_charging():
    d = PeakDecider(make_settings())
    dec = d.decide([charging()], mon(16, 0))
    assert len(dec.actions) == 1 and isinstance(dec.actions[0], ChargeStop)
    assert "VIN1" in d.paused


def test_stop_slightly_early_and_late_within_grace():
    for t in (mon(15, 56), mon(16, 4)):
        d = PeakDecider(make_settings())
        dec = d.decide([charging()], t)
        assert isinstance(dec.actions[0], ChargeStop), t


def test_no_action_outside_boundaries():
    d = PeakDecider(make_settings())
    assert d.decide([charging()], mon(15, 0)).actions == []
    assert d.decide([charging()], mon(18, 0)).actions == []  # strict mode: mid-window idle


def test_no_stop_when_not_charging():
    d = PeakDecider(make_settings())
    dec = d.decide([charging(charging=False)], mon(16, 0))
    assert dec.actions == []


def test_stop_boundary_not_repeated():
    """Second run inside the same grace window issues no duplicate stop."""
    d = PeakDecider(make_settings())
    assert len(d.decide([charging()], mon(16, 0)).actions) == 1
    # car already stopped by us; run again at 16:03 -> nothing
    assert d.decide([charging(charging=False)], mon(16, 3)).actions == []


def test_mid_grace_plugin_caught():
    """Car starts charging between two runs inside the grace window."""
    d = PeakDecider(make_settings())
    assert d.decide([charging(charging=False)], mon(16, 0)).actions == []
    dec = d.decide([charging()], mon(16, 3))
    assert isinstance(dec.actions[0], ChargeStop)


def test_start_at_peak_end_when_paused_and_plugged():
    d = PeakDecider(make_settings())
    d.decide([charging()], mon(16, 0))  # stop -> paused
    dec = d.decide([charging(charging=False, plugged=True)], mon(21, 0))
    assert len(dec.actions) == 1 and isinstance(dec.actions[0], ChargeStart)
    assert "VIN1" not in d.paused


def test_no_start_when_never_paused():
    d = PeakDecider(make_settings())
    dec = d.decide([charging(charging=False, plugged=True)], mon(21, 0))
    assert dec.actions == []
    assert "no resume needed" in dec.note


def test_no_start_when_unplugged():
    d = PeakDecider(make_settings())
    d.decide([charging()], mon(16, 0))
    dec = d.decide([charging(charging=False, plugged=False)], mon(21, 0))
    assert dec.actions == []
    assert "VIN1" not in d.paused  # stale marker cleared


def test_no_start_when_already_charging():
    """User manually resumed mid-window; at peak end the car is already charging."""
    d = PeakDecider(make_settings())
    d.decide([charging()], mon(16, 0))
    dec = d.decide([charging()], mon(21, 0))
    assert dec.actions == []
    assert "VIN1" not in d.paused


def test_no_start_before_peak_end():
    """Resume never fires early — not even inside the grace window."""
    d = PeakDecider(make_settings())
    d.decide([charging()], mon(16, 0))
    assert d.decide([charging(charging=False, plugged=True)], mon(20, 58)).actions == []


def test_start_boundary_not_repeated():
    d = PeakDecider(make_settings())
    d.decide([charging()], mon(16, 0))
    # still plugged, not charging (resume failed silently?) -> second run same boundary
    d.decide([charging(charging=False, plugged=True)], mon(21, 0))
    # marker cleared, so nothing repeats even though car isn't charging
    assert d.decide([charging(charging=False, plugged=True)], mon(21, 2)).actions == []


def test_asleep_vehicle_skipped():
    d = PeakDecider(make_settings())
    dec = d.decide([charging(reachable=False)], mon(16, 0))
    assert dec.actions == []
    assert "never wake" in dec.note


def test_inactive_day_no_action():
    d = PeakDecider(make_settings(days={0, 1, 2, 3, 4}))  # weekdays only
    sat = datetime(2026, 10, 17, 16, 0).astimezone()  # Saturday
    assert d.decide([charging()], sat).actions == []


def test_overnight_wrapped_window():
    """Peak 22:00–06:00: stop at 22:00, resume at 06:00 next morning."""
    s = make_settings(peak_start=dtime(22, 0), peak_end=dtime(6, 0))
    d = PeakDecider(s)
    evening = mon(22, 0)  # Monday 22:00
    assert isinstance(d.decide([charging()], evening).actions[0], ChargeStop)
    morning = evening + timedelta(hours=8)  # Tuesday 06:00
    dec = d.decide([charging(charging=False, plugged=True)], morning)
    assert isinstance(dec.actions[0], ChargeStart)


def test_wrapped_window_respects_day_filter():
    """Days = weekday only: Monday 22:00 stops; Tuesday 06:00 resumes (peak started Monday)."""
    s = make_settings(peak_start=dtime(22, 0), peak_end=dtime(6, 0), days={0, 1, 2, 3, 4})
    d = PeakDecider(s)
    d.decide([charging()], mon(22, 0))
    tuesday_6am = mon(22, 0) + timedelta(hours=8)
    assert isinstance(d.decide([charging(charging=False, plugged=True)], tuesday_6am).actions[0], ChargeStart)


def test_window_mode_mid_window_catch():
    """window mode: a 18:00 cron run stops a car that started charging mid-peak."""
    d = PeakDecider(make_settings(boundary_mode="window"))
    dec = d.decide([charging()], mon(18, 0))
    assert isinstance(dec.actions[0], ChargeStop)


def test_window_mode_resume_after_end():
    d = PeakDecider(make_settings(boundary_mode="window"))
    d.decide([charging()], mon(18, 0))
    dec = d.decide([charging(charging=False, plugged=True)], mon(21, 5))
    assert isinstance(dec.actions[0], ChargeStart)


def test_window_mode_idle_when_nothing_to_do():
    d = PeakDecider(make_settings(boundary_mode="window"))
    assert d.decide([charging(charging=False)], mon(18, 0)).actions == []
    assert d.decide([charging(charging=False, plugged=True)], mon(21, 5)).actions == []


def test_state_roundtrip():
    d = PeakDecider(make_settings())
    d.decide([charging()], mon(16, 0))
    d2 = PeakDecider(make_settings())
    d2.from_dict(d.to_dict())
    dec = d2.decide([charging(charging=False, plugged=True)], mon(21, 0))
    assert isinstance(dec.actions[0], ChargeStart)
