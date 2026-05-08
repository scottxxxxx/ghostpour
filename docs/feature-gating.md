# Feature Gating System

> **Last updated:** March 30, 2026

Features have **three states per tier**, configured in `config/tiers.yml`:

| State | Behavior |
|-------|----------|
| **enabled** | Run the feature check, apply results to the query, capture on response |
| **teaser** | Run the feature check, return metadata headers to client, but **skip applying** results. Used for upgrade nudges. Returns `X-CQ-Gated: true` header |
| **disabled** | Feature doesn't run at all |

## How it works

1. **`config/features.yml`** defines each feature's metadata (display_name, description, teaser_description, upgrade_cta, category, service_module). Loaded at startup into `app.state.feature_config`.
2. **`config/tiers.yml`** sets per-tier state for each feature under the tier's `features:` dict (e.g., `context_quilt: "teaser"`).
3. **`POST /v1/chat`** checks each feature's state for the user's tier:
   - `enabled` → run check + apply results + capture on response
   - `teaser` → run check + return metadata headers + skip injection
   - `disabled` → skip entirely
4. **Client opt-out**: `ChatRequest.skip_teasers: list[str] | None` — client can suppress specific teaser features (e.g., after the user dismisses an upgrade prompt).

## Adding a new feature

1. Add an entry in `config/features.yml` with display metadata
2. Add per-tier state in `config/tiers.yml` under each tier's `features:` dict
3. Implement `check()`, `apply()`, `on_response()` functions in `app/services/<service_module>.py`

## Kill switch

Change a feature from `teaser` → `disabled` in `tiers.yml` and restart. No code changes needed.

## Context Quilt (first feature)

GhostPour integrates with Context Quilt as the first feature using the generic feature gating system. CQ runs when `context_quilt: true` is in the ChatRequest **and** the user's tier has CQ in `enabled` or `teaser` state.

**3-state behavior:**
- **enabled**: recall → inject context into system_prompt → capture query+response after LLM responds
- **teaser**: recall → return `X-CQ-Matched`/`X-CQ-Entities` headers + `X-CQ-Gated: true` → skip injection → skip capture
- **disabled**: skip entirely

**Recall (pre-route, synchronous):**
- Calls `POST {CQ_BASE_URL}/v1/recall` with the user's query text
- 200ms timeout — skips gracefully on timeout or error
- Injects returned context into `system_prompt` (replaces `{{context_quilt}}` placeholder, or prepends)
- Stashes the same recall text on `metadata.cq_recall_block`. Cache-aware adapters (currently only Anthropic) split `system_prompt` at this boundary into `[prefix, recall, suffix]` blocks with `cache_control: ephemeral` on prefix + recall. After CQ #89 made recall byte-stable across calls within a 5-min window for the same input, this lets the base prefix keep cross-turn caching even when recall content differs across turns. Adapters that ignore the metadata key consume `system_prompt` as a single string and behave exactly as before.

**Capture (post-response, async):**
- Fires background `POST {CQ_BASE_URL}/v1/memory` with query, LLM response, and metadata
- Never blocks the response to the user
- Includes `meeting_id`, `project`, `call_type`, `prompt_mode` in metadata
- **Capture skip list** — capture is suppressed for modes that consume or derive from existing quilt data (avoids echo/circular data). Capture only fires when ALL conditions are met:
  1. `cq_state == "enabled"`
  2. `context_quilt == true` in the request
  3. `prompt_mode` not in: `PostMeetingChat`, `ProjectChat`, `AutoSummary`, `PostSessionAnalysis`
  4. `session_duration_sec` is `None` (not an active recording — live transcripts are captured at session end via `/v1/capture-transcript`)
- **Why each mode is skipped:**
  - `PostMeetingChat` — user is querying the quilt, not generating new content
  - `ProjectChat` — output is AI-synthesized from content CQ already has
  - `AutoSummary` — machine-generated summary derived from transcript already captured
  - `PostSessionAnalysis` — machine-generated classification (sentiment/urgency/tags) from an already-captured transcript

**Quilt management (proxy):**
- `GET /v1/quilt/{user_id}` → proxies to `GET {CQ_BASE_URL}/v1/quilt/{user_id}` (fetch patches)
- `PATCH /v1/quilt/{user_id}/patches/{patch_id}` → proxies to CQ (update patch)
- `DELETE /v1/quilt/{user_id}/patches/{patch_id}` → proxies to CQ (delete patch)
- `GET /v1/quilt/{user_id}/graph?format=svg` → proxies to CQ (graph visualization, returns `image/svg+xml` or `image/png`)
- `POST /v1/quilt/{user_id}/prewarm` → proxies to `POST {CQ_BASE_URL}/v1/prewarm` (warm Redis cache at session start, <50ms)
- All require Bearer JWT; users can only access their own quilt
- iOS `QuiltService` routes through GhostPour rather than calling CQ directly

**Response headers (for the client app UI indicator):**
- `X-CQ-Matched`: number of entities matched (e.g., "3")
- `X-CQ-Entities`: comma-separated entity names (e.g., "Bob Martinez,Widget 2.0")
- `X-CQ-Gated`: `"true"` when CQ is in teaser mode (ran recall but didn't inject)

**ChatRequest fields:**
- `context_quilt: bool` — enable CQ for this request (default: false)
- `meeting_id: str | None` — meeting UUID for CQ queue grouping
- `project: str | None` — project display name for CQ metadata
- `project_id: str | None` — project UUID (iOS `Project.id`) for CQ patch grouping and project rename support
- `skip_teasers: list[str] | None` — client-side opt-out for teaser features (e.g., `["context_quilt"]`)

**Config:**
- `CZ_CQ_BASE_URL` — CQ endpoint (e.g., `https://cq.example.com`)
- `CZ_CQ_APP_ID` — app identifier for CQ auth (default: `cloudzap`)
- `CZ_CQ_RECALL_TIMEOUT_MS` — max wait for recall (default: 200)
