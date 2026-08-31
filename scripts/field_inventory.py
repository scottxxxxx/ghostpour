#!/usr/bin/env python
"""Emit GP's wire field names, generated from GP's own code.

WHY THIS EXISTS

`extra="allow"` (#824) closed the half of the typed-hop bug where a proxy
body model silently DROPS a key it was not told about. It does nothing for
the other half:

    a name that is wrong on one side is a 200 on every side

`patch_type` needed all THREE components corrected and any one alone left it
broken. A forwarding test cannot help: it proves a key SURVIVES the hop, not
that the far side UNDERSTANDS it. Three instances in three weeks (`to_name`,
`project_name`, `client_id`/`deadline_date`) would every one of them have
shown up as a name present on one side only.

SO: each team generates this from its own code and the results are DIFFED.
Never from a doc. A doc is a fourth place to be wrong, and it is the place
that rots first, because nothing breaks when it is stale.

WHAT IT DOES NOT DO

It does not prove agreement. It makes disagreement VISIBLE, which is the
part no side can do alone. Two teams reading the same doc and nodding is not
evidence (SS's client decoding `label` while encoding `relationship` was
evidence precisely because it came from a third place neither GP nor CQ
controlled).

It also cannot see a name that never reaches a typed model: a handler that
builds its payload BY HAND is invisible here, exactly as it was invisible to
`model_config["extra"] == "allow"`. Both `assign-project` handlers did that.
Those are listed under "hand_built" rather than omitted, so the gap is on the
face of the output instead of in someone's memory.

USAGE

    python scripts/field_inventory.py              # JSON to stdout
    python scripts/field_inventory.py --names      # flat sorted name list
    python scripts/field_inventory.py --out FILE   # write to FILE
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from typing import Any, get_args, get_origin

# Runnable from anywhere. Without this the script imports nothing and exits
# on ModuleNotFoundError, which is a generator that produces an empty
# inventory rather than a loud failure if anyone ever wraps it in a `|| true`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.params import Depends as DependsParam
from fastapi.routing import APIRoute
from pydantic import BaseModel

# Handlers that do NOT bind the outbound payload to a typed model, so their
# field names cannot be read off a model and this tool is BLIND to them.
# Named on purpose: the first version of the passthrough test went green on
# these two while they kept dropping every key.
HAND_BUILT_NOTE = (
    "handler constructs its outbound payload by hand; names here are not "
    "model-derived and this generator cannot see drift in them"
)


def _unwrap(annotation: Any) -> Any:
    """Reduce Optional[X] / list[X] to X, checking ORIGIN before unwrapping.

    An earlier version of this logic elsewhere stripped Optional[X] and
    list[X] with one line, so list[FromLabel] collapsed to FromLabel. Order
    matters and this is the shape that bit us.
    """
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return _unwrap(args[0]) if args else None
    if origin is not None:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _unwrap(args[0])
    return annotation


def _describe(model: type[BaseModel], seen: set[str] | None = None) -> dict:
    """Field names for a model, recursing into nested models.

    Records `extra` explicitly. A model set to "ignore" is one that can still
    eat a key, so the posture belongs in the inventory next to the names
    rather than being something a reader has to go and check.
    """
    seen = seen or set()
    if model.__name__ in seen:
        return {"recursive_ref": model.__name__}
    seen = seen | {model.__name__}

    fields: dict[str, Any] = {}
    for name, f in model.model_fields.items():
        inner = _unwrap(f.annotation)
        entry: dict[str, Any] = {"required": f.is_required()}
        if f.alias and f.alias != name:
            # An alias IS the wire name. A mismatch between the python
            # attribute and the wire key is the misnaming bug wearing a hat.
            entry["wire_alias"] = f.alias
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            entry["nested"] = _describe(inner, seen)
        fields[name] = entry

    return {
        "model": model.__name__,
        "extra": model.model_config.get("extra", "ignore"),
        "fields": fields,
    }


def _unwrap_optional(annotation):
    """`Model | None` -> `Model`; anything else unchanged.

    See the note at the issubclass check for why this is not cosmetic.
    """
    args = [a for a in get_args(annotation) if a is not type(None)]
    if get_origin(annotation) is not None and len(args) == 1:
        return args[0]
    return annotation


def _body_models(module) -> list[tuple[str, str, type[BaseModel]]]:
    """Every route in `module` that binds a request BODY to a model.

    The Depends(...) check is what separates an injected UserRecord from an
    actual body. Forgetting it turns this into an inventory of the auth
    dependency, which would look plausible and be worthless.
    """
    from app.main import app

    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute) or inspect.getmodule(r.endpoint) is not module:
            continue
        for _n, p in inspect.signature(r.endpoint).parameters.items():
            ann = _unwrap_optional(p.annotation)
            # Unwrap Optional[Model] before the issubclass check. A route
            # binding `body: Model | None = None` annotates a UNION, which is
            # not a type, so a bare issubclass silently skipped it and the
            # route was invisible to this enumeration entirely. That is the
            # blind spot this file exists to close, in this file: a model that
            # can drop keys, on a route the instrument reports as having no
            # model at all.
            #
            # Found by sabotage on 2026-08-31: a typed model bound as
            # Optional on a new route dropped `note` and the inventory stayed
            # green. Every model bound today is non-optional, so it had not
            # bitten yet, which is exactly why it needed finding rather than
            # waiting.
            if (isinstance(ann, type) and issubclass(ann, BaseModel)
                    and not isinstance(p.default, DependsParam)):
                method = sorted(r.methods - {"HEAD", "OPTIONS"})[0]
                out.append((method, r.path, ann))
    return out


def build() -> dict:
    import app.routers.cq_proxy as cq_proxy

    routes = {}
    for method, path, model in sorted(_body_models(cq_proxy),
                                      key=lambda t: (t[1], t[0])):
        routes[f"{method} {path}"] = _describe(model)

    hand_built = {}
    src = inspect.getsource(cq_proxy)
    for marker in ("assign-project",):
        if marker in src:
            hand_built[marker] = HAND_BUILT_NOTE

    return {
        "team": "GP",
        "hop": "SS -> GP -> CQ (cq_proxy body models)",
        "generated_from": "app.routers.cq_proxy route signatures, via app.main.app",
        "generator": "scripts/field_inventory.py",
        "caveat": (
            "Model-derived only. A handler that builds its payload by hand is "
            "NOT covered; see hand_built."
        ),
        "hand_built": hand_built,
        "routes": routes,
    }


def flat_names(inv: dict) -> list[str]:
    """Every distinct wire name, for the cheapest possible diff.

    A set difference over this list is what actually catches the bug: a name
    present on one side only. The structured JSON is for reading AFTER the
    flat diff says there is something to read.
    """
    names: set[str] = set()

    def walk(node: dict) -> None:
        for name, entry in (node.get("fields") or {}).items():
            names.add(entry.get("wire_alias", name))
            if "nested" in entry:
                walk(entry["nested"])

    for node in inv["routes"].values():
        walk(node)
    return sorted(names)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--names", action="store_true",
                    help="print the flat sorted wire-name list instead of JSON")
    ap.add_argument("--out", help="write to this file instead of stdout")
    args = ap.parse_args()

    inv = build()
    text = ("\n".join(flat_names(inv)) if args.names
            else json.dumps(inv, indent=2, sort_keys=True))

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
