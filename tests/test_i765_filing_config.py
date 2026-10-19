"""The I-765 filing document (edition, fees, addresses, online filing,
evidence, attorney triggers, timing), served at GET /v1/config/filing to
X-App-ID i765 through the existing per-app config route, no bespoke
endpoint (agreed with the I-765 lead, 2026-10-09). The client bundles the
same file as its boot default and honors the version counter. Every value
in it carries a uscis.gov source and a read date; GP serves it and does
not know law."""
from __future__ import annotations

import json
import re

I765 = {"X-App-ID": "i765"}
V1_CATEGORIES = {"c9", "c3a", "c3b", "c3c", "a12", "c19", "a17", "a18", "a3", "a5", "c26"}
TOP_KEYS = {"schema_version", "version", "as_of", "app_id", "form", "notes", "sources", "categories",
            "edition", "fees", "addresses", "online_filing", "evidence", "attorney_triggers", "timing"}


def test_the_filing_document_is_served_to_i765_only(client):
    r = client.get("/v1/config/filing", headers=I765)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["app_id"] == "i765" and doc["form"] == "I-765"
    assert doc["version"] == 5 and doc["schema_version"] == 1
    assert TOP_KEYS <= set(doc), TOP_KEYS - set(doc)
    assert V1_CATEGORIES <= set(doc["categories"])
    # No other app has a filing document, and there is no flat fallback for it.
    for app in ("n400", "shouldersurf", "techrehearsal"):
        assert client.get("/v1/config/filing", headers={"X-App-ID": app}).status_code == 404, app


def test_the_bundled_document_matches_the_leads_key_list_and_house_rules():
    doc = json.load(open("config/remote/i765/filing.json"))
    assert "server_only" not in doc, "the phone reads this one"
    assert set(doc["edition"]) >= {"accepted", "blocked", "note_en", "note_es", "note_pt", "checked_on", "source"}
    assert set(doc["fees"]) >= {"currency", "addon_note", "fee_waiver", "premium_processing", "rules"}
    assert set(doc["addresses"]) >= {"effective", "grace", "rules", "charts", "card_error_return", "entries"}
    assert all({"id", "categories", "source"} <= set(rule) for rule in doc["fees"]["rules"])
    assert all({"id", "categories", "address", "source"} & set(e) for e in doc["addresses"]["entries"])
    for item in doc["evidence"]["common"] + doc["evidence"]["specific"]:
        assert {"text_en", "text_es", "text_pt"} <= set(item), item.get("id")
    assert doc["fees"]["currency"] == "USD"
    text = json.dumps(doc, ensure_ascii=False)
    assert not re.search("[–—]", text), "no em or en dashes in anything the app shows"


def test_the_attorney_triggers_cover_the_briefs_categories_and_name_a_source():
    doc = json.load(open("config/remote/i765/filing.json"))
    keys = {t["key"] for t in doc["attorney_triggers"]["triggers"]}
    assert len(keys) >= 10
    for t in doc["attorney_triggers"]["triggers"]:
        assert t["action"] and t["en"] and t["es"] and t["pt"] and t["source"], t["key"]


def test_portuguese_never_says_green_card():
    """The I-765 lead, 2026-10-19: the English loanword is gone from every pt
    value (five of them, bundle v3), and a served copy must match or the
    client's higher-version rule brings the loanword back."""
    import json

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from walk(v, f"{path}[{i}]")
        elif isinstance(o, str):
            yield path, o

    doc = json.load(open("config/remote/i765/filing.json", encoding="utf-8"))
    pt = [(p, s) for p, s in walk(doc) if p.endswith("_pt") or p.endswith(".pt")]
    assert len(pt) > 5, "the walk must actually reach the pt values"
    assert [p for p, s in pt if "green card" in s.lower()] == []
    assert any("residência permanente" in s for _, s in pt)


def test_spanish_and_portuguese_carry_no_english_loanwords():
    """The I-765 lead, 2026-10-19: the client's test flags any of these in an
    es or pt value, and "da USCIS" in pt, so GP's bundle is held to the same
    list. C05_passport_photos said "A-Number" in both until bundle v5."""
    import json

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from walk(v, f"{path}[{i}]")
        elif isinstance(o, str):
            yield path, o

    doc = json.load(open("config/remote/i765/filing.json", encoding="utf-8"))
    loanwords = ("green card", "a-number", "work permit", "receipt notice")
    es_pt = [(p, s) for p, s in walk(doc)
             if p.endswith(("_es", ".es", "_pt", ".pt"))]
    assert len(es_pt) > 10, "the walk must actually reach the es and pt values"
    assert [p for p, s in es_pt if any(w in s.lower() for w in loanwords)] == []
    assert [p for p, s in es_pt if p.endswith(("_pt", ".pt")) and "da USCIS" in s] == []
    assert any("número A" in s for _, s in es_pt)


def test_every_courier_line_is_said_in_each_language():
    """The I-765 lead, 2026-10-19: Social's screenshots showed English under
    the Spanish lockbox address. All 12 courier.carriers values (7 chart rows,
    A01, A02 and its nested I-907 box, both (c)(26) prefix rows) are objects
    with en, es and pt now; a bare string decodes as English only."""
    import json
    found = []

    def walk(o):
        if isinstance(o, dict):
            c = o.get("courier")
            if isinstance(c, dict) and "carriers" in c:
                found.append(c["carriers"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.load(open("config/remote/i765/filing.json", encoding="utf-8")))
    assert len(found) == 12
    assert all(isinstance(c, dict) and set(c) == {"en", "es", "pt"} for c in found), found
    assert {c["es"] for c in found} == {"Envíos por FedEx, UPS y DHL"}
    assert {c["pt"] for c in found} == {"Entregas por FedEx, UPS e DHL"}
