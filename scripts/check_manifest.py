"""Basic manifest sanity checks for custom integration structure."""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST_PATH = Path("custom_components/enocean_ble/manifest.json")
HACS_PATH = Path("hacs.json")
BRAND_ICON_PATH = Path("custom_components/enocean_ble/brand/icon.png")
REPOSITORY = "https://github.com/lafriks/enocean_ble"
REQUIRED_KEYS = {
    "domain",
    "name",
    "version",
    "documentation",
    "issue_tracker",
    "codeowners",
    "config_flow",
    "iot_class",
}


def main() -> int:
    if not MANIFEST_PATH.exists():
        print(f"Manifest not found: {MANIFEST_PATH}")
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED_KEYS - manifest.keys())
    if missing:
        print(f"Manifest missing required keys: {', '.join(missing)}")
        return 1

    if manifest["domain"] != "enocean_ble":
        print("Manifest domain must be enocean_ble")
        return 1

    for key in ("documentation", "issue_tracker"):
        if not str(manifest[key]).startswith(REPOSITORY):
            print(f"Manifest {key} must point to {REPOSITORY}")
            return 1
    if not manifest["codeowners"]:
        print("Manifest codeowners must not be empty")
        return 1
    categories = manifest.get("categories", [])
    if not 1 <= len(categories) <= 3:
        print("Manifest categories must list 1 to 3 integration categories")
        return 1

    if not HACS_PATH.exists():
        print(f"HACS manifest not found: {HACS_PATH}")
        return 1
    hacs = json.loads(HACS_PATH.read_text(encoding="utf-8"))
    if hacs.get("name") != manifest["name"]:
        print("hacs.json name must match manifest name")
        return 1

    if not BRAND_ICON_PATH.exists():
        print(f"Brand icon missing: {BRAND_ICON_PATH}")
        return 1

    print("Manifest sanity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
