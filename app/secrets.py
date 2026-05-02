"""Resolve secrets from environment variables, falling back to GCP Secret Manager.

Local dev sets values in `.env` (or shell env) and the helper returns
them directly. Deployed instances on GCP can leave the env var unset
and the helper fetches from Secret Manager — provided the VM's OAuth
scope and the secret's IAM policy permit access.

The GCP project is read from `CZ_GCP_PROJECT`, falling back to
Application Default Credentials project resolution. No project ID is
hard-coded here so the same code can run against any deployment.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)


def _resolve_project() -> str:
    explicit = os.getenv("CZ_GCP_PROJECT", "").strip()
    if explicit:
        return explicit
    try:
        from google.auth import default as google_auth_default  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        _, project = google_auth_default()
        return project or ""
    except Exception as exc:  # noqa: BLE001 — auth failures are expected in tests/local
        logger.debug("ADC project resolution failed: %s", exc)
        return ""


def _from_secret_manager(secret_name: str) -> str:
    try:
        from google.cloud import secretmanager  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("google-cloud-secret-manager not installed; cannot fetch %s", secret_name)
        return ""
    project = _resolve_project()
    if not project:
        logger.warning("No GCP project resolved for secret %s; set CZ_GCP_PROJECT", secret_name)
        return ""
    try:
        client = secretmanager.SecretManagerServiceClient()
        path = f"projects/{project}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(name=path)
        return response.payload.data.decode("utf-8")
    except Exception as exc:  # noqa: BLE001 — surface broad failures as empty + log
        logger.warning("Secret Manager fetch failed for %s: %s", secret_name, exc)
        return ""


@lru_cache(maxsize=32)
def get_secret(secret_name: str, env_var: str | None = None) -> str:
    """Return the value of `secret_name`, env first then Secret Manager.

    If `env_var` is provided and the environment has a non-empty value
    for it, that wins. Otherwise we fetch from
    `projects/{CZ_GCP_PROJECT}/secrets/{secret_name}/versions/latest`.
    Returns "" if neither source produces a value — callers decide
    whether to treat that as fatal.
    """
    if env_var:
        env_value = os.getenv(env_var, "").strip()
        if env_value:
            return env_value
    return _from_secret_manager(secret_name)
