"""Brazilian Portuguese (2026-11-24): Shoulder Surf ships in pt-BR and phones
send `Accept-Language: pt-BR`. GP serves the `.pt` bundles for it.

The suffix is `.pt`, the primary subtag, the same way es-MX / es-US land on
`.es`. `pt_BR` (a POSIX-style tag some HTTP stacks emit) must resolve the
same way: before this change the resolver split only on "-", so "pt_BR"
became "pt_br", matched no file, and silently served English.

The HTTP test reads `X-Config-Resolved`, the name of the file the route
actually picked, so it proves the served bundle and not just the parser.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.routers.config import _parse_accept_language
from app.services.language_directive import accept_language_primary

REMOTE = pathlib.Path("config/remote")
HEADERS = ("pt-BR", "pt_BR", "pt", "pt-BR,pt;q=0.9,en-US;q=0.8", "pt-br", "PT-BR")
FAMILIES = ("canned-report", "client-config", "feature-highlights", "idle-tips",
            "llm-providers", "model-capabilities", "protected-prompts",
            "report-strings", "tiers")


@pytest.mark.parametrize("header", HEADERS)
def test_every_portuguese_header_parses_to_pt(header):
    assert _parse_accept_language(header) == "pt"
    assert accept_language_primary(header) == "pt"


def test_the_parser_still_maps_the_other_locales():
    """The underscore fix must not move anything that already worked."""
    assert _parse_accept_language("es-MX,es;q=0.9") == "es"
    assert _parse_accept_language("en_US") is None
    assert _parse_accept_language("en-US") is None
    assert _parse_accept_language("ja-JP") == "ja"
    assert _parse_accept_language(None) is None


@pytest.mark.parametrize("family", FAMILIES)
def test_every_family_has_a_pt_file_at_the_english_version(family):
    en = json.loads((REMOTE / f"{family}.json").read_text())
    pt = json.loads((REMOTE / f"{family}.pt.json").read_text())
    assert pt["version"] == en["version"], family


@pytest.mark.parametrize("header", ("pt-BR", "pt_BR", "pt"))
@pytest.mark.parametrize("family", FAMILIES)
def test_the_route_serves_the_pt_file(client, family, header):
    r = client.get(f"/v1/config/{family}", headers={"Accept-Language": header})
    assert r.status_code == 200, (family, header, r.text[:200])
    assert r.headers["X-Config-Resolved"] == f"{family}.pt", (family, header)
    assert r.headers["X-Config-Locale"] == "pt"


def test_a_pt_phone_does_not_get_english_tips(client):
    """The resolved name could be right while the body is English (a copied
    file). The first tip is the one under the orb on every idle visit."""
    en = json.loads((REMOTE / "idle-tips.json").read_text())["tips"]
    r = client.get("/v1/config/idle-tips", headers={"Accept-Language": "pt-BR"})
    tips = r.json()["tips"]
    assert len(tips) == len(en)
    assert tips[0] != en[0]
    assert "Toque" in tips[0]
