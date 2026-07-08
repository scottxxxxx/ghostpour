import os
from collections.abc import AsyncGenerator

import aiosqlite

_db_path: str = ""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    apple_sub TEXT UNIQUE NOT NULL,
    email TEXT,
    tier TEXT NOT NULL DEFAULT 'free',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    monthly_cost_limit_usd REAL,
    monthly_used_usd REAL NOT NULL DEFAULT 0,
    overage_balance_usd REAL NOT NULL DEFAULT 0,
    allocation_resets_at TEXT,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_apple_sub ON users(apple_sub);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);

CREATE TABLE IF NOT EXISTS usage_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost_usd REAL,
    request_timestamp TEXT NOT NULL,
    response_time_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'success',
    error_message TEXT,
    call_type TEXT,
    prompt_mode TEXT,
    image_count INTEGER DEFAULT 0,
    session_duration_sec INTEGER,
    cached_tokens INTEGER,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_usage_user_date ON usage_log(user_id, request_timestamp);
"""


MIGRATIONS = [
    # v1: Add metadata column to usage_log
    "ALTER TABLE usage_log ADD COLUMN metadata TEXT",
    # v2: Add query tracking columns to usage_log
    "ALTER TABLE usage_log ADD COLUMN call_type TEXT",
    "ALTER TABLE usage_log ADD COLUMN prompt_mode TEXT",
    "ALTER TABLE usage_log ADD COLUMN image_count INTEGER DEFAULT 0",
    "ALTER TABLE usage_log ADD COLUMN session_duration_sec INTEGER",
    "ALTER TABLE usage_log ADD COLUMN cached_tokens INTEGER",
    # v3: Add monthly allocation tracking to users
    "ALTER TABLE users ADD COLUMN monthly_cost_limit_usd REAL",
    "ALTER TABLE users ADD COLUMN monthly_used_usd REAL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN overage_balance_usd REAL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN allocation_resets_at TEXT",
    # v4: Add tier simulation columns
    "ALTER TABLE users ADD COLUMN simulated_tier TEXT",
    "ALTER TABLE users ADD COLUMN simulated_exhausted INTEGER DEFAULT 0",
    # v5: Add trial tracking columns
    "ALTER TABLE users ADD COLUMN is_trial INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN trial_start TEXT",
    "ALTER TABLE users ADD COLUMN trial_end TEXT",
    # v6: Add display_name for CQ user identity passthrough
    "ALTER TABLE users ADD COLUMN display_name TEXT",
    # v7: Store Apple originalTransactionId for server notification lookups
    "ALTER TABLE users ADD COLUMN original_transaction_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_users_original_txn ON users(original_transaction_id)",
    # v8: Add meeting_id to usage_log for per-meeting indexing (report generation)
    "ALTER TABLE usage_log ADD COLUMN meeting_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_usage_meeting ON usage_log(meeting_id)",
    # v9: Store transcripts for meeting report generation
    """CREATE TABLE IF NOT EXISTS meeting_transcripts (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        meeting_id TEXT NOT NULL,
        transcript TEXT NOT NULL,
        project TEXT,
        project_id TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_transcripts_meeting ON meeting_transcripts(meeting_id)",
    # v10: Cache generated reports for recovery (30-day retention)
    """CREATE TABLE IF NOT EXISTS meeting_reports (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        meeting_id TEXT NOT NULL UNIQUE,
        report_json TEXT NOT NULL,
        report_html TEXT NOT NULL,
        model TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        cost_usd REAL,
        generation_ms INTEGER,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_reports_meeting ON meeting_reports(meeting_id)",
    "CREATE INDEX IF NOT EXISTS idx_reports_created ON meeting_reports(created_at)",
    # v11: Persist tier-derived ai_tier label at generation time so cached
    # GETs return a stable label that survives model swaps.
    "ALTER TABLE meeting_reports ADD COLUMN ai_tier TEXT",
    # v12: Project Chat per-user quota tracking (calendar-month period, lazy reset)
    "ALTER TABLE users ADD COLUMN project_chat_used_this_period INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN project_chat_period TEXT",
    # v13: Memory capture quota + last-meeting CTA flags. Mirrors Project
    # Chat's lazy-reset pattern. memory_last_origin_id + memory_last_cta_kind
    # are written at /v1/capture-transcript time and consumed (then cleared)
    # by the next /v1/quilt/{user_id} fetch so the synthetic upsell card
    # only appears once per meeting.
    "ALTER TABLE users ADD COLUMN memory_used_this_period INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN memory_period TEXT",
    "ALTER TABLE users ADD COLUMN memory_last_origin_id TEXT",
    "ALTER TABLE users ADD COLUMN memory_last_cta_kind TEXT",
    # v14: meeting-report placeholder tracking. NULL report_status means
    # "real generated report"; any other value (e.g. 'placeholder_budget_blocked')
    # marks a canned upsell response that iOS should treat as non-editable
    # and surface under a 'Hide samples' toggle. is_editable is set explicitly
    # at persist time; NULL on legacy rows is treated as editable=true.
    "ALTER TABLE meeting_reports ADD COLUMN report_status TEXT",
    "ALTER TABLE meeting_reports ADD COLUMN is_editable INTEGER",
    # v15: email webhook event audit log. One row per delivered Resend webhook,
    # keyed by svix-id for idempotent ingest. Stores the full payload so we
    # can replay analysis without re-fetching from Resend.
    """CREATE TABLE IF NOT EXISTS email_events (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        recipient TEXT,
        email_id TEXT,
        bounce_type TEXT,
        payload TEXT NOT NULL,
        received_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_email_events_recipient ON email_events(recipient)",
    "CREATE INDEX IF NOT EXISTS idx_email_events_type ON email_events(event_type, received_at)",
    # v16: active email suppression list. Recipients here are blocked from all
    # future sends. Populated by hard bounces and spam complaints. PK is the
    # lowercased recipient — normalize at write time so case-only differences
    # don't slip past `is_suppressed`.
    """CREATE TABLE IF NOT EXISTS email_suppression (
        recipient TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        source_event_id TEXT,
        suppressed_at TEXT NOT NULL
    )""",
    # v17: per-user marketing email opt-in state. Source of truth for
    # "may we send tips/news to this user." Default 0 (off) — GDPR
    # explicit-opt-in. Source distinguishes how the value was set (ios
    # toggle / unsubscribe link / spam complaint webhook / admin) so
    # we can audit unexpected flips.
    "ALTER TABLE users ADD COLUMN marketing_opt_in INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN marketing_opt_in_updated_at TEXT",
    "ALTER TABLE users ADD COLUMN marketing_opt_in_source TEXT",
    # v18: per-user web-search cap counter + audit log. searches_used is
    # the rolling counter for the current allocation period — reset by
    # the same lazy-reset path as monthly_used_usd. The search_usage
    # table is a per-search audit row written after each search-bearing
    # Anthropic response: enables per-user usage display in the admin
    # dashboard and offline reconciliation if the counter ever drifts
    # (e.g., Anthropic returned a search but our DB increment failed).
    "ALTER TABLE users ADD COLUMN searches_used INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS search_usage (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        request_timestamp TEXT NOT NULL,
        meeting_id TEXT,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        searches_count INTEGER NOT NULL DEFAULT 1,
        search_cost_usd REAL,
        usage_log_id TEXT REFERENCES usage_log(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_search_usage_user_date ON search_usage(user_id, request_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_search_usage_meeting ON search_usage(meeting_id)",
    # v19: drop Project Chat count-quota columns. Replaced by the budget gate
    # (PRs #109-#121). SQLite 3.35+ supports DROP COLUMN; older runtimes
    # raise and the init_db try/except moves on (the columns simply remain
    # — they're unused by the code from this point on).
    "ALTER TABLE users DROP COLUMN project_chat_used_this_period",
    "ALTER TABLE users DROP COLUMN project_chat_period",
    # v20: critical-failure alert recipients. Operator-facing email list
    # for system-level outages (CQ unreachable, managed-provider auth
    # rejected, managed-provider monthly limit exhausted). Each row is
    # one address opted into the alert flow. `categories` is a JSON
    # array of category strings the recipient wants — empty/null = all.
    """CREATE TABLE IF NOT EXISTS alert_recipients (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        display_name TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        categories TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # v21: open-incident state. One row per (category, subject)
    # fingerprint while the incident is open; resolved rows stay for
    # the history view until purged by retention. fingerprint is unique
    # only WHERE resolved_at IS NULL — a resolved+re-opened incident
    # gets a fresh row, so the table doubles as immutable history.
    """CREATE TABLE IF NOT EXISTS alert_incidents (
        id TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        subject TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        trigger_count INTEGER NOT NULL DEFAULT 1,
        details_json TEXT,
        email_sent_at TEXT,
        emailed_recipients TEXT,
        resolved_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_alert_incidents_open ON alert_incidents(fingerprint) WHERE resolved_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_alert_incidents_created ON alert_incidents(first_seen_at DESC)",
    # v22: Persist server-cleaned transcript alongside the report so cached
    # GETs return the same cleaned text the original POST did. Populated
    # when transcript_source is "ocr_captions" (or future modes) AND the
    # cleanup feature flag is enabled; NULL otherwise. iOS reads this
    # field optionally and falls back to its raw transcript when absent.
    "ALTER TABLE meeting_reports ADD COLUMN cleaned_transcript TEXT",
    # Persist the cleaned transcript canonically beside the raw one, so the
    # served transcript can be the cleaned version and the dashboard can show
    # original-vs-cleaned. `transcript` stays raw; `cleaned_transcript` is the
    # cleanup output (null until cleanup runs).
    "ALTER TABLE meeting_transcripts ADD COLUMN cleaned_transcript TEXT",
    "ALTER TABLE meeting_transcripts ADD COLUMN cleaned_at TEXT",
    # v23: Unauthenticated telemetry events for app/meeting lifecycle
    # tracking. iOS pings on app_start, meeting_start, meeting_stop with
    # an anonymous device_id (identifierForVendor) and, when logged in,
    # an optional user_id so we can attribute pre-login activity to a
    # user retroactively. Raw events purge at 30 days; aggregates kept
    # forever in telemetry_daily_rollups. ip_hash is SHA256 of the
    # source IP (not stored raw) for per-IP abuse detection.
    """CREATE TABLE IF NOT EXISTS telemetry_events (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        device_id TEXT NOT NULL,
        user_id TEXT,
        meeting_id TEXT,
        model_id TEXT,
        app_version TEXT,
        os_version TEXT,
        duration_seconds INTEGER,
        ip_hash TEXT,
        received_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_device ON telemetry_events(device_id)",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_user ON telemetry_events(user_id) WHERE user_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_event ON telemetry_events(event_type, received_at)",
    # Daily rollup table — one row per (day, metric) pair, INSERT OR
    # REPLACE for idempotency. Metric keys are flat strings like
    # 'app_starts', 'distinct_devices', 'meetings_started',
    # 'meetings_stopped', 'meetings_per_model:<model_id>',
    # 'duration_avg_sec', 'duration_min_sec', 'duration_max_sec'.
    # Kept indefinitely (tiny table, useful for long-term trend lines).
    """CREATE TABLE IF NOT EXISTS telemetry_daily_rollups (
        day TEXT NOT NULL,
        metric TEXT NOT NULL,
        value REAL NOT NULL,
        PRIMARY KEY (day, metric)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_rollups_day ON telemetry_daily_rollups(day)",
    # v24: Signed cert pin manifests served at /v1/config/cert-pins.
    # iOS fetches the latest manifest, verifies the signature against a
    # baked-in public key, and uses the resulting pins. This decouples
    # pin updates from app releases: when Let's Encrypt rotates the cert
    # chain we sign and publish a new manifest, the app picks it up next
    # launch. Version is a monotonic integer (next_version = MAX + 1)
    # so iOS can refuse any version older than what it has already seen
    # (rollback protection). See app/services/cert_pin_signing.py for
    # the signing implementation and the wire-contract proposal at
    # /Users/scottguida/ShoulderSurf/docs/CERT_PINNING_PROPOSAL.md.
    """CREATE TABLE IF NOT EXISTS cert_pin_manifest (
        version INTEGER PRIMARY KEY,
        pins_json TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        signature TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by_admin_key_suffix TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cert_pin_manifest_version_desc ON cert_pin_manifest(version DESC)",
    # v25: Richer telemetry dimensions for the dashboard. `device_model`
    # is the raw Apple hw.machine code from iOS (e.g. "iPhone17,3"); the
    # server maps it to a marketing name at query time. `app_locale` is
    # BCP-47ish (e.g. "en_US"). Both are optional on the wire so older
    # iOS builds that don't send them still validate.
    "ALTER TABLE telemetry_events ADD COLUMN device_model TEXT",
    "ALTER TABLE telemetry_events ADD COLUMN app_locale TEXT",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_device_model ON telemetry_events(device_model) WHERE device_model IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_app_version ON telemetry_events(app_version) WHERE app_version IS NOT NULL",
    # v26: Persist app identity (X-App-ID) for per-app segmented analytics.
    # Middleware sets request.state.app_id; threaded into the usage_log and
    # telemetry inserts. "unknown" when the header is absent. Lets the admin
    # dashboard split users/usage/models by app (shouldersurf vs techrehearsal).
    "ALTER TABLE usage_log ADD COLUMN app_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_usage_app ON usage_log(app_id) WHERE app_id IS NOT NULL",
    "ALTER TABLE telemetry_events ADD COLUMN app_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_app ON telemetry_events(app_id) WHERE app_id IS NOT NULL",
    # Coarse geo derived from the IP at ingestion (country + region only; never
    # the raw IP, never city). See app/services/geoip.py. Null until the geo DB
    # is installed. Powers Phase 2 region targeting.
    "ALTER TABLE telemetry_events ADD COLUMN country TEXT",
    "ALTER TABLE telemetry_events ADD COLUMN region TEXT",
    # v27: Per-call scenario sub-dimension. Tech Rehearsal is scenario-driven
    # (interview / negotiation / personal conversations) under ONE app_id, so
    # the client tags each call via metadata.scenario and we persist it here.
    # Lets analytics slice scenarios cleanly without splitting app_id. NULL
    # when the client doesn't send it (e.g. SS, or older TR builds).
    "ALTER TABLE usage_log ADD COLUMN scenario TEXT",
    "CREATE INDEX IF NOT EXISTS idx_usage_scenario ON usage_log(scenario) WHERE scenario IS NOT NULL",
    # v28: Server-decided promo campaigns (#promo). GP is the brains: campaigns
    # are authored here, GP decides per device what/whether/how-often to show via
    # the app_start ping, and the client is a thin view that renders + reports.
    # promo_campaigns = the authored definitions (targeting/frequency/schedule are
    # GP-internal JSON; variants carry the SS-facing render payload).
    """CREATE TABLE IF NOT EXISTS promo_campaigns (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',     -- draft|active|paused|archived
        app_id TEXT NOT NULL,                      -- which app (X-App-ID) it targets
        starts_at TEXT,
        expires_at TEXT,
        priority INTEGER NOT NULL DEFAULT 0,
        mutual_exclusion_group TEXT,
        targeting TEXT,                            -- JSON: locales/app_version/usage/devices/tiers/...
        frequency TEXT,                            -- JSON: max_impressions/min_interval/cooldown/...
        placements TEXT,                           -- JSON array of {placement,priority}
        variants TEXT,                             -- JSON array (SS-facing render contract)
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_promo_campaigns_app ON promo_campaigns(app_id, status)",
    # promo_presentations = per (device, campaign) aggregate the decision engine
    # reads to enforce frequency. Written from the client's impression/dismiss/
    # click/convert events. device_id is the IDFV anchor (works pre-sign-in).
    """CREATE TABLE IF NOT EXISTS promo_presentations (
        device_id TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        variant_id TEXT,
        app_id TEXT,
        shown_count INTEGER NOT NULL DEFAULT 0,
        first_shown_at TEXT,
        last_shown_at TEXT,                        -- drives min_interval
        last_dismissed_at TEXT,                    -- drives cooldown_after_dismiss
        last_clicked_at TEXT,
        converted_at TEXT,                         -- drives stop_after_convert
        PRIMARY KEY (device_id, campaign_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_promo_pres_campaign ON promo_presentations(campaign_id)",
    # promo_events = raw event log for analytics: macro funnel, avg view time
    # (visible_ms), and per-CTA click attribution (cta_id). Per-user history too.
    """CREATE TABLE IF NOT EXISTS promo_events (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        device_id TEXT,
        user_id TEXT,
        campaign_id TEXT,
        variant_id TEXT,
        app_id TEXT,
        event_type TEXT NOT NULL,                  -- impression|dismiss|click|convert
        visible_ms INTEGER,                        -- impression/dismiss: time visible
        cta_id TEXT                                -- click: which CTA was tapped
    )""",
    "CREATE INDEX IF NOT EXISTS idx_promo_events_campaign ON promo_events(campaign_id, event_type)",
    "CREATE INDEX IF NOT EXISTS idx_promo_events_device ON promo_events(device_id)",
    # subscription_events = append-only log of every subscription lifecycle
    # transition. users.tier holds only the CURRENT tier; this is the history
    # that answers "ever subscribed?", "first subscribed when?", and the
    # month-by-month tier report (bookkeeping). Fed in real time by the Apple
    # Server Notifications webhook + verify-receipt, and by the reconciliation
    # sweep when it corrects drift. Truth is the event log; users.ever_subscribed
    # / first_subscribed_at are denormalized caches for the hot path.
    """CREATE TABLE IF NOT EXISTS subscription_events (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        event_type TEXT NOT NULL,                  -- normalized: subscribed|renewed|upgraded|downgraded|expired|revoked|refunded|billing_failed|reconciled
        notification_type TEXT,                    -- raw Apple notificationType
        subtype TEXT,                              -- raw Apple subtype
        from_tier TEXT,
        to_tier TEXT,
        product_id TEXT,
        original_transaction_id TEXT,
        transaction_id TEXT,
        expires_at TEXT,                           -- Apple expiresDate (ISO), the period end
        environment TEXT,                          -- Production|Sandbox
        source TEXT NOT NULL DEFAULT 'assn',       -- assn|verify_receipt|reconciliation
        price_usd REAL,                            -- list price for the period (bookkeeping)
        effective_at TEXT NOT NULL,                -- Apple's event timestamp (or now)
        recorded_at TEXT NOT NULL,                 -- when GP wrote the row
        raw TEXT                                   -- decoded transaction JSON, for audit
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sub_events_user ON subscription_events(user_id, effective_at)",
    "CREATE INDEX IF NOT EXISTS idx_sub_events_type ON subscription_events(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_sub_events_effective ON subscription_events(effective_at)",
    # Denormalized caches on users for the offer-code eligibility hot path
    # (never-subscribed targeting) and fast dashboard reads. Source of truth is
    # subscription_events; these are kept in lockstep by the same writers.
    "ALTER TABLE users ADD COLUMN ever_subscribed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN first_subscribed_at TEXT",
    # offer_code_pool = the pool of one-time-use App Store offer codes GP holds
    # and hands out, one per user, behind a storekit_offer promo CTA. Loaded from
    # a Connect-API-minted batch (offer_codes.py); dispensed reserve-once at promo
    # resolve time (offer_dispense.py) and injected into the CTA's action.value.
    # A code belongs to an (offer_id, environment) pool so a sandbox test campaign
    # and the live production campaign never cross-contaminate. reserved_by_user is
    # the idempotency key (device fallback when signed out): the same actor always
    # gets the same code back rather than burning a new one each cold-launch resolve.
    """CREATE TABLE IF NOT EXISTS offer_code_pool (
        code TEXT PRIMARY KEY,                     -- redeemable one-time-use code string
        offer_id TEXT NOT NULL,                    -- ASC subscriptionOfferCodes id
        product_id TEXT,                           -- e.g. com.weirtech.shouldersurf.sub.pro.monthly
        environment TEXT NOT NULL,                 -- sandbox|production
        batch_id TEXT,                             -- ASC one-time-use batch id (provenance)
        status TEXT NOT NULL DEFAULT 'available',  -- available|reserved
        reserved_by_user TEXT,                     -- user_id the code is reserved to
        reserved_by_device TEXT,                   -- device_id fallback when unauthenticated
        reserved_at TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_offer_pool_dispense ON offer_code_pool(offer_id, environment, status)",
    "CREATE INDEX IF NOT EXISTS idx_offer_pool_user ON offer_code_pool(reserved_by_user, offer_id, environment)",
    "CREATE INDEX IF NOT EXISTS idx_offer_pool_device ON offer_code_pool(reserved_by_device, offer_id, environment)",
    # Fine-grained scenario kind alongside the coarse `scenario` bucket. TR's
    # prompts branch on ScenarioKind (jobInterview / payNegotiation /
    # hardConversation / ...), the client sends metadata.scenario_kind on every
    # call (additive, 2026-07-02), and server-side assembly selects guidance by
    # it — persist it so analytics and prompt debugging see the same axis the
    # assembly used. Case is preserved (camelCase enum values from TR).
    "ALTER TABLE usage_log ADD COLUMN scenario_kind TEXT",
    "CREATE INDEX IF NOT EXISTS idx_usage_scenario_kind ON usage_log(scenario_kind) WHERE scenario_kind IS NOT NULL",
    # City joins country/region per the approved targeting design (#318 §9:
    # city targeting on from day one, min-audience floor 25 enforced at
    # campaign authoring AND resolve). Derived from IP at ingestion like the
    # other two; raw IP still never stored. NULL until the client's next ping.
    "ALTER TABLE telemetry_events ADD COLUMN city TEXT",
]


async def init_db(database_url: str) -> None:
    global _db_path
    _db_path = database_url.replace("sqlite+aiosqlite:///", "")
    os.makedirs(os.path.dirname(_db_path) or ".", exist_ok=True)
    async with aiosqlite.connect(_db_path) as db:
        await db.executescript(SCHEMA_SQL)
        # Run migrations for existing databases
        for sql in MIGRATIONS:
            try:
                await db.execute(sql)
            except Exception:
                pass  # Column already exists

        # Purge cached reports older than 30 days
        await db.execute(
            "DELETE FROM meeting_reports WHERE created_at < datetime('now', '-30 days')"
        )

        # Purge email_events older than 90 days. Webhook event audit log
        # — kept long enough for spam-complaint / bounce attribution
        # debugging, then dropped to bound disk + Litestream replication
        # size. Suppression list is NOT pruned here: a suppressed
        # address stays suppressed forever unless explicitly lifted.
        await db.execute(
            "DELETE FROM email_events WHERE received_at < datetime('now', '-90 days')"
        )

        # Purge raw telemetry_events older than 30 days. Aggregates land
        # in telemetry_daily_rollups (computed by the startup rollup job
        # in app.services.telemetry_rollup) and are kept indefinitely.
        await db.execute(
            "DELETE FROM telemetry_events WHERE received_at < datetime('now', '-30 days')"
        )

        await db.commit()


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db
