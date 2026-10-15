"""End-to-end test: fake fleet over virtual time.

Core invariant: exactly one charge_stop lands at the peak-window start,
zero charging happens during the peak window, and exactly one
charge_start resumes charging at the window end.
"""
from datetime import datetime, time as dtime

from tesla_peak_pause.config import Settings, VehicleConfig
from tesla_peak_pause.peak import PeakDecider
from tesla_peak_pause.simulator import FakeVehicle, run_simulation


def make_settings(**over):
    kw = dict(
        vehicles=[VehicleConfig(name="Model Y", vin="VIN1")],
        peak_start=dtime(16, 0), peak_end=dtime(21, 0),
        days=set(range(7)), grace_minutes=5, boundary_mode="strict",
    )
    kw.update(over)
    return Settings(**kw)


def test_full_day_pause_and_resume():
    start = datetime.now().astimezone().replace(hour=13, minute=0, second=0, microsecond=0)
    vehicles = [FakeVehicle(vin="VIN1", name="Model Y", plugged=True,
                            charging=True, soc=40)]
    settings = make_settings()
    report = run_simulation(vehicles, PeakDecider(settings), settings, start, hours=9.0)

    stops = [c for c in report["commands"] if c[0] == "charge_stop"]
    starts = [c for c in report["commands"] if c[0] == "charge_start"]
    assert len(stops) == 1, f"expected exactly one charge_stop, got {report['commands']}"
    assert len(starts) == 1, f"expected exactly one charge_start, got {report['commands']}"

    stop_at = next(t["time"] for t in report["timeline"]
                   if any(a[0] == "ChargeStop" for a in t["actions"]))
    assert "15:55" <= stop_at <= "16:05", f"stop fired at {stop_at}, expected ~16:00"
    start_at = next(t["time"] for t in report["timeline"]
                    if any(a[0] == "ChargeStart" for a in t["actions"]))
    assert "20:55" <= start_at <= "21:05", f"start fired at {start_at}, expected ~21:00"

    # Core invariant: the car never charged during the peak window.
    assert not report["charging_in_window"], "car charged during the peak window!"


def test_no_action_on_inactive_day():
    start = datetime(2026, 10, 17, 13, 0).astimezone().replace(microsecond=0)  # Saturday
    vehicles = [FakeVehicle(vin="VIN1", name="Model Y", plugged=True,
                            charging=True, soc=40)]
    settings = make_settings(days={0, 1, 2, 3, 4})  # weekdays only
    report = run_simulation(vehicles, PeakDecider(settings), settings, start, hours=9.0)
    assert report["commands"] == []
    # Car keeps charging all day — peak rules don't apply on Saturday.
    assert all(t["charging"]["Model Y"] for t in report["timeline"])


def test_asleep_car_never_woken():
    """An asleep car is skipped (no commands), and the sim can't wake it —
    matching the no-wake real behavior."""
    start = datetime.now().astimezone().replace(hour=13, minute=0, second=0, microsecond=0)
    vehicles = [FakeVehicle(vin="VIN1", name="Model Y", plugged=True,
                            charging=False, soc=40, asleep=True)]
    settings = make_settings()
    report = run_simulation(vehicles, PeakDecider(settings), settings, start, hours=9.0)
    assert report["commands"] == []
