"""Config flow for EnOcean BLE integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_process_advertisements,
)
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import EnOceanBleData, async_reset_sequence
from .const import (
    COMMISSIONING_TELEGRAM_LENGTH,
    CONF_MAC_ADDRESS,
    CONF_ONBOARDING,
    CONF_SECURITY_KEY,
    CONF_STRICT_REPLAY_PROTECTION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_STRICT_REPLAY_PROTECTION,
    DOMAIN,
    ENOCEAN_MAC_PREFIX,
    ENOCEAN_MANUFACTURER_ID,
)
from .parser import (
    ParsedCommissioningTelegram,
    parse_commissioning_telegram,
    parse_onboarding_blob,
)

_LOGGER = logging.getLogger(__name__)
FLOW_TAG = "[ENOCEAN_FLOW]"

# The user has to perform the commissioning button sequence; give them time.
COMMISSIONING_TIMEOUT = 120


class EnOceanBleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Commission one switch from Bluetooth discovery."""

    VERSION = CONFIG_ENTRY_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EnOceanBleOptionsFlow:
        """Return the options flow handler."""
        return EnOceanBleOptionsFlow()

    def __init__(self) -> None:
        """Initialize config flow state."""
        self._mac: str | None = None
        self._title: str | None = None
        self._commissioning: ParsedCommissioningTelegram | None = None
        self._task: asyncio.Task[None] | None = None
        self._error: str | None = None

    @property
    def _security_key(self) -> str | None:
        return self._commissioning.security_key_hex if self._commissioning else None

    @callback
    def async_remove(self) -> None:
        """Stop waiting for telegrams when the flow is closed."""
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual setup from the QR/NFC label text (or address and key) of the switch."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                onboarding = parse_onboarding_blob(user_input[CONF_ONBOARDING])
            except ValueError as err:
                _LOGGER.debug("%s ONBOARDING_INVALID error=%s", FLOW_TAG, err)
                errors["base"] = "invalid_onboarding"
            else:
                await self.async_set_unique_id(onboarding.mac_address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"EnOcean switch {onboarding.mac_address}",
                    data={
                        CONF_MAC_ADDRESS: onboarding.mac_address,
                        CONF_SECURITY_KEY: onboarding.security_key_hex,
                    },
                )

        return self.async_show_form(
            step_id="user",
            errors=errors,
            # Home Assistant aliases voluptuous to probatio at runtime; mypy sees the real voluptuous.
            data_schema=vol.Schema(  # type: ignore[arg-type]
                {
                    vol.Required(CONF_ONBOARDING): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
                    )
                }
            ),
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery."""
        mac = discovery_info.address.upper()
        _LOGGER.debug(
            "%s DISCOVERED mac=%s name=%s source=%s rssi=%s",
            FLOW_TAG,
            mac,
            discovery_info.name,
            discovery_info.source,
            discovery_info.rssi,
        )
        if not mac.startswith(ENOCEAN_MAC_PREFIX):
            return self.async_abort(reason="not_enocean_prefix")

        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()

        self._mac = mac
        self._title = discovery_info.name or mac
        self.context["title_placeholders"] = {"name": self._title}

        # The switch may already be in commissioning mode; then discovery itself carried the key.
        if _is_commissioning_telegram(discovery_info):
            try:
                self._commissioning = _parse_commissioning(
                    discovery_info.manufacturer_data[ENOCEAN_MANUFACTURER_ID], mac
                )
            except ValueError:
                pass

        return await self.async_step_commissioning()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-commission an existing switch, e.g. after a factory reset."""
        entry = self._get_reconfigure_entry()
        self._mac = entry.data[CONF_MAC_ADDRESS]
        self._title = entry.title
        self._commissioning = None
        return await self.async_step_commissioning()

    async def async_step_commissioning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for the commissioning telegram while showing the button sequence."""
        if self._mac is None:
            return self.async_abort(reason="no_bluetooth_discovery")

        if self._task is None:
            if self._security_key is not None:
                # Discovery itself carried the commissioning telegram.
                return await self.async_step_bluetooth_confirm()
            if self._error is not None and user_input is None:
                # Previous attempt failed: show the instructions again with the error.
                error, self._error = self._error, None
                return self.async_show_form(
                    step_id="commissioning",
                    errors={"base": error},
                    description_placeholders={"name": self._title or self._mac},
                )
            self._task = self.hass.async_create_task(self._async_wait_for_commissioning())

        if not self._task.done():
            return self.async_show_progress(
                step_id="commissioning",
                progress_action="waiting_commissioning",
                progress_task=self._task,
                description_placeholders={"name": self._title or self._mac},
            )

        task, self._task = self._task, None
        try:
            task.result()
        except TimeoutError:
            _LOGGER.debug("%s COMMISSIONING_TIMEOUT mac=%s", FLOW_TAG, self._mac)
            self._error = "commissioning_not_detected"
        except ValueError as err:
            _LOGGER.debug("%s COMMISSIONING_INVALID mac=%s error=%s", FLOW_TAG, self._mac, err)
            self._error = "invalid_commissioning"

        if self._error is not None:
            return self.async_show_progress_done(next_step_id="commissioning")
        return self.async_show_progress_done(next_step_id="bluetooth_confirm")

    async def _async_wait_for_commissioning(self) -> None:
        """Wait for the next commissioning telegram of this switch and store its key."""
        assert self._mac is not None
        service_info = await async_process_advertisements(
            self.hass,
            _is_commissioning_telegram,
            {
                "address": self._mac,
                "manufacturer_id": ENOCEAN_MANUFACTURER_ID,
                "connectable": False,
            },
            BluetoothScanningMode.PASSIVE,
            COMMISSIONING_TIMEOUT,
        )
        self._commissioning = _parse_commissioning(
            service_info.manufacturer_data[ENOCEAN_MANUFACTURER_ID], self._mac
        )
        _LOGGER.debug("%s COMMISSIONING_READY mac=%s", FLOW_TAG, self._mac)

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to leave commissioning mode and confirm."""
        if self._mac is None or self._commissioning is None:
            return self.async_abort(reason="no_bluetooth_discovery")

        if user_input is None:
            self._set_confirm_only()
            return self.async_show_form(
                step_id="bluetooth_confirm",
                description_placeholders={"name": self._title or self._mac, "mac": self._mac},
            )

        key = self._commissioning.security_key_hex
        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            # The switch starts a new counter sequence with the new key. Reset the running
            # state too: unload persists it, so a reload would otherwise restore the old counter.
            data: EnOceanBleData | None = getattr(entry, "runtime_data", None)
            if data is not None:
                data.security_key = key
                async_reset_sequence(data, self._commissioning.sequence_counter)
            return self.async_update_reload_and_abort(entry, data_updates={CONF_SECURITY_KEY: key})

        return self.async_create_entry(
            title=self._title or self._mac,
            data={CONF_MAC_ADDRESS: self._mac, CONF_SECURITY_KEY: key},
        )


