#!/usr/bin/env python3
"""Generate the vendored jsvectormap US-states map from Natural Earth data.

jsvectormap 1.6 ships only `world.js` and `world-merc.js`; there is no US
states map on the package (every `us_*` filename 404s on the CDN). This
builds one, so the dashboard can drill from the world heat map into
state-level detail without taking on an unvetted third-party map file.

Source data: Natural Earth 1:50m admin-1 states and provinces, which is
public domain ("No permission is needed to use Natural Earth"). Fetched
from nvkelso/natural-earth-vector.

Output: app/static/us-states-map.js, a single `jsVectorMap.addMap(...)`
call keyed by ISO 3166-2 code (US-TX, US-CA, ...), matching the shape of
the library's own world.js.

Projection mirrors the library's own `aea` implementation exactly, read out
of the shipped dist: radius 6381372, standard parallels 29.5 and 45.5,
central meridian passed in. That keeps `coordsToPoint` and `setFocus`
consistent with the emitted geometry.

Alaska and Hawaii go in their own insets, scaled down into the empty
lower-left of the frame, which is the standard treatment. Without that,
Alaska's real extent squashes the lower 48 to a strip.

Usage:  python3 scripts/gen_us_states_map.py [--geojson PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import urllib.request

NE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_50m_admin_1_states_provinces_lakes.geojson"
)
OUT = pathlib.Path("app/static/us-states-map.js")

RADIUS = 6381372.0          # jsvectormap's own value
RAD_DEG = math.pi / 180.0
CENTRAL_MERIDIAN = -96.0    # conventional for a US Albers
SIMPLIFY_PX = 0.35          # drop points closer than this in SVG space
MAIN_WIDTH = 900.0          # matches world.js
AK_WIDTH = 150.0
HI_WIDTH = 68.0

# Each inset gets its own central meridian. Albers distorts badly far from
# its meridian, and Alaska sits ~55 degrees west of -96: projected on the
# mainland's meridian it shears into a wedge and its bounding box comes out
# taller than it is wide. Real inset maps re-project per inset, so we do too.
# The `projection` metadata in the output names the MAIN projection only,
# which is what `coordsToPoint` would use; we place no markers, so the
# insets' own meridians cost nothing.
MERIDIANS = {"main": CENTRAL_MERIDIAN, "AK": -152.0, "HI": -157.0}

# Geographic clips, applied before projection.
#   Alaska: the Aleutian tail runs past the antimeridian, which wraps to a
#   mirrored x and draws a band across the frame.
#   Hawaii: Natural Earth includes the uninhabited Northwestern Hawaiian
#   Islands out to -177, which are never drawn on a states map and would
#   otherwise dominate the inset's extent.
LNG_CLIP = {"AK": (-172.0, -129.0), "HI": (-161.0, -154.0)}


def project(lat: float, lng: float, meridian: float = CENTRAL_MERIDIAN):
    """Albers equal area, transcribed from jsvectormap's `aea`."""
    s = meridian * RAD_DEG
    a = 29.5 * RAD_DEG
    n = 45.5 * RAD_DEG
    r = lat * RAD_DEG
    o = lng * RAD_DEG
    h = (math.sin(a) + math.sin(n)) / 2
    l = math.cos(a) ** 2 + 2 * h * math.sin(a)
    c = h * (o - s)
    u = math.sqrt(l - 2 * h * math.sin(r)) / h
    p = math.sqrt(l - 2 * h * math.sin(0)) / h
    return u * math.sin(c) * RADIUS, -(p - u * math.cos(c)) * RADIUS


def rings(geom):
    """Yield coordinate rings from a Polygon or MultiPolygon."""
    if geom["type"] == "Polygon":
        yield from geom["coordinates"]
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield from poly


def load(path: str | None):
    if path:
        return json.loads(pathlib.Path(path).read_text())
    print(f"fetching {NE_URL}")
    with urllib.request.urlopen(NE_URL, timeout=120) as r:
        return json.loads(r.read().decode())


def ring_area(pts):
    """Shoelace, for dropping slivers (tiny offshore islands)."""
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def simplify(pts, tol):
    """Distance-threshold thinning. Cheap, and plenty for a heat map at
    900px wide; Douglas-Peucker would buy little here."""
    if len(pts) < 4:
        return pts
    out = [pts[0]]
    for p in pts[1:-1]:
        if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= tol:
            out.append(p)
    out.append(pts[-1])
    return out


