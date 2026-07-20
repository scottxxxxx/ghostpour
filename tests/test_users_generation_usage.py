"""Dashboard users list surfaces the per-user generation counter
(2026-07-20): generations_used rides the /admin/users payload so the
admin can see file builds this allocation period against the tier cap
(the Files column). Sibling of the searches_used counter."""

import sqlite3

from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _set_gen_used(db_path, user_id, n):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET generations_used = ? WHERE id = ?", (n, user_id))
    conn.commit()
    conn.close()


def test_users_list_includes_generation_and_search_counters(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="u_gen", tier="pro")
    _set_gen_used(tmp_db_path, "u_gen", 7)

    users = client.get("/webhooks/admin/users?days=30", headers=ADMIN).json()["users"]
    row = next(u for u in users if u["id"] == "u_gen")
    assert row["generations_used"] == 7
    assert row["searches_used"] == 0          # sibling counter present too


def test_generations_used_defaults_zero(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="u_gen0", tier="free")
    users = client.get("/webhooks/admin/users?days=30", headers=ADMIN).json()["users"]
    row = next(u for u in users if u["id"] == "u_gen0")
    assert row["generations_used"] == 0
