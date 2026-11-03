"""Tests for entry setup, replay protection, migration and unload."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.enocean_ble import (
    EnOceanBleData,
    _fire_event,
    _process_advertisement,
    async_migrate_entry,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.enocean_ble.const import (
    AUTH_FAILURE_THRESHOLD,
    CONF_MAC_ADDRESS,
    CONF_SECURITY_KEY,
    ENOCEAN_MANUFACTURER_ID,
)
from custom_components.enocean_ble.crypto import calculate_mic
from custom_components.enocean_ble.diagnostics import async_get_config_entry_diagnostics

_KEY_HEX = "00112233445566778899aabbccddeeff"
_MAC = "E2:15:AA:BB:CC:DD"


@pytest.fixture(autouse=True)
def dispatcher() -> Any:
    """The real dispatcher needs a running Home Assistant; capture sends instead."""
    with patch("custom_components.enocean_ble.async_dispatcher_send") as send:
        yield send


def _telegram(seq: int, status: int = 0x01 | 0x02) -> bytes:
    payload = bytes([status])
    mic = calculate_mic(bytes.fromhex(_KEY_HEX), bytes.fromhex("DDCCBBAA15E2"), seq, payload)
    return seq.to_bytes(4, byteorder="little") + payload + mic


def _service_info(seq: int, status: int = 0x01 | 0x02) -> Any:
    return SimpleNamespace(
        address=_MAC,
        rssi=-60,
        manufacturer_data={ENOCEAN_MANUFACTURER_ID: _telegram(seq, status)},
    )


def _hass() -> Any:
    return SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=MagicMock()))


def _entry(options: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        entry_id="entry1",
        title="Switch",
        unique_id=_MAC,
        version=2,
        data={CONF_MAC_ADDRESS: _MAC, CONF_SECURITY_KEY: _KEY_HEX},
        options=options or {},
        async_on_unload=MagicMock(),
    )


async def _setup(
    stored: dict[str, Any] | None = None, options: dict[str, Any] | None = None
) -> tuple[Any, Any, MagicMock]:
    """Set up an entry with a mocked store; returns (hass, entry, store)."""
    store = MagicMock()
    store.async_load = AsyncMock(return_value=stored)
    store.async_save = AsyncMock()
    hass: Any = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
            async_update_entry=MagicMock(),
        ),
    )
    entry = _entry(options)
    with (
        patch("custom_components.enocean_ble.Store", return_value=store),
        patch("custom_components.enocean_ble.async_register_callback") as register,
        patch("custom_components.enocean_ble.ir.async_delete_issue"),
    ):
        assert await async_setup_entry(hass, entry) is True
    hass.register_callback = register
    return hass, entry, store


def _wrong_key_telegram(seq: int) -> Any:
    payload = bytes([0x03])
    mic = calculate_mic(bytes.fromhex("ff" * 16), bytes.fromhex("DDCCBBAA15E2"), seq, payload)
    telegram = seq.to_bytes(4, byteorder="little") + payload + mic
    return SimpleNamespace(
        address=_MAC, rssi=-60, manufacturer_data={ENOCEAN_MANUFACTURER_ID: telegram}
    )


async def test_setup_restores_sequence_counter_and_rejects_replay(dispatcher: MagicMock) -> None:
    hass, entry, store = await _setup({"last_sequence_counter": 41})
    assert entry.runtime_data.last_sequence_counter == 41

    with patch("custom_components.enocean_ble._emit_button_event") as emit:
        _process_advertisement(hass, entry, _service_info(41))  # replayed telegram
        emit.assert_not_called()
        store.async_delay_save.assert_not_called()

        _process_advertisement(hass, entry, _service_info(42))
        emit.assert_called_once()
    # Every authenticated telegram feeds the diagnostic sensors, replays included.
    assert dispatcher.call_count == 2
    assert dispatcher.call_args.args[1] == "enocean_ble_telegram_entry1"
    assert dispatcher.call_args.args[2]["rssi"] == -60

    assert entry.runtime_data.last_sequence_counter == 42
    data_func, delay = store.async_delay_save.call_args.args
    assert data_func() == {"last_sequence_counter": 42, "last_press_seq": {}}
    assert delay == 5.0


async def test_setup_without_stored_data_starts_fresh() -> None:
    _hass, entry, _store = await _setup(None)
    assert entry.runtime_data.last_sequence_counter == -1
    assert entry.runtime_data.last_press_seq == {}


async def test_bluetooth_callback_is_filtered_by_address() -> None:
    hass, _, _ = await _setup()
    matcher = hass.register_callback.call_args.args[2]
    assert dict(matcher) == {
        "address": _MAC,
        "manufacturer_id": ENOCEAN_MANUFACTURER_ID,
        "connectable": False,
    }


async def test_release_after_reload_pairs_with_restored_press() -> None:
    """A release arriving after a reload must not fire a second event for the same click."""
    hass, entry, _store = await _setup({"last_sequence_counter": 41, "last_press_seq": {"A0": 41}})

    with patch("custom_components.enocean_ble._fire_event") as fire:
        _process_advertisement(hass, entry, _service_info(42, status=0x02))  # A0 release
    fire.assert_not_called()


async def test_strict_option_saves_without_delay() -> None:
    hass, entry, store = await _setup(options={"strict_replay_protection": True})
    assert entry.runtime_data.save_delay == 0.0
    with patch("custom_components.enocean_ble._emit_button_event"):
        _process_advertisement(hass, entry, _service_info(1))
    assert store.async_delay_save.call_args.args[1] == 0.0


async def test_auto_commissioning_stores_key() -> None:
    hass, entry, store = await _setup()
    entry.data = {CONF_MAC_ADDRESS: _MAC}
    entry.runtime_data.security_key = ""
    commissioning = bytes.fromhex("71010000AB4B9A91852B70B8A652A05E92BB12A09F1B000015E2")
    info: Any = SimpleNamespace(
        address=_MAC, rssi=-60, manufacturer_data={ENOCEAN_MANUFACTURER_ID: commissioning}
    )

    _process_advertisement(hass, entry, info)

    assert entry.runtime_data.security_key == "ab4b9a91852b70b8a652a05e92bb12a0"
    assert entry.runtime_data.last_sequence_counter == 0x171
    hass.config_entries.async_update_entry.assert_called_once()
    assert hass.config_entries.async_update_entry.call_args.kwargs["data"][CONF_SECURITY_KEY]
    store.async_delay_save.assert_called_once()


async def test_unload_saves_counter() -> None:
    hass, entry, store = await _setup({"last_sequence_counter": 7})

    assert await async_unload_entry(hass, entry) is True

    store.async_save.assert_awaited_once_with({"last_sequence_counter": 7, "last_press_seq": {}})


async def test_failed_unload_does_not_save() -> None:
    hass, entry, store = await _setup()
    hass.config_entries.async_unload_platforms.return_value = False

    assert await async_unload_entry(hass, entry) is False

    store.async_save.assert_not_awaited()


async def test_advertisement_after_unload_is_ignored() -> None:
    hass, entry, _store = await _setup()
    assert await async_unload_entry(hass, entry) is True
    del entry.runtime_data  # Home Assistant drops it after unload

    with patch("custom_components.enocean_ble._emit_button_event") as emit:
        _process_advertisement(hass, entry, _service_info(1))
    emit.assert_not_called()


async def test_remove_entry_deletes_store() -> None:
    store = MagicMock()
    store.async_remove = AsyncMock()
    with patch("custom_components.enocean_ble.Store", return_value=store):
        await async_remove_entry(_hass(), _entry())
    store.async_remove.assert_awaited_once()


def test_fire_event_dispatches_signal_and_bus_event(dispatcher: MagicMock) -> None:
    hass = MagicMock()
    data = EnOceanBleData(mac_address=_MAC, security_key="", store=MagicMock(), save_delay=5.0)
    with patch("custom_components.enocean_ble.dr.async_get") as dr_get:
        dr_get.return_value.async_get_device.return_value = SimpleNamespace(id="dev42")
        for button, seq, rssi in (("B1", 5, -60), ("A0", 7, -61)):
            _fire_event(
                hass=hass,
                entry=_entry(),
                data=data,
                button=button,
                sequence_counter=seq,
                rssi=rssi,
                mac_address=_MAC,
            )

    assert dispatcher.call_args_list[0].args[1] == "enocean_ble_button_event_entry1_B1"
    assert dispatcher.call_args_list[0].args[2] == {
        "rssi": -60,
        "sequence_counter": 5,
        "mac_address": _MAC,
    }
    assert hass.bus.async_fire.call_args_list[0].args == (
        "enocean_ble_event",
        {
            "device_id": "dev42",
            "button": "B1",
            "type": "press",
            "rssi": -60,
            "sequence_counter": 5,
            "mac_address": _MAC,
        },
    )
    dr_get.return_value.async_get_device.assert_called_once()  # device id is cached
    assert data.device_id == "dev42"


async def test_migrate_entry_v1_removes_sensors_and_options() -> None:
    registry = MagicMock()
    entities = [
        SimpleNamespace(domain="sensor", entity_id="sensor.switch_a0_event"),
        SimpleNamespace(domain="event", entity_id="event.switch_a0"),
    ]
    hass = _hass()
    entry = _entry({"event_mode": "simple"})
    entry.version = 1
    with (
        patch("custom_components.enocean_ble.er.async_get", return_value=registry),
        patch(
            "custom_components.enocean_ble.er.async_entries_for_config_entry",
            return_value=entities,
        ),
    ):
        assert await async_migrate_entry(hass, entry) is True

    registry.async_remove.assert_called_once_with("sensor.switch_a0_event")
    hass.config_entries.async_update_entry.assert_called_once_with(entry, options={}, version=2)


async def test_migrate_entry_rejects_newer_version() -> None:
    entry = _entry()
    entry.version = 3
    assert await async_migrate_entry(_hass(), entry) is False


async def test_diagnostics_redact_security_key() -> None:
    hass, entry, _store = await _setup({"last_sequence_counter": 9, "last_press_seq": {"A1": 8}})

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["data"][CONF_SECURITY_KEY] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_MAC_ADDRESS] == _MAC
    assert diagnostics["runtime"]["last_sequence_counter"] == 9
    assert diagnostics["runtime"]["last_press_seq"] == {"A1": 8}
    assert _KEY_HEX not in str(diagnostics)


async def test_repair_issue_after_repeated_auth_failures() -> None:
    hass, entry, _store = await _setup()
    device = SimpleNamespace(id="dev42")
    with (
        patch("custom_components.enocean_ble.ir.async_create_issue") as create,
        patch("custom_components.enocean_ble.ir.async_delete_issue") as delete,
        patch("custom_components.enocean_ble.dr.async_get") as dr_get,
        patch("custom_components.enocean_ble._emit_button_event"),
    ):
        dr_get.return_value.async_get_device.return_value = device
        for seq in range(1, AUTH_FAILURE_THRESHOLD):
            _process_advertisement(hass, entry, _wrong_key_telegram(seq))
        create.assert_not_called()

        _process_advertisement(hass, entry, _wrong_key_telegram(AUTH_FAILURE_THRESHOLD))
        create.assert_called_once()
        assert create.call_args.args[2] == "auth_failed_entry1"
        placeholders = create.call_args.kwargs["translation_placeholders"]
        assert placeholders["device_url"] == "/config/devices/device/dev42"
        assert placeholders["mac"] == _MAC

        # A valid telegram clears the issue and the counter.
        _process_advertisement(hass, entry, _service_info(1))
        delete.assert_called_once_with(hass, "enocean_ble", "auth_failed_entry1")
    assert entry.runtime_data.auth_failures == 0


async def test_setup_clears_stale_repair_issue() -> None:
    with patch("custom_components.enocean_ble.ir.async_delete_issue") as delete:
        store = MagicMock()
        store.async_load = AsyncMock(return_value=None)
        hass: Any = SimpleNamespace(
            config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock())
        )
        with (
            patch("custom_components.enocean_ble.Store", return_value=store),
            patch("custom_components.enocean_ble.async_register_callback"),
        ):
            assert await async_setup_entry(hass, _entry()) is True
    delete.assert_called_once_with(hass, "enocean_ble", "auth_failed_entry1")
