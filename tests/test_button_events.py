"""Tests for the press/release pairing in __init__.py."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from custom_components.enocean_ble import _emit_button_event

_MAC = "E2:15:00:03:C6:D7"


def _env() -> tuple[Any, Any, dict[str, Any], list[tuple[str, int]]]:
    """Return (hass, entry, entry_data, fired) where fired collects (button, sequence_counter)."""
    return MagicMock(), SimpleNamespace(entry_id="test"), {"last_press_seq": {}}, []


def _emit(
    env: tuple[Any, Any, dict[str, Any], list[tuple[str, int]]],
    event_type: str,
    seq: int,
    button: str = "A0",
) -> None:
    hass, entry, entry_data, fired = env
    with patch(
        "custom_components.enocean_ble._fire_event",
        side_effect=lambda **kw: fired.append((kw["button"], kw["sequence_counter"])),
    ):
        _emit_button_event(
            hass=hass,
            entry=entry,
            entry_data=entry_data,
            button=button,
            event_type=event_type,
            sequence_counter=seq,
            rssi=-70,
            mac_address=_MAC,
        )


def test_short_press_fires_once() -> None:
    env = _env()
    _emit(env, "press", 1)
    _emit(env, "release", 2)
    assert env[3] == [("A0", 1)]


def test_long_press_fires_once() -> None:
    """Hold duration does not matter: press and release still pair by sequence counter."""
    env = _env()
    _emit(env, "press", 10)
    _emit(env, "release", 11)
    assert env[3] == [("A0", 10)]


def test_lost_press_fires_on_release() -> None:
    env = _env()
    _emit(env, "press", 1)
    _emit(env, "release", 2)
    # Press at seq=3 lost
    _emit(env, "release", 4)
    assert env[3] == [("A0", 1), ("A0", 4)]


def test_lost_press_fires_after_other_buttons_and_gaps() -> None:
    env = _env()
    _emit(env, "press", 1)
    _emit(env, "release", 2)
    _emit(env, "press", 3, button="B0")
    _emit(env, "release", 4, button="B0")
    # A0 press at seq=8 lost, several telegrams before it lost too
    _emit(env, "release", 9)
    assert env[3] == [("A0", 1), ("B0", 3), ("A0", 9)]


def test_first_release_without_history_fires() -> None:
    env = _env()
    _emit(env, "release", 5)
    assert env[3] == [("A0", 5)]


def test_lost_release_does_not_block_next_press() -> None:
    env = _env()
    _emit(env, "press", 1)
    # Release at seq=2 lost
    _emit(env, "press", 3)
    _emit(env, "release", 4)
    assert env[3] == [("A0", 1), ("A0", 3)]


def test_multi_button_telegram_pairs_per_button() -> None:
    """A telegram with two active buttons is emitted once per button and pairs per button."""
    env = _env()
    _emit(env, "press", 1, button="A0")
    _emit(env, "press", 1, button="A1")
    _emit(env, "release", 2, button="A0")
    _emit(env, "release", 2, button="A1")
    assert env[3] == [("A0", 1), ("A1", 1)]
