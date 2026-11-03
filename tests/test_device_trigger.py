"""Tests for device triggers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.enocean_ble.device_trigger import (
    async_attach_trigger,
    async_get_triggers,
)


async def test_get_triggers_lists_all_buttons() -> None:
    device = SimpleNamespace(identifiers={("enocean_ble", "entry1")})
    hass: Any = MagicMock()
    with patch("custom_components.enocean_ble.device_trigger.dr.async_get") as registry:
        registry.return_value.async_get.return_value = device
        triggers = await async_get_triggers(hass, "dev1")

    assert [t["subtype"] for t in triggers] == ["A0", "A1", "B0", "B1"]
    assert all(t["type"] == "press" and t["domain"] == "enocean_ble" for t in triggers)


async def test_get_triggers_ignores_other_devices() -> None:
    device = SimpleNamespace(identifiers={("hue", "x")})
    hass: Any = MagicMock()
    with patch("custom_components.enocean_ble.device_trigger.dr.async_get") as registry:
        registry.return_value.async_get.return_value = device
        assert await async_get_triggers(hass, "dev1") == []


async def test_attach_trigger_listens_for_bus_event() -> None:
    hass: Any = MagicMock()
    with (
        # HA's template validators in the event trigger schema need a running Home Assistant.
        patch(
            "custom_components.enocean_ble.device_trigger.event_trigger.TRIGGER_SCHEMA",
            side_effect=lambda config: config,
        ),
        patch(
            "custom_components.enocean_ble.device_trigger.event_trigger.async_attach_trigger",
            AsyncMock(return_value=lambda: None),
        ) as attach,
    ):
        await async_attach_trigger(
            hass,
            {
                "platform": "device",
                "domain": "enocean_ble",
                "device_id": "dev1",
                "type": "press",
                "subtype": "B1",
            },
            MagicMock(),
            MagicMock(),
        )
    event_config = attach.call_args.args[1]
    assert event_config["event_type"] == "enocean_ble_event"
    assert event_config["event_data"] == {"device_id": "dev1", "button": "B1", "type": "press"}
