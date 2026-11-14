"""GP's own N-400 choice catalog behind the option marker and Jev, for a build
that stops sending metadata.choice_fields, and v67's one line that reads the
client's grouped volunteer_fields (auditor, 2026-11-13). Drafted, not served:
v67 waits on Scott."""
import json
import re

from app.services.n400_field_catalog import (
    choice_fields, choice_fields_for, field_ids,
)

CFG = json.load(open("config/remote/n400/interviewer-turn.json"))


def test_the_catalog_matches_build_215s_wire():
    """Measured against the choice_fields Steven's build 215 sent: 130 of 130."""
    c = choice_fields()
    assert len(field_ids()) == 385 and len(c) == 130
    assert c["p1.eligibility_basis"][:2] == ["general_provision", "spouse_usc"]
    assert c["p6.child4.relationship"] == ["biological", "stepchild", "adopted"]
    assert len(c["p1.interview_field_office"]) == 96


def test_the_clients_map_wins_and_a_missing_or_unreadable_one_gets_gps():
    theirs = {"p8.took_trips": ["yes", "no"]}
    assert choice_fields_for(theirs) is theirs
    for missing in (None, {}, "yes,no", {"p8.took_trips": []}):
        assert choice_fields_for(missing) == choice_fields()


def test_v67_adds_only_the_grouped_id_line():
    import subprocess
    old = json.loads(subprocess.run(["git", "show", "main:config/remote/n400/interviewer-turn.json"],
                                    capture_output=True, text=True, check=True).stdout)
    line = (" A grouped id stands for every id it expands to: p4.prior_address1-4.{street,unit,city} means "
            "p4.prior_address1.street, p4.prior_address1.unit, p4.prior_address1.city and the same three for "
            "prior_address2, 3 and 4. A fact always names one expanded id, never the grouped form.")
    assert CFG["systemPrompt"].replace(line, "", 1) == old["systemPrompt"]
    assert CFG["userPromptTemplate"] == old["userPromptTemplate"]
    assert "FIELD CATALOG" not in CFG["systemPrompt"] and not re.search(r" - |[\u2013\u2014]", line)


# --- through the real route --------------------------------------------------

def _send(client, free_user, reply, **md):
    from unittest.mock import patch
    from app.models.chat import ChatResponse
    from tests.conftest import chat_request
    from tests.test_n400_sentence_stream_route import _metadata

    async def fake(provider_router, request, db, settings):
        return ChatResponse(text=reply, input_tokens=100, output_tokens=50, model="claude-sonnet-5",
                            provider="anthropic", usage={"input_tokens": 100, "output_tokens": 50})
    client.app.state.rate_limiter._buckets.clear()
    body = chat_request(system_prompt="", user_content="As a citizen.", metadata=_metadata(**md))
    with patch("app.services.anthropic_or_fallback.route_with_fallback", fake):
        return client.post("/v1/chat", json=body, headers={**free_user["headers"], "X-App-ID": "n400"})


REPLY = json.dumps({"schema_version": 1, "turn_id": "t-stream-1", "intent": {"type": "answer"},
                    "reply": {"en": "Noted. What is your A-Number?"}, "asking": None,
                    "facts": [{"field_id": "p1.eligibility_basis", "value": "citizen", "value_type": "string",
                               "provenance": {"source": "user_stated", "confidence": 0.9,
                                              "utterance": "As a citizen."}}]})


def _marked(r):
    return [o["field_id"] for o in json.loads(r.json()["text"]).get("values_outside_declared_options") or []]


def test_with_no_map_on_the_wire_gps_catalog_still_marks_an_off_option_value(client, free_user):
    """eligibility_basis is not a yes/no gate, so the agenda-only rule cannot see it."""
    r = _send(client, free_user, REPLY)
    assert r.status_code == 200 and _marked(r) == ["p1.eligibility_basis"]


def test_an_old_builds_map_is_the_one_used(client, free_user):
    r = _send(client, free_user, REPLY, choice_fields={"p1.eligibility_basis": ["citizen", "other"]})
    assert r.status_code == 200 and _marked(r) == []


def test_the_catalog_file_ships_in_the_image():
    """It first landed in app/data/, which .gitignore drops and the container
    uses for its database: the fallback would have read a missing file."""
    import subprocess
    from app.services.n400_field_catalog import _PATH
    rel = str(_PATH.relative_to(_PATH.parents[2]))
    assert rel.startswith("app/") and not rel.startswith("app/data/")
    assert subprocess.run(["git", "check-ignore", "-q", rel]).returncode == 1, rel