class EnOceanBleOptionsFlow(config_entries.OptionsFlowWithReload):
    """Handle EnOcean BLE options; the entry is reloaded when they change."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_STRICT_REPLAY_PROTECTION, DEFAULT_STRICT_REPLAY_PROTECTION
        )
        return self.async_show_form(
            step_id="init",
            # Home Assistant aliases voluptuous to probatio at runtime; mypy sees the real voluptuous.
            data_schema=vol.Schema(  # type: ignore[arg-type]
                {vol.Required(CONF_STRICT_REPLAY_PROTECTION, default=current): BooleanSelector()}
            ),
        )


def _is_commissioning_telegram(service_info: BluetoothServiceInfoBleak) -> bool:
    payload = service_info.manufacturer_data.get(ENOCEAN_MANUFACTURER_ID)
    return payload is not None and len(payload) == COMMISSIONING_TELEGRAM_LENGTH


def _parse_commissioning(payload: bytes, mac: str) -> ParsedCommissioningTelegram:
    """Parse a commissioning telegram; its source address must match the switch."""
    commissioning = parse_commissioning_telegram(payload)
    source_hex = commissioning.static_source_address_hex
    # The source address is transmitted little-endian; accept both orders.
    candidates = {_format_mac(source_hex), _format_mac(_reverse_mac_hex(source_hex))}
    if mac not in candidates:
        _LOGGER.debug(
            "%s COMMISSIONING_MAC_MISMATCH mac=%s payload_mac=%s", FLOW_TAG, mac, candidates
        )
        raise ValueError("commissioning_static_source_mismatch")
    return commissioning


def _format_mac(hex6: str) -> str:
    return ":".join(hex6[i : i + 2].upper() for i in range(0, 12, 2))


def _reverse_mac_hex(hex6: str) -> str:
    pairs = [hex6[i : i + 2] for i in range(0, 12, 2)]
    return "".join(reversed(pairs))
