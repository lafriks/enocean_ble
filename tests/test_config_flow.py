"""Tests for the commissioning config flow."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.enocean_ble import EnOceanBleData
from custom_components.enocean_ble.config_flow import (
    EnOceanBleConfigFlow,
    _is_commissioning_telegram,
    _parse_commissioning,
)
from custom_components.enocean_ble.const import ENOCEAN_MANUFACTURER_ID

# Commissioning telegram: seq 0x171, key ab4b..., source address 9f1b000015e2 (little-endian)
_COMMISSIONING = bytes.fromhex("71010000AB4B9A91852B70B8A652A05E92BB12A09F1B000015E2")
_KEY = "ab4b9a91852b70b8a652a05e92bb12a0"
_MAC = "E2:15:00:00:1B:9F"


def _service_info(payload: bytes, address: str = _MAC) -> Any:
    return SimpleNamespace(
        address=address,
        name="EnOcean switch",
        source="hci0",
        rssi=-60,
        manufacturer_data={ENOCEAN_MANUFACTURER_ID: payload},
    )


def _task(flow: EnOceanBleConfigFlow) -> asyncio.Task[None]:
    assert flow._task is not None
    return flow._task


def _flow() -> EnOceanBleConfigFlow:
    flow = EnOceanBleConfigFlow()
    flow.hass = SimpleNamespace(async_create_task=lambda coro: asyncio.ensure_future(coro))  # type: ignore[assignment]
    flow.handler = "enocean_ble"
    flow.flow_id = "test-flow"
    flow.context = {"source": "bluetooth"}
    flow.async_set_unique_id = AsyncMock()  # type: ignore[method-assign]
    flow._abort_if_unique_id_configured = MagicMock()  # type: ignore[method-assign]
    return flow


def test_parse_commissioning_accepts_both_address_orders() -> None:
    assert _parse_commissioning(_COMMISSIONING, "E2:15:00:00:1B:9F").security_key_hex == _KEY
    assert _parse_commissioning(_COMMISSIONING, "9F:1B:00:00:15:E2").security_key_hex == _KEY


def test_parse_commissioning_rejects_other_switch() -> None:
    with pytest.raises(ValueError, match="mismatch"):
        _parse_commissioning(_COMMISSIONING, "E2:15:00:00:00:01")


def test_is_commissioning_telegram() -> None:
    assert _is_commissioning_telegram(_service_info(_COMMISSIONING))
    assert not _is_commissioning_telegram(_service_info(bytes(9)))
    no_enocean: Any = SimpleNamespace(manufacturer_data={})
    assert not _is_commissioning_telegram(no_enocean)


async def test_wrong_prefix_aborts() -> None:
    result = await _flow().async_step_bluetooth(_service_info(bytes(9), "AA:BB:CC:DD:EE:FF"))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_enocean_prefix"


async def test_user_step_aborts() -> None:
    result = await _flow().async_step_user()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_bluetooth_discovery"


async def test_discovery_with_commissioning_telegram_skips_waiting() -> None:
    flow = _flow()
    result = await flow.async_step_bluetooth(_service_info(_COMMISSIONING))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    result = await flow.async_step_bluetooth_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"mac_address": _MAC, "security_key": _KEY}


async def test_waits_for_commissioning_telegram_then_creates_entry() -> None:
    flow = _flow()
    with patch(
        "custom_components.enocean_ble.config_flow.async_process_advertisements",
        AsyncMock(return_value=_service_info(_COMMISSIONING)),
    ) as wait:
        result = await flow.async_step_bluetooth(_service_info(bytes(9)))
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["progress_action"] == "waiting_commissioning"
        await _task(flow)

    matcher = wait.call_args.args[2]
    assert matcher == {
        "address": _MAC,
        "manufacturer_id": ENOCEAN_MANUFACTURER_ID,
        "connectable": False,
    }

    result = await flow.async_step_commissioning()
    assert result["type"] is FlowResultType.SHOW_PROGRESS_DONE
    assert result["step_id"] == "bluetooth_confirm"

    result = await flow.async_step_bluetooth_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["security_key"] == _KEY


@pytest.mark.parametrize(
    ("exc", "error"),
    [(TimeoutError(), "commissioning_not_detected"), (ValueError("bad"), "invalid_commissioning")],
)
async def test_failure_shows_retry_form_then_waits_again(exc: Exception, error: str) -> None:
    flow = _flow()
    with patch(
        "custom_components.enocean_ble.config_flow.async_process_advertisements",
        AsyncMock(side_effect=exc),
    ):
        result = await flow.async_step_bluetooth(_service_info(bytes(9)))
        with pytest.raises(type(exc)):
            await _task(flow)

    result = await flow.async_step_commissioning()
    assert result["type"] is FlowResultType.SHOW_PROGRESS_DONE
    assert result["step_id"] == "commissioning"

    result = await flow.async_step_commissioning()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "commissioning"
    assert result["errors"] == {"base": error}

    # Submitting the form starts a new wait.
    with patch(
        "custom_components.enocean_ble.config_flow.async_process_advertisements",
        AsyncMock(return_value=_service_info(_COMMISSIONING)),
    ):
        result = await flow.async_step_commissioning({})
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await _task(flow)
    result = await flow.async_step_commissioning()
    assert result["step_id"] == "bluetooth_confirm"


async def test_remove_cancels_pending_wait() -> None:
    flow = _flow()
    never: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    with patch(
        "custom_components.enocean_ble.config_flow.async_process_advertisements",
        AsyncMock(side_effect=lambda *a, **k: never),
    ):
        await flow.async_step_bluetooth(_service_info(bytes(9)))
    task = _task(flow)
    flow.async_remove()
    await asyncio.sleep(0)
    assert task.cancelled()


async def test_reconfigure_replaces_key_and_resets_counter() -> None:
    data = EnOceanBleData(
        mac_address=_MAC,
        security_key="old",
        store=MagicMock(),
        save_delay=5.0,
        last_sequence_counter=900,
        last_press_seq={"A0": 899},
        auth_failures=3,
    )
    entry: Any = SimpleNamespace(
        entry_id="entry1", title="Bedroom", data={"mac_address": _MAC, "security_key": "old"}
    )
    entry.runtime_data = data
    flow = _flow()
    flow.context = {"source": "reconfigure", "entry_id": "entry1"}
    flow._get_reconfigure_entry = MagicMock(return_value=entry)  # type: ignore[method-assign]
    flow.async_update_reload_and_abort = MagicMock(  # type: ignore[method-assign]
        return_value={"type": FlowResultType.ABORT, "reason": "reconfigure_successful"}
    )

    with patch(
        "custom_components.enocean_ble.config_flow.async_process_advertisements",
        AsyncMock(return_value=_service_info(_COMMISSIONING)),
    ):
        result = await flow.async_step_reconfigure()
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await _task(flow)
    result = await flow.async_step_commissioning()
    assert result["step_id"] == "bluetooth_confirm"

    result = await flow.async_step_bluetooth_confirm({})

    assert result["reason"] == "reconfigure_successful"
    flow.async_update_reload_and_abort.assert_called_once_with(
        entry, data_updates={"security_key": _KEY}
    )
    assert data.security_key == _KEY
    assert data.last_sequence_counter == 0x171
    assert data.last_press_seq == {}
    assert data.auth_failures == 0
