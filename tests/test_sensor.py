"""Tests for the diagnostic sensors."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.enocean_ble import EnOceanBleData
from custom_components.enocean_ble.sensor import SENSORS, EnOceanBleDiagnosticSensor


def _entry() -> Any:
    entry = SimpleNamespace(entry_id="entry1", unique_id="E2:15:AA:BB:CC:DD", title="Switch")
    entry.runtime_data = EnOceanBleData(
        mac_address="E2:15:AA:BB:CC:DD", security_key="", store=MagicMock(), save_delay=5.0
    )
    return entry


def test_sensors_are_diagnostic_and_disabled_by_default() -> None:
    for description in SENSORS:
        sensor = EnOceanBleDiagnosticSensor(_entry(), description)
        assert sensor.entity_category == "diagnostic"
        assert sensor.entity_registry_enabled_default is False
        assert sensor.unique_id == f"E2:15:AA:BB:CC:DD_{description.key}"


async def test_sensors_update_from_telegram_signal() -> None:
    rssi, seen = (EnOceanBleDiagnosticSensor(_entry(), d) for d in SENSORS)
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    with patch.object(EnOceanBleDiagnosticSensor, "async_write_ha_state") as write:
        for sensor in (rssi, seen):
            sensor.hass = MagicMock()
            sensor._async_handle_telegram({"rssi": -63, "last_seen": now})

    assert rssi.native_value == -63
    assert seen.native_value == now
    assert write.call_count == 2


async def test_last_seen_restores_from_string() -> None:
    seen = EnOceanBleDiagnosticSensor(_entry(), SENSORS[1])
    seen.hass = MagicMock()
    restored = SimpleNamespace(native_value="2026-11-03T12:00:00+00:00")
    with (
        patch.object(
            EnOceanBleDiagnosticSensor,
            "async_get_last_sensor_data",
            AsyncMock(return_value=restored),
        ),
        patch("custom_components.enocean_ble.sensor.async_dispatcher_connect"),
        patch("homeassistant.helpers.restore_state.RestoreEntity.async_added_to_hass", AsyncMock()),
    ):
        await seen.async_added_to_hass()
    assert seen.native_value == datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
