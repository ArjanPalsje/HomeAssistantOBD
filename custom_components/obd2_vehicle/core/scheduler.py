"""Read scheduling (pure logic, no I/O – unit testable).

Decides *when* to talk to the vehicle, *how much* to read and whether the
link should stay open, based on the read mode and what we know about the
vehicle (dongle present, ECU awake, engine running, charging).

Design goals: no needless diagnostic traffic when the vehicle is off (which
could keep control units awake and drain the 12 V battery), quick snapshot
after arrival, frequent SOC updates while charging, exponential backoff on
connection problems.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReadMode(StrEnum):
    SNAPSHOT = "snapshot"
    LIVE = "live"
    SMART = "smart"


@dataclass(slots=True)
class PlannerConfig:
    mode: ReadMode = ReadMode.SMART
    live_interval: float = 15.0
    charging_interval: float = 300.0
    parked_interval: float = 1800.0
    #: How often to check whether a sleeping vehicle woke up (0 = never,
    #: only on arrival or manual request).
    probe_interval: float = 3600.0
    #: Delay after the adapter appears before the first read.
    arrival_delay: float = 5.0
    #: Attempts to read a vehicle that did not answer right after arrival.
    arrival_retries: int = 3
    arrival_retry_interval: float = 120.0
    #: Slow values (odometer, SOH, DTCs, …) are refreshed at most this often.
    full_read_interval: float = 3600.0
    backoff_base: float = 10.0
    backoff_max: float = 900.0
    #: Keep the link open when the next read is sooner than this.
    keep_connected_below: float = 60.0


@dataclass(slots=True)
class PlannerState:
    present: bool = False
    arrived_at: float | None = None
    last_attempt: float | None = None
    last_success: float | None = None
    last_full: float | None = None
    connect_failures: int = 0
    ecu_awake: bool | None = None
    engine_running: bool = False
    charging: bool = False
    snapshot_done_for_arrival: bool = False
    arrival_attempts: int = 0
    manual_request: bool = False
    manual_full: bool = False


@dataclass(frozen=True, slots=True)
class Plan:
    """Next action. ``run_in`` None means: wait for an external event."""

    run_in: float | None
    full: bool = False
    keep_connected: bool = False
    reason: str = ""


def backoff_delay(cfg: PlannerConfig, failures: int) -> float:
    if failures <= 0:
        return 0.0
    return min(cfg.backoff_base * (2 ** (failures - 1)), cfg.backoff_max)


def plan(cfg: PlannerConfig, st: PlannerState, now: float) -> Plan:
    """Return the next action for the given state."""
    if st.manual_request:
        return Plan(0.0, full=st.manual_full, keep_connected=False, reason="manual request")

    if not st.present:
        return Plan(None, reason="adapter not in range")

    # Respect connection backoff
    if st.connect_failures and st.last_attempt is not None:
        wait = st.last_attempt + backoff_delay(cfg, st.connect_failures) - now
        if wait > 0:
            return Plan(wait, reason=f"backoff after {st.connect_failures} failures")

    since_arrival = now - st.arrived_at if st.arrived_at is not None else None
    full_due = st.last_full is None or now - st.last_full >= cfg.full_read_interval

    # First read after arrival (all modes)
    if not st.snapshot_done_for_arrival and st.arrival_attempts < cfg.arrival_retries:
        if since_arrival is not None and since_arrival < cfg.arrival_delay:
            return Plan(cfg.arrival_delay - since_arrival, reason="waiting after arrival")
        if st.arrival_attempts and st.last_attempt is not None:
            # Vehicle did not answer right after arrival: a few spaced retries
            wait = st.last_attempt + cfg.arrival_retry_interval - now
            if wait > 0:
                return Plan(wait, reason="vehicle did not answer after arrival; retrying")
        return Plan(0.0, full=True, reason="snapshot after arrival")

    if cfg.mode is ReadMode.SNAPSHOT:
        return Plan(None, reason="snapshot done; waiting for next arrival")

    last = st.last_attempt or now

    if st.ecu_awake is False:
        if cfg.probe_interval <= 0:
            return Plan(None, reason="vehicle asleep, probing disabled")
        return Plan(max(0.0, last + cfg.probe_interval - now), full=full_due,
                    reason="vehicle asleep; periodic probe")

    if cfg.mode is ReadMode.LIVE:
        return _interval_plan(cfg, last, now, cfg.live_interval, full_due, "live mode")

    # SMART
    if st.engine_running:
        return _interval_plan(cfg, last, now, cfg.live_interval, full_due, "vehicle running")
    if st.charging:
        return _interval_plan(cfg, last, now, cfg.charging_interval, full_due, "charging")
    return _interval_plan(cfg, last, now, cfg.parked_interval, full_due, "parked, ignition on")


def _interval_plan(
    cfg: PlannerConfig, last: float, now: float, interval: float, full_due: bool, reason: str
) -> Plan:
    return Plan(
        max(0.0, last + interval - now),
        full=full_due,
        keep_connected=interval < cfg.keep_connected_below,
        reason=reason,
    )
