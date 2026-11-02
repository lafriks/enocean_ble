"""EnOcean BLE integration."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from .const import (
    ATTR_MAC_ADDRESS,
    ATTR_RSSI,
    ATTR_SEQUENCE_COUNTER,
    COMMISSIONING_TELEGRAM_LENGTH,
    CONF_MAC_ADDRESS,
    CONF_SECURITY_KEY,
    CONF_STRICT_REPLAY_PROTECTION,
    CONFIG_ENTRY_VERSION,
    DATA_TELEGRAM_MIN_LENGTH,
    DEFAULT_STRICT_REPLAY_PROTECTION,
    DOMAIN,
    ENOCEAN_MAC_PREFIX,
    ENOCEAN_MANUFACTURER_ID,
    LEGACY_PLATFORMS,
    PLATFORMS,
    SIGNAL_BUTTON_EVENT,
    STORAGE_SAVE_DELAY_SECONDS,
    STORAGE_SAVE_DELAY_STRICT_SECONDS,
    STORAGE_VERSION,
    TELEGRAM_PRESS,
)
from .parser import parse_commissioning_telegram, parse_data_telegram

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: Any, config: dict[str, Any]) -> bool:
    """Set up EnOcean BLE integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: Any, entry: Any) -> bool:
    """Set up EnOcean BLE from a config entry."""
    from homeassistant.components.bluetooth import (
        BluetoothChange,
        BluetoothScanningMode,
        BluetoothServiceInfoBleak,
        async_register_callback,
    )

    hass.data.setdefault(DOMAIN, {})

    mac_address = entry.data[CONF_MAC_ADDRESS]
    security_key = entry.data.get(CONF_SECURITY_KEY, "")

    store = _sequence_store(hass, entry)
    stored = await store.async_load() or {}
    last_sequence_counter = int(stored.get("last_sequence_counter", -1))

    save_delay = _save_delay(entry)
    hass.data[DOMAIN][entry.entry_id] = {
        CONF_MAC_ADDRESS: mac_address,
        CONF_SECURITY_KEY: security_key,
        "store": store,
        "save_delay": save_delay,
        "last_sequence_counter": last_sequence_counter,
        # Per button: sequence counter of the last press telegram seen. Restored too, so a
        # release arriving right after a reload still pairs with its press.
        "last_press_seq": {k: int(v) for k, v in stored.get("last_press_seq", {}).items()},
    }
    _LOGGER.debug(
        "setup_entry entry_id=%s mac=%s last_sequence_counter=%s save_delay=%s security_key_present=%s security_key_fingerprint=%s",
        entry.entry_id,
        mac_address,
        last_sequence_counter,
        save_delay,
        bool(security_key),
        _fingerprint_key(security_key),
    )

    def _async_handle_advertisement(
        service_info: BluetoothServiceInfoBleak, _change: BluetoothChange
    ) -> None:
        _process_advertisement(hass, entry, service_info)

    entry.async_on_unload(
        async_register_callback(
            hass,
            _async_handle_advertisement,
            # Switches only broadcast; also accept advertisements from passive (non-connectable) scanners.
            {
                "address": mac_address,
                "manufacturer_id": ENOCEAN_MANUFACTURER_ID,
                "connectable": False,
            },
            BluetoothScanningMode.PASSIVE,
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("EnOcean BLE device configured: %s", mac_address)
    return True


def _save_delay(entry: Any) -> float:
    """Return how long to wait before writing the replay counter to disk."""
    strict = entry.options.get(CONF_STRICT_REPLAY_PROTECTION, DEFAULT_STRICT_REPLAY_PROTECTION)
    return STORAGE_SAVE_DELAY_STRICT_SECONDS if strict else STORAGE_SAVE_DELAY_SECONDS


async def _async_update_listener(hass: Any, entry: Any) -> None:
    """Reload the entry when an option changes."""
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    # Data updates (auto-commissioning writing the key) also land here; only reload on option change.
    if entry_data is None or entry_data["save_delay"] == _save_delay(entry):
        return
    await hass.config_entries.async_reload(entry.entry_id)


def _sequence_store(hass: Any, entry: Any) -> Any:
    """Return the store holding the last accepted sequence counter of an entry."""
    from homeassistant.helpers.storage import Store

    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")


def _sequence_data(entry_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "last_sequence_counter": int(entry_data["last_sequence_counter"]),
        "last_press_seq": dict(entry_data["last_press_seq"]),
    }


def _schedule_sequence_save(entry_data: dict[str, Any]) -> None:
    store = entry_data.get("store")
    if store is not None:
        store.async_delay_save(lambda: _sequence_data(entry_data), entry_data["save_delay"])


async def async_migrate_entry(hass: Any, entry: Any) -> bool:
    """Migrate config entries created by older versions."""
    from homeassistant.helpers import entity_registry as er

    if entry.version > CONFIG_ENTRY_VERSION:
        # Created by a newer version; downgrades are not supported.
        return False

    if entry.version == 1:
        # Version 1 had per-button event sensors and an event_mode option; neither exists any more.
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain in LEGACY_PLATFORMS:
                _LOGGER.debug(
                    "entity_removed entry_id=%s entity_id=%s reason=legacy_platform",
                    entry.entry_id,
                    entity.entity_id,
                )
                registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(entry, options={}, version=CONFIG_ENTRY_VERSION)
        _LOGGER.info("EnOcean BLE config entry %s migrated to version 2", entry.entry_id)

    return True


def _process_advertisement(
    hass: Any,
    entry: Any,
    service_info: Any,
) -> None:
    """Process BLE advertisements and emit Home Assistant button events."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_data is None:
        # Entry data is removed on unload before Home Assistant unregisters this callback.
        _LOGGER.debug("adv_ignored entry_id=%s reason=entry_unloaded", entry.entry_id)
        return
    configured_mac = str(entry_data[CONF_MAC_ADDRESS]).upper()
    manufacturer_ids = sorted(service_info.manufacturer_data.keys())
    manufacturer_data = service_info.manufacturer_data.get(ENOCEAN_MANUFACTURER_ID)
    payload_len = len(manufacturer_data) if manufacturer_data is not None else 0
    _LOGGER.debug(
        "adv_received entry_id=%s configured_mac=%s adv_address=%s rssi=%s manufacturer_ids=%s enocean_payload_len=%s",
        entry.entry_id,
        configured_mac,
        service_info.address.upper(),
        service_info.rssi,
        manufacturer_ids,
        payload_len,
    )

    if service_info.address.upper() != configured_mac:
        _LOGGER.debug(
            "adv_filtered entry_id=%s reason=mac_mismatch configured_mac=%s adv_address=%s",
            entry.entry_id,
            configured_mac,
            service_info.address.upper(),
        )
        return
    if not configured_mac.startswith(ENOCEAN_MAC_PREFIX):
        _LOGGER.debug(
            "adv_filtered entry_id=%s reason=mac_prefix_mismatch configured_mac=%s expected_prefix=%s",
            entry.entry_id,
            configured_mac,
            ENOCEAN_MAC_PREFIX,
        )
        return

    if manufacturer_data is None:
        _LOGGER.debug(
            "adv_filtered entry_id=%s reason=enocean_manufacturer_data_missing manufacturer_id=%s",
            entry.entry_id,
            ENOCEAN_MANUFACTURER_ID,
        )
        return
    if len(manufacturer_data) < DATA_TELEGRAM_MIN_LENGTH:
        _LOGGER.debug(
            "adv_filtered entry_id=%s reason=telegram_too_short payload_len=%s min_len=%s",
            entry.entry_id,
            len(manufacturer_data),
            DATA_TELEGRAM_MIN_LENGTH,
        )
        return

    security_key_hex = str(entry_data.get(CONF_SECURITY_KEY, ""))
    if len(manufacturer_data) == COMMISSIONING_TELEGRAM_LENGTH:
        _LOGGER.debug(
            "commissioning_detected entry_id=%s mac=%s payload_len=%s",
            entry.entry_id,
            configured_mac,
            len(manufacturer_data),
        )
        try:
            commissioning = parse_commissioning_telegram(manufacturer_data)
        except ValueError:
            _LOGGER.debug(
                "commissioning_parse_failed entry_id=%s mac=%s payload_len=%s",
                entry.entry_id,
                configured_mac,
                len(manufacturer_data),
            )
            return

        if not security_key_hex:
            entry_data[CONF_SECURITY_KEY] = commissioning.security_key_hex
            entry_data["last_sequence_counter"] = commissioning.sequence_counter
            _schedule_sequence_save(entry_data)
            new_data = dict(entry.data)
            new_data[CONF_SECURITY_KEY] = commissioning.security_key_hex
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.info("EnOcean BLE auto-commissioned device %s", configured_mac)
            _LOGGER.debug(
                "commissioning_applied entry_id=%s mac=%s sequence_counter=%s key_fingerprint=%s",
                entry.entry_id,
                configured_mac,
                commissioning.sequence_counter,
                _fingerprint_key(commissioning.security_key_hex),
            )
            return

        _LOGGER.debug(
            "commissioning_seen_ignored entry_id=%s mac=%s sequence_counter=%s reason=entry_already_has_key key_fingerprint=%s",
            entry.entry_id,
            configured_mac,
            commissioning.sequence_counter,
            _fingerprint_key(security_key_hex),
        )
        return

    if not security_key_hex:
        _LOGGER.debug(
            "telegram_ignored_no_key entry_id=%s mac=%s payload_len=%s",
            entry.entry_id,
            configured_mac,
            len(manufacturer_data),
        )
        return

    _LOGGER.debug(
        "telegram_parse_attempt entry_id=%s mac=%s payload_len=%s key_fingerprint=%s",
        entry.entry_id,
        configured_mac,
        len(manufacturer_data),
        _fingerprint_key(security_key_hex),
    )
    try:
        parsed = parse_data_telegram(
            manufacturer_data,
            mac_address=configured_mac,
            security_key_hex=security_key_hex,
        )
    except ValueError as err:
        _LOGGER.debug(
            "telegram_parse_failed entry_id=%s mac=%s error=%s payload_len=%s",
            entry.entry_id,
            configured_mac,
            str(err),
            len(manufacturer_data),
        )
        return

    _LOGGER.debug(
        "telegram_parse_success entry_id=%s mac=%s buttons=%s event_type=%s sequence_counter=%s",
        entry.entry_id,
        configured_mac,
        parsed.buttons,
        parsed.event_type,
        parsed.sequence_counter,
    )
    if not parsed.buttons:
        _LOGGER.debug(
            "telegram_ignored_no_active_buttons entry_id=%s mac=%s sequence_counter=%s",
            entry.entry_id,
            configured_mac,
            parsed.sequence_counter,
        )
        return

    last_sequence = int(entry_data["last_sequence_counter"])
    if parsed.sequence_counter <= last_sequence:
        _LOGGER.debug(
            "telegram_deduplicated entry_id=%s mac=%s sequence_counter=%s last_sequence=%s",
            entry.entry_id,
            configured_mac,
            parsed.sequence_counter,
            last_sequence,
        )
        return

    entry_data["last_sequence_counter"] = parsed.sequence_counter
    _schedule_sequence_save(entry_data)
    for button in parsed.buttons:
        _emit_button_event(
            hass=hass,
            entry=entry,
            entry_data=entry_data,
            button=button,
            event_type=parsed.event_type,
            sequence_counter=parsed.sequence_counter,
            rssi=service_info.rssi,
            mac_address=configured_mac,
        )


def _emit_button_event(
    *,
    hass: Any,
    entry: Any,
    entry_data: dict[str, Any],
    button: str,
    event_type: str,
    sequence_counter: int,
    rssi: int | None,
    mac_address: str,
) -> None:
    """Emit exactly one press event per physical click.

    The press telegram fires the event. A release telegram fires it only when it
    does not pair with a seen press (sequence counter = press + 1), i.e. the press
    telegram was lost.
    """
    last_press_seq: dict[str, int] = entry_data["last_press_seq"]

    if event_type == TELEGRAM_PRESS:
        last_press_seq[button] = sequence_counter
        _LOGGER.debug(
            "event_press entry_id=%s mac=%s button=%s sequence_counter=%s",
            entry.entry_id,
            mac_address,
            button,
            sequence_counter,
        )
    elif last_press_seq.get(button) == sequence_counter - 1:
        _LOGGER.debug(
            "event_release_paired entry_id=%s mac=%s button=%s sequence_counter=%s",
            entry.entry_id,
            mac_address,
            button,
            sequence_counter,
        )
        return
    else:
        _LOGGER.debug(
            "event_press_recovered entry_id=%s mac=%s button=%s sequence_counter=%s last_press_seq=%s",
            entry.entry_id,
            mac_address,
            button,
            sequence_counter,
            last_press_seq.get(button),
        )

    _fire_event(
        hass=hass,
        entry=entry,
        button=button,
        sequence_counter=sequence_counter,
        rssi=rssi,
        mac_address=mac_address,
    )


def _fire_event(
    *,
    hass: Any,
    entry: Any,
    button: str,
    sequence_counter: int,
    rssi: int | None,
    mac_address: str,
) -> None:
    """Send a press event to the entity of this button."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    signal = SIGNAL_BUTTON_EVENT.format(entry_id=entry.entry_id, button=button)
    event_data = {
        ATTR_RSSI: rssi,
        ATTR_SEQUENCE_COUNTER: sequence_counter,
        ATTR_MAC_ADDRESS: mac_address,
    }
    _LOGGER.debug(
        "event_emit entry_id=%s mac=%s button=%s sequence_counter=%s rssi=%s signal=%s",
        entry.entry_id,
        mac_address,
        button,
        sequence_counter,
        rssi,
        signal,
    )
    async_dispatcher_send(hass, signal, event_data)


async def async_unload_entry(hass: Any, entry: Any) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
        store = entry_data.get("store") if entry_data else None
        if store is not None:
            # Write now instead of after the save delay, so a reload loads the latest counter.
            await store.async_save(_sequence_data(entry_data))
    _LOGGER.debug("unload_entry entry_id=%s unloaded=%s", entry.entry_id, unloaded)
    return bool(unloaded)


async def async_remove_entry(hass: Any, entry: Any) -> None:
    """Remove persisted data of a deleted config entry."""
    await _sequence_store(hass, entry).async_remove()


def _fingerprint_key(key_hex: str) -> str:
    """Return a short, non-reversible key fingerprint for debug logs."""
    if not key_hex:
        return "none"
    digest = hashlib.sha256(key_hex.encode("utf-8")).hexdigest()
    return digest[:8]
