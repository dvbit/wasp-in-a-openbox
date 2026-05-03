"""Config flow for wasp_in_a_openbox integration.

Defines the UI schema for creating and editing Wasp in a Openbox helpers.

References:
  - Spec §6 "Config flow changes":
    · Door sensor is optional (vol.Optional) for border-only rooms.
    · Multi-entity selector for border_ids filtered to binary_sensor/input_boolean.
    · Three new numeric/boolean parameters with defaults.
  - HA docs: https://developers.home-assistant.io/docs/data_entry_flow_index
  - HA docs: https://developers.home-assistant.io/docs/config_entries_config_flow_handler
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import voluptuous as vol

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.const import CONF_NAME
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaConfigFlowHandler,
    SchemaFlowFormStep,
)

from .const import (
    CONF_BORDER_CORRELATION_WINDOW,
    CONF_BORDER_EXIT_TIMEOUT,
    CONF_BORDER_IDS,
    CONF_BORDER_ONLY_MODE,
    CONF_BOX_ID,
    CONF_DOOR_CLOSED_DELAY,
    CONF_DOOR_OPEN_TIMEOUT,
    CONF_IMMEDIATE_ON,
    CONF_WASP_ID,
    DEFAULT_BORDER_CORRELATION_WINDOW,
    DEFAULT_BORDER_EXIT_TIMEOUT,
    DEFAULT_BORDER_ONLY_MODE,
    DEFAULT_DOOR_CLOSED_DELAY,
    DEFAULT_IMMEDIATE_ON,
    DEFAULT_OPEN_DOOR_TIMEOUT,
    DOMAIN,
)

# ---------------------------------------------------------------------------
# Schema for options (edit) and config (create) flows.
#
# Spec §6: door sensor is Optional (can be omitted for border-only rooms).
# Spec §3.3: border fields added with sensible defaults.
# HA selector docs:
#   https://www.home-assistant.io/docs/blueprint/selectors/
# ---------------------------------------------------------------------------

OPTIONS_SCHEMA = vol.Schema(
    {
        # Spec §2: room motion sensor — always required.
        vol.Required(CONF_WASP_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=[BINARY_SENSOR_DOMAIN, INPUT_BOOLEAN_DOMAIN],
                multiple=False,
            ),
        ),
        # Spec §6: door sensor — optional for border-only rooms.
        vol.Optional(CONF_BOX_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=[BINARY_SENSOR_DOMAIN, INPUT_BOOLEAN_DOMAIN],
                multiple=False,
            ),
        ),
        # Original wasp-in-a-box timing parameters.
        vol.Required(
            CONF_DOOR_CLOSED_DELAY, default=DEFAULT_DOOR_CLOSED_DELAY
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=600,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            ),
        ),
        vol.Required(
            CONF_DOOR_OPEN_TIMEOUT, default=DEFAULT_OPEN_DOOR_TIMEOUT
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=3600,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            ),
        ),
        vol.Required(
            CONF_IMMEDIATE_ON, default=DEFAULT_IMMEDIATE_ON
        ): selector.BooleanSelector(),

        # --- Border sensor fields — Spec §3.3 ---

        # Multi-entity selector for curtain PIR / passage sensors.
        vol.Optional(CONF_BORDER_IDS, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=[BINARY_SENSOR_DOMAIN, INPUT_BOOLEAN_DOMAIN],
                multiple=True,
            ),
        ),
        # Spec §3.1: correlation window to confirm entry after border trigger.
        vol.Required(
            CONF_BORDER_CORRELATION_WINDOW,
            default=DEFAULT_BORDER_CORRELATION_WINDOW,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=60,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            ),
        ),
        # Spec §3.2: exit timeout after border crossing with no resumed motion.
        vol.Required(
            CONF_BORDER_EXIT_TIMEOUT,
            default=DEFAULT_BORDER_EXIT_TIMEOUT,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=5,
                max=600,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            ),
        ),
        # Spec §3.3: border_only_mode — room has no physical door.
        vol.Required(
            CONF_BORDER_ONLY_MODE,
            default=DEFAULT_BORDER_ONLY_MODE,
        ): selector.BooleanSelector(),
    }
)

# Config flow adds the name field on top of options.
CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT, autocomplete="off"
            ),
        )
    }
).extend(OPTIONS_SCHEMA.schema)

CONFIG_FLOW = {
    "user": SchemaFlowFormStep(CONFIG_SCHEMA),
}

OPTIONS_FLOW = {
    "init": SchemaFlowFormStep(OPTIONS_SCHEMA),
}


class ConfigFlowHandler(SchemaConfigFlowHandler, domain=DOMAIN):
    """Handle config and options flow for Wasp in a Openbox.

    Spec §9: version bumped to 1.2 for border sensor migration.
    """

    config_flow = CONFIG_FLOW
    options_flow = OPTIONS_FLOW

    VERSION = 1
    MINOR_VERSION = 2

    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Return config entry title."""
        return cast(str, options[CONF_NAME]) if CONF_NAME in options else ""
