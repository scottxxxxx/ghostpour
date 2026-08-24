"""Lost-phone mandate (2026-08-24): Apple hands fullName over once; GP
keeps it and returns it on every sign-in and refresh so a fresh phone
rehydrates the display name. A later null never overwrites it."""
from unittest.mock import patch

VERIFY = "app.services.apple_auth.AppleAuthVerifier.verify_identity_token"


def _apple(client, sub, full_name=None):
    with patch(VERIFY, return_value={"sub": sub, "email": f"{sub}@privaterelay.appleid.com"}):
        body = {"identity_token": "mock.apple.token"}
        if full_name is not None:
            body["full_name"] = full_name
        r = client.post("/auth/apple", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_display_name_persists_and_returns_on_every_sign_in_and_refresh(client):
    first = _apple(client, "sub-dn-1", full_name="Andreina Gonzalez")
    assert first["user"]["display_name"] == "Andreina Gonzalez"
    # Second authorization: Apple sends no name (the fresh-phone case).
    second = _apple(client, "sub-dn-1")
    assert second["user"]["id"] == first["user"]["id"]
    assert second["user"]["display_name"] == "Andreina Gonzalez"
    # Refresh carries it too.
    r = client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["display_name"] == "Andreina Gonzalez"


def test_a_fresh_non_null_name_wins_but_null_never_erases(client):
    _apple(client, "sub-dn-2", full_name="Old Name")
    assert _apple(client, "sub-dn-2", full_name="New Name")["user"]["display_name"] == "New Name"
    assert _apple(client, "sub-dn-2")["user"]["display_name"] == "New Name"


def test_user_with_no_name_ever_returns_null_not_a_placeholder(client):
    assert _apple(client, "sub-dn-3")["user"]["display_name"] is None
