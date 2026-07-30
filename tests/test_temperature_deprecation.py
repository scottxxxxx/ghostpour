"""`temperature` is a 400 on Opus 4.7 and everything newer.

Anthropic deprecated the parameter mid-family: the older models take it,
and from Opus 4.7 on ANY value returns
`400 "temperature is deprecated for this model"`. Several of our lanes pin
a low temperature in CODE for reproducibility (meeting report 0.2, template
extraction 0.2, the Haiku classifiers 0.0) while the model underneath them
is a hot routing dial, so the two can drift apart with no deploy.

They did, live: Pro routing moved to Sonnet 5 on 2026-07-29 and every Pro
document build failed at the provider from then until this fix. The client
saw an empty 200, not an error, which is why nothing alerted.

The boundary below was probed against the live API on 2026-07-30 rather
than read off a table.
"""

import pytest

from app.models.chat import ChatRequest
from app.services.providers.reasoning import anthropic_accepts_temperature

REJECTS = [
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
]

ACCEPTS = [
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
]


def _body(**kw):
    from tests.test_anthropic_cache_split import _adapter

    req = ChatRequest(
        provider="anthropic", system_prompt="base", user_content="hi", **kw
    )
    body, _ = _adapter()._build_body(req)
    return body


# --- the capability ------------------------------------------------------

@pytest.mark.parametrize("model", REJECTS)
def test_deprecated_models_do_not_accept_temperature(model):
    assert anthropic_accepts_temperature(model) is False


@pytest.mark.parametrize("model", ACCEPTS)
def test_older_models_still_accept_temperature(model):
    assert anthropic_accepts_temperature(model) is True


def test_unknown_model_keeps_todays_behavior():
    # Deny-list, not allow-list: a model we haven't probed keeps sending, so
    # a future deprecation fails loudly instead of silently dropping a pin.
    assert anthropic_accepts_temperature("claude-something-9") is True


# --- the outgoing body ---------------------------------------------------

@pytest.mark.parametrize("model", REJECTS)
def test_body_omits_temperature_on_deprecated_models(model):
    assert "temperature" not in _body(model=model, temperature=0.2)


@pytest.mark.parametrize("model", ACCEPTS)
def test_body_keeps_temperature_where_it_is_supported(model):
    assert _body(model=model, temperature=0.2)["temperature"] == 0.2


def test_no_temperature_requested_stays_absent():
    assert "temperature" not in _body(model="claude-sonnet-4-6")


# --- the lanes that pin one in code -------------------------------------

def test_template_extraction_temperature_survives_any_routing_target():
    """The lane that actually broke.

    chat.py pins 0.2 on the template lane while the model comes from the
    routing dial, so this asserts the pairing the dial can produce today:
    every model in the SS routing catalog must build a valid body.
    """
    for model in REJECTS + ACCEPTS:
        body = _body(model=model, temperature=0.2, max_tokens=12000)
        assert body["max_tokens"] == 12000
        if model in REJECTS:
            assert "temperature" not in body
        else:
            assert body["temperature"] == 0.2
