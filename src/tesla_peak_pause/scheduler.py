"""Cron pass: read charge state (no-wake), decide, execute.

This app is schedule-driven, NOT a poller: `tpp run` does exactly one
pass per invocation. Point two cron entries at it — one at the peak
start, one at the peak end. The car is never woken just to decide.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .config import Settings
from .peak import ChargeStart, ChargeStop, Decision, PeakDecider, VehicleChargeState
from .tesla_client import TeslaApiError, TeslaClient

log = logging.getLogger("tpp")


def build_states(client, settings: Settings) -> list[VehicleChargeState]:
    """Read charge state WITHOUT waking the car.

    If the vehicle is asleep or unreachable we mark it unreachable and the
    decider skips it — wakes are the priciest Fleet API call and are never
    spent just to decide.
    """
    states = []
    for vc in settings.vehicles:
        try:
            data = client.vehicle_data(vc.vin)
        except TeslaApiError as exc:
            log.warning("%s: unreachable: %s", vc.name, exc)
            states.append(VehicleChargeState(vin=vc.vin, name=vc.name,
                                             reachable=False, charging=False,
                                             plugged=False))
            continue
        if (data or {}).get("state") != "online":
            log.info("%s: asleep (%s) — skipping, no wake", vc.name, data.get("state"))
            states.append(VehicleChargeState(vin=vc.vin, name=vc.name,
                                             reachable=False, charging=False,
                                             plugged=False))
            continue
        charge = data.get("charge_state", {}) or {}
        charging_state = str(charge.get("charging_state", ""))
        latch = str(charge.get("charge_port_latch", ""))
        states.append(VehicleChargeState(
            vin=vc.vin,
            name=vc.name,
            reachable=True,
            charging=charging_state == "Charging",
            plugged=latch == "Engaged",
            soc=charge.get("battery_level"),
        ))
    return states


def load_decider(settings: Settings) -> PeakDecider:
    decider = PeakDecider(settings)
    path = Path(settings.state_file).expanduser()
    if path.exists():
        try:
            decider.from_dict(json.loads(path.read_text()))
        except Exception as exc:
            log.warning("Could not load state file: %s", exc)
    return decider


def save_decider(decider: PeakDecider, settings: Settings) -> None:
    path = Path(settings.state_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decider.to_dict(), indent=2))


def execute(client, settings: Settings, decision: Decision) -> None:
    for action in decision.actions:
        if isinstance(action, ChargeStop):
            log.info("CHARGE STOP  %s (%s)", action.name, action.reason)
            if not settings.dry_run:
                try:
                    client.charge_stop(action.vin)
                except TeslaApiError as exc:
                    log.warning("charge_stop failed for %s: %s", action.name, exc)
        elif isinstance(action, ChargeStart):
            log.info("CHARGE START %s (%s)", action.name, action.reason)
            if not settings.dry_run:
                try:
                    client.charge_start(action.vin)
                except TeslaApiError as exc:
                    log.warning("charge_start failed for %s: %s", action.name, exc)
    if decision.note:
        log.info("note: %s", decision.note)


def run_once(client, decider: PeakDecider, settings: Settings,
             now: datetime | None = None) -> Decision:
    """Single sense-decide-act pass (cron calls this once and exits)."""
    now = now or datetime.now().astimezone()
    states = build_states(client, settings)
    for s in states:
        log.info("%-12s reachable=%s charging=%s plugged=%s soc=%s",
                 s.name, s.reachable, s.charging, s.plugged, s.soc)
    decision = decider.decide(states, now)
    execute(client, settings, decision)
    save_decider(decider, settings)
    return decision
