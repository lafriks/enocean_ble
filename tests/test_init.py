"""Basic setup tests for integration bootstrap."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.enocean_ble import (
    _process_advertisement,
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


def _service_info(seq: int) -> SimpleNamespace:
    return SimpleNamespace(
        address=_MAC,
        rssi=-60,
        manufacturer_data={ENOCEAN_MANUFACTURER_ID: _telegram(seq)},
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
    entry = _entry()
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
    assert data_func() == {"last_sequence_counter": 42}


async def test_setup_without_stored_data_starts_fresh() -> None:
    hass, entry, _store = await _setup(None)
    assert hass.data[DOMAIN][entry.entry_id]["last_sequence_counter"] == -1


async def test_unload_cancels_timers_and_saves_counter() -> None:
    hass, entry, store = await _setup({"last_sequence_counter": 7})
    cancel_long = MagicMock()
    cancel_timeout = MagicMock()
    hass.data[DOMAIN][entry.entry_id]["buttons"]["A0"] = {
        "cancel_long_cb": cancel_long,
        "cancel_timeout_cb": cancel_timeout,
    }

    assert await async_unload_entry(hass, entry) is True

    cancel_long.assert_called_once()
    cancel_timeout.assert_called_once()
    store.async_save.assert_awaited_once_with({"last_sequence_counter": 7})
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_failed_unload_keeps_timers_and_data() -> None:
    hass, entry, store = await _setup(None)
    hass.config_entries.async_unload_platforms.return_value = False
    cancel_long = MagicMock()
    hass.data[DOMAIN][entry.entry_id]["buttons"]["A0"] = {"cancel_long_cb": cancel_long}

    assert await async_unload_entry(hass, entry) is False

    cancel_long.assert_not_called()
    store.async_save.assert_not_awaited()
    assert entry.entry_id in hass.data[DOMAIN]


async def test_remove_entry_deletes_store() -> None:
    store = MagicMock()
    store.async_remove = AsyncMock()
    with patch("homeassistant.helpers.storage.Store", return_value=store):
        await async_remove_entry(SimpleNamespace(), _entry())
    store.async_remove.assert_awaited_once()
