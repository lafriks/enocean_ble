# EnOcean BLE for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![CI](https://github.com/lafriks/enocean_ble/actions/workflows/ci.yml/badge.svg)](https://github.com/lafriks/enocean_ble/actions/workflows/ci.yml)
[![Hassfest](https://github.com/lafriks/enocean_ble/actions/workflows/hassfest.yml/badge.svg)](https://github.com/lafriks/enocean_ble/actions/workflows/hassfest.yml)
[![HACS validation](https://github.com/lafriks/enocean_ble/actions/workflows/hacs.yml/badge.svg)](https://github.com/lafriks/enocean_ble/actions/workflows/hacs.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Home Assistant integration for EnOcean BLE energy-harvesting wall switches, the battery-free kinetic switches
built on the EnOcean PTM 215B / PTM 216B modules.

- Bluetooth discovery and guided commissioning, no keys to type in
- Authenticated telegrams (AES-128 MIC verification, replay protection)
- One `event` entity per button, one `press` event per click

## Installation

### Home Assistant Marketplace (2026.10 or newer)
1. Settings -> Devices & services -> `Marketplace` -> `Add from link`
2. Enter `lafriks/enocean_ble` and click `Add`
3. Install `EnOcean BLE`
4. Restart Home Assistant

### HACS
[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=lafriks&repository=enocean_ble&category=integration)

1. HACS -> `...` -> `Custom repositories`
2. Add `https://github.com/lafriks/enocean_ble`, type: `Integration`
3. Install `EnOcean BLE`
4. Restart Home Assistant

### Manual
1. Copy [`custom_components/enocean_ble`](custom_components/enocean_ble) to your HA `custom_components` directory
2. Restart Home Assistant
3. Settings -> Devices & services -> `Add Integration` -> `EnOcean BLE`

## Add Your Device

1. Press any button on the switch - it will appear as a discovered device in Home Assistant.
2. Click `Add` when it appears.
3. Follow the sequence on button 1:
   - hold for about 7 seconds,
   - press briefly,
   - hold again for about 7 seconds.
4. When the confirmation screen appears, press another button (not button 1) to exit commissioning mode.
5. Click `Submit`.

If the switch is already in commissioning mode, the dialog can complete almost immediately.

### Without the button sequence

Settings -> Devices & services -> `Add Integration` -> `EnOcean BLE` and paste the text of the QR code printed on
the switch module (under the rocker), e.g. `30SE2150000ABCD+Z0123...+30PPTM215B+...`; the address and the
32-digit key in any other form work too. Useful when the switch is hard to reach or its radio commissioning was
disabled by another system.

The key on the label is the factory key. If the switch was used in another system (Casambi, ABB, Niko, ...), that
system may have written a different key into the module over NFC; then the label is out of date and
commissioning by button fails. In that case read the current address and key with the phone against the
module using EnOcean's NFC app ([EnOcean Tool](https://www.enocean.com/en/products/enocean-tool/)) and paste
them into the `Add Integration` form described above.

## Options

Settings -> Devices & services -> EnOcean BLE -> Configure:

- **Strict replay protection** (off by default). The integration rejects telegrams it has already seen by
  remembering the switch's counter; by default that counter is written to disk a few seconds after a click.
  With this option it is written after every click, so recorded radio traffic cannot be replayed even
  after a crash or power loss in those few seconds. Turn it on if the switch controls a lock, door or
  similar; it costs a small disk write per click.

## Events

The integration creates one `event` entity per rocker contact: `A0`, `A1`, `B0`, `B1`.

Each physical click fires exactly one `press` event on that entity, no matter how long the button is held.
If the press telegram is lost over the air, the event is fired from the release telegram instead, so a click
is only missed when both telegrams are lost.

Event data:
- `mac_address`
- `rssi`
- `sequence_counter`

Two diagnostic sensors per switch, **Signal strength** (dBm) and **Last seen**, are created disabled; enable
them on the device page if you want to check reception.

## Usage Examples

### In the automation editor

Either add a **Device** trigger, pick the switch and choose e.g. *Button A0 pressed*, or add an
**Entity → Event received** trigger on `event.<switch>_a0`. Both fire exactly once per click and ignore
Home Assistant restarts.

### Toggle a light

```yaml
alias: EnOcean A0 - Toggle light
triggers:
  - trigger: device
    domain: enocean_ble
    device_id: 0123456789abcdef0123456789abcdef   # pick the switch in the editor
    type: press
    subtype: A0
actions:
  - action: light.toggle
    target:
      entity_id: light.living_room
```

### On with one button, off with the other

```yaml
alias: EnOcean A - Living room light
triggers:
  - trigger: event.received
    target:
      entity_id: event.living_room_switch_a0
    id: "on"
  - trigger: event.received
    target:
      entity_id: event.living_room_switch_a1
    id: "off"
actions:
  - action: "light.turn_{{ trigger.id }}"
    target:
      entity_id: light.living_room
```

On Home Assistant versions without the `event.received` trigger, use a `state` trigger on the event entity
instead and add a template condition that `trigger.from_state.state` and `trigger.to_state.state` are not
`unavailable` or `unknown` (a restart also changes the state).

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) and [docs/DEBUGGING.md](docs/DEBUGGING.md).

## Re-commissioning

If a switch was factory-reset or paired with another system, its telegrams can no longer be authenticated and
a repair issue appears. Open the device page, choose **Reconfigure** from the three-dot menu and repeat the
commissioning sequence; entities and automations are kept.

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) and [docs/DEBUGGING.md](docs/DEBUGGING.md).

## Factory Reset

A switch that was previously used in another system (Casambi, ABB free@home, Niko, ...) may have commissioning
disabled or a custom key set via NFC. If commissioning never completes, reset the switch module:

1. Remove the rocker and housing to reach the four module contacts.
2. Press `A0 + A1 + B0 + B1` together.
3. While holding them, press the energy bow and keep it pressed for at least 10 seconds.
4. Release and add the device again.

![PTM 21xB contact map and energy bow](assets/docs/ptm21xb_contacts_readme.png)

This is fiddly: hold the four contacts with one hand (or a non-conductive tool) and press the energy bow with
the other.

> [!WARNING]
> A factory reset restores the EnOcean default settings. Re-joining the original system afterwards may require
> re-provisioning, and in some OEM setups may not be possible without the manufacturer's tools.

## Supported Devices

Any wall switch built on the EnOcean **PTM 215B** or **PTM 216B** BLE module should work. Tested with:

- Feller EDIZIOdue BLE switch
- JUNG BLE LS 995 SWM

Not supported: EnOcean 868/902 MHz switches (PTM 210/215) and Zigbee Green Power switches (PTM 215Z/216Z,
"Friends of Hue"). They look the same but use a different radio.

Known PTM 215B/216B products:

| Manufacturer | Model | Module | Documentation |
|---|---|---|---|
| EnOcean (Germany) | EWSSB - Easyfit Single Rocker Wall Switch, EU 55 mm | PTM 215B (older rev) / PTM 216B (rev DD) | [EWSxB Datasheet (PDF)](https://www.enocean.com/wp-content/uploads/downloads-produkte/en/products/enocean_modules_24ghz_ble/easyfit-single-double-rocker-wall-switch-for-ble-ewssb-ewsdb/data-sheet-pdf/EWSxB_Datasheet.pdf) · [User Manual rev DD (PDF)](https://www.enocean.com/wp-content/uploads/downloads-produkte/en/products/enocean_modules_24ghz_ble/easyfit-single-double-rocker-wall-switch-for-ble-ewssb-ewsdb/user-manual-pdf/EWSxB_DD_User_Manual.pdf) |
| EnOcean (Germany) | EWSDB - Easyfit Double Rocker Wall Switch, EU 55 mm | PTM 215B (older rev) / PTM 216B (rev DD) | Same as EWSSB above |
| EnOcean (Germany) | ESRPB - Easyfit Single Rocker Pad, US Decora style | PTM 216B | [Product page](https://www.enocean.com/en/product/easyfit-single-double-rocker-pad-for-ble-esrpb-edrpb/) |
| EnOcean (Germany) | EDRPB - Easyfit Double Rocker Pad, US Decora style | PTM 216B | Same as ESRPB above |
| Feller (Switzerland) | 4122.1.S.F - EDIZIOdue BLE Funktaster, single rocker, 2-channel | PTM 215B | [Online catalog](https://online-katalog.feller.ch/kat_details.php?fnr=4122.1.S.F.67) |
| Feller (Switzerland) | 4122.2.S.F - EDIZIOdue BLE Funktaster, double rocker, 4-channel | PTM 215B | [Online catalog](https://online-katalog.feller.ch/kat_details.php?fnr=4122.2.S.FMI.67) |
| Eltako (Germany) | FTE215BLE - Wireless pushbutton insert, BLE | PTM 215B | [Datasheet (PDF)](https://www.eltako.com/fileadmin/downloads/en/_datasheets/Datasheet_FTE.pdf) · [Product page](https://www.eltako.com/en/product/professional-standard-en/accessories-professional-standard/fte215ble/) |
| Vimar (Italy) | 03925 - Bluetooth Low Energy RF 4-button device | PTM 215B | [Installer manual (PDF)](https://www.vimar.com/irj/go/km/docs/z_catalogo/DOCUMENT/03925IEN.80268.pdf) · [Product page](https://www.vimar.com/en/int/catalog/product/index/code/03925) |
| Hytronik (China) | HBES01 - Wireless BLE kinetic wall switch, EU 55 mm | PTM 215B | [Datasheet (PDF)](https://hytronik.com/system-level-components/switch-enocean-hbes01-b/switch-enocean-hbes01-b.pdf) · [Product page](https://hytronik.com/product/switch-enocean-hbes01-b) |
| AIMOTION (Germany) | Switch 55 (1051xx / 1052xx) - Casambi BLE wall switch | PTM 216B | [Datasheet (PDF)](https://casambi-aimotion.de/wp-content/uploads/2025/03/AIMOTION_1051_1052_Casambi_EnOcean_Switch_55_v.3.7.pdf) · [Product page](https://casambi-aimotion.de/en/produkt/switch-55-white/) |
| Niko (Belgium) | Dimmer switch, Bluetooth® - wireless dimmer rocker | PTM 216B | [Niko product page](https://www.niko.eu/en/products/wireless-controls/niko-dimmer-switch-enocean-productmodel-niko-785fb59a-c90e-5349-a30b-35fc36009b20) · [EnOcean partner page](https://www.enocean.com/en/batteryfree/niko-dimmer-switch-bluetooth/) |
| Kopp (Germany) | Blue-control Wandschaltermodul (867001011) - BT Mesh wall switch | PTM 215B | [Product page](https://produkte.kopp.eu/de/produkt/blue-control-energieautarkes-bluetooth-wandschaltermodul-mit-montagerahmen-2/) |
| Busch-Jaeger / ABB (Germany) | 6716 UBT - BLE Smart Switch insert | PTM 215B (all specs match; released 2022, before PTM 216B existed) | [Product page](https://www.busch-jaeger.de/en/online-catalogue/detail/2CKA006710A0015) · [Product manual (PDF)](https://www.busch-jaeger.de/files/files_ONLINE/BLE_Smart%20Switch_BJE_DE_18.08.2022.pdf) |
| Häfele (Germany) | 850.00.025 - BLE Single Rocker Wall Switch, US Decora | PTM 215B (FCC filing references PTM 215B user manual) | [Product page](https://www.hafele.com/us/en/product/wall-switch-ble-single-rocker-white-usa/85000025/) |
| Häfele (Germany) | 850.00.940 / 850.00.944 - Battery-Free Wireless Wall Switch, US Decora (replaces 850.00.025/026) | PTM 215B or PTM 216B | [Product page (double)](https://www.hafele.com/us/en/product/double-rocker-kinetic-wall-switch-bluetooth-battery-free-wireless-connect-mesh/P-01956566/) |
| Retrotouch (UK) | Crystal EnOcean Smart Switch (02623 BLE variant) - glass rocker, 86 mm | PTM 215B or PTM 216B (EnOcean-certified partner) | [Manufacturer page](https://www.retrotouch.co.uk/enocean-wireless-kinetic-switches.html) · [EnOcean partner page](https://www.enocean.com/en/battery-free-products/retrotouch/) |
| JUNG (Germany) | BLE A 590/595, BLE LS 990/995 (e.g. BLE LS 995 SWM), BLE 2990-L/2995-L - Bluetooth Low Energy radio transmitter, 2/4-channel | PTM 216B (not named by JUNG; 4 dBm transmit power and reset procedure match PTM 216B) | [Product page](https://www.jung-group.com/en-UK/p/Bluetooth-Low-Energy-radio-transmitter-4-channel-matt-graphite-black/BLE-LS-995-SWM) · [Datasheet (PDF)](https://application.jung.de/downloads/download/70338/en_GB) · [Operating instructions (PDF)](https://application.jung.de/downloads/download/62063/en) |
| Tunto (Finland) | Wireless Tunto Switch - designer BLE rocker | PTM 215B (transmit power 0.4 dBm matches PTM 215B spec exactly) | [Product page](https://www.tunto.com/product-page/enocean-switch) · [Casambi listing](https://casambi.com/ecosystem/tunto-enocean-switch/) |

The first 12 rows name the module in the manufacturer's documentation; the others are identified by their
specifications (BLE 2.4 GHz energy harvesting with NFC and AES-128 is unique to these modules).

## Development

Requires Python 3.14.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_dev.txt
pre-commit install
pytest
```

## References

- [PTM-215B User Manual](https://www.enocean.com/wp-content/uploads/downloads-produkte/en/products/enocean_modules_24ghz_ble/ptm-215b/user-manual-pdf/PTM-215B-User-Manual.pdf)
- [PTM-216B User Manual](https://www.enocean.com/wp-content/uploads/downloads-produkte/en/products/enocean_modules_24ghz_ble/ptm-216b/user-manual-pdf/PTM-216B-User-Manual-3.pdf)
- [Troubleshooting](docs/TROUBLESHOOTING.md) and [Debugging](docs/DEBUGGING.md)

## Credits

This integration started as a fork of [Superkikim/enocean-ble](https://github.com/Superkikim/enocean-ble)
by Akim Sissaoui. It is published separately under the same MIT license.
