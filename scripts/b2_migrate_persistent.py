"""B2 prod persistent-dir migration (#249): relocate the flat `tr-*` remote-config
files into the techrehearsal/ subdir (dropping the prefix), file-by-file, with
verify-before-delete. ShoulderSurf files stay flat (see target_subpath).

Run INSIDE the ghostpour container (the persistent dir lives on its data
volume). Mode is taken from the B2_MODE env var so it works over `docker exec`:

  # 1. DRY RUN (default) — show the planned moves, touch nothing:
  sudo docker exec -i ghostpour python3 - < scripts/b2_migrate_persistent.py

  # 2. POPULATE — copy each flat file to its subdir target if missing, verify
  #    it parses with a version. Leaves the flat source in place.
  sudo docker exec -e B2_MODE=populate -i ghostpour python3 - < scripts/b2_migrate_persistent.py
  sudo docker restart ghostpour      # reload load_remote_configs() with subdir slugs

  # 3. VERIFY (manual, between phases): fetch /v1/config/<name> with the app's
  #    X-App-ID for every moved file + locale; assert X-Config-Resolved is the
  #    subdir slug and the body matches the pre-move bytes. For SS also assert a
  #    NO-header fetch still returns the same bytes.

  # 4. CLEANUP — only after verify passes. Snapshot each flat source to
  #    .bak-<UTC> then delete it. Restart again.
  sudo docker exec -e B2_MODE=cleanup -i ghostpour python3 - < scripts/b2_migrate_persistent.py
  sudo docker restart ghostpour

DELIBERATELY two invocations (populate, then cleanup) — never a single
combined move+delete. Keep the .bak-* files until both apps confirm correct
config post-migration, then sweep them.
"""

import json
import os
import shutil
from datetime import datetime, timezone

from app.routers.config import CONFIG_DIR

MODE = os.environ.get("B2_MODE", "dryrun").lower()


def target_subpath(flat_name: str) -> str | None:
    """Map a flat filename to its per-app subdir path, or None if it stays flat.

    Mirrors the repo bundle layout: ONLY `tr-*` files move (→ techrehearsal/,
    prefix dropped). ShoulderSurf files stay flat — SS is the default app, its
    flat slugs don't collide with anything, and several server-side consumers
    (tunable_config / search_caps / budget_cta / client_config) look SS configs
    up by flat slug, so moving them is a separate, higher-blast-radius refactor.
    """
    if flat_name.startswith("tr-"):
        return f"techrehearsal/{flat_name[3:]}"
    return None


def _valid(path) -> bool:
    try:
        data = json.loads(path.read_text())
        return isinstance(data, dict) and "version" in data
    except (json.JSONDecodeError, OSError):
        return False


def main() -> None:
    if not CONFIG_DIR.is_dir():
        print(f"CONFIG_DIR not found: {CONFIG_DIR}")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    flat = sorted(p for p in CONFIG_DIR.glob("*.json"))  # top-level only
    print(f"=== B2 migrate (mode={MODE}) — {len(flat)} flat files in {CONFIG_DIR} ===")
    moved = skipped = removed = 0
    for src in flat:
        rel = target_subpath(src.name)
        if rel is None:
            print(f"  stay   {src.name}")
            skipped += 1
            continue
        dest = CONFIG_DIR / rel
        if MODE == "populate":
            if dest.exists():
                print(f"  exists {rel} (leave)")
            elif _valid(src):
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                print(f"  copied {src.name} -> {rel}")
                moved += 1
            else:
                print(f"  SKIP   {src.name} (invalid JSON/version)")
        elif MODE == "cleanup":
            if dest.exists() and _valid(dest):
                bak = src.with_suffix(f".json.bak-{stamp}")
                shutil.copy2(src, bak)
                src.unlink()
                print(f"  removed {src.name} (dest verified; backup {bak.name})")
                removed += 1
            else:
                print(f"  KEEP   {src.name} (dest missing/invalid — NOT safe to delete)")
        else:  # dryrun
            state = "dest exists" if dest.exists() else "dest MISSING"
            print(f"  plan   {src.name} -> {rel}   [{state}]")
    print(f"=== done: copied={moved} removed={removed} stay/skip={skipped} ===")
    if MODE in ("populate", "cleanup"):
        print("Restart the container so load_remote_configs() reloads.")


if __name__ == "__main__":
    main()
