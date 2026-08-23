# Alignment Layer: the GP proxy (2026-08-23)

Scott's direction via CQ (Claude Design project e6ee7ae8). CQ owns the
feature and the contract: `docs/architecture/20-alignment-layer.md` in
their repo, declared in their `docs/openapi.yaml`, phase 1 live on their
main `54b5037`. GP proxies four routes and models nothing.

All four are app-authenticated like People: bearer JWT, the caller may
only reach their own `user_id` (403 otherwise), and the `alignment`
entitlement is enabled on every tier and checked anyway so the dashboard
toggle closes the door rather than hiding the tab.

| GP route | CQ route | Notes |
|---|---|---|
| `GET /v1/alignment/{user_id}/meetings/{origin_id}` | same | the meeting card; `events: []` is a 200 meaning no card, never a 404 |
| `GET /v1/alignment/{user_id}/projects/{project_id}` | same | the record: `current_directions`, `awaiting_confirmation`, `history`, `direction_change_count`, `cumulative_impact`, `definitions` |
| `POST /v1/alignment/{user_id}/events/{event_id}/confirm` | same | body `{confirmed_by, on_behalf}` forwarded verbatim; 409 `NOT_CONFIRMABLE` |
| `POST /v1/alignment/{user_id}/events/{event_id}/correct` | same | body `{statement, reason, corrected_by, rationale?}` forwarded verbatim; 422 `SHARED_TEXT_REJECTED` (carries `term`), 409 `CORRECTION_CONFLICT` (carries `existing` and `proposed`) |

## What is load bearing on this hop

**The 4xx bodies are the contract.** A client acts on `term`, on
`existing` and `proposed`. GP forwards CQ's status and JSON body
unchanged; a middlebox that drops 4xx bodies breaks the feature while
every status code stays correct. Pinned by test, with the sabotage that
blanks 4xx bodies going red on all three.

**Every array keeps its order**: `supersedes`, `impact`, `evidence`,
`history`. The only transform on this hop is `_null_non_finite`, which
rebuilds lists in place. Pinned against a fixture whose arrays are
deliberately not in sorted order.

**POST bodies are `dict`, not a model**, so a field CQ adds later crosses
without a GP deploy. Pinned request-side at the httpx boundary, including
an invented future key.

Nothing in any response is private. CQ never selects the one private
column, so there is nothing for GP to strip, and GP strips nothing.

Query strings are forwarded verbatim on the GETs.
