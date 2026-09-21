"""The `companion` config: where the iPhone's "Set up on my Mac" button goes.

ShoulderSurf, 2026-09-21: the URL and the decision to show the button were
compiled in, so the day the Mac App Store listing is approved would have
needed an iPhone release just to change a link. This document moves the
DESTINATION and nothing else. Whether Mac Companion exists at all stays
compiled into the build App Review sees, so the file must never grow a key
that could turn the feature on or off.

The client half (SS's) reads `channel` as a plain string and sends anything
it does not recognise to `webURL`. These tests pin the half GP owns: the
shape, the URL forms SS's allowlist will accept, and the existing config wire.
"""
import json
import re
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent / "config" / "remote" / "companion.json"
APP_STORE_URL = re.compile(r"^https://apps\.apple\.com/app/id\d+$")
# SS's client refuses any other host and falls back to its bundled snapshot.
CLIENT_ALLOWED_HOSTS = {"shouldersurf.com", "apps.apple.com"}


def _doc() -> dict:
    return json.loads(BUNDLE.read_text())


def _host(url: str) -> str:
    return url.split("//", 1)[1].split("/", 1)[0]


def test_shape_is_the_one_agreed_with_shouldersurf():
    d = _doc()
    assert {k for k in d if not k.startswith("_")} == {"version", "channel", "webURL", "appStoreURL"}
    assert isinstance(d["version"], int) and not isinstance(d["version"], bool) and d["version"] >= 1
    assert d["channel"] in ("web", "appStore")
    assert isinstance(d["webURL"], str)
    assert d["appStoreURL"] is None or isinstance(d["appStoreURL"], str)


def test_the_website_is_always_a_usable_fallback():
    # Every client sends an unrecognised channel to webURL, so webURL must be
    # present and good whatever the channel says.
    d = _doc()
    assert d["webURL"].startswith("https://") and _host(d["webURL"]) == "shouldersurf.com"


def test_an_app_store_url_is_the_country_free_slug_free_form():
    # A country segment forces a storefront; a slug changes when the listing
    # is renamed. SS's PromoCTARouter builds exactly this form from a bare id.
    assert APP_STORE_URL.match("https://apps.apple.com/app/id6745123456")
    for bad in ("https://apps.apple.com/us/app/id6745123456",
                "https://apps.apple.com/app/shoulder-surf/id6745123456",
                "http://apps.apple.com/app/id6745123456",
                "https://apps.apple.com/app/id"):
        assert not APP_STORE_URL.match(bad), bad
    url = _doc()["appStoreURL"]
    assert url is None or APP_STORE_URL.match(url), url


def test_the_app_store_channel_is_never_served_without_a_url():
    # A client on "appStore" with no URL falls back to the website, which is
    # safe, but it means the flip silently did nothing. Catch it here.
    d = _doc()
    if d["channel"] == "appStore":
        assert d["appStoreURL"], "channel says appStore and there is nowhere to send them"


def test_every_url_is_on_a_host_the_client_will_accept():
    d = _doc()
    for key in ("webURL", "appStoreURL"):
        if d[key]:
            assert _host(d[key]) in CLIENT_ALLOWED_HOSTS, (key, d[key])


def test_it_carries_no_feature_switch():
    # App Store guideline 2.3.1 is about turning a mode on after review. This
    # document moves a link. A key like these would make it a switch.
    banned = re.compile(r"(?i)enabled|disabled|show|hidden|visible|available|feature|kill|minimum|minbuild")
    assert not [k for k in _doc() if not k.startswith("_") and banned.search(k)]


def test_it_is_not_localized():
    # Two URLs and a channel have no locale, and a localized copy is a second
    # place for the flip to be forgotten.
    assert not list(BUNDLE.parent.glob("companion.*.json"))


def test_it_reaches_a_device_over_the_existing_config_wire(client):
    client.app.state.remote_configs = {"companion": _doc()}
    r = client.get("/v1/config/companion")
    assert r.status_code == 200
    body = r.json()
    # A full fetch is the DOCUMENT ITSELF. There is no "changed": true key on
    # it, whatever config.py's module docstring says; only the up to date
    # answer carries `changed`. I told ShoulderSurf otherwise from the
    # docstring, this test caught it, and they were corrected.
    assert "changed" not in body
    # Whatever the document says today, not the launch values: this test must
    # still pass on the day the bundle is edited to point at the App Store.
    assert body == _doc()
    assert r.headers["X-Config-Version"] == str(_doc()["version"])

    same = client.get("/v1/config/companion", headers={"X-Config-Version": str(_doc()["version"])})
    assert same.status_code == 200
    assert same.json() == {"changed": False, "version": _doc()["version"]}


def test_a_flip_is_a_new_version_the_phone_picks_up(client):
    flipped = {**_doc(), "version": _doc()["version"] + 1, "channel": "appStore",
               "appStoreURL": "https://apps.apple.com/app/id6745123456"}
    client.app.state.remote_configs = {"companion": flipped}
    r = client.get("/v1/config/companion", headers={"X-Config-Version": str(_doc()["version"])})
    assert "changed" not in r.json() and r.json()["channel"] == "appStore"
    assert r.json()["version"] == _doc()["version"] + 1
    assert APP_STORE_URL.match(r.json()["appStoreURL"])


def test_the_dashboard_says_what_it_is():
    from app.routers.webhooks import _CONFIG_PURPOSE
    assert "never a feature switch" in _CONFIG_PURPOSE["companion"]
