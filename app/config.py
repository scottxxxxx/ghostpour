"""Application Settings — pydantic-settings shape.

Resolution order for each field (when not provided programmatically):
1. Environment variable (`CZ_<FIELD>` per `env_prefix` below).
2. `.env` file in cwd (loaded by pydantic-settings automatically).
3. **Secret Manager fallback** for fields listed in
   `_SECRET_MANAGER_MAPPINGS` — runs at `get_settings()` time before
   pydantic builds the instance. Lets `.env.prod` ship without
   plaintext secrets once the operator has provisioned the SM
   counterparts. The mapping is `CZ_FOO` → secret name `foo` (lower-
   kebab) by default; override on a case-by-case basis.

Field defaults are still required so pydantic's required-field
validation passes when a secret is absent in BOTH env AND SM (i.e.
local dev test runs where neither is wired). For required fields like
`jwt_secret`, the .env in tests still has to carry a value — same as
before.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # JWT
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # Apple Sign In
    apple_bundle_id: str = "com.example.myapp"

    # App Store Server API (outbound, JWT-signed) — used to verify transactions
    # at signup and to reconcile subscription state against Apple's truth. All
    # blank by default → the client and the reconciliation sweep stay DORMANT
    # until the key is provisioned (no behavior change, no failures).
    app_store_issuer_id: str = ""           # ASC API issuer id (UUID)
    app_store_key_id: str = ""              # ASC API key id
    app_store_private_key_b64: str = ""     # base64 of the .p8 EC private key
    app_store_environment: str = "Sandbox"  # Sandbox (TestFlight) | Production
    # The single bundle id the App Store Server API JWT `bid` claim must carry.
    # apple_bundle_id can be a comma-joined list (this gateway serves several
    # apps); the Server API needs ONE — the app that owns the subscriptions.
    # Blank => fall back to the first entry of apple_bundle_id.
    app_store_bundle_id: str = ""
    subscription_reconcile_enabled: bool = False
    subscription_reconcile_interval_seconds: int = 21600  # 6h

    # App Store CONNECT API key — for minting subscription offer codes.
    # DISTINCT from the App Store Server API key above: that one is an In-App
    # Purchase key (signs storekit Server API calls); minting offer codes is the
    # App Store Connect API (api.appstoreconnect.apple.com), which needs a team
    # key (Admin/App Manager role, AuthKey_*.p8). Blank by default → the minting
    # client stays DORMANT until the key is provisioned.
    asc_connect_issuer_id: str = ""          # Connect API issuer id (UUID)
    asc_connect_key_id: str = ""             # Connect API key id
    asc_connect_private_key_b64: str = ""    # base64 of the .p8 EC private key

    # Provider API Keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    xai_api_key: str = ""
    deepseek_api_key: str = ""
    kimi_api_key: str = ""
    qwen_api_key: str = ""
    openrouter_api_key: str = ""  # Used by Context Quilt worker

    # Admin
    admin_key: str = ""

    # Cert pin manifest signing (proposal in
    # /Users/scottguida/ShoulderSurf/docs/CERT_PINNING_PROPOSAL.md).
    # 32-byte Ed25519 private key, raw bytes encoded base64. Used by
    # app/services/cert_pin_signing.py to sign the JSON manifest served
    # at GET /v1/config/cert-pins. iOS bakes in the matching public key
    # and verifies the signature on every fetch.
    #
    # Generation, custody, and rotation:
    #   - Generated locally on a trusted operator machine with
    #     `openssl genpkey -algorithm Ed25519`, never in CI, never in a
    #     shared environment.
    #   - Stored in GCP Secret Manager as `cert-pin-signing-key-raw-b64`
    #     (mapped below); .env mirror for local dev only.
    #   - The key NEVER lands in this repo. .env is gitignored and a
    #     belt-and-suspenders pattern in .gitignore excludes any file
    #     matching `*signing_private*` in case of accidental copy.
    #   - Rotation is rare (years). When rotated, iOS ships the next
    #     public key in an app release first (both keys baked in for
    #     the transition window), THEN we cut over server-side signing
    #     to the new key, so old installs keep verifying during the
    #     transition.
    cert_pin_signing_key_raw_b64: str = ""

    # Hostname the auto-republish task probes for the live TLS chain.
    # Defaults to prod so the local dev image stays a no-op (cert_pin_self_host
    # is only meaningful when paired with a configured signing key). Override
    # via CZ_CERT_PIN_SELF_HOST for staging.
    cert_pin_self_host: str = "cz.shouldersurf.com"

    # Context Quilt integration
    cq_base_url: str = ""              # e.g., "https://cq.example.com"
    cq_app_id: str = "cloudzap"        # Default CQ app identity (ShoulderSurf rides this)
    cq_client_secret: str = ""         # Client secret for CQ JWT auth (empty = use X-App-ID fallback)
    # Per-app CQ identity: a second CQ app (Tech Rehearsal) rides GP under its
    # own CQ app_id + secret so CQ loads the right schema. app_id is set in
    # apps.yml (apps.techrehearsal.cq); the secret resolves here. Empty until
    # CQ provisions it. See app/services/context_quilt.py _cq_identity().
    tr_cq_client_secret: str = ""

    # GeoIP: paths to the local .mmdb files (sapics/ip-location-db dbip-city,
    # pulled from GitHub Releases). sapics splits IPv4/IPv6 into two files, so
    # we keep both and route by IP family at lookup time. Geo is disabled
    # (returns null) until the files are present. See app/services/geoip.py.
    geoip_db_path: str = "data/geo/dbip-city-ipv4.mmdb"
    geoip_db_ipv6_path: str = "data/geo/dbip-city-ipv6.mmdb"
    cq_recall_timeout_ms: int = 200    # Max wait for CQ recall (ms)
    # The rundown dossier is the deliberate heavyweight path (user asked
    # for everything; the turn runs a minute regardless) — reusing the
    # 200ms recall budget starved it on a cold cache (live 2026-07-16
    # 15:06Z: dossier timeout, then the recall fallback timed out too,
    # turn answered memory-blind).
    cq_dossier_timeout_ms: int = 5000
    # Correction lane (Context Flow Contract item 9). DARK until CQ's
    # worker handler for interaction_type=correction is live — shipping
    # first would bounce corrections at their ingest gate and lose them
    # (their never-lose-a-memory rule). Flip: CZ_CQ_CORRECTIONS_ENABLED=true
    # in the prod env + restart, on CQ's go signal.
    cq_corrections_enabled: bool = False
    # Render-time "(you)" suffix sanitizer in the CQ recall context.
    # Historical patches stored "Name (you)" forms that the LLM would echo
    # back. CQ #43 (extraction voice rules) and #93 (self-typed-patch voice
    # + owner stripping) tightened the upstream extraction so new patches
    # use second-person "You" natively. The render-time regex should be
    # retiring; this flag lets a canary build run without it to confirm.
    # Default false (sanitizer ON) — matches today's behavior. Flip to
    # true on a single canary deploy to see whether unsanitized recall
    # produces grammatical output.
    cq_disable_you_suffix_sanitizer: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/ghostpour.db"

    # Pricing
    pricing_source_url: str = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json"
    )
    pricing_refresh_seconds: int = 86400  # 24 hours

    # Critical-failure alerting (operator-facing email alerts).
    # Sender address must be on a domain verified in Resend with DKIM/SPF.
    # The address itself isn't a secret; the Resend API key is (already
    # in CZ_RESEND_API_KEY / Secret Manager).
    alert_email_from: str = "alerts@noreply.invalid"

    # Captions/STT transcript cleanup feature flag. When true, requests
    # that carry transcript_source="ocr_captions" (and in the future
    # "speech_to_text") on the report POST or /v1/chat analysis call
    # will trigger an LLM cleanup pass before the main call runs. The
    # cleaned transcript is persisted alongside the report and returned
    # to iOS as the optional `cleaned_transcript` response field. When
    # false (default), cleanup is silently skipped and iOS falls back
    # to its raw transcript. Set CZ_CAPTIONS_CLEANUP_ENABLED=true to flip.
    captions_cleanup_enabled: bool = False

    # Debug
    verbose_logging: bool = False       # Log full request/response bodies (set CZ_VERBOSE_LOGGING=true)

    # Config file paths
    tier_config_path: str = "config/tiers.yml"
    feature_config_path: str = "config/features.yml"
    provider_config_path: str = "config/providers.yml"
    # Provider health probe daemon. Periodically pings managed providers
    # so we get paged the moment a key revokes or budget runs out, instead
    # of finding out when iOS starts seeing failed chats. Anthropic uses
    # /v1/messages/count_tokens (free, validates auth without burning
    # tokens). OpenRouter uses the existing /v1/auth/key balance API.
    # OpenAI uses a tiny chat completion only if a key is configured.
    # See app/services/provider_health.py.
    provider_health_check_interval_seconds: int = 900  # 15 minutes
    # OpenRouter alert threshold. When `remaining_usd` (limit - used) drops
    # below this, fire provider_budget_exhausted. 1.00 USD by default gives
    # ~ a day's headroom at our current burn rate before total exhaustion.
    openrouter_low_balance_threshold_usd: float = 1.00

    # Allocation reset sweep daemon. lazy_reset_if_due only fires on the
    # usage path, so a user who never makes a request after their reset
    # date keeps a stale monthly_used_usd counter — which the Overview
    # allocation-alert panel reads directly, producing a permanent false
    # alert. This daemon applies the same lazy reset to all due users on a
    # cadence so inactive users get reset at the period boundary too.
    # See app/services/allocation_reset_sweep.py.
    allocation_reset_sweep_interval_seconds: int = 3600  # 1 hour

    # Per-app version registry served by GET /v1/app/version. Keyed by
    # bundle id. Missing file is non-fatal (endpoint just 404s on every
    # bundle); see app/services/app_version.py.
    app_versions_path: str = "config/app-versions.yml"

    model_config = {"env_prefix": "CZ_", "env_file": ".env", "extra": "ignore"}


# Map of CZ_<env var> → Secret Manager secret name. When the env var is
# absent (or empty) at startup, `_ensure_secrets_in_env()` fetches from
# SM and sets os.environ so pydantic Settings loads the value normally.
#
# Adding a secret here is the FIRST step of migrating it from
# .env.prod plaintext to SM:
#   1. Add a row here.
#   2. Provision the secret in SM (runbook in ghostpour-ops).
#   3. Remove the corresponding line from .env.prod on the VM.
#   4. Restart the container — startup will fetch from SM.
#
# Don't add CZ_GCP_PROJECT or other config-style vars here — those
# aren't secrets, they're configuration that's safe in env.
_SECRET_MANAGER_MAPPINGS: dict[str, str] = {
    "CZ_JWT_SECRET": "jwt-secret",
    "CZ_ADMIN_KEY": "admin-key",
    "CZ_ANTHROPIC_API_KEY": "anthropic-api-key",
    "CZ_OPENAI_API_KEY": "openai-api-key",
    "CZ_OPENROUTER_API_KEY": "openrouter-api-key",
    "CZ_GOOGLE_API_KEY": "google-api-key",
    "CZ_XAI_API_KEY": "xai-api-key",
    "CZ_DEEPSEEK_API_KEY": "deepseek-api-key",
    "CZ_KIMI_API_KEY": "kimi-api-key",
    "CZ_QWEN_API_KEY": "qwen-api-key",
    "CZ_CQ_CLIENT_SECRET": "cq-client-secret",
    "CZ_TR_CQ_CLIENT_SECRET": "tr-cq-client-secret",
    "CZ_CERT_PIN_SIGNING_KEY_RAW_B64": "cert-pin-signing-key-raw-b64",
    "CZ_APP_STORE_PRIVATE_KEY_B64": "app-store-private-key-b64",
    "CZ_ASC_CONNECT_PRIVATE_KEY_B64": "asc-connect-private-key-b64",
}


def _ensure_secrets_in_env() -> None:
    """Fill os.environ from Secret Manager for any mapping that's
    currently empty. Called once before Settings instantiation. Idempotent.

    Calls into `app.secrets.get_secret`, which itself prefers env over
    SM — so this loop is a no-op for any secret already wired in env.
    The whole point is to let `.env.prod` ship without these values
    (or with empty strings for them) and let SM fill in.

    Logs a structured INFO line (`secret_filled_from_sm`) for each secret
    that was filled from SM. Operators who migrated a secret from .env.prod
    to SM should expect to see one such line per restart per migrated
    secret — confirms the migration is wired correctly.

    For secrets still pinned in env, this also emits a WARNING
    (`env_shadows_sm`) when the env value differs from the current SM
    value — i.e. a rotated SM value is being shadowed by a stale env
    entry. That's the failure mode where live key rotation appears to
    revert on the next restart; the fix is to clear the env entry so SM
    becomes authoritative. Runbook section "shadow risk".
    """
    # Local import: avoid a circular dep with anything that imports config
    # before app.secrets is available, and keep import cost off the
    # `from app.config import Settings` path until startup.
    import logging
    from app.secrets import get_secret

    log = logging.getLogger(__name__)

    filled: list[str] = []
    env_resident: list[str] = []
    no_value: list[str] = []
    for env_var, secret_name in _SECRET_MANAGER_MAPPINGS.items():
        existing = os.environ.get(env_var, "").strip()
        if existing:
            env_resident.append(secret_name)
            # Env wins over SM at startup (pydantic reads env directly), so
            # a rotated Secret Manager value silently loses to a stale value
            # left in `.env`/.env.prod. That's the "shadow trap": a live key
            # rotation via /admin/update-key writes the new value to SM and
            # to process memory, then the next restart reloads the OLD env
            # value and the rotation appears to revert. Consult SM read-only
            # and warn loudly when the two diverge so the operator knows to
            # clear the env entry. We never log the values, only lengths.
            shadowed = get_secret(secret_name)
            if shadowed and shadowed != existing:
                log.warning(
                    "env_shadows_sm env_var=%s sm_secret=%s env_len=%d "
                    "sm_len=%d — the environment value wins at startup, so "
                    "the rotated Secret Manager value is IGNORED until %s is "
                    "removed from .env/.env.prod. Live key rotation will keep "
                    "appearing to revert on restart until then.",
                    env_var, secret_name, len(existing), len(shadowed), env_var,
                )
            continue
        # Pass env_var=None so get_secret skips the env check (it'd
        # short-circuit to "" anyway since we already know it's empty)
        # and goes straight to SM.
        sm_value = get_secret(secret_name)
        if sm_value:
            filled.append(secret_name)
            os.environ[env_var] = sm_value
            log.info(
                "secret_filled_from_sm env_var=%s sm_secret=%s len=%d",
                env_var, secret_name, len(sm_value),
            )
        else:
            no_value.append(secret_name)

    # One line of ground truth per boot — secret NAMES only, never values.
    # `no_value` is expected for BYOK-only provider keys; anything else
    # listed there means the secret is missing from both env and SM.
    log.info(
        "secret_resolution_summary filled_from_sm=[%s] env_resident=[%s] no_value=[%s]",
        ",".join(filled), ",".join(env_resident), ",".join(no_value),
    )


@lru_cache
def get_settings() -> Settings:
    _ensure_secrets_in_env()
    return Settings()
