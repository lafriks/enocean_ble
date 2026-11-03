# Troubleshooting

If none of this helps, enable debug logging (see [`DEBUGGING.md`](DEBUGGING.md)) and open an issue with the
log lines and your switch model.

## The switch is not discovered

- Press any button on the switch first. It only transmits when a button is pressed, so Home Assistant cannot
  see it before that.
- Check that a Bluetooth adapter or Bluetooth proxy is in range (Settings -> Devices & services -> Bluetooth).
- Make sure the switch is a BLE (2.4 GHz) model. EnOcean 868/902 MHz and Zigbee Green Power switches use
  different radios and are not supported.

## Device is discovered but commissioning does not complete

- Keep the switch close to the Bluetooth receiver while commissioning.
- Follow the exact button sequence shown in the dialog: hold button 1 for 7 s, press it briefly, hold again for 7 s.
- When the confirmation screen appears, press another button (not button 1), then click Submit.
- If it times out, just try again.
- If it keeps failing, the switch may have commissioning disabled by a previous system (Casambi, ABB, Niko...).
  See "Migration / Factory Reset" in the main README.

## Device gets recreated immediately after deletion

- The switch is still in commissioning mode and keeps announcing itself.
- Press any button other than button 1 to leave commissioning mode, then delete the device again.

## Automations do not trigger

- Use a state trigger on `event.<device>_<button>`; every state change is one click.
- Automations from older versions that used the `enocean_ble_button_event` bus event or the
  `sensor.<device>_<button>_event` entities no longer trigger: those were removed. Change them to state
  triggers on the event entities (see the examples in the main README).
- Open the event entity and watch its state while clicking; it should change on every click.

## Automation fires on Home Assistant restart

- Event entities briefly become `unavailable` during a restart or reload, which is also a state change.
- Add a condition that `trigger.from_state.state` and `trigger.to_state.state` are not `unavailable` or
  `unknown`, as in the examples in the main README.

## Sensor entities disappeared after an update

- The `... event` sensors and the long-press/release event types of older versions were removed. Each button
  now has one event entity that fires one `press` event per click. Update your automations accordingly.

## Switch stops responding after a factory reset

- A factory reset (or pairing the switch with another system) gives the switch a new security key, so its
  telegrams can no longer be authenticated. After a few such telegrams a repair issue appears under
  Settings -> System -> Repairs.
- Open the device page, choose **Reconfigure** from the three-dot menu and repeat the commissioning
  sequence. The device, its entities and your automations are kept.

## Some clicks are missed

- BLE on 2.4 GHz can lose packets. A click is recovered when only its press telegram was lost; if both
  press and release are lost nothing can recover it.
- Move the Bluetooth adapter away from USB 3 ports and metal cases, use a short USB extension cable, or add a
  Bluetooth proxy closer to the switch.
- If you use an ESPHome Bluetooth proxy, let it listen continuously:

  ```yaml
  esp32_ble_tracker:
    scan_parameters:
      interval: 320ms
      window: 320ms
      active: true
  ```

- See [`DEBUGGING.md`](DEBUGGING.md) to measure how many presses are lost.
