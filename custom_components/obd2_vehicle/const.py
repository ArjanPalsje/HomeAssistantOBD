"""Constants for the OBD2 Vehicle integration."""

from __future__ import annotations

from typing import Final

from .core.specs import CAT_EMISSIONS, CAT_ENGINE, CAT_EV, CAT_GENERAL, CAT_MANUFACTURER

DOMAIN: Final = "obd2_vehicle"

# Config entry data
CONF_CONNECTION: Final = "connection"
CONN_BLE: Final = "ble"
CONN_TCP: Final = "tcp"
CONN_SIMULATOR: Final = "simulator"
CONF_ADDRESS: Final = "address"
CONF_ADAPTER_NAME: Final = "adapter_name"
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_SIM_VEHICLE: Final = "sim_vehicle"
CONF_VEHICLE_NAME: Final = "vehicle_name"

# Options
CONF_MODE: Final = "mode"
CONF_PROFILE: Final = "profile"
CONF_LIVE_INTERVAL: Final = "live_interval"
CONF_CHARGING_INTERVAL: Final = "charging_interval"
CONF_PARKED_INTERVAL: Final = "parked_interval"
CONF_PROBE_INTERVAL: Final = "probe_interval"
CONF_FULL_INTERVAL: Final = "full_read_interval"
CONF_CATEGORIES: Final = "categories"
CONF_ADVANCED: Final = "advanced_diagnostics"
CONF_LOG_TRAFFIC: Final = "log_traffic"
CONF_COMMAND_TIMEOUT: Final = "command_timeout"

DEFAULT_PORT: Final = 35000
DEFAULT_MODE: Final = "smart"
DEFAULT_LIVE_INTERVAL: Final = 15
DEFAULT_CHARGING_INTERVAL: Final = 300
DEFAULT_PARKED_INTERVAL: Final = 1800
DEFAULT_PROBE_INTERVAL: Final = 3600
DEFAULT_FULL_INTERVAL: Final = 3600
DEFAULT_COMMAND_TIMEOUT: Final = 5.0
DEFAULT_CATEGORIES: Final = [CAT_GENERAL, CAT_ENGINE, CAT_EV, CAT_MANUFACTURER]
ALL_CATEGORIES: Final = [CAT_GENERAL, CAT_ENGINE, CAT_EMISSIONS, CAT_EV, CAT_MANUFACTURER]

#: A dongle must have been gone this long before reappearing counts as an arrival.
ARRIVAL_MIN_ABSENCE: Final = 300.0
#: Hard limit for a single read session.
SESSION_TIMEOUT: Final = 180.0
CONNECT_TIMEOUT: Final = 30.0

# Connection states (enum sensor)
STATE_NOT_IN_RANGE: Final = "not_in_range"
STATE_IDLE: Final = "idle"
STATE_CONNECTING: Final = "connecting"
STATE_READING: Final = "reading"
STATE_CONNECTED: Final = "connected"
STATE_VEHICLE_OFF: Final = "vehicle_off"
STATE_ERROR: Final = "error"
CONNECTION_STATES: Final = [
    STATE_NOT_IN_RANGE,
    STATE_IDLE,
    STATE_CONNECTING,
    STATE_READING,
    STATE_CONNECTED,
    STATE_VEHICLE_OFF,
    STATE_ERROR,
]

EVENT_READ_COMPLETED: Final = f"{DOMAIN}_read_completed"
EVENT_DTC_DETECTED: Final = f"{DOMAIN}_dtc_detected"

STORAGE_VERSION: Final = 1
