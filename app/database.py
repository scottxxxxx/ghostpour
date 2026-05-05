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

        await db.commit()


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db
