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
        await db.commit()


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db
