"""Force-upgrade gate: default-off + fail-open safety, blocklist, and the 426
enforcement middleware across the LLM/CQ/config paths."""

from app.services import version_gate

APPS = {"apps": {"shouldersurf": {"bundle_id": "com.x.SS"}, "nobundle": {}}}


def _reg(*, blocking=False, floor="1.5", blocked=None, upgrade="https://up"):
    return {"com.x.SS": {"platforms": {"ios": {
        "min_supported_version": floor,
        "min_supported_blocking": blocking,
        "blocked_versions": blocked or [],
        "latest": {"upgrade_url": upgrade},
    }}}}


# --- evaluate(): default-off + the blocking path ---------------------------

def test_default_off_never_blocks_even_below_floor():
    assert version_gate.evaluate(_reg(blocking=False), APPS, "shouldersurf", "1.0", "100") is None


def test_blocking_on_below_floor_blocks_with_contract_body():
    b = version_gate.evaluate(_reg(blocking=True, floor="1.5"), APPS, "shouldersurf", "1.4", "500")
    assert b == {
        "code": "upgrade_required",
        "message": version_gate.DEFAULT_MESSAGE,
        "upgrade_url": "https://up",
        "min_supported_version": "1.5",
    }


def test_blocking_on_at_or_above_floor_allows():
    assert version_gate.evaluate(_reg(blocking=True, floor="1.5"), APPS, "shouldersurf", "1.5", "5") is None
    assert version_gate.evaluate(_reg(blocking=True, floor="1.5"), APPS, "shouldersurf", "1.6", "5") is None


# --- surgical blocklist (works even with the flag off / above the floor) ----

def test_blocklist_by_version_blocks_above_floor_flag_off():
    b = version_gate.evaluate(_reg(blocking=False, floor="1.0", blocked=["1.7"]), APPS, "shouldersurf", "1.7", "999")
    assert b is not None and b["code"] == "upgrade_required"


def test_blocklist_by_build_number():
    b = version_gate.evaluate(_reg(blocking=False, blocked=["666"]), APPS, "shouldersurf", "2.0", "666")
    assert b is not None


# --- FAIL OPEN: every ambiguous case must NOT block ------------------------

def test_fail_open_missing_version():
    assert version_gate.evaluate(_reg(blocking=True), APPS, "shouldersurf", None, None) is None


def test_fail_open_unparseable_version():
    assert version_gate.evaluate(_reg(blocking=True), APPS, "shouldersurf", "garbage", "1") is None


def test_fail_open_unknown_app():
    assert version_gate.evaluate(_reg(blocking=True), APPS, "ghostpour", "1.0", "1") is None


def test_fail_open_app_without_bundle_id():
    assert version_gate.evaluate(_reg(blocking=True), APPS, "nobundle", "1.0", "1") is None


def test_fail_open_no_floor_config_for_bundle():
    assert version_gate.evaluate({}, APPS, "shouldersurf", "1.0", "1") is None


def test_semver_parse():
    assert version_gate._semver("1.14") == (1, 14, 0)
    assert version_gate._semver("1.2.3") == (1, 2, 3)
    assert version_gate._semver("garbage") is None
    assert version_gate._semver("") is None


# --- middleware integration (enforced paths, exemptions, fail-open) --------

def _block_state(client, monkeypatch, floor="1.5"):
    reg = {"com.shouldersurf.ShoulderSurf": {"platforms": {"ios": {
        "min_supported_version": floor, "min_supported_blocking": True,
        "latest": {"upgrade_url": "https://up"}}}}}
    monkeypatch.setattr(client.app.state, "app_versions", reg, raising=False)


def test_middleware_426s_chat_below_floor(client, monkeypatch):
    _block_state(client, monkeypatch)
    # The gate runs before auth/handler, so a below-floor build is 426'd outright.
    r = client.post("/v1/chat", json={}, headers={
        "X-App-ID": "shouldersurf", "X-App-Version": "1.4", "X-App-Build": "500"})
    assert r.status_code == 426
    assert r.json()["code"] == "upgrade_required"
    assert r.json()["upgrade_url"] == "https://up"


def test_middleware_exempts_version_endpoint(client, monkeypatch):
    # A blocked client must still reach /v1/app/version to learn how to recover.
    _block_state(client, monkeypatch)
    r = client.get("/v1/app/version", headers={
        "X-App-Bundle-Id": "com.shouldersurf.ShoulderSurf",
        "X-App-ID": "shouldersurf", "X-App-Version": "1.4"})
    assert r.status_code != 426


def test_middleware_fails_open_without_version_header(client, monkeypatch):
    # No X-App-Version -> never block (old builds predate the header).
    _block_state(client, monkeypatch)
    r = client.post("/v1/chat", json={}, headers={"X-App-ID": "shouldersurf"})
    assert r.status_code != 426
