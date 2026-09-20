"""LIVE, costs about eleven cents, NOT collected by a normal run.

    .venv/bin/python -m pytest tests/live_n400_warmup_capture.py -q -s -p no:cacheprovider

The one thing no mock can show: that Anthropic READS, on a warm up, the
cache entry a real interviewer turn wrote. One real turn through the real
adapter, then a warm up with the "already warm" shortcut defeated so it has
to ask Anthropic. A read of about 24k tokens and a write of 0 is the proof
that the two requests share a prefix. Also writes the captured warm up
request and response for the N-400 client to build against.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.services import n400_warmup as w
from tests.test_n400_sentence_stream_route import _metadata

OUT = Path(__file__).resolve().parent.parent / "qa" / "fixtures" / "n400-warmup"


def test_live(app_env, tmp_db_path):
    from tests.conftest import _insert_user, _jwt_token, chat_request
    from app.main import app
    hdr = {"Authorization": f"Bearer {_jwt_token('live-warmup-user')}", "X-App-ID": "n400"}
    warm_body = chat_request(system_prompt="", user_content="[warm up]",
                             metadata={"call_type": w.WARMUP_CALL_TYPE, "locale": "en",
                                       "form_code": "N-400", "jurisdiction": "US-TX"})
    for k in ("provider", "model"):
        warm_body[k] = "auto"
    with TestClient(app, raise_server_exceptions=False) as c:
        _insert_user(tmp_db_path, user_id="live-warmup-user", tier="pro", monthly_limit=5.10)
        cold = c.post("/v1/chat", headers=hdr, json=warm_body)
        print("\n1. warm up before any real turn :", cold.status_code, cold.json())

        turn = c.post("/v1/chat", headers=hdr, json=chat_request(
            system_prompt="", user_content="on my own, about six years",
            metadata=_metadata(turn_id="t_001")))
        u = turn.json().get("usage") or {}
        print("2. real turn                     :", turn.status_code,
              "write", u.get("cache_creation_input_tokens"), "by_ttl", u.get("cache_creation"),
              "read", u.get("cache_read_input_tokens"))

        short = c.post("/v1/chat", headers=hdr, json=warm_body)
        print("3. warm up right after it        :", short.status_code, short.json())

        w._TOUCHED.clear()            # defeat the shortcut: make it ask Anthropic
        asked = c.post("/v1/chat", headers=hdr, json=warm_body)
        print("4. warm up forced to ask         :", asked.status_code, asked.json())

        again = c.post("/v1/chat", headers=hdr, json=warm_body)
        print("5. a second one inside the window:", again.status_code, again.json())

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "request.json").write_text(json.dumps(warm_body, indent=1))
    for name, r in (("response-no_prefix_yet", cold), ("response-already_warm-no-provider-call", short),
                    ("response-asked-anthropic", asked), ("response-skipped_recent", again)):
        (OUT / f"{name}.json").write_text(json.dumps(
            {"status": r.status_code, "content_type": r.headers.get("content-type"), "body": r.json()}, indent=1))
    assert asked.json()["cache_read_tokens"] > 1000 and asked.json()["cache_write_tokens"] == 0, \
        "the warm up did not read the entry the real turn wrote: the prefixes differ"
