"""Basic setup tests for integration bootstrap."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.enocean_ble import (
    _async_update_listener,
    _fire_event,
    _process_advertisement,
    async_migrate_entry,
    async_remove_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.enocean_ble.const import (
    CONF_MAC_ADDRESS,
    CONF_SECURITY_KEY,
    DOMAIN,
    ENOCEAN_MANUFACTURER_ID,
)
from custom_components.enocean_ble.crypto import calculate_mic


async def test_async_setup_initializes_domain() -> None:
    hass = SimpleNamespace(data={})
    assert await async_setup(hass, {}) is True
    assert DOMAIN in hass.data


# ---------------------------------------------------------------------------
# Sequence counter persistence and unload cleanup
# ---------------------------------------------------------------------------

_KEY_HEX = "00112233445566778899aabbccddeeff"
_MAC = "E2:15:AA:BB:CC:DD"


def _telegram(seq: int, status: int = 0x01 | 0x02) -> bytes:
    payload = bytes([status])
    mic = calculate_mic(bytes.fromhex(_KEY_HEX), bytes.fromhex("DDCCBBAA15E2"), seq, payload)
    return seq.to_bytes(4, byteorder="little") + payload + mic


def _service_info(seq: int, status: int = 0x01 | 0x02) -> SimpleNamespace:
    return SimpleNamespace(
        address=_MAC,
        rssi=-60,
        manufacturer_data={ENOCEAN_MANUFACTURER_ID: _telegram(seq, status)},
    )


def _entry() -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="entry1",
        data={CONF_MAC_ADDRESS: _MAC, CONF_SECURITY_KEY: _KEY_HEX},
        options={},
        async_on_unload=MagicMock(),
        add_update_listener=MagicMock(),
    )


async def _setup(stored: dict[str, Any] | None) -> tuple[Any, Any, MagicMock]:
    return await _setup_with(_entry(), stored)


async def _setup_with(
    entry: Any, stored: dict[str, Any] | None = None
) -> tuple[Any, Any, MagicMock]:
    store = MagicMock()
    store.async_load = AsyncMock(return_value=stored)
    store.async_save = AsyncMock()
    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
        ),
    )
    with patch("homeassistant.helpers.storage.Store", return_value=store):
        assert await async_setup_entry(hass, entry) is True
    return hass, entry, store


async def test_setup_restores_sequence_counter_and_rejects_replay() -> None:
    hass, entry, store = await _setup({"last_sequence_counter": 41})
    entry_data = hass.data[DOMAIN][entry.entry_id]
    assert entry_data["last_sequence_counter"] == 41

    with patch("custom_components.enocean_ble._emit_button_event") as emit:
        _process_advertisement(hass, entry, _service_info(41))  # replayed telegram
        emit.assert_not_called()
        store.async_delay_save.assert_not_called()

        _process_advertisement(hass, entry, _service_info(42))
        emit.assert_called_once()

    assert entry_data["last_sequence_counter"] == 42
    store.async_delay_save.assert_called_once()
    data_func = store.async_delay_save.call_args.args[0]
    assert data_func() == {"last_sequence_counter": 42, "last_press_seq": {}}


async def test_setup_without_stored_data_starts_fresh() -> None:
    hass, entry, _store = await _setup(None)
    assert hass.data[DOMAIN][entry.entry_id]["last_sequence_counter"] == -1


async def test_unload_saves_counter_and_clears_data() -> None:
    hass, entry, store = await _setup({"last_sequence_counter": 7})

    assert await async_unload_entry(hass, entry) is True

    store.async_save.assert_awaited_once_with({"last_sequence_counter": 7, "last_press_seq": {}})
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_failed_unload_keeps_data() -> None:
    hass, entry, store = await _setup(None)
    hass.config_entries.async_unload_platforms.return_value = False

    assert await async_unload_entry(hass, entry) is False

    store.async_save.assert_not_awaited()
    assert entry.entry_id in hass.data[DOMAIN]


async def test_remove_entry_deletes_store() -> None:
    store = MagicMock()
    store.async_remove = AsyncMock()
    with patch("homeassistant.helpers.storage.Store", return_value=store):
        await async_remove_entry(SimpleNamespace(), _entry())
    store.async_remove.assert_awaited_once()


async def test_bluetooth_callback_is_filtered_by_address() -> None:
    with patch("homeassistant.components.bluetooth.async_register_callback") as register:
        await _setup(None)
    matcher = register.call_args.args[2]
    assert matcher == {
        "address": _MAC,
        "manufacturer_id": ENOCEAN_MANUFACTURER_ID,
        "connectable": False,
    }


async def test_advertisement_after_unload_is_ignored() -> None:
    hass, entry, _store = await _setup(None)
    assert await async_unload_entry(hass, entry) is True

    with patch("custom_components.enocean_ble._emit_button_event") as emit:
        _process_advertisement(hass, entry, _service_info(1))
    emit.assert_not_called()


def test_fire_event_dispatches_per_button_signal() -> None:
    hass = MagicMock()
    entry = _entry()
    with patch("homeassistant.helpers.dispatcher.async_dispatcher_send") as send:
        _fire_event(
            hass=hass,
            entry=entry,
            button="B1",
            sequence_counter=5,
            rssi=-60,
            mac_address=_MAC,
        )

    send.assert_called_once()
    assert send.call_args.args[1] == "enocean_ble_button_event_entry1_B1"
    assert send.call_args.args[2] == {"rssi": -60, "sequence_counter": 5, "mac_address": _MAC}


async def test_release_after_reload_pairs_with_restored_press() -> None:
    """A release arriving after a reload must not fire a second event for the same click."""
    hass, entry, _store = await _setup({"last_sequence_counter": 41, "last_press_seq": {"A0": 41}})

    with patch("custom_components.enocean_ble._fire_event") as fire:
        _process_advertisement(hass, entry, _service_info(42, status=0x02))  # A0 release
    fire.assert_not_called()


async def test_migrate_entry_v1_removes_sensors_and_options() -> None:
    registry = MagicMock()
    entities = [
        SimpleNamespace(domain="sensor", entity_id="sensor.switch_a0_event"),
        SimpleNamespace(domain="event", entity_id="event.switch_a0"),
    ]
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=MagicMock()))
    entry = _entry()
    entry.version = 1
    entry.options = {"event_mode": "simple"}
    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=registry),
        patch(
            "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
            return_value=entities,
        ),
    ):
        assert await async_migrate_entry(hass, entry) is True

    registry.async_remove.assert_called_once_with("sensor.switch_a0_event")
    hass.config_entries.async_update_entry.assert_called_once_with(entry, options={}, version=2)


async def test_migrate_entry_rejects_newer_version() -> None:
    entry = _entry()
    entry.version = 3
    assert await async_migrate_entry(SimpleNamespace(), entry) is False


async def test_strict_option_saves_without_delay() -> None:
    hass, entry, store = await _setup(None)
    assert hass.data[DOMAIN][entry.entry_id]["save_delay"] == 5.0

    entry.options = {"strict_replay_protection": True}
    hass, entry, store = await _setup_with(entry)
    with patch("custom_components.enocean_ble._emit_button_event"):
        _process_advertisement(hass, entry, _service_info(1))
    assert store.async_delay_save.call_args.args[1] == 0.0


async def test_update_listener_reloads_only_when_option_changes() -> None:
    hass, entry, _store = await _setup(None)
    hass.config_entries.async_reload = AsyncMock()

    await _async_update_listener(hass, entry)  # e.g. auto-commissioning wrote the key
    hass.config_entries.async_reload.assert_not_awaited()

    entry.options = {"strict_replay_protection": True}
    await _async_update_listener(hass, entry)
    hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)
