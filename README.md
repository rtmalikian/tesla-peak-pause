# Tesla Peak Pause — Stop Charging During Expensive Peak Hours

**Free & open source Tesla Fleet API app that stops your Model 3 / Model Y / Model S / Model X / Cybertruck from charging during expensive time-of-use peak hours, and resumes charging when the cheap window returns. Because Tesla's native off-peak scheduling only takes a start time — so it charges straight through the peak window.**

Tesla's built-in scheduled charging asks for an *off-peak start time* and nothing else. On an SCE-style time-of-use plan the expensive peak is 4–9pm — and the car will happily charge straight through it. This script fixes that: one `charge_stop` at your peak-window start, one `charge_start` at the end.

## Why?

- On SCE's 4–9pm peak, charging at peak rates can cost **2–3x the off-peak price**. One forgotten plug-in at 5pm is real money.
- Tesla's off-peak schedule has no end time: it starts charging at your off-peak start and keeps going through the whole peak window the next day.
- Paid apps (Tessie, Teslabot-style services) solve this on third-party APIs for a monthly fee. This does it with **two Fleet API calls a day on Tesla's official API**.

## How it works

Cron-driven, **not a poller**: two cron entries — one at `TPP_PEAK_START`, one at `TPP_PEAK_END`. Each run does exactly one thing:

1. **Peak start run:** no-wake read of each car's charging state. If it's charging → `charge_stop` (pause recorded locally). If not charging → nothing.
2. **Peak end run:** if *this script* paused the car and the cable is still plugged in → `charge_start`. Unplugged? Pause marker cleared, nothing sent.

The car is **never woken to make the decision** — wakes are the priciest Fleet API call. An asleep car is simply skipped. Resume only happens for cars the script itself paused, so it never fights your manual charging decisions.

```
13:00  Plugged in, charging            → nothing (off-peak, cron idle)
16:00  Peak window starts (4pm)        → ✅ charge_stop (paused for peak)
16:05  Still in peak                   → nothing (already stopped)
18:30  You plug in mid-peak            → nothing in strict mode*
21:00  Peak window ends (9pm)          → ✅ charge_start (cheap rates resume)
21:05  Charging again                  → nothing
```

*Mid-peak plug-ins: set `TPP_BOUNDARY_MODE=window` and add a third cron run mid-window — it stops any car found charging during the window.

**Expected API cost:** ~$0.18/month per vehicle at the default 2 cron runs/day (2 no-wake data reads + up to 2 commands/day; community-reported rates ~$1/500 data calls, ~$1/1000 commands — re-verify against developer.tesla.com). Wakes are never issued by this app.

## Quickstart

```bash
git clone https://github.com/rtmalikian/tesla-peak-pause.git
cd tesla-peak-pause
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. Configure (secrets stay local — .env is git-ignored)
cp .env.example .env
# edit .env: credentials, TPP_VEHICLES, TPP_PEAK_START/TPP_PEAK_END

# 2. Authorize with your Tesla account (one-time)
tpp auth

# 3. Install the virtual key on each car (one-time)
tpp pairing

# 4. Watch it work on a fake peak day — no API calls, no real cars
tpp simulate

# 5. Single safe pass against your real cars
tpp run

# 6. Add the two cron entries (example: peak 16:00–21:00)
#    0 16 * * * cd /path/to/tesla-peak-pause && .venv/bin/tpp run
#    0 21 * * * cd /path/to/tesla-peak-pause && .venv/bin/tpp run
```

## Tesla developer setup (one-time, ~15 minutes)

