"""Decision logic: stop charging at the peak-window start, resume at the end.

Pure logic — no network, `now` injected — so it is fully unit-testable.

The app is cron-driven, not a poller: the cron runs at (or near) the peak
start and peak end times. In "strict" mode (default) a run only acts near
a boundary: within TPP_GRACE_MINUTES on either side of the peak start
(stopping early is harmless), or at/after the peak end within grace (the
resume never fires early). In "window"
mode any run inside the peak window acts like a stop-boundary run and any
run outside it resumes a paused vehicle — useful with a mid-window cron
run to catch cars plugged in after the window started.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class VehicleChargeState:
    vin: str
    name: str
    reachable: bool          # False when asleep/unreachable (never wake to decide)
    charging: bool
    plugged: bool
    soc: int | None = None


@dataclass
class ChargeStop:
    vin: str
    name: str
    reason: str


@dataclass
class ChargeStart:
    vin: str
    name: str
    reason: str


@dataclass
class Decision:
    actions: list = field(default_factory=list)
    note: str = ""


class PeakDecider:
    """Decides charge pause/resume actions for the fleet at a given moment."""

    def __init__(self, settings):
        self.s = settings
        self.paused: set[str] = set()          # vins WE stopped this window
        self.acted: dict[str, set[str]] = {}   # vin -> boundary ids already acted on

    # -- helpers ---------------------------------------------------------
    def _day_active(self, day) -> bool:
        """`day` is a date or weekday int."""
        wd = day.weekday() if hasattr(day, "weekday") else day
        return wd in self.s.days

    def _in_range(self, now: datetime, t, before: timedelta, after: timedelta) -> bool:
        """Is now within [t - before, t + after] today?"""
        anchor = datetime.combine(now.date(), t)
        cur = datetime.combine(now.date(), now.time())
        return anchor - before <= cur <= anchor + after

    def _boundary(self, now: datetime) -> str | None:
        """Classify this cron run: 'stop', 'start', or None.

        The stop boundary is two-sided (stopping a few minutes early is
        harmless). The start boundary is one-sided: the resume only fires
        at or after the peak end — never early, when power is still at
        peak price.
        """
        start, end = self.s.peak_start, self.s.peak_end
        today = now.date()
        grace = timedelta(minutes=self.s.grace_minutes)
        wrapped = end <= start
        if not wrapped:
            if self._day_active(today) and self._in_range(now, start, grace, grace):
                return "stop"
            if self._day_active(today) and self._in_range(now, end, timedelta(0), grace):
                return "start"
        else:
            if self._day_active(today) and self._in_range(now, start, grace, grace):
                return "stop"
            if (self._in_range(now, end, timedelta(0), grace)
                    and self._day_active(today - timedelta(days=1))):
                return "start"
        return None

    def _in_window(self, now: datetime) -> bool:
        """Is `now` inside the peak window (day-aware, wrap-aware)?"""
        start, end = self.s.peak_start, self.s.peak_end
        today = now.date()
        tod = now.time()
        wrapped = end <= start
        if not wrapped:
            return start <= tod < end and self._day_active(today)
        return ((tod >= start and self._day_active(today))
                or (tod < end and self._day_active(today - timedelta(days=1))))

    def _fmt(self, t) -> str:
        return t.strftime("%H:%M")

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "paused": sorted(self.paused),
            "acted": {vin: sorted(bids) for vin, bids in self.acted.items()},
        }

    def from_dict(self, data: dict) -> None:
        self.paused = set(data.get("paused", []))
        self.acted = {vin: set(bids) for vin, bids in data.get("acted", {}).items()}

    # -- main --------------------------------------------------------------
    def decide(self, states: list[VehicleChargeState], now: datetime) -> Decision:
        actions: list = []
        notes: list[str] = []
        strict = self.s.boundary_mode == "strict"
        boundary = self._boundary(now) if strict else None
        in_window = self._in_window(now) if not strict else None

        for st in states:
            if not st.reachable:
                notes.append(f"{st.name}: asleep/unreachable — skipping (never wake to decide)")
                continue

            if strict:
                if boundary is None:
                    continue  # nothing to do at this time
                role = boundary
                bid = f"{now.date().isoformat()}:{role}"
            else:
                if in_window:
                    role = "stop"
                    bid = None  # window mode: idempotent via state, no bid bookkeeping
                elif st.vin in self.paused:
                    role = "start"
                    bid = None
                else:
                    continue

            if bid is not None and bid in self.acted.get(st.vin, set()):
                continue

            if role == "stop":
                if st.charging:
                    actions.append(ChargeStop(
                        st.vin, st.name,
                        f"peak window {self._fmt(self.s.peak_start)}–{self._fmt(self.s.peak_end)} started",
                    ))
                    self.paused.add(st.vin)
                    notes.append(f"{st.name}: charge STOPPED for peak window")
                    # Mark the boundary only when we actually stopped: a later
                    # run inside the same grace window can still catch a car
                    # that starts charging mid-grace.
                    if bid is not None:
                        self.acted.setdefault(st.vin, set()).add(bid)
                else:
                    notes.append(f"{st.name}: not charging at peak boundary — nothing to stop")
            else:  # "start"
                if st.vin in self.paused:
                    if st.plugged and not st.charging:
                        actions.append(ChargeStart(
                            st.vin, st.name,
                            f"peak window ended at {self._fmt(self.s.peak_end)}",
                        ))
                        notes.append(f"{st.name}: charge RESUMED after peak window")
                    elif not st.plugged:
                        notes.append(f"{st.name}: unplugged since pause — no resume, marker cleared")
                    else:
                        notes.append(f"{st.name}: already charging — pause marker cleared")
                    self.paused.discard(st.vin)
                else:
                    notes.append(f"{st.name}: nothing was paused — no resume needed")
                if bid is not None:
                    self.acted.setdefault(st.vin, set()).add(bid)

        return Decision(actions, note="; ".join(notes))
