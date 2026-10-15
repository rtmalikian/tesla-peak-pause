"""CLI: tpp auth | status | run | simulate | pairing"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from datetime import datetime, time as dtime, timedelta

from dotenv import load_dotenv

from . import __version__
from .config import ConfigError, Settings, VehicleConfig, load_settings
from .peak import PeakDecider
from .scheduler import build_states, load_decider, run_once
from .simulator import FakeVehicle, run_simulation
from .tesla_client import TeslaApiError, TeslaClient

log = logging.getLogger("tpp")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")


def _client(settings: Settings) -> TeslaClient:
    return TeslaClient(client_id=settings.client_id, client_secret=settings.client_secret,
                       region=settings.region, redirect_uri=settings.redirect_uri,
                       token_file=settings.token_file)


def cmd_auth(args, settings: Settings) -> int:
    client = _client(settings)
    url = client.authorize_url()
    print("1. Open this URL and sign in with your Tesla account:\n\n   %s\n" % url)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("2. After sign-in you'll land on a localhost URL that fails to load.")
    print("   Paste that FULL redirected URL here (it contains ?code=...).")
    redirected = input("Redirected URL: ").strip()
    if "code=" not in redirected:
        print("ERROR: no ?code= found.", file=sys.stderr)
        return 1
    try:
        client.exchange_code(redirected.split("code=")[1].split("&")[0])
    except TeslaApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"\nAuthorized! Tokens saved to {settings.token_file} (mode 600).")
    print("Next: run `tpp pairing` for the one-time virtual-key setup.")
    return 0


def cmd_pairing(_args, _settings: Settings) -> int:
    print(
        "One-time virtual-key setup (required before commands work):\n\n"
        "  1. Host your app's public key at\n"
        "     https://<your-domain>/.well-known/appspecific/com.tesla.3p.public-key.pem\n"
        "  2. Register it: POST /api/1/partner_accounts {\"domain\": \"<your-domain>\"}\n"
        "  3. In EACH Tesla, open https://www.tesla.com/_ak/<your-domain>\n"
        "     and approve the key on the touchscreen.\n"
    )
    return 0


def cmd_status(_args, settings: Settings) -> int:
    client = _client(settings)
    states = build_states(client, settings)
    print(f"{'Vehicle':<12}{'Reachable':<11}{'Charging':<10}{'Plugged':<9}SoC")
    print("-" * 50)
    for s in states:
        print(f"{s.name:<12}{str(s.reachable):<11}{str(s.charging):<10}"
              f"{str(s.plugged):<9}{s.soc if s.soc is not None else '-'}")
    return 0


def cmd_run(_args, settings: Settings) -> int:
    """One cron pass: sense, decide, act, exit. Never loops."""
    run_once(_client(settings), load_decider(settings), settings)
    return 0


def cmd_simulate(_args, _settings: Settings) -> int:
    """Demo day: car starts charging at 13:00, peak window 16:00–21:00."""
    start = datetime.now().astimezone().replace(hour=13, minute=0, second=0, microsecond=0)
    vehicles = [FakeVehicle(vin="SIM1", name="Model Y", plugged=True,
                            charging=True, soc=62)]
    settings = Settings(
        vehicles=[VehicleConfig(name="Model Y", vin="SIM1")],
        peak_start=dtime(16, 0), peak_end=dtime(21, 0),
        days=set(range(7)), grace_minutes=5, boundary_mode="strict",
    )
    decider = PeakDecider(settings)
    report = run_simulation(vehicles, decider, settings, start, hours=9.0)

    print(f"\nSimulating a day — charging from 13:00, peak window 16:00–21:00\n")
    print(f"{'Time':<8}{'Charging':<10}Note")
    print("-" * 60)
    last = None
    for tick in report["timeline"]:
        key = (tick["charging"]["Model Y"], tick["note"])
        if key != last:
            print(f"{tick['time']:<8}{str(tick['charging']['Model Y']):<10}{tick['note']}")
            last = key
    stops = [c for c in report["commands"] if c[0] == "charge_stop"]
    starts = [c for c in report["commands"] if c[0] == "charge_start"]
    print(f"\ncharge_stop issued: {len(stops)} time(s) "
          f"({'PASS' if len(stops) == 1 else 'FAIL — bug!'})")
    print(f"charge_start issued: {len(starts)} time(s) "
          f"({'PASS' if len(starts) == 1 else 'FAIL — bug!'})")
    print(f"Charging during peak window 16:00–21:00: "
          f"{'yes — FAIL' if report['charging_in_window'] else 'no (PASS)'}")
    print(f"\nEstimated API cost: {report['cost_estimate']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tpp",
                                description="Pause Tesla charging during peak TOU hours.")
    p.add_argument("--version", action="version", version=f"tpp {__version__}")
    p.add_argument("--env-file", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("auth", help="Authorize with your Tesla account (one-time)")
    sub.add_parser("pairing", help="Virtual-key pairing instructions")
    sub.add_parser("status", help="Show charging state (no-wake read)")
    sub.add_parser("run", help="Single cron pass: sense, decide, act, exit")
    sub.add_parser("simulate", help="Demo a peak-window day against a fake car (no API calls)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    load_dotenv(args.env_file)
    try:
        settings = load_settings(require_auth=args.command in ("auth", "status", "run"))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    return {
        "auth": cmd_auth, "pairing": cmd_pairing, "status": cmd_status,
        "run": cmd_run, "simulate": cmd_simulate,
    }[args.command](args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
