# Debugging

## Enable debug logging

Settings -> Devices & services -> EnOcean BLE -> three-dot menu -> **Enable debug logging**.
When you disable it again, Home Assistant offers the collected log as a download.

Or permanently in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.enocean_ble: debug
```

Security keys are never written to the log; only a short fingerprint of the key is shown.

## Reading the log

### Device setup

```
setup_entry entry_id=... mac=E2:15:00:0A:E0:13 last_sequence_counter=526 ...
event_entity_attached ... button=A0 ...
EnOcean BLE device configured: E2:15:00:0A:E0:13
```

`last_sequence_counter` is the counter restored from the previous run (`-1` on a fresh install).

### A button click

Every telegram from the switch produces these lines:

```
adv_received ... adv_address=E2:15:00:0A:E0:13 rssi=-69 enocean_payload_len=9
telegram_parse_success ... buttons=('A1',) event_type=press sequence_counter=511
event_emit ... button=A1 sequence_counter=511
```

- `rssi`: signal strength. Values around -60 are good, below -85 are weak.
- `buttons`: which rocker contact was used (`A0`, `A1`, `B0`, `B1`).
- `event_type`: `press` or `release`, as sent by the switch.
- `sequence_counter`: increases by 1 with every telegram. A click uses two values (press, release).

Other lines you may see:

| Line | Meaning |
|---|---|
| `telegram_parse_failed ... error=MIC verification failed` | Wrong security key. Delete the device and commission it again. |
| `telegram_deduplicated` | The same telegram was received twice (several Bluetooth receivers). Harmless. |
| `telegram_ignored_no_key` | The device has no key yet; commissioning did not finish. |
| `commissioning_detected` | The switch is in commissioning mode. Press another button to leave it. |
| `adv_ignored ... reason=entry_unloaded` | A telegram arrived while the device was reloading. Harmless. |

### Press and release pairing

```
event_release_paired ... sequence_counter=512
```
The release of a click whose press was already handled. Nothing is fired. This is the normal case.

```
event_press_recovered ... sequence_counter=514 last_press_seq=511
```
A release arrived whose press telegram was never received. The click is fired from the release instead, so
nothing is lost, but it means the press was dropped by the radio.

## Measuring lost presses

Count both kinds of pairing lines over a test with a few dozen clicks:

```bash
ha core logs | grep -c event_press_recovered   # clicks whose press telegram was lost
ha core logs | grep -c event_release_paired    # clicks where both telegrams arrived
```

Ideally the first number is close to zero. If it is a large share of all clicks, the Bluetooth receiver is
missing packets. See "Some clicks are missed" in [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

## Which receiver hears the switch

Lines like `[ENOCEAN_FLOW] DISCOVERED ... source=24:D7:EB:B2:2A:9E` show the address of the Bluetooth
adapter or proxy that received the telegram. Compare it with the adapters listed under
Settings -> Devices & services -> Bluetooth to find out which device is in range of the switch.

## Commissioning

Commissioning lines are prefixed with `[ENOCEAN_FLOW]`:

| Line | Meaning |
|---|---|
| `DISCOVERED` | The switch was seen; the Add dialog appears. |
| `COMMISSIONING_READY` | The commissioning telegram was received and the key is stored. |
| `COMMISSIONING_TIMEOUT` | No commissioning telegram within 2 minutes. Repeat the button sequence. |
| `COMMISSIONING_MAC_MISMATCH` | A commissioning telegram from a different switch was received. Keep other switches out of commissioning mode. |
