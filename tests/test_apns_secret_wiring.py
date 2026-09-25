"""The APNs signing key must actually be fetchable from Secret Manager.

The agreed plan said "GP base64s it into Secret Manager as
apns-private-key-b64". The mapping table that makes that work did not
contain it, so the secret could have been created, stored correctly, and
never read: `_ensure_secrets_in_env` only fills env vars it has a mapping
for, and the build would have stayed dormant with a perfectly good key
sitting in Secret Manager.

That is a plan and an implementation disagreeing where neither side looks
wrong on its own, which is the shape this project keeps paying for.
"""
from app.config import _SECRET_MANAGER_MAPPINGS


def test_the_apns_private_key_is_wired_to_secret_manager():
    assert _SECRET_MANAGER_MAPPINGS.get("CZ_APNS_PRIVATE_KEY_B64") == "apns-private-key-b64"


def test_the_key_id_and_team_id_are_NOT_secrets():
    """Identifiers, not credentials. The table's own comment says config
    belongs in env; putting them here would imply a secrecy they do not
    have and would fail closed if Secret Manager were unreachable."""
    assert "CZ_APNS_KEY_ID" not in _SECRET_MANAGER_MAPPINGS
    assert "CZ_APNS_TEAM_ID" not in _SECRET_MANAGER_MAPPINGS


def test_every_apple_private_key_is_mapped_the_same_way():
    """Every `*_private_key_b64` SETTING must be fetchable from Secret Manager.

    The first version of this test enumerated the MAPPING and promised that
    a key added later "should fail this test until it is wired". It could
    not: a setting missing from the mapping is absent from what it looked
    at. The Sign in with Apple key sat in Settings unmapped, so prod could
    only have read it from plaintext in .env.prod, and on 2026-09-24
    `is_configured()` was False in prod with three real deletions logging
    "revocation skipped". The expected set is now read from Settings.
    """
    from app.config import Settings
    settings_keys = {f"CZ_{name.upper()}" for name in Settings.model_fields
                     if name.endswith("_private_key_b64")}
    apple_keys = {k for k in _SECRET_MANAGER_MAPPINGS if k.endswith("_PRIVATE_KEY_B64")}
    assert settings_keys == apple_keys, (
        f"unmapped: {sorted(settings_keys - apple_keys)}, "
        f"mapped but no setting: {sorted(apple_keys - settings_keys)}")
    for env_var in apple_keys:
        sm_name = _SECRET_MANAGER_MAPPINGS[env_var]
        assert sm_name == env_var[3:].lower().replace("_", "-"), (
            f"{env_var} -> {sm_name} breaks the naming convention")
