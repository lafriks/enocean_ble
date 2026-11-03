"""Diagnostic sensors: signal strength and last seen (disabled by default)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import EnOceanBleConfigEntry
from .const import (
    ATTR_RSSI,
    ATTR_SEEN,
    DOMAIN,
    INTEGRATION_NAME,
    SIGNAL_TELEGRAM,
)

_LOGGER = logging.getLogger(__name__)

SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key=ATTR_RSSI,
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_SEEN,
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnOceanBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the diagnostic sensors."""
    async_add_entities(EnOceanBleDiagnosticSensor(entry, description) for description in SENSORS)


class EnOceanBleDiagnosticSensor(RestoreSensor):
    """Value taken from the last authenticated telegram of the switch."""

    _attr_has_entity_name = True

    def __init__(self, entry: EnOceanBleConfigEntry, description: SensorEntityDescription) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(dr.CONNECTION_BLUETOOTH, entry.runtime_data.mac_address)},
            name=entry.title,
            manufacturer="EnOcean",
            model=INTEGRATION_NAME,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last value and subscribe to telegrams of this switch."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = self._restored_value(last.native_value)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_TELEGRAM.format(entry_id=self._entry.entry_id),
                self._async_handle_telegram,
            )
        )

    def _restored_value(self, value: Any) -> Any:
        if self.entity_description.key == ATTR_SEEN and isinstance(value, str):
            return dt_util.parse_datetime(value)
        return value

    @callback
    def _async_handle_telegram(self, telegram: dict[str, Any]) -> None:
        value: int | datetime | None = telegram.get(self.entity_description.key)
        if value is None:
            return
        self._attr_native_value = value
        self.async_write_ha_state()
