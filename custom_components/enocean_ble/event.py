"""Event entities for EnOcean BLE buttons."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnOceanBleConfigEntry
from .const import BUTTONS, DOMAIN, EVENT_TYPE_PRESS, INTEGRATION_NAME, SIGNAL_BUTTON_EVENT

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnOceanBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one event entity per button."""
    _LOGGER.debug(
        "event_platform_setup entry_id=%s unique_id=%s button_count=%s",
        entry.entry_id,
        entry.unique_id,
        len(BUTTONS),
    )
    async_add_entities(EnOceanBleButtonEventEntity(entry, button) for button in BUTTONS)


class EnOceanBleButtonEventEntity(EventEntity):
    """Event entity for one physical EnOcean BLE rocker button."""

    _attr_has_entity_name = True
    _attr_device_class = EventDeviceClass.BUTTON

    def __init__(self, entry: EnOceanBleConfigEntry, button: str) -> None:
        """Initialize the event entity."""
        self._attr_event_types = [EVENT_TYPE_PRESS]
        self._entry = entry
        self._button = button
        self._attr_name = button
        self._attr_unique_id = f"{entry.unique_id}_{button.lower()}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(dr.CONNECTION_BLUETOOTH, entry.runtime_data.mac_address)},
            name=entry.title,
            manufacturer="EnOcean",
            model=INTEGRATION_NAME,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to button events of this entry and button."""
        signal = SIGNAL_BUTTON_EVENT.format(entry_id=self._entry.entry_id, button=self._button)
        _LOGGER.debug(
            "event_entity_attached entry_id=%s unique_id=%s button=%s signal=%s",
            self._entry.entry_id,
            self.unique_id,
            self._button,
            signal,
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._async_handle_button_event)
        )

    @callback
    def _async_handle_button_event(self, event_data: dict[str, Any]) -> None:
        """Fire a press event on this entity."""
        _LOGGER.debug(
            "event_entity_trigger entry_id=%s unique_id=%s button=%s event_data=%s",
            self._entry.entry_id,
            self.unique_id,
            self._button,
            event_data,
        )
        self._trigger_event(EVENT_TYPE_PRESS, event_data)
        self.async_write_ha_state()
