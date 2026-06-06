"""Apple sysctl `hw.machine` identifier → marketing name.

iOS pings us with the raw code (e.g. "iPhone17,3") because that's a
one-line `sysctlbyname("hw.machine", ...)` call on their side. Doing
the marketing name translation server side means iOS never has to ship
an updated lookup table for a new device.

The list below covers iPhone 11 onwards and current iPad lineup. Older
or genuinely unknown codes return a tidied form of the raw code so the
dashboard still has something to show. Source of truth for codes:
https://gist.github.com/adamawolf/3048717 and Apple's developer docs.

Update cadence: append a row each fall when Apple ships new hardware.
Missing entries are non-fatal — they just show up under "Unknown
(iPhoneXX,Y)" until added.
"""

from __future__ import annotations

_MARKETING_NAMES: dict[str, str] = {
    # --- iPhone -----------------------------------------------------------
    # iPhone 11 family
    "iPhone12,1": "iPhone 11",
    "iPhone12,3": "iPhone 11 Pro",
    "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2nd gen)",
    # iPhone 12 family
    "iPhone13,1": "iPhone 12 mini",
    "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,4": "iPhone 12 Pro Max",
    # iPhone 13 family
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13",
    "iPhone14,6": "iPhone SE (3rd gen)",
    # iPhone 14 family
    "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",
    # iPhone 15 family
    "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    # iPhone 16 family
    "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus",
    "iPhone17,5": "iPhone 16e",
    # iPhone 17 family (anticipated)
    "iPhone18,1": "iPhone 17 Pro",
    "iPhone18,2": "iPhone 17 Pro Max",
    "iPhone18,3": "iPhone 17",
    "iPhone18,4": "iPhone 17 Plus",
    "iPhone18,5": "iPhone Air",

    # --- iPad -------------------------------------------------------------
    # iPad Pro 11-inch
    "iPad8,1": "iPad Pro 11\" (1st gen)",
    "iPad8,2": "iPad Pro 11\" (1st gen)",
    "iPad8,3": "iPad Pro 11\" (1st gen)",
    "iPad8,4": "iPad Pro 11\" (1st gen)",
    "iPad8,9": "iPad Pro 11\" (2nd gen)",
    "iPad8,10": "iPad Pro 11\" (2nd gen)",
    "iPad13,4": "iPad Pro 11\" (3rd gen)",
    "iPad13,5": "iPad Pro 11\" (3rd gen)",
    "iPad13,6": "iPad Pro 11\" (3rd gen)",
    "iPad13,7": "iPad Pro 11\" (3rd gen)",
    "iPad14,3": "iPad Pro 11\" (4th gen)",
    "iPad14,4": "iPad Pro 11\" (4th gen)",
    "iPad16,3": "iPad Pro 11\" (M4)",
    "iPad16,4": "iPad Pro 11\" (M4)",
    # iPad Pro 12.9 / 13-inch
    "iPad8,5": "iPad Pro 12.9\" (3rd gen)",
    "iPad8,6": "iPad Pro 12.9\" (3rd gen)",
    "iPad8,7": "iPad Pro 12.9\" (3rd gen)",
    "iPad8,8": "iPad Pro 12.9\" (3rd gen)",
    "iPad8,11": "iPad Pro 12.9\" (4th gen)",
    "iPad8,12": "iPad Pro 12.9\" (4th gen)",
    "iPad13,8": "iPad Pro 12.9\" (5th gen)",
    "iPad13,9": "iPad Pro 12.9\" (5th gen)",
    "iPad13,10": "iPad Pro 12.9\" (5th gen)",
    "iPad13,11": "iPad Pro 12.9\" (5th gen)",
    "iPad14,5": "iPad Pro 12.9\" (6th gen)",
    "iPad14,6": "iPad Pro 12.9\" (6th gen)",
    "iPad16,5": "iPad Pro 13\" (M4)",
    "iPad16,6": "iPad Pro 13\" (M4)",
    # iPad Air
    "iPad13,1": "iPad Air (4th gen)",
    "iPad13,2": "iPad Air (4th gen)",
    "iPad13,16": "iPad Air (5th gen)",
    "iPad13,17": "iPad Air (5th gen)",
    "iPad14,8": "iPad Air 11\" (M2)",
    "iPad14,9": "iPad Air 11\" (M2)",
    "iPad14,10": "iPad Air 13\" (M2)",
    "iPad14,11": "iPad Air 13\" (M2)",
    # iPad Air M4 (paired Wi-Fi / Cellular per Apple's convention)
    "iPad16,7": "iPad Air 11\" (M4)",
    "iPad16,8": "iPad Air 11\" (M4)",
    "iPad16,9": "iPad Air 13\" (M4)",
    "iPad16,10": "iPad Air 13\" (M4)",
    # iPad mini
    "iPad14,1": "iPad mini (6th gen)",
    "iPad14,2": "iPad mini (6th gen)",
    "iPad16,1": "iPad mini (A17 Pro)",
    "iPad16,2": "iPad mini (A17 Pro)",
    # iPad (base)
    "iPad11,6": "iPad (8th gen)",
    "iPad11,7": "iPad (8th gen)",
    "iPad12,1": "iPad (9th gen)",
    "iPad12,2": "iPad (9th gen)",
    "iPad13,18": "iPad (10th gen)",
    "iPad13,19": "iPad (10th gen)",
    "iPad15,7": "iPad (A16)",
    "iPad15,8": "iPad (A16)",

    # --- Simulators (dev) -------------------------------------------------
    "x86_64": "Simulator (Intel)",
    "arm64": "Simulator (Apple Silicon)",
    "i386": "Simulator (legacy)",
}


def to_marketing_name(raw: str | None) -> str | None:
    """Translate a sysctl hw.machine code to a human marketing name.

    Priority order:
    1. The synced map from app/services/device_models_sync (refreshed
       weekly from adamawolf's gist, the de facto community-maintained
       canonical source).
    2. The hand-curated static table above, which acts as a fallback
       during the brief window between deploy and first sync, and for
       edge cases the upstream gist hasn't picked up.
    3. `Unknown (<raw>)` string preserving the original code so an
       operator can grep, look it up, and add a row.

    None / empty input → None.
    """
    if not raw:
        return None
    # Local import keeps this module standalone for tests and avoids a
    # circular import if anything in sync grows to depend on us.
    try:
        from app.services import device_models_sync
        synced = device_models_sync.lookup(raw)
        if synced is not None:
            return synced
    except Exception:
        # Sync module not loaded yet (e.g., during early import in
        # test fixtures). Fall through to static table.
        pass
    name = _MARKETING_NAMES.get(raw)
    if name is not None:
        return name
    return f"Unknown ({raw})"
