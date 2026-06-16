"""Tests for the critical-failure alerting feature.

Covers:
  - Recipients CRUD endpoints (admin-authenticated)
  - Incident dedup (once-per-fingerprint while open)
  - Auto-resolution after the quiet window
  - Subscription filtering (recipient only gets categories they opted into)
  - Test-send endpoint
  - Settings sender-address plumbing
  - Incident history endpoint

`send_email` is patched globally so no real Resend calls are made.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
import pytest_asyncio

from app.services.alerting import (
    INCIDENT_AUTO_RESOLVE_MINUTES,
    KNOWN_CATEGORIES,
    list_incidents,
    report_incident,
)
from app.services.email_send import SendResult


# ---------------------------------------------------------------------------
# Service-level tests (direct call into alerting.report_incident)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(tmp_path):
    """Bare aiosqlite connection with the schema applied, no app
    context. Mirrors what report_incident sees in production."""
    from app.database import init_db
    db_path = str(tmp_path / "alerts.db")
    await init_db(f"sqlite+aiosqlite:///{db_path}")
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


@pytest.fixture
def patched_send_email():
    """Patch send_email so no real Resend calls fire. Returns the mock
    so tests can assert call counts."""
    canned = SendResult(sent=True, resend_id="resend-test-id", status_code=200)
    with patch(
        "app.services.alerting.send_email",
        new_callable=AsyncMock,
        return_value=canned,
    ) as mock:
        yield mock


async def _add_recipient(
    db: aiosqlite.Connection, email: str, *, categories=None, active=True,
):
    import uuid
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO alert_recipients "
        "(id, email, display_name, active, categories, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            email.lower(),
            None,
            1 if active else 0,
            json.dumps(categories) if categories else None,
            now, now,
        ),
    )
    await db.commit()


class TestIncidentDedup:
    @pytest.mark.asyncio
    async def test_first_report_creates_open_incident_and_emails(
        self, db, patched_send_email,
    ):
        await _add_recipient(db, "scott@example.com")
        result = await report_incident(
            db, category="cq_unreachable", subject="cq", details={"kind": "timeout"},
        )
        assert result.is_new is True
        assert result.emailed_to == ["scott@example.com"]
        assert result.suppressed_reason is None
        assert patched_send_email.await_count == 1

    @pytest.mark.asyncio
    async def test_duplicate_report_does_not_re_email(
        self, db, patched_send_email,
    ):
        """Same fingerprint within the quiet window must not fire a second email."""
        await _add_recipient(db, "scott@example.com")
        first = await report_incident(
            db, category="cq_unreachable", subject="cq", details={"kind": "timeout"},
        )
        second = await report_incident(
            db, category="cq_unreachable", subject="cq", details={"kind": "timeout"},
        )
        assert first.is_new is True
        assert second.is_new is False
        assert second.suppressed_reason == "incident_already_open"
        # Only one email — the first call.
        assert patched_send_email.await_count == 1

    @pytest.mark.asyncio
    async def test_dedup_increments_trigger_count(self, db, patched_send_email):
        await _add_recipient(db, "scott@example.com")
        for _ in range(5):
            await report_incident(
                db, category="cq_unreachable", subject="cq", details={},
            )
        cursor = await db.execute(
            "SELECT trigger_count FROM alert_incidents "
            "WHERE category=? AND subject=? AND resolved_at IS NULL",
            ("cq_unreachable", "cq"),
        )
        row = await cursor.fetchone()
        assert row["trigger_count"] == 5

    @pytest.mark.asyncio
    async def test_different_subjects_are_separate_incidents(
        self, db, patched_send_email,
    ):
        """provider_auth_failed for openai is a different incident
        from provider_auth_failed for anthropic; both should email."""
        await _add_recipient(db, "scott@example.com")
        a = await report_incident(
            db, category="provider_auth_failed", subject="openai", details={},
        )
        b = await report_incident(
            db, category="provider_auth_failed", subject="anthropic", details={},
        )
        assert a.is_new and b.is_new
        assert patched_send_email.await_count == 2


class TestAutoResolve:
    @pytest.mark.asyncio
    async def test_stale_open_incident_auto_resolves_on_next_report(
        self, db, patched_send_email,
    ):
        """When last_seen is older than INCIDENT_AUTO_RESOLVE_MINUTES,
        the next report opens a fresh incident (and re-emails)."""
        await _add_recipient(db, "scott@example.com")
        # First report.
        first = await report_incident(
            db, category="cq_unreachable", subject="cq", details={},
        )
        assert first.is_new is True

        # Manually age the row past the quiet window.
        stale = (
            datetime.now(timezone.utc)
            - timedelta(minutes=INCIDENT_AUTO_RESOLVE_MINUTES + 5)
        ).isoformat()
        await db.execute(
            "UPDATE alert_incidents SET last_seen_at = ? WHERE id = ?",
            (stale, first.incident_id),
        )
        await db.commit()

        # Re-report should auto-resolve the stale row and open a fresh one.
        second = await report_incident(
            db, category="cq_unreachable", subject="cq", details={},
        )
        assert second.is_new is True
        assert second.incident_id != first.incident_id
        assert patched_send_email.await_count == 2

        # The original row should now be resolved.
        cursor = await db.execute(
            "SELECT resolved_at FROM alert_incidents WHERE id = ?",
            (first.incident_id,),
        )
        assert (await cursor.fetchone())["resolved_at"] is not None

    @pytest.mark.asyncio
    async def test_stale_open_incident_resolves_on_list(
        self, db, patched_send_email,
    ):
        """Loading the dashboard list sweeps stale-but-quiet opens, so a
        one-off incident with nothing alerting after it shows as resolved
        instead of sticking open forever (the openrouter_low_balance case)."""
        first = await report_incident(
            db, category="provider_budget_exhausted",
            subject="openrouter_low_balance", details={},
        )
        assert first.is_new is True

        # Age it past the quiet window; nothing re-fires after this.
        stale = (
            datetime.now(timezone.utc)
            - timedelta(minutes=INCIDENT_AUTO_RESOLVE_MINUTES + 5)
        ).isoformat()
        await db.execute(
            "UPDATE alert_incidents SET last_seen_at = ? WHERE id = ?",
            (stale, first.incident_id),
        )
        await db.commit()

        # Loading the list alone should sweep it to resolved.
        rows = await list_incidents(db)
        row = next(r for r in rows if r["id"] == first.incident_id)
        assert row["status"] == "resolved"
        assert row["resolved_at"] is not None


class TestSubscriptionFiltering:
    @pytest.mark.asyncio
    async def test_recipient_with_category_subscription_only_gets_those(
        self, db, patched_send_email,
    ):
        """Recipient subscribed to cq_unreachable only should not get
        provider_auth_failed emails."""
        await _add_recipient(db, "cq-only@example.com", categories=["cq_unreachable"])
        await report_incident(
            db, category="cq_unreachable", subject="cq", details={},
        )
        await report_incident(
            db, category="provider_auth_failed", subject="openai", details={},
        )
        # Only the CQ incident should have triggered an email to this recipient.
        assert patched_send_email.await_count == 1

    @pytest.mark.asyncio
    async def test_recipient_with_no_categories_gets_everything(
        self, db, patched_send_email,
    ):
        await _add_recipient(db, "all@example.com", categories=None)
        await report_incident(db, category="cq_unreachable", subject="cq", details={})
        await report_incident(db, category="provider_auth_failed", subject="openai", details={})
        assert patched_send_email.await_count == 2

    @pytest.mark.asyncio
    async def test_inactive_recipient_does_not_get_emails(
        self, db, patched_send_email,
    ):
        await _add_recipient(db, "paused@example.com", active=False)
        result = await report_incident(
            db, category="cq_unreachable", subject="cq", details={},
        )
        assert result.is_new is True
        assert result.emailed_to == []
        assert result.suppressed_reason == "no_recipients"
        assert patched_send_email.await_count == 0


class TestNoRecipientsCase:
    @pytest.mark.asyncio
    async def test_incident_recorded_even_with_zero_recipients(
        self, db, patched_send_email,
    ):
        """The history table records the incident regardless. We don't
        want to skip writing to alert_incidents because someone forgot
        to add a recipient — the dashboard still surfaces it."""
        result = await report_incident(
            db, category="cq_unreachable", subject="cq", details={},
        )
        assert result.is_new is True
        assert result.suppressed_reason == "no_recipients"
        assert patched_send_email.await_count == 0

        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM alert_incidents "
            "WHERE fingerprint = ?", ("cq_unreachable:cq",),
        )
        assert (await cursor.fetchone())["c"] == 1


class TestResendTagShape:
    @pytest.mark.asyncio
    async def test_send_includes_stack_gp_tag(self, db, patched_send_email):
        """Every alert send must carry a `stack=gp` tag so analytics on
        the shared Resend account (GP alerts + CQ alerts on the same
        sender domain) can cleanly partition traffic per stack.
        Coordinated with CQ team 2026-05-21 — CQ uses stack=cq on their
        side."""
        await _add_recipient(db, "scott@example.com")
        await report_incident(
            db, category="cq_unreachable", subject="cq", details={},
        )
        assert patched_send_email.await_count == 1
        call_kwargs = patched_send_email.await_args.kwargs
        tags = call_kwargs.get("tags") or []
        tag_map = {t["name"]: t["value"] for t in tags}
        assert tag_map.get("stack") == "gp", (
            f"expected stack=gp tag in {tags}"
        )
        # Sanity: the other two tags we've always had stay in place.
        assert tag_map.get("purpose") == "critical-alert"
        assert tag_map.get("category") == "cq_unreachable"


class TestSendFailureResilience:
    @pytest.mark.asyncio
    async def test_send_email_raising_does_not_propagate(self, db):
        """If Resend transport raises, the alert call must still
        return successfully. The triggering request must not break
        because alerting failed."""
        await _add_recipient(db, "scott@example.com")
        with patch(
            "app.services.alerting.send_email",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated transport failure"),
        ):
            result = await report_incident(
                db, category="cq_unreachable", subject="cq", details={},
            )
        # Incident still opened (we want it in history).
        assert result.is_new is True
        # Nobody got the email; the failure was swallowed.
        assert result.emailed_to == []


class TestHistoryListing:
    @pytest.mark.asyncio
    async def test_list_incidents_returns_open_and_resolved_newest_first(
        self, db, patched_send_email,
    ):
        await _add_recipient(db, "scott@example.com")
        # Two open incidents under different fingerprints.
        await report_incident(db, category="cq_unreachable", subject="cq", details={})
        await report_incident(db, category="provider_auth_failed", subject="openai", details={})

        rows = await list_incidents(db)
        assert len(rows) == 2
        # Newest first (the second insert came later).
        assert rows[0]["category"] == "provider_auth_failed"
        assert rows[1]["category"] == "cq_unreachable"
        assert all(r["status"] == "open" for r in rows)


# ---------------------------------------------------------------------------
# Admin endpoint integration tests (CRUD + test-send through HTTP)
# ---------------------------------------------------------------------------

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}


class TestRecipientCRUDEndpoints:
    def test_categories_endpoint_lists_known_categories(self, client):
        resp = client.get(
            "/webhooks/admin/alerts/categories",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        ids = {c["id"] for c in body["categories"]}
        assert "cq_unreachable" in ids
        assert "provider_auth_failed" in ids
        assert "provider_budget_exhausted" in ids

    def test_create_and_list_recipient(self, client):
        create = client.post(
            "/webhooks/admin/alerts/recipients",
            headers=ADMIN_HEADERS,
            json={
                "email": "Scott@Example.COM",
                "display_name": "Scott",
                "categories": ["cq_unreachable"],
            },
        )
        assert create.status_code == 200, create.text
        # Email should be normalized to lowercase.
        assert create.json()["email"] == "scott@example.com"

        listing = client.get(
            "/webhooks/admin/alerts/recipients", headers=ADMIN_HEADERS,
        )
        assert listing.status_code == 200
        rows = listing.json()["recipients"]
        assert len(rows) == 1
        assert rows[0]["email"] == "scott@example.com"
        assert rows[0]["categories"] == ["cq_unreachable"]
        assert rows[0]["active"] is True

    def test_create_rejects_invalid_email(self, client):
        resp = client.post(
            "/webhooks/admin/alerts/recipients",
            headers=ADMIN_HEADERS,
            json={"email": "not-an-email"},
        )
        assert resp.status_code == 400

    def test_create_rejects_unknown_category(self, client):
        resp = client.post(
            "/webhooks/admin/alerts/recipients",
            headers=ADMIN_HEADERS,
            json={"email": "scott@example.com", "categories": ["nonsense"]},
        )
        assert resp.status_code == 400
        assert "unknown" in resp.json()["detail"].lower()

    def test_create_dup_email_returns_409(self, client):
        client.post(
            "/webhooks/admin/alerts/recipients",
            headers=ADMIN_HEADERS,
            json={"email": "scott@example.com"},
        )
        dup = client.post(
            "/webhooks/admin/alerts/recipients",
            headers=ADMIN_HEADERS,
            json={"email": "scott@example.com"},
        )
        assert dup.status_code == 409

    def test_patch_toggles_active(self, client):
        create = client.post(
            "/webhooks/admin/alerts/recipients",
            headers=ADMIN_HEADERS,
            json={"email": "scott@example.com"},
        )
        rid = create.json()["id"]
        patch_resp = client.patch(
            f"/webhooks/admin/alerts/recipients/{rid}",
            headers=ADMIN_HEADERS,
            json={"active": False},
        )
        assert patch_resp.status_code == 200
        listing = client.get(
            "/webhooks/admin/alerts/recipients", headers=ADMIN_HEADERS,
        ).json()["recipients"]
        assert listing[0]["active"] is False

    def test_delete_removes_recipient(self, client):
        create = client.post(
            "/webhooks/admin/alerts/recipients",
            headers=ADMIN_HEADERS,
            json={"email": "scott@example.com"},
        )
        rid = create.json()["id"]
        delete = client.delete(
            f"/webhooks/admin/alerts/recipients/{rid}", headers=ADMIN_HEADERS,
        )
        assert delete.status_code == 200
        listing = client.get(
            "/webhooks/admin/alerts/recipients", headers=ADMIN_HEADERS,
        ).json()["recipients"]
        assert listing == []

    def test_admin_key_required(self, client):
        resp = client.get("/webhooks/admin/alerts/recipients")
        assert resp.status_code in (400, 401, 403, 422)  # auth not provided


class TestTestSendEndpoint:
    def test_test_send_with_no_recipients_returns_suppressed(self, client):
        resp = client.post(
            "/webhooks/admin/alerts/test-send",
            headers=ADMIN_HEADERS,
            json={"category": "cq_unreachable"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_new"] is True
        assert body["suppressed_reason"] == "no_recipients"

    def test_test_send_rejects_unknown_category(self, client):
        resp = client.post(
            "/webhooks/admin/alerts/test-send",
            headers=ADMIN_HEADERS,
            json={"category": "nonsense"},
        )
        assert resp.status_code == 400


class TestIncidentHistoryEndpoint:
    def test_history_includes_test_sends(self, client):
        # Trigger a test-send → creates an incident.
        client.post(
            "/webhooks/admin/alerts/test-send",
            headers=ADMIN_HEADERS,
            json={"category": "cq_unreachable", "note": "deliverability check"},
        )
        history = client.get(
            "/webhooks/admin/alerts/incidents", headers=ADMIN_HEADERS,
        ).json()["incidents"]
        assert len(history) >= 1
        assert history[0]["category"] == "cq_unreachable"
        assert history[0]["subject"].startswith("test-send/")
