"""Promo creative store — hot-reloadable, no deploy needed.

Bundled creatives (app/static/promo/*.html) ship with the image as defaults.
A writable persistent overlay lives next to the SQLite DB, so it rides the same
persisted volume and is tmp-isolated under tests. Admin upload writes there and
the store copy wins over the bundled default — so a creative can be updated live
without a code deploy (the design's CDN model, in-house).

Deliberately NOT seeded: only live-edited creatives live in the store, so a new
or updated bundled creative shipped via deploy serves immediately (no store copy
shadowing it). A live edit intentionally overrides bundled until it's deleted.
"""

from pathlib import Path

from app import database

BUNDLED_DIR = (Path(__file__).resolve().parent.parent / "static" / "promo")
MAX_BYTES = 512_000


def store_dir() -> Path:
    """Persistent, writable creative dir — beside the SQLite DB so it shares the
    same persisted volume (and lands in the per-test tmp dir under tests)."""
    base = Path(database._db_path).parent if database._db_path else Path("data")
    return base / "promo-assets"


def safe_name(name: str) -> str | None:
    """Allow only a flat *.html filename — no path separators, no dotfiles."""
    if not name.endswith(".html") or "/" in name or "\\" in name or name.startswith("."):
        return None
    return name


def resolve_path(name: str) -> Path | None:
    """The live store wins; the bundled default is the fallback. None if neither."""
    safe = safe_name(name)
    if not safe:
        return None
    store = store_dir() / safe
    if store.is_file():
        return store
    bundled = BUNDLED_DIR / safe
    if bundled.is_file():
        return bundled
    return None


def save(name: str, content: bytes) -> Path:
    safe = safe_name(name)
    if not safe:
        raise ValueError("invalid asset name")
    sd = store_dir()
    sd.mkdir(parents=True, exist_ok=True)
    dest = sd / safe
    dest.write_bytes(content)
    return dest


def remove(name: str) -> bool:
    """Delete the live copy (reverts to the bundled default if one exists)."""
    safe = safe_name(name)
    if not safe:
        return False
    dest = store_dir() / safe
    if dest.is_file():
        dest.unlink()
        return True
    return False


def listing() -> list[dict]:
    """All creatives by name; a store copy shadows bundled and is tagged source=store."""
    out: dict[str, dict] = {}
    for src in BUNDLED_DIR.glob("*.html"):
        out[src.name] = {"name": src.name, "source": "bundled", "bytes": src.stat().st_size}
    sd = store_dir()
    if sd.exists():
        for src in sd.glob("*.html"):
            out[src.name] = {"name": src.name, "source": "store", "bytes": src.stat().st_size}
    return sorted(out.values(), key=lambda d: d["name"])
