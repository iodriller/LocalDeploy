"""Post-process heat.exe's harvested files.wxs for a per-user MSI install.

Windows Installer requires every component that installs into the user
profile (LocalAppDataFolder here) to use an HKCU registry value as its
KeyPath, not a file (enforced by the ICE38/ICE64 validators) - MSI tracks
per-user component state through the registry, since one machine-wide MSI
package can be "installed" independently for several different Windows
accounts. heat.exe's default harvest makes each <File> its own KeyPath,
which is only valid for a per-machine (Program Files) install.

This walks the harvested XML and, for every <Component>, turns off the
File's KeyPath and adds a sibling <RegistryValue> KeyPath under
HKCU\\Software\\LocalDeploy\\Components\\<componentId> instead. The file
still gets installed exactly as heat.exe described; only which entry MSI
uses to detect "is this component present" changes.

A component with both a File and a RegistryValue no longer qualifies for
WiX's auto-generated Guid="*" (that shortcut only applies to single-resource
components), so this also assigns each component a real GUID - deterministic
(uuid5, derived from the component Id) rather than random, so rebuilding from
the same PyInstaller output reproduces the same GUIDs and upgrades behave
correctly instead of every rebuild looking like a full uninstall+reinstall.

Usage: python fix_perfile_keypaths.py <in.wxs> <out.wxs>
"""
from __future__ import annotations

import sys
import uuid
import xml.etree.ElementTree as ET

WIX_NS = "http://schemas.microsoft.com/wix/2006/wi"
ET.register_namespace("", WIX_NS)

# Fixed, arbitrary namespace UUID for this project's component-GUID derivation.
# Do not change - changing it would reassign every component a new GUID, which
# breaks in-place upgrades for anyone who already has an older MSI installed.
_GUID_NAMESPACE = uuid.UUID("6f1b3e2a-6b6a-4a3f-9d0a-2b6e7c9f4a11")


def _tag(name: str) -> str:
    return f"{{{WIX_NS}}}{name}"


def fix(in_path: str, out_path: str) -> int:
    tree = ET.parse(in_path)
    root = tree.getroot()
    fixed = 0
    for component in root.iter(_tag("Component")):
        component_id = component.get("Id")
        file_el = component.find(_tag("File"))
        if file_el is None or file_el.get("KeyPath") != "yes":
            continue
        file_el.set("KeyPath", "no")
        component.set("Guid", str(uuid.uuid5(_GUID_NAMESPACE, component_id)).upper())
        registry_value = ET.SubElement(component, _tag("RegistryValue"))
        registry_value.set("Root", "HKCU")
        registry_value.set("Key", f"Software\\LocalDeploy\\Components\\{component_id}")
        registry_value.set("Name", "Installed")
        registry_value.set("Type", "integer")
        registry_value.set("Value", "1")
        registry_value.set("KeyPath", "yes")
        # Keep the File element first for readability; SubElement already
        # appended the RegistryValue after it since File was already present.
        fixed += 1
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return fixed


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: fix_perfile_keypaths.py <in.wxs> <out.wxs>", file=sys.stderr)
        raise SystemExit(2)
    count = fix(sys.argv[1], sys.argv[2])
    print(f"Redirected {count} component KeyPath(s) to HKCU registry values.")
