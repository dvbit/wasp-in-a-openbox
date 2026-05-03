"""Custom integration: Wasp in a Openbox — occupancy helpers for Home Assistant.

Extends the classic "wasp in a box" pattern with border (curtain PIR) sensors
for open-plan rooms that have no physical door.

References:
  - Spec §9 "Backward compatibility" — migration from v1.1 to v1.2
  - Spec §6 "Config flow changes" — optional door sensor, border entity list
  - HA docs: https://developers.home-assistant.io/docs/config_entries_index

For more details: https://github.com/dvbit/HA-Wasp-In-A-Openbox
"""

from __future__ import annotations

import voluptuous as vol
from awesomeversion.awesomeversion import AwesomeVersion

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION  # noqa: N812
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.event import async_track_entity_registry_updated_event
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BORDER_CORRELATION_WINDOW,
    CONF_BORDER_EXIT_TIMEOUT,
    CONF_BORDER_IDS,
    CONF_BORDER_ONLY_MODE,
    CONF_BOX_ID,
    CONF_WASP_ID,
    DEFAULT_BORDER_CORRELATION_WINDOW,
    DEFAULT_BORDER_EXIT_TIMEOUT,
    DEFAULT_BORDER_ONLY_MODE,
    DOMAIN,
    LOGGER,
    MIN_HA_VERSION,
    PLATFORMS,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(
    hass: HomeAssistant,
    config: ConfigType,
) -> bool:
    """Integration setup — verify minimum HA version."""

    if AwesomeVersion(HA_VERSION) < AwesomeVersion(MIN_HA_VERSION):  # pragma: no cover
        msg = (
            "This integration requires at least Home Assistant version "
            f" {MIN_HA_VERSION}, you are running version {HA_VERSION}."
            " Please upgrade Home Assistant to continue using this integration."
        )
        LOGGER.critical(msg)
        return False

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries from older versions.

    Spec §9 "Backward compatibility":
      - v1.1 → v1.2: add border sensor defaults so existing entries
        continue to behave identically (border_ids=[], border_only_mode=False).
    """
    if entry.minor_version < 2:
        LOGGER.info(
            "Migrating config entry %s from version %s.%s to 1.2",
            entry.entry_id,
            entry.version,
            entry.minor_version,
        )
        new_options = {**entry.options}
        # Inject defaults for all new border fields.
        new_options.setdefault(CONF_BORDER_IDS, [])
        new_options.setdefault(
            CONF_BORDER_CORRELATION_WINDOW, DEFAULT_BORDER_CORRELATION_WINDOW
        )
        new_options.setdefault(CONF_BORDER_EXIT_TIMEOUT, DEFAULT_BORDER_EXIT_TIMEOUT)
        new_options.setdefault(CONF_BORDER_ONLY_MODE, DEFAULT_BORDER_ONLY_MODE)

        hass.config_entries.async_update_entry(
            entry, options=new_options, minor_version=2
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Wasp in a Openbox from a config entry.

    Validates all referenced entities exist in the entity registry, then
    forwards setup to the binary_sensor platform.

    Spec §6: door sensor is now optional (can be None for border-only rooms).
    Spec §7: border entities are validated with graceful warnings for missing ones.
    """

    entity_registry = er.async_get(hass)

    # --- Validate wasp (motion) entity — always required ---
    try:
        wasp_entity_id = er.async_validate_entity_id(
            entity_registry, entry.options[CONF_WASP_ID]
        )
    except vol.Invalid:
        LOGGER.error(
            "Failed to setup wasp_in_a_openbox for unknown entity %s",
            entry.options[CONF_WASP_ID],
        )
        return False

    # --- Validate box (door) entity — optional per Spec §6 ---
    box_entity_id = None
    if entry.options.get(CONF_BOX_ID):
        try:
            box_entity_id = er.async_validate_entity_id(
                entity_registry, entry.options[CONF_BOX_ID]
            )
        except vol.Invalid:
            LOGGER.error(
                "Failed to setup wasp_in_a_openbox for unknown entity %s",
                entry.options[CONF_BOX_ID],
            )
            return False

    # --- Validate border sensor entities — Spec §7: graceful skip ---
    border_entity_ids: list[str] = []
    for border_id in entry.options.get(CONF_BORDER_IDS, []):
        try:
            validated_id = er.async_validate_entity_id(entity_registry, border_id)
            border_entity_ids.append(validated_id)
        except vol.Invalid:
            LOGGER.warning(
                "Skipping unknown border sensor entity %s",
                border_id,
            )

    # --- Track entity registry changes (rename / removal) ---

    async def async_registry_updated(
        event: Event[er.EventEntityRegistryUpdatedData],
    ) -> None:
        """Handle entity registry update."""
        data = event.data
        if data["action"] == "remove":
            await hass.config_entries.async_remove(entry.entry_id)

        if data["action"] != "update":
            return

        if "entity_id" in data["changes"]:
            await hass.config_entries.async_reload(entry.entry_id)

    # Track wasp entity
    entry.async_on_unload(
        async_track_entity_registry_updated_event(
            hass, wasp_entity_id, async_registry_updated
        )
    )

    # Track box entity if configured
    if box_entity_id:
        entry.async_on_unload(
            async_track_entity_registry_updated_event(
                hass, box_entity_id, async_registry_updated
            )
        )

    # Track each border sensor entity
    for border_eid in border_entity_ids:
        entry.async_on_unload(
            async_track_entity_registry_updated_event(
                hass, border_eid, async_registry_updated
            )
        )

    entry.async_on_unload(entry.add_update_listener(config_entry_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def config_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener — reload when config entry options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
