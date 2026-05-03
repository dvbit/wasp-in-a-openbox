"""Constants for wasp_in_a_openbox.

This module defines all configuration keys, defaults, attribute names,
service identifiers, and state enums used across the integration.

References:
  - Spec §2 "Proposed sensor taxonomy" — sensor types and config keys
  - Spec §3 "Border sensor logic" — border-specific parameters
  - Spec §5 "Exposed attributes" — attribute constants
  - Spec §8 "Services" — service identifiers
"""

from __future__ import annotations

from logging import Logger, getLogger

from homeassistant.const import Platform

LOGGER: Logger = getLogger(__package__)

# Minimum Home Assistant version required for this integration.
MIN_HA_VERSION = "2026.1"

# Integration domain — used in manifest.json, config flow, and services.
DOMAIN = "wasp_in_a_openbox"

# Platforms provided by this integration.
PLATFORMS = [Platform.BINARY_SENSOR]

# ---------------------------------------------------------------------------
# Configuration keys — Spec §2 "Proposed sensor taxonomy"
# ---------------------------------------------------------------------------

# Room motion sensor entity id (the "wasp" — detects movement inside the room).
CONF_WASP_ID = "wasp_id"

# Door contact sensor entity id (the "box" — binary open/closed state).
# Optional: can be None for border-only rooms.
CONF_BOX_ID = "box_id"

# Seconds to wait after door closes before evaluating occupancy.
# Spec §3.3: "If motion is detected when the delay expires, occupied."
CONF_DOOR_CLOSED_DELAY = "door_closed_delay"

# Seconds of no motion with door open before marking unoccupied.
CONF_DOOR_OPEN_TIMEOUT = "door_open_timeout"

# When True, occupancy activates immediately on motion or door open.
# When False, the door_closed_delay applies before activation.
CONF_IMMEDIATE_ON = "immediate_on"

# ---------------------------------------------------------------------------
# Border sensor configuration keys — Spec §3.3 "New config parameters"
# ---------------------------------------------------------------------------

# List of curtain PIR / passage sensor entity ids.
# These detect crossings between rooms without a physical door.
CONF_BORDER_IDS = "border_ids"

# Seconds to correlate a border crossing with room motion to infer entry.
# Spec §3.1: "Border PIR fires → shortly after, room motion fires → entry."
CONF_BORDER_CORRELATION_WINDOW = "border_correlation_window"

# Seconds after a border crossing with no resumed motion → exit confirmed.
# Spec §3.2: "Room motion clear → border fires → no motion resumes → exit."
CONF_BORDER_EXIT_TIMEOUT = "border_exit_timeout"

# When True, no door sensor is used. Occupancy relies entirely on
# border + motion correlation. Spec §3.3: "border_only_mode".
CONF_BORDER_ONLY_MODE = "border_only_mode"

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

DEFAULT_DOOR_CLOSED_DELAY = 30       # seconds
DEFAULT_OPEN_DOOR_TIMEOUT = 300      # seconds (5 minutes)
DEFAULT_IMMEDIATE_ON = True

DEFAULT_BORDER_IDS: list[str] = []
DEFAULT_BORDER_CORRELATION_WINDOW = 5   # seconds
DEFAULT_BORDER_EXIT_TIMEOUT = 30        # seconds
DEFAULT_BORDER_ONLY_MODE = False

# ---------------------------------------------------------------------------
# Entity attributes — Spec §5 "Exposed attributes"
# ---------------------------------------------------------------------------

# State of the room motion sensor (on/off/unknown).
ATTR_MOTION_SENSOR_STATE = "motion_sensor_state"

# State of the door contact sensor (on=open / off=closed / none / unknown).
ATTR_DOOR_SENSOR_STATE = "door_sensor_state"

# Dict of {border_entity_id: last_triggered_iso_timestamp or None}.
ATTR_BORDER_SENSORS_STATE = "border_sensors_state"

# ISO timestamp of the most recent border crossing event.
ATTR_LAST_BORDER_EVENT = "last_border_event"

# Inferred direction of the last border crossing: entry / exit / unknown.
ATTR_INFERRED_DIRECTION = "inferred_direction"

# Which logic path determined the current occupancy state.
ATTR_OCCUPANCY_SOURCE = "occupancy_source"

# Whether the room is fully enclosed (door only, no borders).
# Spec §7: "fully_enclosed: false when borders are present."
ATTR_FULLY_ENCLOSED = "fully_enclosed"

# ---------------------------------------------------------------------------
# Service identifiers — Spec §8 "Services"
# ---------------------------------------------------------------------------

# Reset occupancy to off, cancel all timers, clear border history.
SERVICE_RESET = "reset"

# Manually signal an entry event — Spec §8: "force_entry".
SERVICE_FORCE_ENTRY = "force_entry"

# Manually signal an exit event — Spec §8: "force_exit".
SERVICE_FORCE_EXIT = "force_exit"

# ---------------------------------------------------------------------------
# Direction inference values — Spec §5 "inferred_direction"
# ---------------------------------------------------------------------------

DIRECTION_ENTRY = "entry"
DIRECTION_EXIT = "exit"
DIRECTION_UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Occupancy source values — Spec §5 "occupancy_source"
# ---------------------------------------------------------------------------

SOURCE_DOOR = "door"        # Classic door logic determined state
SOURCE_BORDER = "border"    # Border sensor correlation determined state
SOURCE_MOTION = "motion"    # Motion alone (immediate_on) determined state
SOURCE_MANUAL = "manual"    # force_entry / force_exit service was called
SOURCE_NONE = "none"        # No occupancy established
