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


# --- phase 2: memory CREATION is namespaced too -----------------------
#
# The routes are the read/write surface, but capture() is what creates
# memory, so an unnamespaced write there is what would actually commingle
# two apps for a shared user. Both halves have to be right or a second app
# writes to one subject and reads from another, which fails as "my memory
# is empty" rather than as an error.


import inspect

from app.services import context_quilt as cq


def test_capture_and_recall_both_accept_an_app_id():
    for fn in (cq.capture, cq.recall):
        assert "app_id" in inspect.signature(fn).parameters, fn.__name__


def test_both_send_the_subject_and_not_the_raw_user_id():
    src = inspect.getsource(cq)
    # The outbound body key is "user_id" on CQ's side; what we put IN it
    # must be the namespaced subject.
    assert src.count('"user_id": subject_for(app_id, user_id)') == 2
    assert '"user_id": user_id,\n        "text"' not in src
    assert '"user_id": user_id,\n        "interaction_type"' not in src


def _cq_call_sites() -> list[tuple[str, int]]:
    """Every REAL cq.capture(/cq.recall( call in the app, found by parsing
    rather than by grepping.

    The text version of this flagged four sites that were prose: three
    docstring mentions in memory_capture_policy and one comment in the
    hook. A scan that cries wolf on comments is a scan people learn to
    silence, so it parses the AST and only sees calls."""
    import ast as _ast
    import pathlib
    out = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = _ast.parse(path.read_text())
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, _ast.Attribute)
                    and fn.attr in ("capture", "recall")
                    and isinstance(fn.value, _ast.Name)
                    and fn.value.id == "cq"):
                continue
            if not any(kw.arg == "app_id" for kw in node.keywords):
                out.append((str(path), node.lineno))
    return out


def test_every_call_site_passes_an_app_id():
    """The guard, same shape as the outbound-path scan. A new capture site
    that forgets app_id writes to the unnamespaced subject and NOTHING
    errors: for SS that is even correct, so it would ship, and only a
    second app would find its memory missing."""
    missing = _cq_call_sites()
    assert not missing, "cq.capture/recall without app_id at: " + ", ".join(
        f"{p}:{ln}" for p, ln in missing)


def test_the_hook_carries_app_id_through_its_interface():
    """The hook is where most captures originate and it had no access to
    the calling app at all, which is why creation went unnamespaced while
    the routes were fixed."""
    from app.services.features.context_quilt_hook import ContextQuiltHook
    for name in ("before_llm", "after_llm"):
        params = inspect.signature(getattr(ContextQuiltHook, name)).parameters
        assert "app_id" in params, name
