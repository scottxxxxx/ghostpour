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


def test_plain_offer_shape_identical_across_surfaces(client, free_user, mock_provider):
    _enable_confirmed_generation(client)
    ask = "Can you make me a well formatted excel doc of the action items"
    pc = _offer(client, free_user, "ProjectChat", False, ask)
    mc = _offer(client, free_user, "PostMeetingChat", True, ask)
    _assert_parity(pc, mc, template_id=None)


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
    assert mc["feature_state"]["cta"]["details"]["expected_seconds"] == 45
