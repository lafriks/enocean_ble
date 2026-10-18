# Roadmap

## Completed

- HACS validation and hassfest checks.
- Commissioning flow simplified to:
1. progress (`commissioning`)
2. confirm (`bluetooth_confirm`)
3. create entry
- Canonical bus event: `enocean_ble_button_event`.
- Legacy bus event compatibility kept: `enocean_ble_button_action`.
- Per-button event sensors added for UX-friendly state automations.
- Local branding assets integrated for HA 2026.3+ behavior.
- Simple event mode (options flow): one `press` event per click, no sensors, no bus events.
- Lost-press recovery checks against the device-wide sequence counter (advanced mode).
- Latvian translation.

## Next

- Persist the last sequence counter across restarts (replay protection currently resets on restart).
- Cancel pending advanced-mode timers on entry unload.
- Collect user feedback on simple mode.
- Optional: richer diagnostics tooling if needed by field feedback.
