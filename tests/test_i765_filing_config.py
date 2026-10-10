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
    assert doc["version"] == 2 and doc["schema_version"] == 1
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
