"""Parsers for EnOcean BLE onboarding blobs and telegram frames."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .const import (
    BUTTON_BIT_TO_NAME,
    COMMISSIONING_TELEGRAM_LENGTH,
    ENOCEAN_MAC_PREFIX,
    MIC_LENGTH,
    STATUS_BIT_ENERGY_BOW,
    TELEGRAM_PRESS,
    TELEGRAM_RELEASE,
)
from .crypto import verify_mic

# Hex runs delimited by non-hex characters. The QR label of a PTM 215B/216B reads
# "30S<12 hex address>+Z<32 hex key>+30P<product>+2P<step>+S<serial>", so "Z" and "S"
# are the only separators there; word boundaries would not work.
_HEX = "[0-9A-Fa-f]"
MAC_RE = re.compile(rf"({_HEX}{{2}}(?:[:\-]{_HEX}{{2}}){{5}})")
BARE_MAC_RE = re.compile(rf"(?<!{_HEX})({_HEX}{{12}})(?!{_HEX})")
KEY_RE = re.compile(rf"(?<!{_HEX})({_HEX}{{32}})(?!{_HEX})")


@dataclass(slots=True, frozen=True)
class OnboardingData:
    """Normalized onboarding information extracted from NFC/QR payload."""

    mac_address: str
    security_key_hex: str


@dataclass(slots=True, frozen=True)
class ParsedTelegram:
    """Parsed EnOcean BLE data telegram."""

    sequence_counter: int
    buttons: tuple[str, ...]
    event_type: str


@dataclass(slots=True, frozen=True)
class ParsedCommissioningTelegram:
    """Parsed commissioning telegram payload (manufacturer data only)."""

    sequence_counter: int
    security_key_hex: str
    static_source_address_hex: str


def parse_onboarding_blob(raw: str) -> OnboardingData:
    """Extract MAC and AES-128 key from the QR/NFC label text or a free-form "MAC key" string.

    Accepts the address as "E2:15:..", "E2-15-.." or 12 bare hex digits (the QR "30S" field),
    and the key as 32 hex digits anywhere in the text (the QR "Z" field).
    """
    key_match = KEY_RE.search(raw)
    if key_match is None:
        raise ValueError("No 128-bit security key found in onboarding payload")
    # Remove the key before looking for the address, so no slice of it is taken as one.
    rest = raw[: key_match.start()] + " " + raw[key_match.end() :]

    if mac_match := MAC_RE.search(rest):
        mac_hex = mac_match.group(1).replace(":", "").replace("-", "")
    elif bare := BARE_MAC_RE.search(rest):
        mac_hex = bare.group(1)
    else:
        raise ValueError("No MAC address found in onboarding payload")

    mac_address = ":".join(mac_hex[i : i + 2].upper() for i in range(0, 12, 2))
    if not mac_address.startswith(ENOCEAN_MAC_PREFIX):
        raise ValueError("MAC prefix is not EnOcean E2:15")

    return OnboardingData(mac_address=mac_address, security_key_hex=key_match.group(1).lower())


def parse_data_telegram(
    telegram: bytes,
    *,
    mac_address: str,
    security_key_hex: str,
) -> ParsedTelegram:
    """Parse and verify an EnOcean BLE telegram with AES-128 MIC.

    Telegram layout:
    - sequence counter: 4 bytes LE
    - payload: >=1 byte (switch status + optional bytes)
    - MIC: 4 bytes
    """
    if len(telegram) < 5 + MIC_LENGTH:
        raise ValueError("EnOcean BLE telegram too short")

    sequence_counter = int.from_bytes(telegram[0:4], byteorder="little")
    payload = telegram[4:-MIC_LENGTH]
    mic = telegram[-MIC_LENGTH:]

    if not payload:
        raise ValueError("EnOcean BLE telegram payload missing")

    key = bytes.fromhex(security_key_hex)
    # Source address in nonce must be little-endian as specified by PTM 215B/PTM 216B manuals.
    mac = bytes.fromhex(mac_address.replace(":", ""))[::-1]

    if not verify_mic(
        security_key=key,
        mac_address_bytes=mac,
        sequence_counter=sequence_counter,
        payload=payload,
        mic=mic,
    ):
        raise ValueError("MIC verification failed")

    status = payload[0]
    button_names = _extract_active_buttons(status)
    event_type = _extract_event_type(status)

    return ParsedTelegram(
        sequence_counter=sequence_counter,
        buttons=button_names,
        event_type=event_type,
    )


def _extract_active_buttons(status: int) -> tuple[str, ...]:
    return tuple(name for bit, name in BUTTON_BIT_TO_NAME.items() if status & bit)


def _extract_event_type(status: int) -> str:
    # Energy Bar bit encodes push/release action. Long events are derived at runtime.
    return TELEGRAM_PRESS if (status & STATUS_BIT_ENERGY_BOW) else TELEGRAM_RELEASE


def parse_commissioning_telegram(telegram: bytes) -> ParsedCommissioningTelegram:
    """Parse commissioning telegram payload from manufacturer data."""
    if len(telegram) != COMMISSIONING_TELEGRAM_LENGTH:
        raise ValueError("EnOcean commissioning telegram must be exactly 26 bytes")

    sequence_counter = int.from_bytes(telegram[0:4], byteorder="little")
    return ParsedCommissioningTelegram(
        sequence_counter=sequence_counter,
        security_key_hex=telegram[4:20].hex(),
        static_source_address_hex=telegram[20:26].hex(),
    )
