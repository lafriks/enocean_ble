"""Diagnostics support for EnOcean BLE."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EnOceanBleConfigEntry, _fingerprint_key
from .const import CONF_SECURITY_KEY

TO_REDACT = {CONF_SECURITY_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EnOceanBleConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry; the security key is redacted."""
    data = entry.runtime_data
    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "runtime": {
            "security_key_fingerprint": _fingerprint_key(data.security_key),
            "save_delay": data.save_delay,
            "last_sequence_counter": data.last_sequence_counter,
            "last_press_seq": dict(data.last_press_seq),
        },
    }
