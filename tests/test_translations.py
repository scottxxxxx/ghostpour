"""POST /v1/translations — contract behaviors that do not need a model:
validation, cache idempotency, id round-trip parsing, retention."""
import json

import pytest

from app.services import meeting_title
from app.services import translations as tr


# --- parse_model_output: the server-side fence/shape gate --------------------

IDS = ["a", "b"]


def test_parses_clean_json():
    out = tr.parse_model_output('[{"id":"a","text":"x"},{"id":"b","text":"y"}]', IDS)
    assert [o["id"] for o in out] == IDS


def test_strips_fences_server_side():
    fenced = '```json\n[{"id":"a","text":"x"},{"id":"b","text":"y"}]\n```'
    out = tr.parse_model_output(fenced, IDS)
    assert out and out[0]["text"] == "x"


@pytest.mark.parametrize("bad", [
    '[{"id":"a","text":"x"}]',                                  # missing id
    '[{"id":"b","text":"y"},{"id":"a","text":"x"}]',            # order changed
    '[{"id":"a","text":"x"},{"id":"a","text":"x2"}]',           # duplicate id
    '[{"id":"a","text":"x"},{"id":"c","text":"y"}]',            # foreign id
    '{"id":"a","text":"x"}',                                    # not an array
    'no json at all',
    '[{"id":"a","text":1},{"id":"b","text":"y"}]',              # non-string text
])
def test_rejects_every_broken_shape(bad):
    assert tr.parse_model_output(bad, IDS) is None


def test_extra_keys_are_dropped_not_echoed():
    out = tr.parse_model_output(
        '[{"id":"a","text":"x","note":"z"},{"id":"b","text":"y"}]', IDS)
    assert out and "note" not in out[0]


# --- cache key: the idempotency contract -------------------------------------

def test_cache_key_is_content_stable_and_dimension_sensitive():
    segs = [{"id": "a", "text": "hola"}]
    k = tr.cache_key(segs, "es", "en")
    assert k == tr.cache_key([{"text": "hola", "id": "a"}], "es", "en")  # key order irrelevant
    assert k != tr.cache_key(segs, "es", "fr")
    assert k != tr.cache_key([{"id": "a", "text": "hola!"}], "es", "en")
    assert k.endswith(f"v{tr.ENGINE_VERSION}")


def test_language_normalization():
    assert tr.normalize_language("es-US") == "es-US"
    assert tr.normalize_language("es") == "es"
    assert tr.normalize_language("") is None
    assert tr.normalize_language(None) is None
    assert tr.normalize_language("not a tag!") is None


def test_transcript_prompt_is_faithful_and_prose_prompt_carries_no_dash_rule():
    assert "em dash" not in tr.system_prompt("transcript")
    assert "em dash" in tr.system_prompt("summary")
    assert "em dash" in tr.system_prompt("report")


# --- endpoint validation (no model call reached) -----------------------------

def _body(**over):
    d = {"source_language": "es", "target_language": "en",
         "artifact": "transcript", "segments": [{"id": "s1", "text": "hola"}]}
    d.update(over)
    return d


def test_422_on_malformed_source_language(client, pro_user):
    r = client.post("/v1/translations", json=_body(source_language="???"), headers=pro_user["headers"])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_language"


def test_422_on_unknown_artifact(client, pro_user):
    r = client.post("/v1/translations", json=_body(artifact="audio"), headers=pro_user["headers"])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_artifact"


def test_413_on_oversized_group(client, pro_user):
    segs = [{"id": f"s{i}", "text": "x" * 19_000} for i in range(4)]
    r = client.post("/v1/translations", json=_body(segments=segs), headers=pro_user["headers"])
    assert r.status_code == 413


def test_401_without_auth(client):
    r = client.post("/v1/translations", json=_body())
    assert r.status_code in (401, 403)


# --- happy path against the mock provider ------------------------------------

def test_translation_round_trip_meters_and_caches(client, pro_user, mock_provider, tmp_db_path):
    """First call runs the model (mocked to return valid JSON), logs a
    call_type=translation usage row, and caches; second call is served
    from cache with zero additional model calls."""
    mock_provider.return_value.text = '[{"id":"s1","text":"hello"}]'
    r1 = client.post("/v1/translations", json=_body(), headers=pro_user["headers"])
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["segments"] == [{"id": "s1", "text": "hello"}]
    assert d1["cached"] is False and d1["engine_version"] == tr.ENGINE_VERSION
    calls_after_first = mock_provider.call_count

    r2 = client.post("/v1/translations", json=_body(), headers=pro_user["headers"])
    assert r2.status_code == 200
    assert r2.json()["cached"] is True
    assert mock_provider.call_count == calls_after_first  # no second model call

    import sqlite3
    conn = sqlite3.connect(tmp_db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM usage_log WHERE user_id=? AND call_type='translation'",
        (pro_user["user_id"],)).fetchone()[0]
    assert n == 1  # the cache hit logged nothing


