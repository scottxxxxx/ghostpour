"""Resolve secrets from environment variables, falling back to GCP Secret Manager.

Local dev sets values in `.env` (or shell env) and the helper returns
them directly. Deployed instances on GCP can leave the env var unset
and the helper fetches from Secret Manager — provided the VM's OAuth
scope and the secret's IAM policy permit access.

The GCP project is read from `CZ_GCP_PROJECT`, falling back to
Application Default Credentials project resolution. No project ID is
hard-coded here so the same code can run against any deployment.

Cached values expire after `_TTL_SECONDS` (default 300s) so a rotated
secret gets picked up without restarting the container — bounded
staleness + bounded SM call rate. Callers can clear the cache
manually via `get_secret.cache_clear()` (lru_cache-compatible alias)
for tests / explicit invalidation.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


# How long (seconds) a resolved value is cached in-process. Override via
# CZ_SECRET_CACHE_TTL_SECONDS if needed; production default is 5 min.
_TTL_SECONDS = float(os.getenv("CZ_SECRET_CACHE_TTL_SECONDS", "300"))
_MAX_ENTRIES = 64

_cache: dict[tuple, tuple[str, float]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: tuple) -> tuple[str, bool]:
    """Return (value, hit). On expired entry, drop it and miss."""
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return "", False
        value, expires_at = entry
        if now >= expires_at:
            _cache.pop(key, None)
            return "", False
        return value, True


def _cache_put(key: tuple, value: str) -> None:
    expires_at = time.monotonic() + _TTL_SECONDS
    with _cache_lock:
        if len(_cache) >= _MAX_ENTRIES and key not in _cache:
            # Simple FIFO eviction — drop the oldest insertion. Insertion
            # order is preserved by dict in Python 3.7+.
            try:
                oldest_key = next(iter(_cache))
                _cache.pop(oldest_key, None)
            except StopIteration:
                pass
        _cache[key] = (value, expires_at)


def _cache_clear() -> None:
    global _adc_project
    with _cache_lock:
        _cache.clear()
    _adc_project = None


# ADC project resolution result, cached for the process lifetime ("" =
# resolved to nothing). Without this, every get_secret cache miss re-runs
# google.auth.default(), which on a machine with no ADC probes the GCE
# metadata server and waits out its timeout — multiplied by the 12 mapped
# secrets, that turned local test runs into minutes of silent waiting.
# The explicit CZ_GCP_PROJECT path is deliberately NOT cached so tests
# and operators can change it at runtime.
_adc_project: str | None = None


def _resolve_project() -> str:
    global _adc_project
    explicit = os.getenv("CZ_GCP_PROJECT", "").strip()
    if explicit:
        return explicit
    if _adc_project is not None:
        return _adc_project
    try:
        from google.auth import default as google_auth_default  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        _, project = google_auth_default()
        _adc_project = project or ""
    except Exception as exc:  # noqa: BLE001 — auth failures are expected in tests/local
        logger.debug("ADC project resolution failed: %s", exc)
        _adc_project = ""
    return _adc_project


def _is_not_found(exc: Exception) -> bool:
    try:
        from google.api_core.exceptions import NotFound  # type: ignore[import-not-found]
    except ImportError:
        return False
    return isinstance(exc, NotFound)


def _from_secret_manager(secret_name: str) -> str:
    try:
        from google.cloud import secretmanager  # type: ignore[import-not-found]
        from google.auth import default as auth_default  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("google-cloud-secret-manager not installed; cannot fetch %s", secret_name)
        return ""
    project = _resolve_project()
    if not project:
        logger.warning("No GCP project resolved for secret %s; set CZ_GCP_PROJECT", secret_name)
        return ""
    try:
        # Compute Engine metadata-service credentials report
        # `requires_scopes=True` and the SM SDK doesn't auto-supply the
        # scope when constructing the client. Result: a default
        # `SecretManagerServiceClient()` call comes back as 403
        # IAM_PERMISSION_DENIED ("or it may not exist") even when the
        # SA has `roles/secretmanager.secretAccessor` on the secret.
        # Pass `cloud-platform` explicitly so the metadata token has
        # the right scope.
        creds, _ = auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        client = secretmanager.SecretManagerServiceClient(credentials=creds)
        path = f"projects/{project}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(name=path)
        return response.payload.data.decode("utf-8")
    except Exception as exc:  # noqa: BLE001 — surface broad failures as empty + log
        if _is_not_found(exc):
            # A missing secret is a normal outcome here: the startup shadow
            # check consults SM read-only for env-resident secrets, and
            # BYOK-only provider keys have no managed value at all. Only
            # real trouble (IAM, scope, network) belongs at WARNING — nine
            # benign 404 warnings per boot once got misread as "Secret
            # Manager is down for every secret".
            logger.info("Secret %s not provisioned in Secret Manager", secret_name)
        else:
            logger.warning("Secret Manager fetch failed for %s: %s", secret_name, exc)
        return ""


def get_secret(secret_name: str, env_var: str | None = None) -> str:
    """Return the value of `secret_name`, env first then Secret Manager.

    If `env_var` is provided and the environment has a non-empty value
    for it, that wins. Otherwise we fetch from
    `projects/{CZ_GCP_PROJECT}/secrets/{secret_name}/versions/latest`.
    Returns "" if neither source produces a value — callers decide
    whether to treat that as fatal.

    Resolution is cached for `_TTL_SECONDS` (default 5 minutes) so
    repeated calls in tight loops don't hammer Secret Manager and so
    rotations propagate automatically without container restart.
    """
    cache_key = (secret_name, env_var)
    cached, hit = _cache_get(cache_key)
    if hit:
        return cached

    value = ""
    if env_var:
        env_value = os.getenv(env_var, "").strip()
        if env_value:
            value = env_value
    if not value:
        value = _from_secret_manager(secret_name)

    _cache_put(cache_key, value)
    return value


# lru_cache-compatible alias so existing test code calling
# `get_secret.cache_clear()` keeps working unchanged.
get_secret.cache_clear = _cache_clear  # type: ignore[attr-defined]
