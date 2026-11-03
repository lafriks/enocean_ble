"""EnOcean BLE integration."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_register_callback,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_BUTTON,
    ATTR_DEVICE_ID,
    ATTR_MAC_ADDRESS,
    ATTR_RSSI,
    ATTR_SEEN,
    ATTR_SEQUENCE_COUNTER,
    ATTR_TYPE,
    AUTH_FAILURE_THRESHOLD,
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
    EVENT_ENOCEAN_BLE,
    EVENT_TYPE_PRESS,
    ISSUE_AUTH_FAILED,
    LEGACY_PLATFORMS,
    PLATFORMS,
    SIGNAL_BUTTON_EVENT,
    SIGNAL_TELEGRAM,
    STORAGE_SAVE_DELAY_SECONDS,
    STORAGE_SAVE_DELAY_STRICT_SECONDS,
    STORAGE_VERSION,
    TELEGRAM_PRESS,
)
from .parser import parse_commissioning_telegram, parse_data_telegram

_LOGGER = logging.getLogger(__name__)


@dataclass
class EnOceanBleData:
    """Runtime data of one switch."""

    mac_address: str
    security_key: str
    store: Store[dict[str, Any]]
    # Delay before the replay counter is written to disk (0 = strict replay protection).
    save_delay: float
    # Highest sequence counter accepted so far; telegrams at or below it are replays.
    last_sequence_counter: int = -1
    # Per button: sequence counter of the last press telegram seen.
    last_press_seq: dict[str, int] = field(default_factory=dict)
    # Consecutive telegrams that failed MIC verification (wrong key after a reset).
    auth_failures: int = 0
    # Device registry id, looked up on the first event (the device is created by the platforms).
    device_id: str | None = None


type EnOceanBleConfigEntry = ConfigEntry[EnOceanBleData]


async def async_setup_entry(hass: HomeAssistant, entry: EnOceanBleConfigEntry) -> bool:
    """Set up one switch from a config entry."""
    mac_address: str = entry.data[CONF_MAC_ADDRESS]
    security_key: str = entry.data.get(CONF_SECURITY_KEY, "")

    store = _sequence_store(hass, entry)
    stored = await store.async_load() or {}
    data = EnOceanBleData(
        mac_address=mac_address,
        security_key=security_key,
        store=store,
        save_delay=_save_delay(entry),
        last_sequence_counter=int(stored.get("last_sequence_counter", -1)),
        last_press_seq={k: int(v) for k, v in stored.get("last_press_seq", {}).items()},
    )
    entry.runtime_data = data
    # A (re)load starts with a clean slate; the issue is raised again if telegrams keep failing.
    ir.async_delete_issue(hass, DOMAIN, ISSUE_AUTH_FAILED.format(entry_id=entry.entry_id))
    _LOGGER.debug(
        "setup_entry entry_id=%s mac=%s last_sequence_counter=%s save_delay=%s security_key_present=%s security_key_fingerprint=%s",
        entry.entry_id,
        mac_address,
        data.last_sequence_counter,
        data.save_delay,
        bool(security_key),
        _fingerprint_key(security_key),
    )

    @callback
    def _async_handle_advertisement(
        service_info: BluetoothServiceInfoBleak, _change: BluetoothChange
    ) -> None:
        _process_advertisement(hass, entry, service_info)

    entry.async_on_unload(
        async_register_callback(
            hass,
            _async_handle_advertisement,
            # Switches only broadcast; also accept advertisements from passive (non-connectable) scanners.
            BluetoothCallbackMatcher(
                address=mac_address,
                manufacturer_id=ENOCEAN_MANUFACTURER_ID,
                connectable=False,
            ),
            BluetoothScanningMode.PASSIVE,
        )
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _save_delay(entry: ConfigEntry) -> float:
    """Return how long to wait before writing the replay counter to disk."""
    strict = entry.options.get(CONF_STRICT_REPLAY_PROTECTION, DEFAULT_STRICT_REPLAY_PROTECTION)
    return STORAGE_SAVE_DELAY_STRICT_SECONDS if strict else STORAGE_SAVE_DELAY_SECONDS


def _sequence_store(hass: HomeAssistant, entry: ConfigEntry) -> Store[dict[str, Any]]:
    """Return the store holding the replay counter of an entry."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")


def _sequence_data(data: EnOceanBleData) -> dict[str, Any]:
    return {
        "last_sequence_counter": data.last_sequence_counter,
        "last_press_seq": dict(data.last_press_seq),
    }


def _schedule_sequence_save(data: EnOceanBleData) -> None:
    data.store.async_delay_save(lambda: _sequence_data(data), data.save_delay)


@callback
def async_reset_sequence(data: EnOceanBleData, sequence_counter: int) -> None:
    """Start replay protection over from a new baseline (new key after re-commissioning)."""
    data.last_sequence_counter = sequence_counter
    data.last_press_seq.clear()
    data.auth_failures = 0


