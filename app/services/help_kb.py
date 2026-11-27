"""GP's own copy of each app's Help articles (Scott, 2026-11-27).

Until now the app chose three articles by keyword and sent their text with
every question, and GP could only answer from those three. The first real
question on 1.20 ("how do i see who owes something after a meeting") showed
the cost: the ranker missed the one article that answers it, so the model
could name that article and not read it. GP now holds the articles and
finds them itself.

The copy has to be the one inside the build that is asking, or a link GP
returns may not open. So every imported version lives in its own folder
named by a hash of its files, the app sends the hash of what it has
bundled (`kb_hash`), and GP answers from that folder or says it does not
have it. Nothing here ever picks "the newest": a build is only ever
answered from its own articles.

Layout: config/help-kb/<app_id>/<kb_hash>/index.json and *.md for English,
and one folder per other language (es/, fr/, ja/, pt-BR/) with the same
ids. scripts/import_help_kb.py writes it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import re
from functools import lru_cache

logger = logging.getLogger("ghostpour.help_kb")

KB_ROOT = pathlib.Path(__file__).resolve().parents[2] / "config" / "help-kb"
KB_HASH_PATTERN = r"^[0-9a-f]{64}$"
_KB_HASH_RE = re.compile(KB_HASH_PATTERN)
_APP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SOURCE_LANGUAGE = "en"


def is_kb_file(rel_path: str) -> bool:
    """Whether a path under the Help folder is part of the hashed set."""
    name = rel_path.rsplit("/", 1)[-1]
    return name == "index.json" or name.endswith(".md")


def kb_hash_of(files: dict[str, bytes]) -> str:
    """The agreed hash over {relative path: bytes}. Paths sort in byte
    order, which is what `LC_ALL=C sort` and Swift's UTF-8 comparison do."""
    lines = [f"{path}\t{hashlib.sha256(data).hexdigest()}\n".encode("utf-8")
             for path, data in sorted(files.items(), key=lambda kv: kv[0].encode("utf-8"))]
    return hashlib.sha256(b"".join(lines)).hexdigest()


def _read_files(folder: pathlib.Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in folder.rglob("*")
            if p.is_file() and is_kb_file(p.relative_to(folder).as_posix())}


def _language_folder(folder: pathlib.Path, app_locale: str | None) -> tuple[pathlib.Path, str]:
    """The folder for the app's language: an exact match ("pt-BR"), then
    the bare language ("es" for "es-MX"), then English at the root."""
    tag = (app_locale or "").replace("_", "-").strip()
    names = {p.name.lower(): p for p in folder.iterdir() if p.is_dir()}
    for want in (tag.lower(), tag.split("-")[0].lower()):
        if want and want != SOURCE_LANGUAGE:
            if want in names:
                return names[want], names[want].name
            # "pt" finds "pt-BR": one regional folder stands for the language.
            regional = sorted(n for n in names if n.split("-")[0] == want)
            if regional:
                return names[regional[0]], names[regional[0]].name
    return folder, SOURCE_LANGUAGE


@lru_cache(maxsize=16)
def _verified_folder(app_id: str, kb_hash: str) -> pathlib.Path | None:
    """The folder for this version, only if its files still hash to its
    name. A folder that was edited by hand is not the build's articles any
    more, and it is refused loudly instead of served."""
    if not _APP_RE.match(app_id or "") or not _KB_HASH_RE.match(kb_hash or ""):
        return None
    folder = KB_ROOT / app_id / kb_hash
    if not folder.is_dir():
        return None
    got = kb_hash_of(_read_files(folder))
    if got != kb_hash:
        logger.error("help_kb_hash_mismatch app=%s folder=%s computed=%s: refused",
                     app_id, kb_hash, got)
        return None
    return folder


@lru_cache(maxsize=64)
def _load(app_id: str, kb_hash: str, language: str) -> tuple[dict, ...] | None:
    folder = _verified_folder(app_id, kb_hash)
    if folder is None:
        return None
    try:
        # The English index is the list: its order, its ids, its file
        # names. A translated index is {id: {title, summary}} and its
        # articles are <language>/<same file name>. An article with no
        # translation is served in English, as the app does.
        source = json.loads((folder / "index.json").read_text("utf-8"))["articles"]
        translated = {}
        if language != SOURCE_LANGUAGE:
            translated = json.loads(
                (folder / language / "index.json").read_text("utf-8"))["articles"]
        out = []
        for entry in source:
            local = translated.get(entry["id"]) or {}
            path = folder / language / entry["file"]
            if language == SOURCE_LANGUAGE or not path.is_file():
                path = folder / entry["file"]
            title = local.get("title") or entry["title"]
            out.append({"id": entry["id"], "title": title,
                        "summary": local.get("summary") or entry.get("summary") or title,
                        "body": path.read_text("utf-8")})
        return tuple(out)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.error("help_kb_unreadable app=%s kb=%s lang=%s: %s", app_id, kb_hash,
                     language, type(exc).__name__)
        return None


@lru_cache(maxsize=8)
def _builds(app_id: str) -> dict:
    if not _APP_RE.match(app_id or ""):
        return {}
    try:
        doc = json.loads((KB_ROOT / app_id / "builds.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in doc.items()
            if isinstance(v, str) and _KB_HASH_RE.match(v)} if isinstance(doc, dict) else {}


def kb_for_build(app_id: str, app_build: str | None) -> str | None:
    """The Help set inside a build that does not send `kb_hash` itself
    (builds from before the field existed). Only builds whose bundle was
    compared with the imported set are listed, in <app>/builds.json."""
    return _builds(app_id).get(app_build or "")


def knows(app_id: str, kb_hash: str) -> bool:
    return _verified_folder(app_id, kb_hash) is not None


def articles(app_id: str, kb_hash: str, app_locale: str | None) -> tuple[list[dict], str] | None:
    """Every article of this version in the app's language, as
    [{id, title, summary, body}], and the language served. None when GP
    does not have this version."""
    folder = _verified_folder(app_id, kb_hash)
    if folder is None:
        return None
    _, language = _language_folder(folder, app_locale)
    loaded = _load(app_id, kb_hash, language)
    if loaded is None and language != SOURCE_LANGUAGE:
        language, loaded = SOURCE_LANGUAGE, _load(app_id, kb_hash, SOURCE_LANGUAGE)
    if loaded is None:
        return None
    return [dict(a) for a in loaded], language