def test_fenced_model_output_reaches_client_clean(client, pro_user, mock_provider):
    mock_provider.return_value.text = '```json\n[{"id":"s1","text":"hello"}]\n```'
    r = client.post("/v1/translations", json=_body(segments=[{"id": "s1", "text": "hola!"}]),
                    headers=pro_user["headers"])
    assert r.status_code == 200
    assert r.json()["segments"][0]["text"] == "hello"
    assert "```" not in r.text


# --- the `title` artifact (2026-08-31) ---------------------------------------
# The defect these pin: ARTIFACTS was a three-element tuple, so a translated
# meeting kept its English headline forever. The endpoint answered 422
# invalid_artifact for the one field the user actually looks at first.

def test_title_is_an_accepted_artifact_end_to_end(client, pro_user, mock_provider):
    """THE bug. Before the fix this returned 422 invalid_artifact, which is
    why three of four visible fields swapped language and the headline did
    not. Goes through the endpoint, not the tuple, because a tuple assert
    would still pass if the router validated against something else."""
    mock_provider.return_value.text = '[{"id":"t1","text":"Escalación de precios Q3"}]'
    r = client.post("/v1/translations",
                    json=_body(artifact="title", source_language="en", target_language="es",
                               segments=[{"id": "t1", "text": "Q3 Pricing Escalation"}]),
                    headers=pro_user["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["segments"] == [{"id": "t1", "text": "Escalación de precios Q3"}]


def test_title_prompt_carries_the_name_rule_and_the_no_dash_rule():
    """A title is a NAME with a display budget, so it needs rules the prose
    prompt does not carry, and it still needs the served no-dash rule."""
    p = tr.system_prompt("title")
    assert "TITLE OF ONE MEETING" in p     # it is a name, not a sentence
    assert "more generic" in p             # the way a translated title fails
    assert "60 characters" in p            # the card budget is stated
    assert "em dash" in p                  # prose rule still applies


def test_title_rules_do_not_leak_into_the_other_artifacts():
    """system_prompt branches; a wrong branch would hand transcript
    translation a 60-character budget and quietly truncate speech."""
    for other in ("transcript", "summary", "report"):
        assert "TITLE OF ONE MEETING" not in tr.system_prompt(other)
    # and the transcript carve-out is unchanged by the new branch
    assert "em dash" not in tr.system_prompt("transcript")


def test_over_budget_titles_flags_only_the_long_ones():
    budget = meeting_title.MAX_TITLE_CHARS
    got = tr.over_budget_titles([{"id": "short", "text": "ok"},
                                 {"id": "at", "text": "y" * budget},
                                 {"id": "over", "text": "x" * (budget + 1)}])
    assert got == ["over"]          # strictly greater than, not >=
    assert tr.over_budget_titles([]) == []


def test_an_over_budget_title_still_reaches_the_client(client, pro_user, mock_provider):
    """Non-blocking BY DESIGN: the fallback we would drop back to is the
    English title on a Spanish card, the exact defect this artifact
    removes. If this ever 4xxs or truncates, the feature has regressed
    into the thing it replaced."""
    long_es = "R" + "a" * meeting_title.MAX_TITLE_CHARS
    assert len(long_es) > meeting_title.MAX_TITLE_CHARS
    mock_provider.return_value.text = json.dumps([{"id": "t1", "text": long_es}])
    r = client.post("/v1/translations",
                    json=_body(artifact="title", segments=[{"id": "t1", "text": "Pricing"}]),
                    headers=pro_user["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["segments"][0]["text"] == long_es   # whole, not truncated


def test_the_budget_follows_meeting_title_and_is_not_a_second_copy(monkeypatch):
    """Two copies of 60 would drift the first time meeting_title moved, and
    equality CANNOT catch that: a duplicated literal compares equal, and
    `is` compares TRUE too, because CPython interns small ints. The only
    instrument that separates a live link from a copy is MOVING the budget
    and seeing whether the translation side follows."""
    monkeypatch.setattr(meeting_title, "MAX_TITLE_CHARS", 5)
    # 8 characters: over a budget of 5, under the real 60. A copy of 60
    # still living in translations.py flags nothing here.
    assert tr.over_budget_titles([{"id": "t", "text": "12345678"}]) == ["t"]