1. Register an app at [developer.tesla.com](https://developer.tesla.com) and get a client ID + secret.
2. Add scopes: `openid offline_access vehicle_device_data vehicle_cmds vehicle_charging_cmds`.
3. `tpp auth` — sign in with your Tesla account; tokens are stored at `~/.tcc/tokens.json` (mode 600).
4. `tpp pairing` — host your public key, register the domain, approve it on each car's touchscreen at `https://www.tesla.com/_ak/<your-domain>`.
5. Set `TPP_PEAK_START`/`TPP_PEAK_END` to your utility's peak hours and `TPP_DAYS` to the days they apply (some TOU plans differ on weekends).

## Configuration (`.env` reference)

| Variable | Default | Meaning |
|---|---|---|
| `TESLA_CLIENT_ID` / `TESLA_CLIENT_SECRET` | — | Your Tesla developer app credentials |
| `TESLA_REGION` | `na` | `na`, `eu`, or `cn` |
| `TESLA_REDIRECT_URI` | `http://localhost:8080/callback` | OAuth callback |
| `TESLA_TOKEN_FILE` | `~/.tcc/tokens.json` | Local token storage (mode 600) |
| `TPP_VEHICLES` | — | `name=VIN,...` — one entry per car |
| `TPP_PEAK_START` | `16:00` | Peak window start (24h HH:MM) |
| `TPP_PEAK_END` | `21:00` | Peak window end (24h HH:MM); may cross midnight (e.g. `22:00`–`06:00`) |
| `TPP_DAYS` | all 7 | Days the peak window applies: `mon,tue,wed,thu,fri,sat,sun` |
| `TPP_GRACE_MINUTES` | `5` | Minutes on either side of the peak start a cron run acts in; for the peak end, runs act at/after the end within grace (resume never fires early) |
| `TPP_BOUNDARY_MODE` | `strict` | `strict`: act only near boundaries. `window`: any in-window run stops charging, any out-of-window run resumes a paused car |
| `TPP_STATE_FILE` | `~/.tcc/peak_pause_state.json` | Local pause tracking (which cars WE paused) |
| `TPP_DRY_RUN` | `false` | Log what WOULD happen without sending commands |

## Try before you connect: `tpp simulate`

Runs a virtual peak day against a fake car: charging from 13:00, `charge_stop` at 16:00, quiet through the window, `charge_start` at 21:00. Prints the timeline, a PASS/FAIL verdict, and the estimated API cost. Zero credentials, zero network.

## FAQ

**Why not just use Tesla's scheduled charging?**
Tesla's off-peak scheduling takes a start time, not an end time. It begins charging at the cheap start time and keeps charging straight through the next day's peak window. This script is the missing end time.

**Will it stop my road-trip Supercharging?**
It acts on any charging session found at the peak boundary — home or away. In practice you'll charge overnight on trips and be driving during peak hours; if you know you'll Supercharge in the evening, skip the peak cron run (or unplug), same as any manual habit.

**What if the car is asleep when the peak starts?**
The run is skipped — this app never wakes the car to decide (wakes are the most expensive Fleet API call). Note: a charging car is never asleep, so the peak-start pause always lands.

**What if I unplug after it paused my car?**
At the peak-end run it sees the cable unplugged, clears its pause marker, and sends nothing. Plug back in and charging resumes under your car's normal schedule.

**Can I resume charging manually during the peak?**
Yes — the resume run only fires for cars the script itself paused, and only if the cable is still plugged in. If you manually resumed at 19:00, the 21:00 run just clears the marker.

**What if I plug in mid-peak?**
In default `strict` mode the stop already fired, so it keeps charging. Set `TPP_BOUNDARY_MODE=window` and add a third cron run mid-window — it stops any car found charging during the window.

**Does it work with multiple cars?**
Yes — every vehicle in `TPP_VEHICLES` is checked independently each pass; each car's pause state is tracked separately.

## Development

```bash
pip install -e .          # install
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
PYTHONPATH=src .venv/bin/python -m tesla_peak_pause.cli simulate
```

Decision logic lives in `src/tesla_peak_pause/peak.py` — pure functions, `now` injected, no network. The Fleet API wrapper is `tesla_client.py` (official `vehicle_data` reads plus `charge_start`/`charge_stop` commands).

## License

MIT — Raphael Malikian.
