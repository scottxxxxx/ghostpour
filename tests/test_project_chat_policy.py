"""Unit tests for the Project Chat verdict resolver.

Post-Slice 5 routing-only matrix: `send_to_gp_with_cta` and quota CTA
kinds were removed; the budget gate handles Free-tier blocking. Any
change to resolve_project_chat_verdict must keep these passing.
"""

import pytest

from app.services.project_chat_policy import resolve_project_chat_verdict

# Tuple shape: (logged_in, tier, gp_chat_flag, selected_model, expected_verdict, expected_cta_kind)
STATE_MATRIX = [
    # gp_chat_flag = "all" — anyone, routes by selected_model
    (True,  "free", "all",       "ssai",     "send_to_gp",         None),
    (True,  "plus", "all",       "ssai",     "send_to_gp",         None),
    (True,  "pro",  "all",       "ssai",     "send_to_gp",         None),
    (True,  "free", "all",       "external", "send_to_user_model", None),
    (True,  "plus", "all",       "external", "send_to_user_model", None),
    (True,  "pro",  "all",       "external", "send_to_user_model", None),
    (False, None,   "all",       "external", "send_to_user_model", None),

    # gp_chat_flag = "ssai" — login required, GP overrides user model
    (True,  "free", "ssai",      "ssai",     "send_to_gp",          None),
    (True,  "plus", "ssai",      "ssai",     "send_to_gp",          None),
    (True,  "pro",  "ssai",      "ssai",     "send_to_gp",          None),
    (True,  "free", "ssai",      "external", "send_to_gp",          None),
    (True,  "plus", "ssai",      "external", "send_to_gp",          None),
    (True,  "pro",  "ssai",      "external", "send_to_gp",          None),
    (False, None,   "ssai",      "external", "login_required",      "login_required"),

    # gp_chat_flag = "logged_in" — login required, follows user model
    (True,  "free", "logged_in", "ssai",     "send_to_gp",          None),
    (True,  "plus", "logged_in", "ssai",     "send_to_gp",          None),
    (True,  "pro",  "logged_in", "ssai",     "send_to_gp",          None),
    (True,  "free", "logged_in", "external", "send_to_user_model",  None),
    (True,  "plus", "logged_in", "external", "send_to_user_model",  None),
    (True,  "pro",  "logged_in", "external", "send_to_user_model",  None),
    (False, None,   "logged_in", "external", "login_required",      "login_required"),

    # gp_chat_flag = "ssai_free_only" — hybrid: ssai for Free, logged_in for paid
    (True,  "free", "ssai_free_only", "ssai",     "send_to_gp",         None),
    (True,  "plus", "ssai_free_only", "ssai",     "send_to_gp",         None),
    (True,  "pro",  "ssai_free_only", "ssai",     "send_to_gp",         None),
    (True,  "free", "ssai_free_only", "external", "send_to_gp",         None),
    (True,  "plus", "ssai_free_only", "external", "send_to_user_model", None),
    (True,  "pro",  "ssai_free_only", "external", "send_to_user_model", None),
    (False, None,   "ssai_free_only", "external", "login_required",     "login_required"),

    # gp_chat_flag = "plus" — Plus/Pro free; Free still routes to GP (budget gate blocks)
    (True,  "free", "plus",      "ssai",     "send_to_gp",          None),
    (True,  "free", "plus",      "external", "send_to_gp",          None),
    (True,  "plus", "plus",      "ssai",     "send_to_gp",          None),
    (True,  "pro",  "plus",      "ssai",     "send_to_gp",          None),
    (True,  "plus", "plus",      "external", "send_to_user_model",  None),
    (True,  "pro",  "plus",      "external", "send_to_user_model",  None),
    (False, None,   "plus",      "external", "login_required",      "login_required"),
]


@pytest.mark.parametrize(
    "logged_in,tier,flag,model,expected_verdict,expected_cta",
    STATE_MATRIX,
)
def test_state_matrix(logged_in, tier, flag, model, expected_verdict, expected_cta):
    result = resolve_project_chat_verdict(
        is_logged_in=logged_in,
        tier=tier,
        gp_chat_flag=flag,
        selected_model=model,
    )
    assert result.verdict == expected_verdict, (
        f"verdict mismatch for {logged_in=} {tier=} {flag=} {model=}: "
        f"got {result.verdict}, expected {expected_verdict}"
    )
    assert result.cta_kind == expected_cta, (
        f"cta_kind mismatch for {logged_in=} {tier=} {flag=} {model=}: "
        f"got {result.cta_kind}, expected {expected_cta}"
    )
