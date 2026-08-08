"""Per-app CQ subjects (2026-08-08).

CQ decided each app gets its own subject space (doc 18). The problem that
makes it urgent is ours: we send the bare GP user id as the subject with no
app scoping, and SS and TR share ONE users row because Apple's apple_sub is
issued per developer TEAM rather than per app. Two production users are
active in both apps today, so the isolation CQ decided is one real TR
capture away from being violated by our identity minting. It holds right
now only because TR has never written real memory.

Two properties do the work:

ShoulderSurf keeps the bare user id forever. It has real data and a live
install base, it was the only writer, and moving its subject would orphan
every patch that exists for no benefit.

Clients never learn about this. The namespace is applied at the outbound
CQ boundary only, so the path a client sends still carries the plain GP
user id and the ownership guard still compares against it unchanged.
"""

import re

import pytest

from app.services.cq_subject import subject_for

UID = "fa4d903c-24c0-45d5-9fdb-b5496e32501b"


# --- the mapping ------------------------------------------------------


def test_shouldersurf_keeps_the_bare_id():
    """The one that must never change. Every subject in production today
    was written under it."""
    assert subject_for("shouldersurf", UID) == UID


def test_another_app_is_namespaced():
    assert subject_for("techrehearsal", UID) == f"techrehearsal:{UID}"


def test_the_namespace_lives_inside_the_id():
    """CQ builds 'user:' || $1 in about fourteen SQL sites and treats
    everything after the prefix as opaque. A namespace that changed the
    PREFIX would break all of them; one inside the id costs nothing."""
    assert not subject_for("techrehearsal", UID).startswith("user:")
    assert subject_for("techrehearsal", UID).endswith(UID)


@pytest.mark.parametrize("app", [None, "", "unknown", "  "])
def test_unattributed_traffic_stays_in_the_existing_space(app):
    """Deliberate. Unattributed calls have always written to the
    unnamespaced subject, so treating them as a new namespace would strand
    memory that already exists. A request we cannot attribute is far more
    likely to be an old SS build than a new app."""
    assert subject_for(app, UID) == UID


def test_the_app_id_is_matched_case_insensitively():
    """X-App-ID is a client-supplied header. A capitalised one must not
    mint a second namespace for the same app."""
    assert subject_for("SHOULDERSURF", UID) == UID
    assert subject_for("TechRehearsal", UID) == f"techrehearsal:{UID}"


def test_a_user_id_containing_a_colon_still_round_trips():
    """CQ confirmed everything after 'user:' is opaque and may contain
    colons, so we do not have to escape and must not mangle."""
    weird = "already:has:colons"
    assert subject_for("techrehearsal", weird) == f"techrehearsal:{weird}"


# --- no route may forget it -------------------------------------------


SRC = open("app/routers/cq_proxy.py").read()


def test_no_outbound_path_interpolates_a_raw_user_id():
    """The guard that matters more than any single route. A new route that
    copied an old one would write to the wrong subject and nothing would
    error: SS traffic would look correct, and only a second app would
    notice, by finding its own memory missing.

    So the subject call is inlined at every call site rather than assigned
    to a local, and this scan fails if a raw user_id comes back."""
    offenders = re.findall(r'f"/v1/[^"]*\{user_id\}[^"]*"', SRC)
    assert not offenders, (
        "outbound CQ paths must use _subj(request, user_id), not user_id: "
        + ", ".join(offenders))


def test_every_outbound_path_that_names_a_subject_uses_the_helper():
    assert SRC.count("_subj(request, user_id)") >= 22


def test_the_ownership_guard_still_compares_the_plain_user_id():
    """The namespace must NOT leak into the guard. The path carries the GP
    user id, so `user.id != user_id` stays correct; comparing against the
    namespaced form would 403 every request with a message about accessing
    another user's quilt, which is both wrong and misleading."""
    assert "if user.id != user_id:" in SRC
    assert "user.id != _subj" not in SRC
    assert "_subj(request, user.id)" not in SRC
