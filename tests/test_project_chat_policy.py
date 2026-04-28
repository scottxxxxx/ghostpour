"""Unit tests for the Project Chat verdict resolver.

Parametrized over Scott's full state matrix — each row of the spec table
becomes one test case. Any change to resolve_project_chat_verdict must
keep these passing.
"""

import pytest

from app.services.project_chat_policy import (
    resolve_project_chat_verdict,
    render_cta_text,
)

# State table verbatim from docs/wire-contracts/project-chat.md.
# Tuple shape: (logged_in, tier, has_quota, gp_chat_flag, selected_model, expected_verdict, expected_cta_kind)
STATE_MATRIX = [
    # gp_chat_flag = "all" — anyone, routes by selected_model
    (True,  "free", True,  "all",       "ssai",     "send_to_gp",         None),
    (True,  "free", False, "all",       "ssai",     "send_to_gp",         None),
    (True,  "plus", True,  "all",       "ssai",     "send_to_gp",         None),
    (True,  "pro",  True,  "all",       "ssai",     "send_to_gp",         None),
    (True,  "free", True,  "all",       "external", "send_to_user_model", None),
    (True,  "free", False, "all",       "external", "send_to_user_model", None),
    (True,  "plus", True,  "all",       "external", "send_to_user_model", None),
    (True,  "pro",  True,  "all",       "external", "send_to_user_model", None),
    (False, None,   True,  "all",       "external", "send_to_user_model", None),

    # gp_chat_flag = "ssai" — login required, GP overrides user model
    (True,  "free", True,  "ssai",      "ssai",     "send_to_gp",          None),
    (True,  "free", False, "ssai",      "ssai",     "send_to_gp",          None),
    (True,  "plus", True,  "ssai",      "ssai",     "send_to_gp",          None),
    (True,  "pro",  True,  "ssai",      "ssai",     "send_to_gp",          None),
    (True,  "free", True,  "ssai",      "external", "send_to_gp",          None),
    (True,  "free", False, "ssai",      "external", "send_to_gp_with_cta", "quota_exhausted"),
    (True,  "plus", True,  "ssai",      "external", "send_to_gp",          None),
    (True,  "pro",  True,  "ssai",      "external", "send_to_gp",          None),
    (False, None,   True,  "ssai",      "external", "login_required",      "login_required"),

    # gp_chat_flag = "logged_in" — login required, follows user model
    (True,  "free", True,  "logged_in", "ssai",     "send_to_gp",          None),
    (True,  "free", False, "logged_in", "ssai",     "send_to_gp",          None),
    (True,  "plus", True,  "logged_in", "ssai",     "send_to_gp",          None),
    (True,  "pro",  True,  "logged_in", "ssai",     "send_to_gp",          None),
    (True,  "free", True,  "logged_in", "external", "send_to_user_model",  None),
    (True,  "free", False, "logged_in", "external", "send_to_user_model",  None),
    (True,  "plus", True,  "logged_in", "external", "send_to_user_model",  None),
    (True,  "pro",  True,  "logged_in", "external", "send_to_user_model",  None),
    (False, None,   True,  "logged_in", "external", "login_required",      "login_required"),

    # gp_chat_flag = "plus" — Plus/Pro free; Free always gets CTA wrap
    (True,  "free", True,  "plus",      "ssai",     "send_to_gp_with_cta", "quota_remaining"),
    (True,  "free", False, "plus",      "ssai",     "send_to_gp_with_cta", "quota_exhausted"),
    (True,  "plus", True,  "plus",      "ssai",     "send_to_gp",          None),
    (True,  "pro",  True,  "plus",      "ssai",     "send_to_gp",          None),
    (True,  "free", True,  "plus",      "external", "send_to_gp_with_cta", "quota_remaining"),
    (True,  "free", False, "plus",      "external", "send_to_gp_with_cta", "quota_exhausted"),
    (True,  "plus", True,  "plus",      "external", "send_to_user_model",  None),
    (True,  "pro",  True,  "plus",      "external", "send_to_user_model",  None),
    (False, None,   True,  "plus",      "external", "login_required",      "login_required"),
]


@pytest.mark.parametrize(
    "logged_in,tier,has_quota,flag,model,expected_verdict,expected_cta",
    STATE_MATRIX,
)
def test_state_matrix(logged_in, tier, has_quota, flag, model, expected_verdict, expected_cta):
    result = resolve_project_chat_verdict(
        is_logged_in=logged_in,
        tier=tier,
        gp_chat_flag=flag,
        selected_model=model,
        has_quota=has_quota,
        free_quota_per_month=1,
    )
    assert result.verdict == expected_verdict, (
        f"verdict mismatch for {logged_in=} {tier=} {has_quota=} {flag=} {model=}: "
        f"got {result.verdict}, expected {expected_verdict}"
    )
    assert result.cta_kind == expected_cta, (
        f"cta_kind mismatch for {logged_in=} {tier=} {has_quota=} {flag=} {model=}: "
        f"got {result.cta_kind}, expected {expected_cta}"
    )


def test_unlimited_quota_returns_unlimited_cta_kind():
    """When free_quota_per_month=-1, CTA-bearing verdicts use 'unlimited' kind."""
    result = resolve_project_chat_verdict(
        is_logged_in=True,
        tier="free",
        gp_chat_flag="plus",
        selected_model="external",
        has_quota=True,  # always True under unlimited
        free_quota_per_month=-1,
    )
    assert result.verdict == "send_to_gp_with_cta"
    assert result.cta_kind == "unlimited"


def test_zero_quota_per_month_treats_first_send_as_exhausted():
    """When free_quota_per_month=0, every Free send is quota_exhausted."""
    result = resolve_project_chat_verdict(
        is_logged_in=True,
        tier="free",
        gp_chat_flag="plus",
        selected_model="ssai",
        has_quota=False,  # 0 quota means no remaining from the start
        free_quota_per_month=0,
    )
    assert result.verdict == "send_to_gp_with_cta"
    assert result.cta_kind == "quota_exhausted"


def test_render_cta_text_substitutes_placeholders():
    cta_strings = {
        "quota_remaining": "You have {remaining} of {total} free uses.",
        "quota_exhausted": "You've used your {total} free uses.",
        "unlimited": "No limit.",
    }
    assert (
        render_cta_text("quota_remaining", cta_strings, remaining=2, total=3)
        == "You have 2 of 3 free uses."
    )
    assert (
        render_cta_text("quota_exhausted", cta_strings, remaining=0, total=3)
        == "You've used your 3 free uses."
    )
    assert render_cta_text("unlimited", cta_strings) == "No limit."


def test_render_cta_text_missing_template_returns_empty():
    """Missing template key returns empty string (defensive — UI should handle)."""
    assert render_cta_text("quota_remaining", {}) == ""
