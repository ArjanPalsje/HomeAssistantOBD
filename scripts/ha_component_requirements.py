"""Print the pip requirements of Home Assistant components we depend on.

pytest-homeassistant-custom-component installs Home Assistant core, but not the
requirements of individual integrations (bluetooth, usb, …). This script reads
the manifests of the installed Home Assistant package, follows their
dependencies recursively and prints every requirement, one per line:

    python scripts/ha_component_requirements.py > ha_reqs.txt
    pip install -r ha_reqs.txt
"""

from __future__ import annotations

import json
from pathlib import Path

import homeassistant.components as components

ROOT_DOMAINS = ["bluetooth_adapters", "bluetooth", "usb", "diagnostics"]


def main() -> None:
    base = Path(components.__file__).parent
    seen: set[str] = set()
    requirements: list[str] = []
    todo = list(ROOT_DOMAINS)
    while todo:
        domain = todo.pop()
        if domain in seen:
            continue
        seen.add(domain)
        manifest_file = base / domain / "manifest.json"
        if not manifest_file.exists():
            continue
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        requirements.extend(r for r in manifest.get("requirements", []) if r not in requirements)
        todo.extend(manifest.get("dependencies", []))
    print("\n".join(requirements))


if __name__ == "__main__":
    main()
