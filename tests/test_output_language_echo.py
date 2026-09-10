"""`output_language`: what GP DIRECTED, echoed on every /v1/chat response.

SS's translation redesign (2026-09-09) regenerates summaries, reports and
answers per language instead of translating them, and stamps every
generated artifact with the language it is in so the client knows which
rendition still owes. Until GP echoed it the client stamped what it SENT,
which is wrong in exactly one case: English. `locale: en` sends NO
directive, and the served recipe says "write in the language the
participants speak in the transcript", so an English phone with a Spanish
meeting gets a Spanish summary the client would have stamped "en".

So the contract is: the BCP-47 primary subtag of the directive GP sent, or
None when it sent none. None is never reported as "en", because GP does not
know what the model wrote in that case. The same key rides the streaming
"done" event.
"""

from app.services.locale_injection import output_language_subtag
from tests.conftest import chat_request


def _headers(user, **extra):
    return {**user["headers"], "X-App-ID": "shouldersurf", **extra}


# --- the rule, at the unit -------------------------------------------------

def test_subtag_of_a_directed_locale():
    assert output_language_subtag("es") == "es"
    assert output_language_subtag("pt-BR") == "pt"
    assert output_language_subtag("es_MX") == "es"


def test_none_when_no_directive_was_sent_and_never_en_by_inference():
    assert output_language_subtag(None) is None
    assert output_language_subtag("") is None


# --- the JSON body ----------------------------------------------------------

def test_a_spanish_locale_is_echoed_on_the_body(client, free_user, mock_provider):
    r = client.post("/v1/chat",
                    json=chat_request(call_type="summary", locale="es"),
                    headers=_headers(free_user))
    assert r.status_code == 200, r.text
    assert r.json()["output_language"] == "es"
    assert r.headers.get("X-Output-Locale") == "es", "the older header still rides"


def test_a_regional_locale_is_echoed_as_its_primary_subtag(client, free_user, mock_provider):
    r = client.post("/v1/chat",
                    json=chat_request(call_type="summary", locale="pt-BR"),
                    headers=_headers(free_user))
    assert r.status_code == 200, r.text
    assert r.json()["output_language"] == "pt"


def test_metadata_locale_is_honoured_per_call_over_the_device_language(
        client, free_user, mock_provider):
    """The regenerate case: a Spanish phone asking for one meeting in French."""
    r = client.post("/v1/chat",
                    json=chat_request(call_type="summary",
                                      metadata={"locale": "fr"}),
                    headers=_headers(free_user, **{"Accept-Language": "es-US"}))
    assert r.status_code == 200, r.text
    assert r.json()["output_language"] == "fr"


def test_english_is_directed_and_echoed_as_en(client, free_user, mock_provider):
    """Scott's ruling, 2026-09-10: the phone's language wins in every case,
    so `en` sends a directive like any other language and the echo says so.
    Before this, English was a no-op and the echo was null, which was the one
    case the client could not stamp correctly on its own."""
    r = client.post("/v1/chat",
                    json=chat_request(call_type="summary", locale="en"),
                    headers=_headers(free_user, **{"Accept-Language": "es-MX"}))
    assert r.status_code == 200, r.text
    assert r.json()["output_language"] == "en"
    assert r.headers.get("X-Output-Locale") == "en"


def test_no_locale_anywhere_sends_no_directive_and_echoes_null(client, free_user, mock_provider):
    """Null is reserved for a request that carried no locale at all: no
    locale field, no metadata.locale, no Accept-Language. GP sent nothing,
    so it says nothing; "en" here would be a status field that lies."""
    r = client.post("/v1/chat",
                    json=chat_request(call_type="summary"),
                    headers=_headers(free_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "output_language" in body, "the key is always present"
    assert body["output_language"] is None
    assert "X-Output-Locale" not in r.headers


def test_an_english_phone_sending_only_accept_language_is_directed_english(
        client, free_user, mock_provider):
    """Older builds send no locale field. Accept-Language en-US alone must
    still produce the English directive, or the ruling only holds for the
    newest build."""
    r = client.post("/v1/chat",
                    json=chat_request(call_type="summary"),
                    headers=_headers(free_user, **{"Accept-Language": "en-US,en;q=0.9"}))
    assert r.status_code == 200, r.text
    assert r.json()["output_language"] == "en"


def test_a_missing_locale_falls_back_to_accept_language(client, free_user, mock_provider):
    r = client.post("/v1/chat",
                    json=chat_request(call_type="summary"),
                    headers=_headers(free_user, **{"Accept-Language": "ja-JP,ja;q=0.9"}))
    assert r.status_code == 200, r.text
    assert r.json()["output_language"] == "ja"


# --- the streaming done event ------------------------------------------------

def _stream_done(client, free_user, monkeypatch, accept_language="en-US", **body):
    import json as _json

    from app.models.chat import ChatResponse

    def _fake_stream(provider_router, body, db, settings):
        async def _gen():
            yield {"text": "Hola."}
            yield {"done": True, "response": ChatResponse(
                text="Hola.", input_tokens=10, output_tokens=5,
                model="claude-haiku-4-5-20251001", provider="anthropic",
                usage={"input_tokens": 10, "output_tokens": 5})}
        return _gen()

    monkeypatch.setattr(
        "app.services.anthropic_or_fallback.route_stream_with_fallback",
        _fake_stream)
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="PostMeetingChat", call_type="query", stream=True,
        user_content="Current question: what was decided?", **body,
    ), headers=_headers(free_user, **({"Accept-Language": accept_language} if accept_language else {})))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [_json.loads(line[len("data: "):])
              for line in r.text.splitlines() if line.startswith("data: ")]
    done = [e for e in events if e.get("type") == "done"]
    assert len(done) == 1, "exactly one done event"
    return done[0]


def test_the_streaming_done_event_carries_the_directed_language(
        client, free_user, mock_provider, monkeypatch):
    done = _stream_done(client, free_user, monkeypatch, locale="es")
    assert done["output_language"] == "es"


def test_the_streaming_done_event_says_en_for_an_english_phone(
        client, free_user, mock_provider, monkeypatch):
    done = _stream_done(client, free_user, monkeypatch, locale="en")
    assert done["output_language"] == "en"


def test_the_streaming_done_event_says_null_when_no_locale_was_sent(
        client, free_user, mock_provider, monkeypatch):
    done = _stream_done(client, free_user, monkeypatch, accept_language=None)
    assert "output_language" in done
    assert done["output_language"] is None
