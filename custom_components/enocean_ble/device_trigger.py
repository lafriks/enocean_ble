"""Device triggers: "Button A0 pressed" etc. in the automation editor."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.components.homeassistant.triggers.event import CONF_EVENT_TYPE
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_EVENT,
    CONF_EVENT_DATA,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_BUTTON,
    ATTR_DEVICE_ID,
    ATTR_TYPE,
    BUTTONS,
    CONF_SUBTYPE,
    DOMAIN,
    EVENT_ENOCEAN_BLE,
    EVENT_TYPE_PRESS,
)

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): EVENT_TYPE_PRESS,
        vol.Required(CONF_SUBTYPE): vol.In(BUTTONS),
    }
)


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, str]]:
    """List one "pressed" trigger per button of an EnOcean BLE device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None or not any(domain == DOMAIN for domain, _ in device.identifiers):
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: EVENT_TYPE_PRESS,
            CONF_SUBTYPE: button,
        }
        for button in BUTTONS
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach the trigger to the matching bus event."""
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            CONF_PLATFORM: CONF_EVENT,
            CONF_EVENT_TYPE: EVENT_ENOCEAN_BLE,
            CONF_EVENT_DATA: {
                ATTR_DEVICE_ID: config[CONF_DEVICE_ID],
                ATTR_BUTTON: config[CONF_SUBTYPE],
                ATTR_TYPE: config[CONF_TYPE],
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
