# Troubleshooting

## Device is discovered but commissioning does not complete

- Keep the switch close to the HA Bluetooth receiver for commissioning.
- Follow the exact button sequence shown in the progress step.
- Wait for the confirm screen, press another button (not button 1), then submit.
- If timeout repeats, retry.
- If repeated failures persist, review reset guidance in `README.md` (factory reset disclaimer).

## Device gets recreated immediately after deletion

- This usually means the switch is still sending commissioning telegrams (`len=26`).
- Exit commissioning mode on the physical switch before deleting/re-adding.

## Automations do not trigger

- Check the device's event mode (Settings -> Devices & services -> EnOcean BLE Switches -> Configure).
- `simple` mode:
  - Use state triggers on `event.<device>_<button>`; every state change is one click.
  - Bus events and `sensor.<device>_<button>_event` entities do not exist in this mode; automations using them stop triggering.
- `advanced` mode:
  - Prefer state triggers on `sensor.<device>_<button>_event` for UX simplicity.
  - Or use event triggers on `enocean_ble_button_event` for full payload filtering.
  - Verify current event payload with Developer Tools -> Events.

## Automation runs several times per click

- In `advanced` mode the event entity changes state for every event type (`press`, `single_press`, `release`, ...).
- Switch the device to `simple` mode, or filter on the `event_type` attribute.

## Switch stops responding after a factory reset

- The integration rejects telegrams whose sequence counter is not higher than the last accepted one,
  and remembers that counter across restarts.
- If a reset restarts the switch's counter, its telegrams are ignored as replays.
- Delete the device in Home Assistant and add it again; removing the entry also deletes the stored counter.

## Sensor entities disappeared

- Switching a device to `simple` mode removes its event sensors. Switching back to `advanced` recreates them.

## Missing logs

- Enable debug for `custom_components.enocean_ble`.
- Check container logs for `FLOW_TRACE_V3` and runtime parser lines.

## About occasional missed actions

BLE on 2.4 GHz can drop packets in real environments. This is expected behavior and can happen on any passive BLE button setup.
