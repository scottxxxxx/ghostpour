"""The vendored jsvectormap US-states map.

jsvectormap 1.6 ships only world.js and world-merc.js, so state-level
rendering needed a map file we generate ourselves from public-domain Natural
Earth data (scripts/gen_us_states_map.py). These tests guard the artifact,
since a silently malformed map file renders as an empty panel.
"""

import json
import re
from pathlib import Path

ASSET = Path("app/static/us-states-map.js")

STATES = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA "
    "WV WI WY"
).split()


def _payload():
    src = ASSET.read_text()
    return json.loads(re.search(r'addMap\("us-states",(\{.*?\})\);', src, re.S).group(1))


def _points(path_d):
    """Walk a relative SVG path into absolute points."""
    out, x, y = [], 0.0, 0.0
    for cmd, val in re.findall(r"([MlZ])([-0-9.,]*)", path_d):
        if cmd == "Z":
            continue
        a, b = (val.split(",") + ["0"])[:2]
        if cmd == "M":
            x, y = float(a), float(b)
        else:
            x, y = x + float(a), y + float(b)
        out.append((x, y))
    return out


def test_asset_registers_itself_and_the_name_index():
    src = ASSET.read_text()
    assert src.startswith("// GENERATED FILE"), "must say it is generated"
    assert "scripts/gen_us_states_map.py" in src, "must say how to regenerate"
    assert "Natural Earth" in src, "public-domain source credited"
    assert 'jsVectorMap.addMap("us-states"' in src
    assert "window.US_STATE_CODE_BY_NAME=" in src


def test_every_state_and_dc_is_present():
    paths = _payload()["paths"]
    missing = [f"US-{s}" for s in STATES if f"US-{s}" not in paths]
    assert not missing, f"missing regions: {missing}"
    assert len(paths) == 51, f"expected 50 states + DC, got {len(paths)}"
    assert all(p.get("name") for p in paths.values()), "every region needs a name"


def test_geometry_stays_inside_the_frame():
    # Anything outside the viewBox is invisible, and a single stray point
    # silently rescales the whole map when jsvectormap fits it to a container.
    d = _payload()
    pts = [p for entry in d["paths"].values() for p in _points(entry["path"])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert -0.5 <= min(xs) and max(xs) <= d["width"] + 0.5
    assert -0.5 <= min(ys) and max(ys) <= d["height"] + 0.5


def test_alaska_and_hawaii_insets_do_not_overlap_the_mainland():
    # Projected on the mainland's meridian and left at natural scale, Alaska
    # lands on top of California. Both go in scaled insets in the empty
    # lower-left; this is what keeps them from covering real states.
    d = _payload()
    pts = {c: _points(e["path"]) for c, e in d["paths"].items()}
    mainland = [
        p for c, v in pts.items() if c not in ("US-AK", "US-HI") for p in v
    ]
    for code in ("US-AK", "US-HI"):
        v = pts[code]
        x0, x1 = min(p[0] for p in v), max(p[0] for p in v)
        y0, y1 = min(p[1] for p in v), max(p[1] for p in v)
        inside = [p for p in mainland if x0 <= p[0] <= x1 and y0 <= p[1] <= y1]
        assert not inside, f"{code} inset overlaps {len(inside)} mainland points"


def test_insets_are_declared_for_the_library():
    d = _payload()
    assert len(d["insets"]) == 3, "main, Alaska, Hawaii"
    for inset in d["insets"]:
        assert inset["width"] > 0 and inset["height"] > 0
        (lo, hi) = inset["bbox"]
        assert lo["x"] < hi["x"] and lo["y"] < hi["y"], "bbox must be min then max"
    assert d["projection"]["type"] == "aea"


def test_name_index_covers_both_spellings():
    src = ASSET.read_text()
    idx = json.loads(re.search(r"window\.US_STATE_CODE_BY_NAME=(\{.*?\});", src, re.S).group(1))
    # GeoIP sends names; abbreviations show up too, so both must resolve.
    assert idx["Texas"] == "US-TX"
    assert idx["TX"] == "US-TX"
    assert idx["District of Columbia"] == "US-DC"
    assert len(idx) == 102, "51 regions x 2 spellings"


def test_asset_is_served_with_a_long_cache(client):
    resp = client.get("/admin/us-states-map.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    # /admin is no-store; this one is immutable geometry and must not be
    # re-downloaded on every dashboard load.
    assert "max-age" in resp.headers.get("cache-control", "")
    assert "no-store" not in resp.headers.get("cache-control", "")
