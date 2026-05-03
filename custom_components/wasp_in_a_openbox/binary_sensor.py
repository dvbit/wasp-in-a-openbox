"""Binary sensor platform for wasp_in_a_openbox.

Implements a three-mode occupancy state machine:
  1. Door-only mode   — classic wasp-in-a-box (Spec §4.1, door path)
  2. Mixed mode       — door + border sensors (Spec §4.1)
  3. Border-only mode — no door, border + motion correlation (Spec §4.2)

Key concepts:
  - A door sensor has PERSISTENT state (open/closed) — "seals" the room.
  - A border PIR fires TRANSIENT events — detects crossings, not state.
  - Direction inference uses motion correlation within time windows.

References:
  - Spec §3 "Border sensor logic" — entry/exit inference
  - Spec §4 "State machine enhancement" — three-mode calculation
  - Spec §5 "Exposed attributes" — extra_state_attributes
  - Spec §7 "Edge cases" — debounce, shared sensors, PIR cooldown
  - Spec §8 "Services" — reset, force_entry, force_exit
  - HA docs: https://developers.home-assistant.io/docs/core/entity/binary-sensor
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_BORDER_SENSORS_STATE,
    ATTR_DOOR_SENSOR_STATE,
    ATTR_FULLY_ENCLOSED,
    ATTR_INFERRED_DIRECTION,
    ATTR_LAST_BORDER_EVENT,
    ATTR_MOTION_SENSOR_STATE,
    ATTR_OCCUPANCY_SOURCE,
    CONF_BORDER_CORRELATION_WINDOW,
    CONF_BORDER_EXIT_TIMEOUT,
    CONF_BORDER_IDS,
    CONF_BORDER_ONLY_MODE,
    CONF_BOX_ID,
    CONF_DOOR_CLOSED_DELAY,
    CONF_DOOR_OPEN_TIMEOUT,
    CONF_IMMEDIATE_ON,
    CONF_WASP_ID,
    DIRECTION_ENTRY,
    DIRECTION_EXIT,
    DIRECTION_UNKNOWN,
    LOGGER,
    SERVICE_FORCE_ENTRY,
    SERVICE_FORCE_EXIT,
    SERVICE_RESET,
    SOURCE_BORDER,
    SOURCE_DOOR,
    SOURCE_MANUAL,
    SOURCE_MOTION,
    SOURCE_NONE,
)

# Spec §7: "Multiple border sensors firing within a short window (< 2s)
# should be treated as a single crossing (debounce)."
BORDER_DEBOUNCE_WINDOW = 2.0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> bool:
    """Initialize config entry and register services.

    Reads all config options and creates the WaspInAOpenboxSensor entity.
    Registers three entity services: reset, force_entry, force_exit (Spec §8).
    """

    # --- Read config options ---
    wasp_entity_id: str = config_entry.options[CONF_WASP_ID]
    box_entity_id: str | None = config_entry.options.get(CONF_BOX_ID)
    delay = config_entry.options[CONF_DOOR_CLOSED_DELAY]
    timeout = config_entry.options[CONF_DOOR_OPEN_TIMEOUT]
    immediate_on = config_entry.options[CONF_IMMEDIATE_ON]

    # Border sensor config — defaults for migrated v1.1 entries (Spec §9).
    border_entity_ids: list[str] = config_entry.options.get(CONF_BORDER_IDS, [])
    border_correlation_window: int = config_entry.options.get(
        CONF_BORDER_CORRELATION_WINDOW, 5
    )
    border_exit_timeout: int = config_entry.options.get(CONF_BORDER_EXIT_TIMEOUT, 30)
    border_only_mode: bool = config_entry.options.get(CONF_BORDER_ONLY_MODE, False)

    async_add_entities(
        [
            WaspInAOpenboxSensor(
                hass,
                wasp_entity_id=wasp_entity_id,
                box_entity_id=box_entity_id,
                delay=delay,
                timeout=timeout,
                immediate_on=immediate_on,
                border_entity_ids=border_entity_ids,
                border_correlation_window=border_correlation_window,
                border_exit_timeout=border_exit_timeout,
                border_only_mode=border_only_mode,
                name=config_entry.title,
                unique_id=config_entry.entry_id,
            )
        ]
    )

    # --- Register entity services (Spec §8) ---
    platform = async_get_current_platform()
    platform.async_register_entity_service(SERVICE_RESET, {}, "async_reset")
    platform.async_register_entity_service(SERVICE_FORCE_ENTRY, {}, "async_force_entry")
    platform.async_register_entity_service(SERVICE_FORCE_EXIT, {}, "async_force_exit")

    return True


class WaspInAOpenboxSensor(BinarySensorEntity):
    """Occupancy sensor with door and border sensor support.

    Implements three operating modes:
      - Door-only:   classic wasp-in-a-box (Spec §4, original behavior)
      - Mixed:       door + border sensors (Spec §4.1)
      - Border-only: no door, border + motion correlation (Spec §4.2)
    """

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_should_poll = False
    _attr_translation_key = "wasp_in_a_openbox"

    def __init__(  # noqa: PLR0913
        self,
        hass: HomeAssistant,
        *,
        wasp_entity_id: str,
        box_entity_id: str | None,
        delay: int,
        timeout: int,
        immediate_on: bool,
        border_entity_ids: list[str],
        border_correlation_window: int,
        border_exit_timeout: int,
        border_only_mode: bool,
        name: str | None,
        unique_id: str | None,
    ) -> None:
        """Initialize the occupancy sensor.

        Args:
            wasp_entity_id: Room motion sensor (Spec §2: "wasp").
            box_entity_id: Door contact sensor (Spec §2: "box"), or None.
            delay: Door closed delay in seconds.
            timeout: Door open timeout in seconds.
            immediate_on: Activate immediately on motion/door open.
            border_entity_ids: List of curtain PIR entity ids (Spec §2).
            border_correlation_window: Seconds to correlate border+motion (Spec §3.1).
            border_exit_timeout: Seconds to confirm exit after border (Spec §3.2).
            border_only_mode: True if no door sensor is used (Spec §3.3).
            name: User-assigned name for this helper.
            unique_id: Config entry ID used as unique_id.
        """
        self._attr_unique_id = unique_id
        self._attr_name = name

        # Sensor references
        self._wasp_entity_id = wasp_entity_id
        self._box_entity_id = box_entity_id
        self._border_entity_ids = border_entity_ids

        # Timing configuration
        self._delay = delay
        self._timeout = timeout
        self._immediate_on = immediate_on
        self._border_correlation_window = border_correlation_window
        self._border_exit_timeout = border_exit_timeout
        self._border_only_mode = border_only_mode

        # --- Internal state ---
        self._state: str = STATE_UNKNOWN
        self._wasp_state: str = STATE_UNKNOWN
        self._box_state: str = STATE_UNKNOWN
        self._motion_was_detected: bool = False

        # Timer handles (CALLBACK_TYPE cancels the timer when called)
        self._door_closed_delay_timer: CALLBACK_TYPE | None = None
        self._door_open_timeout_timer: CALLBACK_TYPE | None = None
        self._border_correlation_timer: CALLBACK_TYPE | None = None
        self._border_exit_timer: CALLBACK_TYPE | None = None

        # Border sensor tracking (Spec §5: border_sensors_state attribute)
        self._border_last_triggered: dict[str, str | None] = {
            eid: None for eid in border_entity_ids
        }
        self._last_border_event: str | None = None
        self._last_border_event_time: datetime | None = None
        self._inferred_direction: str = DIRECTION_UNKNOWN
        self._occupancy_source: str = SOURCE_NONE
        self._border_pending_entry: bool = False
        self._border_pending_exit: bool = False

        # Startup flags — skip the first replayed state to avoid false triggers.
        self._awaiting_first_wasp_state: bool = True
        self._awaiting_first_box_state: bool = True
        self._state_had_real_change = False

    # --- Properties ---

    @property
    def _has_door(self) -> bool:
        """True if a door sensor is configured and border_only_mode is off."""
        return self._box_entity_id is not None and not self._border_only_mode

    @property
    def _has_borders(self) -> bool:
        """True if at least one border sensor is configured."""
        return len(self._border_entity_ids) > 0

    @property
    def _fully_enclosed(self) -> bool:
        """True if room is fully enclosed — door only, no border passages.

        Spec §7: "fully_enclosed: false when borders are present."
        """
        return self._has_door and not self._has_borders

    # --- Lifecycle ---

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes and replay current state on startup.

        Spec §7 "HA restart / state recovery": replay current states of all
        tracked entities; border state set to unknown until first real event.
        """
        await super().async_added_to_hass()

        # Subscribe to wasp (motion) sensor
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._wasp_entity_id, self._async_wasp_state_listener,
            )
        )

        # Subscribe to box (door) sensor if configured
        if self._box_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._box_entity_id, self._async_box_state_listener,
                )
            )

        # Subscribe to each border sensor
        for border_eid in self._border_entity_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, border_eid, self._async_border_state_listener,
                )
            )

        # Validate entities exist in registry
        registry = er.async_get(self.hass)
        wasp_entry = registry.async_get(self._wasp_entity_id)
        if not wasp_entry:
            LOGGER.warning("Unable to find entity %s", self._wasp_entity_id)

        if self._box_entity_id:
            box_entry = registry.async_get(self._box_entity_id)
            if not box_entry:
                LOGGER.warning("Unable to find entity %s", self._box_entity_id)

        for border_eid in self._border_entity_ids:
            border_entry = registry.async_get(border_eid)
            if not border_entry:
                LOGGER.warning("Unable to find border sensor entity %s", border_eid)

        # Replay current wasp state
        wasp_state = self.hass.states.get(self._wasp_entity_id)
        wasp_state_event: Event[EventStateChangedData] = Event(
            "", {"entity_id": self._wasp_entity_id, "new_state": wasp_state, "old_state": None},
        )
        self._async_wasp_state_listener(wasp_state_event)

        # Replay current box state if configured
        if self._box_entity_id:
            box_state = self.hass.states.get(self._box_entity_id)
            box_state_event: Event[EventStateChangedData] = Event(
                "", {"entity_id": self._box_entity_id, "new_state": box_state, "old_state": None},
            )
            self._async_box_state_listener(box_state_event)

    async def async_will_remove_from_hass(self) -> None:
        """Cancel all pending timers on removal."""
        self._cancel_all_timers()

    def _cancel_all_timers(self) -> None:
        """Cancel all pending async_call_later timers."""
        for timer_attr in (
            "_door_closed_delay_timer",
            "_door_open_timeout_timer",
            "_border_correlation_timer",
            "_border_exit_timer",
        ):
            timer = getattr(self, timer_attr)
            if timer is not None:
                timer()
                setattr(self, timer_attr, None)

    @property
    def is_on(self) -> bool | None:
        """Return True if occupancy is detected, None if unknown."""
        if self._state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return None
        return self._state == STATE_ON

    @property
    def extra_state_attributes(self) -> dict[str, str | dict | bool | None]:
        """Return state attributes.

        Spec §5 "Exposed attributes": always includes motion/door/source;
        border-specific attributes only when borders are configured.
        """
        attrs: dict[str, str | dict | bool | None] = {
            ATTR_MOTION_SENSOR_STATE: self._wasp_state,
            ATTR_DOOR_SENSOR_STATE: (
                self._box_state if self._box_entity_id else "none"
            ),
            ATTR_OCCUPANCY_SOURCE: self._occupancy_source,
        }

        # Add border-specific attributes only when relevant
        if self._has_borders:
            attrs[ATTR_BORDER_SENSORS_STATE] = self._border_last_triggered
            attrs[ATTR_LAST_BORDER_EVENT] = self._last_border_event
            attrs[ATTR_INFERRED_DIRECTION] = self._inferred_direction
            attrs[ATTR_FULLY_ENCLOSED] = self._fully_enclosed

        return attrs

    # ── Wasp (motion) sensor listener ────────────────────────────────────

    @callback
    def _async_wasp_state_listener(self, event: Event[EventStateChangedData]) -> None:
        """Handle room motion sensor state changes.

        Covers:
          - Original door open timeout logic (motion cleared + door open).
          - Spec §3.1: motion after a border event confirms entry.
          - Spec §3.2: motion cleared prepares for border exit detection.
        """
        new_state = event.data["new_state"]
        old_state = event.data.get("old_state")

        # Skip first replayed state on startup
        if self._awaiting_first_wasp_state:
            self._awaiting_first_wasp_state = False
            return

        LOGGER.debug("Wasp state changed from %s to %s", old_state, new_state)

        # Parse state, treating None/unknown/unavailable as unknown
        if (
            new_state is None
            or new_state.state is None
            or new_state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]
        ):
            self._wasp_state = STATE_UNKNOWN
        else:
            self._wasp_state = new_state.state

        # Cancel any existing door open timeout timer
        if self._door_open_timeout_timer is not None:
            self._door_open_timeout_timer()
            self._door_open_timeout_timer = None

        # Original logic: motion cleared + door open → start timeout
        if self._has_door and self._wasp_state == STATE_OFF and (
            self._box_state in [STATE_ON, STATE_UNKNOWN]
        ):
            LOGGER.debug(
                "Motion unoccupied and door open, waiting %ss before recalculating",
                self._timeout,
            )
            self._door_open_timeout_timer = async_call_later(
                self.hass, self._timeout, self._async_door_open_timeout_callback
            )

        # Spec §3.1: motion detected after a border crossing → confirm entry
        if self._wasp_state == STATE_ON and self._border_pending_entry:
            LOGGER.debug("Motion detected after border event — confirming entry")
            self._border_pending_entry = False
            self._inferred_direction = DIRECTION_ENTRY
            self._occupancy_source = SOURCE_BORDER
            # Cancel correlation timer — entry is confirmed
            if self._border_correlation_timer is not None:
                self._border_correlation_timer()
                self._border_correlation_timer = None
            # Cancel any pending exit — we just got a confirmed entry
            if self._border_exit_timer is not None:
                self._border_exit_timer()
                self._border_exit_timer = None
            self._border_pending_exit = False

        self.async_calculate_state()

    # ── Box (door) sensor listener ───────────────────────────────────────

    @callback
    def _async_box_state_listener(self, event: Event[EventStateChangedData]) -> None:
        """Handle door contact sensor state changes.

        Implements the original wasp-in-a-box door logic:
          - Door closes → start delay timer before evaluating occupancy.
          - Door opens → cancel delay, start open timeout if no motion.
        """
        new_state = event.data["new_state"]
        old_state = event.data.get("old_state")

        # Skip first replayed state on startup
        if self._awaiting_first_box_state:
            self._awaiting_first_box_state = False
            return

        LOGGER.debug("Box state changed from %s to %s", old_state, new_state)

        if (
            new_state is None
            or new_state.state is None
            or new_state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]
        ):
            self._box_state = STATE_UNKNOWN
        else:
            # Detect door just closed (open→closed transition)
            door_just_closed = (
                old_state is not None
                and old_state.state == STATE_ON
                and new_state.state == STATE_OFF
            )

            self._box_state = new_state.state

            if door_just_closed:
                # Cancel any existing delay timer
                if self._door_closed_delay_timer is not None:
                    self._door_closed_delay_timer()
                    self._door_closed_delay_timer = None

                # Start door closed delay before evaluating occupancy
                LOGGER.debug(
                    "Door closed, waiting %ss before recalculating", self._delay,
                )
                self._door_closed_delay_timer = async_call_later(
                    self.hass, self._delay, self._async_door_closed_delay_callback
                )
                self._occupancy_source = SOURCE_DOOR
                return

        # Cancel pending timers if door opens or state becomes unknown
        if self._door_closed_delay_timer is not None:
            self._door_closed_delay_timer()
            self._door_closed_delay_timer = None

        if self._door_open_timeout_timer is not None:
            self._door_open_timeout_timer()
            self._door_open_timeout_timer = None

        # Door open + no motion → start open timeout
        if self._wasp_state == STATE_OFF and self._box_state == STATE_ON:
            LOGGER.debug(
                "Motion unoccupied and door open, waiting %ss before recalculating",
                self._timeout,
            )
            self._door_open_timeout_timer = async_call_later(
                self.hass, self._timeout, self._async_door_open_timeout_callback
            )

        self._occupancy_source = SOURCE_DOOR
        self.async_calculate_state()

    # ── Border (curtain PIR) sensor listener ─────────────────────────────

    @callback
    def _async_border_state_listener(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle border sensor state changes.

        A border crossing = transition to ON (motion detected in passage).
        This method implements:
          - Spec §7: debounce multiple border sensors firing within 2s.
          - Spec §3.1: entry inference (border fires → wait for room motion).
          - Spec §3.2: exit inference (border fires while occupied → wait for
            motion to resume or timeout → exit confirmed).
        """
        new_state = event.data["new_state"]
        old_state = event.data.get("old_state")
        entity_id = event.data["entity_id"]

        if new_state is None or new_state.state is None:
            return

        # Only care about transitions to ON (= crossing detected)
        if new_state.state != STATE_ON:
            return

        # Ignore if already ON (not a new crossing)
        if old_state is not None and old_state.state == STATE_ON:
            return

        now = dt_util.utcnow()
        now_iso = now.isoformat()

        # Spec §7: debounce multiple border sensors firing simultaneously
        if (
            self._last_border_event_time is not None
            and (now - self._last_border_event_time).total_seconds()
            < BORDER_DEBOUNCE_WINDOW
        ):
            LOGGER.debug(
                "Border sensor %s fired within debounce window, merging events",
                entity_id,
            )
            self._border_last_triggered[entity_id] = now_iso
            return

        LOGGER.debug("Border sensor %s triggered (crossing event)", entity_id)

        # Record the event (Spec §5: border_sensors_state, last_border_event)
        self._border_last_triggered[entity_id] = now_iso
        self._last_border_event = now_iso
        self._last_border_event_time = now

        # Determine entry vs exit based on current room state
        motion_is_active = self._wasp_state == STATE_ON
        room_is_occupied = self._state == STATE_ON

        if motion_is_active and room_is_occupied:
            # Spec §3.2: motion active + border fires = likely leaving
            LOGGER.debug(
                "Border event while motion active — potential exit, "
                "starting exit timeout (%ss)", self._border_exit_timeout,
            )
            self._border_pending_exit = True
            self._inferred_direction = DIRECTION_EXIT
            if self._border_exit_timer is not None:
                self._border_exit_timer()
            self._border_exit_timer = async_call_later(
                self.hass, self._border_exit_timeout,
                self._async_border_exit_timeout_callback,
            )

        elif not motion_is_active and not room_is_occupied:
            # Spec §3.1: no motion + room empty + border fires = entering
            LOGGER.debug(
                "Border event while room empty — potential entry, "
                "waiting %ss for motion confirmation",
                self._border_correlation_window,
            )
            self._border_pending_entry = True
            self._inferred_direction = DIRECTION_ENTRY
            if self._border_correlation_timer is not None:
                self._border_correlation_timer()
            self._border_correlation_timer = async_call_later(
                self.hass, self._border_correlation_window,
                self._async_border_correlation_timeout_callback,
            )

        elif not motion_is_active and room_is_occupied:
            # Occupied but no current motion + border = someone left while still
            LOGGER.debug(
                "Border event while room occupied but no motion — "
                "starting exit timeout (%ss)", self._border_exit_timeout,
            )
            self._border_pending_exit = True
            self._inferred_direction = DIRECTION_EXIT
            if self._border_exit_timer is not None:
                self._border_exit_timer()
            self._border_exit_timer = async_call_later(
                self.hass, self._border_exit_timeout,
                self._async_border_exit_timeout_callback,
            )

        elif motion_is_active and not room_is_occupied:
            # Motion active but room not yet occupied + border = immediate entry
            LOGGER.debug("Border event with active motion — immediate entry")
            self._inferred_direction = DIRECTION_ENTRY
            self._occupancy_source = SOURCE_BORDER
            self._motion_was_detected = True
            self.async_calculate_state()

        self.async_write_ha_state()

    # ── Timer callbacks ──────────────────────────────────────────────────

    @callback
    def _async_door_closed_delay_callback(self, _now: datetime) -> None:
        """Door closed delay expired — evaluate occupancy now."""
        self._door_closed_delay_timer = None
        LOGGER.debug("Door closed delay expired, recalculating state")
        self._motion_was_detected = False
        self._occupancy_source = SOURCE_DOOR
        self.async_calculate_state()

    @callback
    def _async_door_open_timeout_callback(self, _now: datetime) -> None:
        """Door open timeout expired — no motion for too long, clear occupancy."""
        self._door_open_timeout_timer = None
        LOGGER.debug("Door open timeout expired, setting state to off")
        self._wasp_state = STATE_OFF
        self._motion_was_detected = False
        self._occupancy_source = SOURCE_DOOR
        self._state = STATE_OFF
        self.async_write_ha_state()

    @callback
    def _async_border_correlation_timeout_callback(self, _now: datetime) -> None:
        """Border correlation window expired without room motion.

        Spec §4.2: "Border fires while room has no motion and no motion
        follows → transient pass-through, no occupancy change."
        """
        self._border_correlation_timer = None
        self._border_pending_entry = False
        LOGGER.debug(
            "Border correlation window expired without motion — "
            "treating as pass-through"
        )
        self._inferred_direction = DIRECTION_UNKNOWN
        self.async_write_ha_state()

    @callback
    def _async_border_exit_timeout_callback(self, _now: datetime) -> None:
        """Border exit timeout expired — confirm exit if motion hasn't resumed.

        Spec §3.2: "Room motion clear → border fires → no motion resumes
        within timeout → someone likely left."
        """
        self._border_exit_timer = None
        self._border_pending_exit = False

        # If motion resumed during the timeout, cancel exit
        if self._wasp_state == STATE_ON:
            LOGGER.debug(
                "Border exit timeout expired but motion resumed — "
                "room still occupied"
            )
            self._inferred_direction = DIRECTION_UNKNOWN
            self.async_write_ha_state()
            return

        # Confirm exit
        LOGGER.debug(
            "Border exit timeout expired with no motion — "
            "confirming exit, room unoccupied"
        )
        self._motion_was_detected = False
        self._occupancy_source = SOURCE_BORDER
        self._inferred_direction = DIRECTION_EXIT
        self._state = STATE_OFF
        self.async_write_ha_state()

    # ── State calculation — Spec §4 "State machine enhancement" ──────────

    @callback
    def async_calculate_state(self) -> None:
        """Calculate occupancy based on wasp, box, and border states.

        Routes to the appropriate mode:
          - border_only_mode → _calculate_border_only_state (Spec §4.2)
          - door + borders   → _calculate_mixed_state (Spec §4.1)
          - door only        → _calculate_door_only_state (original)
        """
        LOGGER.debug(
            "Calculating state: wasp=%s, box=%s, motion_was=%s, "
            "border_only=%s, source=%s",
            self._wasp_state, self._box_state, self._motion_was_detected,
            self._border_only_mode, self._occupancy_source,
        )

        if self._wasp_state == STATE_UNKNOWN:
            self._state = STATE_UNKNOWN
            self.async_write_ha_state()
            return

        motion_detected_now = self._wasp_state == STATE_ON
        motion_detected = motion_detected_now or self._motion_was_detected

        if self._border_only_mode:
            self._calculate_border_only_state(motion_detected_now, motion_detected)
        elif self._has_door and self._has_borders:
            self._calculate_mixed_state(motion_detected_now, motion_detected)
        else:
            self._calculate_door_only_state(motion_detected_now, motion_detected)

        self._motion_was_detected = motion_detected
        self.async_write_ha_state()

    @callback
    def _calculate_door_only_state(
        self, motion_now: bool, motion_any: bool
    ) -> None:
        """Classic wasp-in-a-box: door + motion only (original behavior)."""
        door_closed = (
            False if self._box_state == STATE_UNKNOWN else self._box_state == STATE_OFF
        )

        # Core rule: door closed + motion detected = occupied
        if door_closed and motion_any:
            self._state = STATE_ON
            self._occupancy_source = SOURCE_DOOR
        else:
            self._state = STATE_OFF
            if not motion_any:
                self._occupancy_source = SOURCE_NONE

        # Immediate on: door open → occupied
        if not door_closed and self._immediate_on:
            self._state = STATE_ON
            self._occupancy_source = SOURCE_DOOR

        # Immediate on: motion detected → occupied
        if motion_now and self._immediate_on:
            self._state = STATE_ON
            self._occupancy_source = SOURCE_MOTION

    @callback
    def _calculate_border_only_state(
        self, motion_now: bool, motion_any: bool
    ) -> None:
        """Border-only mode: no door, rely on border + motion correlation.

        Spec §4.2:
          - Border fires → room motion follows within window → occupied.
          - Room motion clear for border_exit_timeout → unoccupied.
          - Border fires with no motion following → pass-through, no change.
          - Room motion alone + immediate_on → occupied.
        """
        # If currently occupied via border, stay until exit is confirmed
        if self._state == STATE_ON and self._occupancy_source == SOURCE_BORDER:
            if motion_now or self._border_pending_exit:
                self._state = STATE_ON
                return
            if not motion_any:
                self._state = STATE_OFF
                self._occupancy_source = SOURCE_NONE
            return

        # Room is not currently occupied
        if motion_now and self._occupancy_source == SOURCE_BORDER:
            self._state = STATE_ON
            return

        if motion_now and self._immediate_on:
            self._state = STATE_ON
            self._occupancy_source = SOURCE_MOTION
            return

        if not motion_any:
            self._state = STATE_OFF
            self._occupancy_source = SOURCE_NONE

    @callback
    def _calculate_mixed_state(
        self, motion_now: bool, motion_any: bool
    ) -> None:
        """Mixed mode: room has both door and border sensors.

        Spec §4.1:
          - Door closed + motion → occupied (classic, takes priority).
          - Door open + border entry confirmed → occupied.
          - "Door logic still takes priority."
        """
        door_closed = (
            False if self._box_state == STATE_UNKNOWN else self._box_state == STATE_OFF
        )

        # Door-based logic takes priority when door is closed
        if door_closed and motion_any:
            self._state = STATE_ON
            self._occupancy_source = SOURCE_DOOR
            return

        # Door is open — border logic can contribute
        if not door_closed:
            if (
                self._state == STATE_ON
                and self._occupancy_source == SOURCE_BORDER
            ):
                if motion_now or self._border_pending_exit:
                    self._state = STATE_ON
                    return
                if not motion_any:
                    self._state = STATE_OFF
                    self._occupancy_source = SOURCE_NONE
                    return

            if motion_now and self._occupancy_source == SOURCE_BORDER:
                self._state = STATE_ON
                return

            # Immediate on with door open
            if self._immediate_on:
                if motion_now:
                    self._state = STATE_ON
                    self._occupancy_source = SOURCE_MOTION
                    return
                self._state = STATE_ON
                self._occupancy_source = SOURCE_DOOR
                return

        # Default: unoccupied
        self._state = STATE_OFF
        if not motion_any:
            self._occupancy_source = SOURCE_NONE

    # ── Services — Spec §8 ───────────────────────────────────────────────

    async def async_reset(self) -> None:
        """Reset occupancy to off, cancel all timers, clear border history.

        Spec §8: "Extend the existing reset service to also clear border
        event history and timers."
        """
        self._cancel_all_timers()

        self._motion_was_detected = False
        self._border_pending_entry = False
        self._border_pending_exit = False
        self._inferred_direction = DIRECTION_UNKNOWN
        self._occupancy_source = SOURCE_NONE
        self._last_border_event = None
        self._last_border_event_time = None
        self._border_last_triggered = {
            eid: None for eid in self._border_entity_ids
        }

        self._state = STATE_OFF
        self.async_write_ha_state()

    async def async_force_entry(self) -> None:
        """Manually signal an entry event (Spec §8: force_entry)."""
        LOGGER.debug("Force entry triggered")

        if self._border_exit_timer is not None:
            self._border_exit_timer()
            self._border_exit_timer = None
        self._border_pending_exit = False
        self._border_pending_entry = False

        self._motion_was_detected = True
        self._inferred_direction = DIRECTION_ENTRY
        self._occupancy_source = SOURCE_MANUAL
        self._state = STATE_ON
        self.async_write_ha_state()

    async def async_force_exit(self) -> None:
        """Manually signal an exit event (Spec §8: force_exit)."""
        LOGGER.debug("Force exit triggered")

        self._cancel_all_timers()

        self._motion_was_detected = False
        self._border_pending_entry = False
        self._border_pending_exit = False
        self._inferred_direction = DIRECTION_EXIT
        self._occupancy_source = SOURCE_MANUAL
        self._state = STATE_OFF
        self.async_write_ha_state()