def _record_auth_failure(hass: HomeAssistant, entry: ConfigEntry, data: EnOceanBleData) -> None:
    """Count a telegram that failed authentication and raise a repair issue when it persists."""
    data.auth_failures += 1
    if data.auth_failures < AUTH_FAILURE_THRESHOLD:
        return
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    device_url = (
        f"/config/devices/device/{device.id}"
        if device
        else f"/config/integrations/integration/{DOMAIN}"
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_AUTH_FAILED.format(entry_id=entry.entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="auth_failed",
        translation_placeholders={
            "name": entry.title,
            "mac": data.mac_address,
            "device_url": device_url,
        },
    )


def _record_auth_success(hass: HomeAssistant, entry: ConfigEntry, data: EnOceanBleData) -> None:
    if data.auth_failures >= AUTH_FAILURE_THRESHOLD:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_AUTH_FAILED.format(entry_id=entry.entry_id))
    data.auth_failures = 0


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries created by older versions."""
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
    hass: HomeAssistant,
    entry: EnOceanBleConfigEntry,
    service_info: BluetoothServiceInfoBleak,
) -> None:
    """Process BLE advertisements and emit Home Assistant button events."""
    data: EnOceanBleData | None = getattr(entry, "runtime_data", None)
    if data is None:
        # Home Assistant drops runtime_data on unload before unregistering this callback.
        _LOGGER.debug("adv_ignored entry_id=%s reason=entry_unloaded", entry.entry_id)
        return
    configured_mac = data.mac_address.upper()
    manufacturer_data = service_info.manufacturer_data.get(ENOCEAN_MANUFACTURER_ID)
    payload_len = len(manufacturer_data) if manufacturer_data is not None else 0
    _LOGGER.debug(
        "adv_received entry_id=%s configured_mac=%s adv_address=%s rssi=%s manufacturer_ids=%s enocean_payload_len=%s",
        entry.entry_id,
        configured_mac,
        service_info.address.upper(),
        service_info.rssi,
        sorted(service_info.manufacturer_data.keys()),
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

        if not data.security_key:
            data.security_key = commissioning.security_key_hex
            async_reset_sequence(data, commissioning.sequence_counter)
            _schedule_sequence_save(data)
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_SECURITY_KEY: commissioning.security_key_hex}
            )
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
            _fingerprint_key(data.security_key),
        )
        return

    if not data.security_key:
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
        _fingerprint_key(data.security_key),
    )
    try:
        parsed = parse_data_telegram(
            manufacturer_data,
            mac_address=configured_mac,
            security_key_hex=data.security_key,
        )
    except ValueError as err:
        _LOGGER.debug(
            "telegram_parse_failed entry_id=%s mac=%s error=%s payload_len=%s",
            entry.entry_id,
            configured_mac,
            str(err),
            len(manufacturer_data),
        )
        _record_auth_failure(hass, entry, data)
        return
    _record_auth_success(hass, entry, data)
    async_dispatcher_send(
        hass,
        SIGNAL_TELEGRAM.format(entry_id=entry.entry_id),
        {ATTR_RSSI: service_info.rssi, ATTR_SEEN: dt_util.utcnow()},
    )

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

    if parsed.sequence_counter <= data.last_sequence_counter:
        _LOGGER.debug(
            "telegram_deduplicated entry_id=%s mac=%s sequence_counter=%s last_sequence=%s",
            entry.entry_id,
            configured_mac,
            parsed.sequence_counter,
            data.last_sequence_counter,
        )
        return

    data.last_sequence_counter = parsed.sequence_counter
    _schedule_sequence_save(data)
    for button in parsed.buttons:
        _emit_button_event(
            hass=hass,
            entry=entry,
            data=data,
            button=button,
            event_type=parsed.event_type,
            sequence_counter=parsed.sequence_counter,
            rssi=service_info.rssi,
            mac_address=configured_mac,
        )


def _emit_button_event(
    *,
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: EnOceanBleData,
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
    if event_type == TELEGRAM_PRESS:
        data.last_press_seq[button] = sequence_counter
        _LOGGER.debug(
            "event_press entry_id=%s mac=%s button=%s sequence_counter=%s",
            entry.entry_id,
            mac_address,
            button,
            sequence_counter,
        )
    elif data.last_press_seq.get(button) == sequence_counter - 1:
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
            data.last_press_seq.get(button),
        )

    _fire_event(
        hass=hass,
        entry=entry,
        data=data,
        button=button,
        sequence_counter=sequence_counter,
        rssi=rssi,
        mac_address=mac_address,
    )


def _fire_event(
    *,
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: EnOceanBleData,
    button: str,
    sequence_counter: int,
    rssi: int | None,
    mac_address: str,
) -> None:
    """Send a press event to the button's entity and fire the bus event for device triggers."""
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

    if data.device_id is None:
        device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        data.device_id = device.id if device else None
    hass.bus.async_fire(
        EVENT_ENOCEAN_BLE,
        {
            ATTR_DEVICE_ID: data.device_id,
            ATTR_BUTTON: button,
            ATTR_TYPE: EVENT_TYPE_PRESS,
            **event_data,
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: EnOceanBleConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # Write now instead of after the save delay, so a reload loads the latest counter.
        data = entry.runtime_data
        await data.store.async_save(_sequence_data(data))
    _LOGGER.debug("unload_entry entry_id=%s unloaded=%s", entry.entry_id, unloaded)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persisted data of a deleted config entry."""
    await _sequence_store(hass, entry).async_remove()


def _fingerprint_key(key_hex: str) -> str:
    """Return a short, non-reversible key fingerprint for debug logs."""
    if not key_hex:
        return "none"
    digest = hashlib.sha256(key_hex.encode("utf-8")).hexdigest()
    return digest[:8]
