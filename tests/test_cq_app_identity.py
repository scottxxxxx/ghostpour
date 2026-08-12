"""Per-app CQ identity dispatch — a second CQ app (Tech Rehearsal) rides GP
under its own CQ app_id so CQ loads the right schema."""

def test_cq_identity_dispatch():
    from app.services import context_quilt as cq
    from app.config import get_settings
    s = get_settings()
    # default (no app id) -> the default ShoulderSurf/ghostpour CQ identity
    assert cq._cq_identity(None)[0] == s.cq_app_id
    # techrehearsal -> its own CQ app_id from apps.yml
    app_tr, _secret = cq._cq_identity("techrehearsal")
    assert app_tr == "bc6efb4c-2854-49c0-9e8d-437c99610588"
    # case-insensitive
    assert cq._cq_identity("TechRehearsal")[0] == "bc6efb4c-2854-49c0-9e8d-437c99610588"
    # unknown / unregistered app -> falls back to default identity
    assert cq._cq_identity("nope")[0] == s.cq_app_id
    # shouldersurf -> its own CQ app_id (moved off the shared default)
    assert cq._cq_identity("shouldersurf")[0] == "886a527b-1d8f-46e1-aadc-d4b05e16256e"
    # a header-less client is ShoulderSurf by definition, but identity dispatch
    # keys on the resolved app id, so None still rides the default until the
    # default itself is retired
    assert cq._cq_identity(None)[0] == s.cq_app_id
