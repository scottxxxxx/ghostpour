"""Offer-envelope surface parity (SS contract).

Born from the 2026-07-13 meeting-chat echo incident: SS's client arms the
offer reply exclusively from feature_state.cta.details, so the envelope
must be shape-identical on every chat surface — project chat's forced
non-streaming lane and meeting chat's single-JSON-on-the-SSE-request lane
— for plain AND template-intercepted offers, with offer_id always inside
cta.details.
"""


def _enable_confirmed_generation(client):
    docs = client.app.state.remote_configs["client-config"].setdefault("documents", {})
    docs["generation"] = {"enabled": True, "min_tier": "free",
                          "confirmation": {"enabled": True, "expected_seconds": 150}}


def _offer(client, free_user, prompt_mode, stream, user_content):
    from tests.conftest import chat_request
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode=prompt_mode,
        call_type="meeting_chat" if prompt_mode == "PostMeetingChat" else "query",
        stream=stream,
        user_content=user_content,
    ), headers=free_user["headers"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    return r.json()


def _assert_parity(pc_body, mc_body, template_id):
    pc_cta = pc_body["feature_state"]["cta"]
    mc_cta = mc_body["feature_state"]["cta"]
    assert sorted(pc_body.keys()) == sorted(mc_body.keys())
    assert sorted(pc_cta.keys()) == sorted(mc_cta.keys())
    assert "offer_id" in pc_cta["details"]
    assert "offer_id" in mc_cta["details"]
    assert pc_cta["details"].get("template_id") == template_id
    assert mc_cta["details"].get("template_id") == template_id
    pc_d = {k: v for k, v in pc_cta["details"].items() if k != "offer_id"}
    mc_d = {k: v for k, v in mc_cta["details"].items() if k != "offer_id"}
    assert pc_d == mc_d, f"details shapes differ: {pc_d} vs {mc_d}"


def test_plain_offer_shape_identical_across_surfaces(client, free_user, mock_provider,
                                                     monkeypatch):
    # Since the explicit-command fast path (2026-07-28), a deterministic
    # non-template ask arms generation directly, so the plain OFFER
    # envelope now belongs to classifier-judged soft phrasings. Patch the
    # classifier to the file_request verdict and use a soft ask.
    _enable_confirmed_generation(client)
    from app.services import document_generation as dg

    async def _judged(provider_router, user_content, on_subcall=None):
        return {"file_request": True, "format": "xlsx", "gist": ""}
    monkeypatch.setattr(dg, "classify_generation_intent", _judged)
    ask = "It would be great to have the action items as a spreadsheet"
    pc = _offer(client, free_user, "ProjectChat", False, ask)
    mc = _offer(client, free_user, "PostMeetingChat", True, ask)
    _assert_parity(pc, mc, template_id=None)


def test_explicit_ask_skips_the_offer_and_arms(client, free_user, mock_provider):
    """Fast path (Scott 2026-07-28: 'Put it in a word document' drew a
    second 'Want the file?'): a deterministic explicit ask with no
    template match must NOT return an offer envelope — generation arms on
    that very turn. Template asks keep their offer (covered by the
    template parity test)."""
    _enable_confirmed_generation(client)
    from tests.conftest import chat_request
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query", stream=False,
        user_content="Put it in a word document",
    ), headers=free_user["headers"])
    assert r.status_code == 200, r.text
    # Armed generation rides the SSE lane; the redundant-confirm failure
    # mode would be a JSON offer envelope with an offer_id instead.
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: generation_result" in r.text
    assert "offer_id" not in r.text


def test_template_offer_shape_identical_across_surfaces(client, free_user, mock_provider):
    # mirrors the live incident: 'gantt' arrives via the assembled history,
    # the explicit-verb catch fires on the question portion, and the
    # template branch mutates the envelope on both lanes alike
    _enable_confirmed_generation(client)
    ask = (
        "Previous conversation in this chat: "
        "Q: Build a nice Gantt chart from this meeting showing who owns "
        "what and the blockers "
        "A: # Project Gantt Chart. Structured view of owners and blockers.\n\n"
        "User question: Can you make it into a well formatted excel doc "
        "like smart sheets"
    )
    pc = _offer(client, free_user, "ProjectChat", False, ask)
    mc = _offer(client, free_user, "PostMeetingChat", True, ask)
    _assert_parity(pc, mc, template_id="gantt_smartsheet")
    assert mc["feature_state"]["cta"]["details"]["expected_seconds"] == 25
