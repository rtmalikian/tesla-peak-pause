"""Configuration for Tesla Peak Pause. All settings come from the
environment (see .env.example). Copy .env.example to .env — .env is
git-ignored so secrets never leave your machine."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time as dtime


class ConfigError(Exception):
    pass


@dataclass
class VehicleConfig:
    name: str
    vin: str


@dataclass
class Settings:
    client_id: str = ""
    client_secret: str = ""
    region: str = "na"
    redirect_uri: str = "http://localhost:8080/callback"
    token_file: str = "~/.tcc/tokens.json"
    vehicles: list[VehicleConfig] = field(default_factory=list)
    peak_start: dtime = dtime(16, 0)
    peak_end: dtime = dtime(21, 0)
    # Python weekday ints (0=Monday..6=Sunday) the peak window applies to.
    days: set[int] = field(default_factory=lambda: set(range(7)))
    # Minutes on either side of the peak start (stopping early is harmless)
    # inside which a cron run acts; for the peak end, runs act at/after the
    # end within grace — the resume never fires early.
    grace_minutes: int = 5
    # "strict": act only within grace of the boundaries.
    # "window": inside the window -> stop-if-charging; outside -> resume-if-paused.
    boundary_mode: str = "strict"
    state_file: str = "~/.tcc/peak_pause_state.json"
    dry_run: bool = False


DAY_ABBR = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _parse_vehicles(raw: str) -> list[VehicleConfig]:
    """Format: name=VIN,name2=VIN2 (or bare VINs)."""
    vehicles = []
    for idx, item in enumerate(raw.split(",")):
        item = item.strip()
        if not item:
            continue
        name = None
        if "=" in item:
            name, item = item.split("=", 1)
            name = name.strip()
        vin = item.strip()
        if not vin:
            raise ConfigError("Empty VIN in TPP_VEHICLES")
        vehicles.append(VehicleConfig(name=name or f"car-{idx + 1}", vin=vin))
    if not vehicles:
        raise ConfigError("TPP_VEHICLES is empty — add at least one name=VIN entry")
    return vehicles


def _hhmm(env: dict, key: str, default: dtime) -> dtime:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        h, m = raw.split(":")
        t = dtime(int(h), int(m))
    except (ValueError, TypeError):
        raise ConfigError(f"{key} must be HH:MM (24h), got {raw!r}")
    if not (0 <= t.hour < 24 and 0 <= t.minute < 60):
        raise ConfigError(f"{key} must be HH:MM (24h), got {raw!r}")
    return t


def _days(env: dict, key: str, default: set[int]) -> set[int]:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return set(default)
    days: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part not in DAY_ABBR:
            raise ConfigError(
                f"{key}: unknown day {part!r} — use mon,tue,wed,thu,fri,sat,sun")
        days.add(DAY_ABBR[part])
    return days


def _int(env: dict, key: str, default: int) -> int:
    try:
        return int(env.get(key, default))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{key} must be an integer") from exc


def _bool(env: dict, key: str, default: bool) -> bool:
    return env.get(key, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


def load_settings(env: dict | None = None, require_auth: bool = True) -> Settings:
    env = env if env is not None else os.environ

    client_id = env.get("TESLA_CLIENT_ID", "").strip()
    client_secret = env.get("TESLA_CLIENT_SECRET", "").strip()
    if require_auth and (not client_id or not client_secret):
        raise ConfigError("TESLA_CLIENT_ID and TESLA_CLIENT_SECRET are required (see README).")

    region = env.get("TESLA_REGION", "na").strip().lower()
    if region not in ("na", "eu", "cn"):
        raise ConfigError(f"TESLA_REGION must be na/eu/cn, got {region!r}")

    vehicles_raw = env.get("TPP_VEHICLES", "").strip()
    vehicles = _parse_vehicles(vehicles_raw) if (require_auth or vehicles_raw) else []

    boundary_mode = env.get("TPP_BOUNDARY_MODE", "strict").strip().lower()
    if boundary_mode not in ("strict", "window"):
        raise ConfigError(f"TPP_BOUNDARY_MODE must be strict or window, got {boundary_mode!r}")

    grace = _int(env, "TPP_GRACE_MINUTES", 5)
    if grace < 0 or grace > 60:
        raise ConfigError(f"TPP_GRACE_MINUTES must be 0-60, got {grace}")

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        region=region,
        redirect_uri=env.get("TESLA_REDIRECT_URI", "http://localhost:8080/callback").strip(),
        token_file=env.get("TESLA_TOKEN_FILE", "~/.tcc/tokens.json").strip(),
        vehicles=vehicles,
        peak_start=_hhmm(env, "TPP_PEAK_START", dtime(16, 0)),
        peak_end=_hhmm(env, "TPP_PEAK_END", dtime(21, 0)),
        days=_days(env, "TPP_DAYS", set(range(7))),
        grace_minutes=grace,
        boundary_mode=boundary_mode,
        state_file=env.get("TPP_STATE_FILE", "~/.tcc/peak_pause_state.json").strip(),
        dry_run=_bool(env, "TPP_DRY_RUN", False),
    )
