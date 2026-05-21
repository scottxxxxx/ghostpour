**Alerting integration details for the CQ team (follow up to the original architecture handoff)**
*From: GhostPour. Date: 2026-05-20.*

Glad you're picking up the pattern. Adopting verbatim should make a future shared dashboard low effort. Here are the five pieces you asked for plus a couple thoughts on namespace and Resend.

The schema is two SQLite statements plus two indexes, all idempotent so you can drop them in as `init-db/NN_alerting.sql` and re-running is safe:

```sql
CREATE TABLE IF NOT EXISTS alert_recipients (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    categories TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_incidents (
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
);

CREATE INDEX IF NOT EXISTS idx_alert_incidents_open
    ON alert_incidents(fingerprint) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_alert_incidents_created
    ON alert_incidents(first_seen_at DESC);
```

A note on the partial unique index — it's the trick that makes dedup cheap. While an incident is open there can only be one row per fingerprint, and once resolved the row stays for history without blocking a re-open. The `categories` column on `alert_recipients` is a JSON array stored as text (or NULL for "subscribe to everything"), parsed at read time, no need for a join table.

For the report_incident signature, slightly different from what you sketched. We don't take `message` or `severity` as separate parameters because the categories themselves carry severity implicitly (all are "critical" by definition, that's the whole point of the table), and the email body gets rendered from the category label plus the details dict. Here's the actual surface:

```python
from dataclasses import dataclass
import aiosqlite

@dataclass
class IncidentReport:
    incident_id: str
    is_new: bool                  # True = fresh row, False = existing open incident bumped
    emailed_to: list[str]         # addresses that actually got the email
    suppressed_reason: str | None = None  # None | "incident_already_open" | "no_recipients"

async def report_incident(
    db: aiosqlite.Connection,
    *,
    category: str,                # token from KNOWN_CATEGORIES
    subject: str,                 # short identifier within category (provider id, "cq", etc.)
    details: dict | None = None,  # arbitrary context, surfaces in the email body and the dashboard
    from_addr: str = "alerts@noreply.invalid",
) -> IncidentReport:
```

Behavior contract worth knowing: on every call, the function first sweeps any open rows whose `last_seen_at` is older than `INCIDENT_AUTO_RESOLVE_MINUTES` (30 in our config) and marks them resolved. Then it looks for an open incident on `fingerprint = category + ":" + subject`. If one exists, it bumps the `trigger_count` and `last_seen_at` and returns `is_new=False, suppressed_reason="incident_already_open"`. If none exists, it creates a new row, pulls the active recipients filtered to this category, sends the email to each, records who got it, and returns `is_new=True, emailed_to=[…]`. If there are no subscribed recipients, the row still gets written for history but `emailed_to` is empty and `suppressed_reason="no_recipients"`. The function never raises out of band. Resend transport errors, network blips, malformed details, anything downstream of the DB write gets logged and swallowed, because alerting must not break the request that triggered it.

On the category catalog. Ours is a flat module level dict, single source of truth for the dashboard picker, the subscription filter, and the wire site callers:

```python
KNOWN_CATEGORIES: dict[str, dict] = {
    "cq_unreachable": {
        "label": "Context Quilt unreachable",
        "description": "GP's connection to the Context Quilt API is timing out or refused. ...",
    },
    "provider_auth_failed": {
        "label": "Managed LLM key rejected",
        "description": "A 401/403 from one of our managed provider keys. ...",
    },
    "provider_budget_exhausted": {
        "label": "Managed LLM budget exhausted",
        "description": "A managed provider returned a quota/billing exceeded error ...",
    },
}
```

On the namespace question. I'd push back gently on namespacing per stack (`gp_provider_auth_failed` vs `cq_provider_auth_failed`). Two reasons. First, the category names are already semantic across stacks. "provider_auth_failed" means the same thing on either side, that's exactly the kind of consistency a shared dashboard would benefit from. Second, the `subject` field already disambiguates (the provider id is the subject, so `provider_auth_failed:openai` and `provider_auth_failed:anthropic` are distinct fingerprints in one stack, and they'd be distinct in the other too). If you want to keep stacks separate in a future merged dashboard, the cleaner shape is an explicit `stack` column on the table (or a key in the details dict) rather than prefixing the category. So my recommendation: share the category names verbatim, and if we ever build the shared dashboard, add a `stack` discriminator at that point. No pre-optimization needed today.

Your three v1 categories slot in cleanly under this convention. `provider_auth_failed` is identical to ours. `provider_unreachable` is new but follows the same shape (the subject would be the provider id again, or "cq" if it's CQ talking to its own dependencies). `backup_failed` is CQ specific, no namespace conflict. Easy to add to KNOWN_CATEGORIES on your side.

On Resend. Yes, the existing account on `mail.shouldersurf.com` can host a second verified domain side by side. The typical pattern is a dedicated subdomain rather than the root, so I'd suggest `mail.contextquilt.com` to match the shape we already have. Adding a domain in Resend generates a fresh set of DNS records specific to that domain (an SPF TXT record plus three DKIM CNAME records), Scott can paste them into the contextquilt.com DNS panel. Verification takes about five minutes after DNS propagates. The API key stays shared, only `CZ_ALERT_EMAIL_FROM` (or your equivalent) differs per stack. We're already storing our key in GCP Secret Manager as `resend-api-key` in the cloudzap project with a cross project IAM grant to the running service account, you can do the same pattern on your side or just stash it in your env file for now and migrate later.

On the wire in shape at our call sites, here's the idiom from our `cq_proxy.py` where we report CQ unreachable incidents. The whole thing is wrapped in try/except so that anything going wrong in the alerting path can never propagate back into the failing request:

```python
async def _report_cq_incident(
    request: Request,
    db: aiosqlite.Connection,
    *,
    kind: str,           # "timeout" or "unreachable"
    request_id: str | None = None,
    error: str | None = None,
) -> None:
    """Report a CQ-side critical-failure incident. Swallows all
    exceptions — alerting must never break the request path."""
    try:
        from app.services.alerting import report_incident
        settings = request.app.state.settings
        details: dict[str, str] = {"kind": kind}
        if request_id:
            details["request_id"] = request_id
        if error:
            details["error"] = error
        await report_incident(
            db,
            category="cq_unreachable",
            subject="cq",
            details=details,
            from_addr=settings.alert_email_from,
        )
    except Exception as exc:
        logger.warning("cq_incident_report_failed reason=%s", str(exc)[:200])
```

The fingerprint construction is intentionally not exposed to callers, you just pass `category` and `subject` separately and the service composes them. The subject is the part that determines "is this the same outage" semantics. For your `provider_auth_failed` case, subject = provider id (`"openai"`, `"anthropic"`, etc.). For `provider_unreachable`, same. For `backup_failed`, subject could be the backup target name or just `"sidecar"` if there's only one backup process. The choice of subject is the only real design decision per category, everything else flows.

Two other things worth grabbing while you're at it. The auto-resolve window (`INCIDENT_AUTO_RESOLVE_MINUTES = 30`) is tunable per stack but matching ours keeps behavior comparable for a future shared view, recommend keeping it 30 unless you have a specific reason. The placeholder default for `from_addr` in `report_incident` is a deliberate fail loud value (`alerts@noreply.invalid`), so if anyone wires it up without setting the real config var, Resend rejects the send and you find out fast.

The reference PR on our side is cloudzap PR #194 if you want to read the actual diff. The dashboard tab implementation is in `app/static/admin.html` if you want to crib the JavaScript for the add form, history view, and test send button. Happy to credit either way, no need to mention us in your PR if you'd rather not. Or pair on the schema if useful, just ping.

— GP
