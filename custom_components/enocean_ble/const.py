"""Constants for EnOcean BLE integration."""

from __future__ import annotations

DOMAIN = "enocean_ble"
INTEGRATION_NAME = "PTM 215B/PTM 216B"
PLATFORMS = ["event"]

# Config entry version 1 (up to 2026.5) had per-button event sensors and an event_mode option.
CONFIG_ENTRY_VERSION = 2
LEGACY_PLATFORMS = ["sensor"]

CONF_MAC_ADDRESS = "mac_address"
CONF_SECURITY_KEY = "security_key"
# Option: write the replay counter to disk after every telegram instead of a few seconds later.
CONF_STRICT_REPLAY_PROTECTION = "strict_replay_protection"
DEFAULT_STRICT_REPLAY_PROTECTION = False

ENOCEAN_MANUFACTURER_ID = 0x03DA
ENOCEAN_MAC_PREFIX = "E2:15:"

# One signal per button, so only the matching entity receives an event.
SIGNAL_BUTTON_EVENT = "enocean_ble_button_event_{entry_id}_{button}"

ATTR_RSSI = "rssi"
ATTR_SEQUENCE_COUNTER = "sequence_counter"
ATTR_MAC_ADDRESS = "mac_address"

# Telegram actions as reported by the switch.
TELEGRAM_PRESS = "press"
TELEGRAM_RELEASE = "release"
# Each physical click fires exactly one event of this type.
EVENT_TYPE_PRESS = "press"

DATA_TELEGRAM_MIN_LENGTH = 9
COMMISSIONING_TELEGRAM_LENGTH = 26
MIC_LENGTH = 4

# Last accepted sequence counter is persisted so replay protection survives restarts.
STORAGE_VERSION = 1
STORAGE_SAVE_DELAY_SECONDS = 5.0
STORAGE_SAVE_DELAY_STRICT_SECONDS = 0.0

STATUS_BIT_ENERGY_BOW = 0x01
STATUS_BIT_A0 = 0x02
STATUS_BIT_A1 = 0x04
STATUS_BIT_B0 = 0x08
STATUS_BIT_B1 = 0x10

BUTTON_BIT_TO_NAME = {
    STATUS_BIT_A0: "A0",
    STATUS_BIT_A1: "A1",
    STATUS_BIT_B0: "B0",
    STATUS_BIT_B1: "B1",
}
BUTTONS = tuple(BUTTON_BIT_TO_NAME.values())
