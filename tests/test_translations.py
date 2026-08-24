"""POST /v1/translations — contract behaviors that do not need a model:
validation, cache idempotency, id round-trip parsing, retention."""
import json

import pytest

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
