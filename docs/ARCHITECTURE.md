# Architecture

## Runtime Components

- `config_flow.py`
- Bluetooth discovery entrypoint, commissioning progress stage (`len=26`), explicit confirm, config entry creation.
- Options flow with the `event_mode` setting (`advanced` / `simple`).
- `__init__.py`
- Passive BLE callback registration, telegram parsing, per-mode event emission, event bus emission, dispatcher signal fan-out.
- Reloads the entry when `event_mode` changes; in `simple` mode removes leftover sensor entities from the entity registry.
- `parser.py`
- Commissioning and runtime telegram parsing.
- `crypto.py`
- MIC/integrity validation utilities.
- `event.py`
- Four button event entities (`A0`, `A1`, `B0`, `B1`). Exposed event types depend on `event_mode`.
- `sensor.py`
- Four enum sensors mirroring the latest per-button event. Loaded in `advanced` mode only.
- `const.py`
- Domain constants, event names, protocol constants.

## Data Path

1. BLE advertisement is received by Home Assistant Bluetooth.
2. Integration filters by configured MAC and EnOcean manufacturer data.
3. Payload is parsed and MIC-verified; repeated telegrams are dropped by sequence counter.
4. Raw `press`/`release` is turned into events according to `event_mode` (see below).
5. Integration dispatches internal signal per entry.
6. In `advanced` mode, integration also fires bus events:
- `enocean_ble_button_event` (canonical)
- `enocean_ble_button_action` (legacy compatibility)
7. Event entities (and in `advanced` mode sensor entities) update from the same payload.

## Event Modes

The sequence counter is shared by all buttons of a device and increments with every telegram,
so a press and its release normally have consecutive counters.

### Advanced (`_emit_button_event`)

Per-button state machine with timers:

- `press` fires `press` + `single_press`, starts long-press (`1.2s`) and release-timeout (`8s`) timers.
- `release` after a known press fires `release` (+ `long_release` if `long_press` fired).
- `release` without a known press fires `orphan_release`; it also fires `single_press` if the gap to the
  device's previous telegram (any button) is exactly 2, i.e. only the press telegram was lost.

### Simple (`_emit_simple_button_event`)

No timers. Each button remembers the counter of its last seen press:

- `press` fires `press`.
- `release` with counter = last press + 1 is the paired release and is ignored.
- Any other `release` means the press telegram was lost, so it fires `press`.

Exactly one `press` event per physical click, regardless of how long the button was held.

## Commissioning Path

1. Discovery starts config flow.
2. Progress step waits for commissioning telegram (`len=26`, timeout `120s`).
3. Payload is validated and security key stored in flow state.
4. Confirm step waits for explicit `Submit`.
5. Entry is created with `mac_address` and `security_key`.

## Device Model in HA

Device registry metadata currently uses:

- `manufacturer = "EnOcean"`
- `model = "PTM 215B/PTM 216B"`

## Logging

- Config flow trace marker: `FLOW_TRACE_V3`.
- Flow logger prefix: `[ENOCEAN_FLOW]`.
- Runtime logs include parse/filter decisions and emitted event payload context.