def to_path(rings_svg):
    """Relative SVG path, 2dp, same style as the library's world.js."""
    parts = []
    for pts in rings_svg:
        parts.append(f"M{pts[0][0]:.2f},{pts[0][1]:.2f}")
        px, py = pts[0]
        for x, y in pts[1:]:
            parts.append(f"l{x - px:.2f},{y - py:.2f}")
            px, py = x, y
        parts.append("Z")
    return "".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", help="local copy of the Natural Earth file")
    args = ap.parse_args()

    data = load(args.geojson)
    feats = [
        f for f in data["features"]
        if (f["properties"].get("iso_a2") or f["properties"].get("adm0_a3")) in ("US", "USA")
        and f["properties"].get("postal")
    ]
    if len(feats) != 51:
        print(f"warning: expected 51 US admin-1 features, got {len(feats)}")

    # Project every ring once, grouped by which inset it belongs to.
    groups: dict[str, list[tuple[str, str, list]]] = {"main": [], "AK": [], "HI": []}
    for f in feats:
        postal = f["properties"]["postal"]
        code = f["properties"].get("iso_3166_2") or f"US-{postal}"
        name = f["properties"]["name"]
        group = postal if postal in ("AK", "HI") else "main"
        meridian = MERIDIANS[group]
        lo, hi = LNG_CLIP.get(group, (-180.0, 180.0))
        projected = []
        for ring in rings(f["geometry"]):
            ring = [c for c in ring if lo <= c[0] <= hi]
            if len(ring) < 4:
                continue
            pts = [project(lat, lng, meridian) for lng, lat in ring]
            if len(pts) >= 4:
                projected.append(pts)
        if projected:
            groups[group].append((code, name, projected))

    # Drop sliver rings so offshore rocks don't dominate a bbox.
    for g, entries in groups.items():
        for _, _, polys in entries:
            areas = [ring_area(p) for p in polys]
            biggest = max(areas) if areas else 0
            keep = [p for p, a in zip(polys, areas) if a >= biggest * 1e-4]
            polys[:] = keep

    def bbox(entries):
        xs = [x for _, _, polys in entries for pts in polys for x, _ in pts]
        ys = [y for _, _, polys in entries for pts in polys for _, y in pts]
        return min(xs), min(ys), max(xs), max(ys)

    mx0, my0, mx1, my1 = bbox(groups["main"])
    main_scale = MAIN_WIDTH / (mx1 - mx0)
    main_height = (my1 - my0) * main_scale

    ax0, ay0, ax1, ay1 = bbox(groups["AK"])
    ak_scale = AK_WIDTH / (ax1 - ax0)
    ak_height = (ay1 - ay0) * ak_scale

    hx0, hy0, hx1, hy1 = bbox(groups["HI"])
    hi_scale = HI_WIDTH / (hx1 - hx0)
    hi_height = (hy1 - hy0) * hi_scale

    # Park both insets in the empty lower-left of the frame: in a US Albers
    # that corner is open Pacific and northern Mexico, so nothing overlaps
    # the mainland and the frame stays the shape of the lower 48 (important,
    # since it renders into a wide, short dashboard card).
    total_height = round(main_height, 4)
    ak_top = total_height - ak_height - 4
    hi_top = total_height - hi_height - 12

    insets = [
        {"kind": "main", "left": 0.0, "top": 0.0, "width": MAIN_WIDTH,
         "height": main_height, "scale": main_scale, "ox": mx0, "oy": my0,
         "bbox": (mx0, my0, mx1, my1)},
        {"kind": "AK", "left": 4.0, "top": ak_top, "width": AK_WIDTH,
         "height": ak_height, "scale": ak_scale, "ox": ax0, "oy": ay0,
         "bbox": (ax0, ay0, ax1, ay1)},
        {"kind": "HI", "left": AK_WIDTH + 22.0, "top": hi_top, "width": HI_WIDTH,
         "height": hi_height, "scale": hi_scale, "ox": hx0, "oy": hy0,
         "bbox": (hx0, hy0, hx1, hy1)},
    ]

    paths: dict[str, dict[str, str]] = {}
    for inset in insets:
        for code, name, polys in groups[inset["kind"]]:
            svg_rings = []
            for pts in polys:
                sp = [
                    ((x - inset["ox"]) * inset["scale"] + inset["left"],
                     (y - inset["oy"]) * inset["scale"] + inset["top"])
                    for x, y in pts
                ]
                sp = simplify(sp, SIMPLIFY_PX)
                if len(sp) >= 4:
                    svg_rings.append(sp)
            if svg_rings:
                paths[code] = {"path": to_path(svg_rings), "name": name}

    payload = {
        "insets": [
            {"width": round(i["width"], 4), "top": round(i["top"], 4),
             "left": round(i["left"], 4), "height": round(i["height"], 4),
             "bbox": [{"y": i["bbox"][1], "x": i["bbox"][0]},
                      {"y": i["bbox"][3], "x": i["bbox"][2]}]}
            for i in insets
        ],
        "paths": paths,
        "height": total_height,
        "width": MAIN_WIDTH,
        "projection": {"type": "aea", "centralMeridian": CENTRAL_MERIDIAN},
    }

    header = (
        "// GENERATED FILE. Do not hand-edit.\n"
        "//   regenerate: python3 scripts/gen_us_states_map.py\n"
        "// US states map for jsvectormap, which ships only world maps.\n"
        "// Geometry: Natural Earth 1:50m admin-1 (public domain).\n"
        '// Made with Natural Earth. naturalearthdata.com\n'
        '"use strict";'
    )
    # GeoIP hands us region *names* ("Texas"), and occasionally the postal
    # abbreviation. Emit both spellings so the dashboard can map either onto a
    # region code without hand-maintaining fifty entries.
    by_name: dict[str, str] = {}
    for f in feats:
        code = f["properties"].get("iso_3166_2") or f"US-{f['properties']['postal']}"
        if code in paths:
            by_name[f["properties"]["name"]] = code
            by_name[f["properties"]["postal"]] = code

    body = (
        "jsVectorMap.addMap(\"us-states\","
        + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        + ");\n"
        + "window.US_STATE_CODE_BY_NAME="
        + json.dumps(by_name, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        + ";\n"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(header + body)

    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    print(f"  regions: {len(paths)}")
    print(f"  viewBox: {MAIN_WIDTH:.0f} x {total_height:.1f}")
    for i in insets:
        print(f"  inset {i['kind']:5} left={i['left']:.0f} top={i['top']:.0f} "
              f"{i['width']:.0f}x{i['height']:.0f}")
    missing = {"US-" + p for p in (
        "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
        "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA "
        "WV WI WY").split()} - set(paths)
    print(f"  missing: {sorted(missing) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
