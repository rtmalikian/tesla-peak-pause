"""Fake fleet for testing and demos. No API calls, no real cars."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# API cost model (community-reported rates, ~$1/1000 commands, ~$1/500 data
# calls — re-verify against developer.tesla.com): each `run` pass does one
# no-wake data read per vehicle, plus at most one command per boundary.
READS_PER_CRON_RUN = 1
COMMANDS_PER_CRON_RUN_MAX = 1
CRON_RUNS_PER_DAY = 2
READ_COST = 1 / 500    # $ per data call
COMMAND_COST = 1 / 1000  # $ per command


@dataclass
class FakeVehicle:
    vin: str
    name: str
    plugged: bool = True
    charging: bool = False
    soc: int = 60
    asleep: bool = False  # when True, vehicle_data reports a non-online state

    def tick(self, minutes: float) -> None:
        # Battery gains ~1% SoC per 6 minutes of charging in the sim.
        if self.charging and not self.asleep:
            self.soc = min(100, self.soc + int(round(minutes / 6)))
            if self.soc >= 100:
                self.charging = False


class FakeTeslaClient:
    """Stand-in for TeslaClient with the methods the scheduler uses."""

    def __init__(self, vehicles: list[FakeVehicle]):
        self.vehicles = {v.vin: v for v in vehicles}
        self.command_log: list[tuple[str, str]] = []

    def vehicle_data(self, vin: str) -> dict:
        """No-wake read: never wakes an asleep car, mirrors the real API."""
        v = self.vehicles[vin]
        if v.asleep:
            return {"state": "asleep"}
        return {
            "state": "online",
            "charge_state": {
                "charging_state": "Charging" if v.charging else
                                  ("Stopped" if v.plugged else "Disconnected"),
                "charge_port_latch": "Engaged" if v.plugged else "Disengaged",
                "battery_level": v.soc,
            },
        }

    def charge_stop(self, vin: str) -> dict:
        self.vehicles[vin].charging = False
        self.command_log.append(("charge_stop", vin))
        return {"result": True}

    def charge_start(self, vin: str) -> dict:
        v = self.vehicles[vin]
        if v.plugged:
            v.charging = True
        self.command_log.append(("charge_start", vin))
        return {"result": True}

    def tick(self, minutes: float) -> None:
        for v in self.vehicles.values():
            v.tick(minutes)


def estimate_daily_cost(commands_per_day: int, vehicles: int) -> str:
    reads = READS_PER_CRON_RUN * CRON_RUNS_PER_DAY * vehicles
    max_cmds = COMMANDS_PER_CRON_RUN_MAX * CRON_RUNS_PER_DAY * vehicles
    monthly = 30 * (reads * READ_COST + max_cmds * COMMAND_COST)
    return (f"~${monthly:.2f}/month per vehicle at {CRON_RUNS_PER_DAY} cron runs/day "
            f"({reads} no-wake reads/day, up to {max_cmds} commands/day; "
            f"community-reported rates ~$1/500 data calls, ~$1/1000 commands "
            f"— re-verify against developer.tesla.com)")


def run_simulation(vehicles: list[FakeVehicle], decider, settings,
                   start: datetime, hours: float = 9.0,
                   tick_minutes: float = 2.0) -> dict:
    """Drive the real scheduler against the fake fleet over virtual time."""
    from .scheduler import run_once

    client = FakeTeslaClient(vehicles)
    now = start
    end = start + timedelta(hours=hours)
    timeline: list[dict] = []
    while now < end:
        decision = run_once(client, decider, settings, now=now)
        timeline.append({
            "time": now.strftime("%H:%M"),
            "charging": {v.name: v.charging for v in vehicles},
            "note": decision.note,
            "actions": [(type(a).__name__, a.name) for a in decision.actions],
        })
        client.tick(tick_minutes)
        now += timedelta(minutes=tick_minutes)
    charging_in_window = False
    for t in timeline:
        tod = datetime.strptime(t["time"], "%H:%M").time()
        check = now.replace(hour=tod.hour, minute=tod.minute, second=0, microsecond=0)
        if decider._in_window(check) and any(t["charging"].values()):
            charging_in_window = True
            break
    n_cmd = len(client.command_log)
    return {
        "timeline": timeline,
        "commands": client.command_log,
        "charging_in_window": charging_in_window,
        "cost_estimate": estimate_daily_cost(n_cmd, len(vehicles)),
    }
