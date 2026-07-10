# Documents phase 2 — returned files

Status: DRAFT for approval (Scott GO 2026-07-10). Designed to be shared
with SS — they asked to be at the table from day one, and the client UX
decisions in §7 are theirs to shape. Phase 1 (input fidelity) is shipped
and untouched by this document.

## 1. Goal

The user gets real files back. Three user stories, in increasing order of
plumbing:

1. **Generate from content**: "turn this meeting into a tracking
   spreadsheet" → a real `.xlsx` lands in the chat. No input file needed —
   the source is the conversation/meeting context GP already assembles.
2. **Transform an attached file**: attach the weekly status deck, say
   "update this for this week" → an updated `.pptx` comes back with the
   original's layout, branding, and structure intact.
3. **Iterate**: the returned file becomes a Reference, gets re-attached
   with more asks, and versions forward.

Story 2 also retires the phase-1 office-format stopgap as a side effect:
the mechanism that transforms a pptx must be able to *read* a raw pptx,
which is the same gap xlsx input has today.

## 2. Architecture

**Recommended: the provider's execution sandbox + files surface** (option
A). The upstream platform exposes a sandboxed execution environment with
document libraries preinstalled and a files API for moving bytes in and
out. The flow:

```
client ──documents field──▶ GP ──upload──▶ provider files
                            GP ──chat + sandbox tools──▶ model works the file
                            GP ◀──download generated files──
client ◀──file references in chat response── GP (stores + serves bytes)
```

- The model opens the RAW file programmatically (real spreadsheet
  structure, real deck internals — not our extraction), does the work,
  and writes output files the sandbox captures.
- The provider also ships prebuilt document-generation skills (pptx,
  xlsx, docx, pdf) that produce high-quality artifacts — we get document
  quality without authoring generation prompts ourselves.
- GP downloads the outputs, stores them, and serves them to the client.
  The client never learns any of this — it sees "the answer came with
  files" (relay opacity holds; no provider details cross the wire).

**Rejected: GP-local generation** (option B — python-pptx pipelines on
our box, the way the ABM deck was hand-built). Deterministic and cheap,
but it only does template-edits we pre-program; every new document
operation is GP engineering. Kept as a fallback idea if sandbox economics
surprise us.

**Explicitly retired by this design**: the office-format middle options
from phase 1 (embedded-image cherry-picking, LibreOffice conversion) —
the sandbox subsumes both.

## 3. Wire contract (client-facing)

Additive response field on the chat response (decoder-safe):

```json
"generated_files": [
  {
    "file_id": "gpf_…",
    "name": "ABM Summary_Jul10.pptx",
    "media_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "size_bytes": 812345,
    "url": "https://…/v1/generated-files/gpf_…",
    "expires_at": "2026-08-09T00:00:00Z"
  }
]
```

- `url` is GP-served, authenticated (bearer), and time-limited.
- Absent field = no files (every response today). Nothing else on the
  wire changes; the request side is phase 1's `documents` field.
- Config: the existing `documents` key grows a `generation` subkey
  (`enabled`, `min_tier` or matrix cell, `formats`, `max_files_out`,
  `max_file_out_mb`) — same served-config pattern, client reads it to
  show/hide the capability, server enforces.

## 4. GP-side file store (the deferred decision lands here)

Phase 1 deliberately refused to make GP a store of user files. Phase 2
cannot: generated artifacts must live somewhere the client can fetch
them. Designed once, properly:

- New table `generated_files(id, user_id, app_id, name, media_type,
  size_bytes, storage_path, created_at, expires_at)` + bytes on disk
  under the persistent volume (Litestream/DR posture documented).
- **Ownership**: files belong to the requesting user; the serve endpoint
  authenticates and checks ownership.
- **Retention**: default 30 days, then purged (startup sweep, same
  pattern as meeting_reports). The client's "save as Reference" is the
  user's way to keep one — the durable copy lives in their References,
  on their device/their storage, not on GP.
- **Quota**: per-user cap (e.g. 500MB live storage); generation refuses
  politely when full.
- This same store CAN later back References upload-once/reference-by-id
  (the phase-1 deferral) — but that is NOT in phase 2 scope; resend
  works and stays.

## 5. Entitlements, metering, budget

- Gate: Pro at launch, expressed as its own feature (`document_generation`)
  so the entitlements matrix (separate design, in review) can move it or
  grant it per-user (IAP add-on candidate #1) without touching this code.
- Metering: one usage_log row per generation call chain, with sandbox
  time and file counts/bytes in metadata; cost flows through record_cost
  and counts against the user's budget like everything else.
- Cost reality: a generation call chain (sandbox + skills + iterations)
  will be the most expensive single feature in the product — likely
  cents-per-generation vs the ~$0.01 of a document read. Budget gate
  applies; a per-generation cost estimate should be measured in e2e and
  fed back into pricing/entitlement decisions.

## 6. Limits and failure semantics

Same philosophy as phase 1 — downgrade, never dead-end:

- Output caps served in config: `max_files_out` (2), `max_file_out_mb` (25).
- Sandbox execution timeout → the chat answer still returns, explaining
  what was produced or why not; partial artifacts are not served.
- Generation failure ≠ request failure: the model's text answer always
  comes back; `generated_files` is best-effort.
- Input side unchanged: phase-1 caps and ceilings govern what goes in.

## 7. Client UX (SS's table — open by design)

Decisions that are SS's to make, listed so the doc is a shared artifact:

- Render of a returned file in the chat transcript (card? inline
  preview? QuickLook?).
- Download / share / **save as Reference** affordances — save-as-Reference
  is the one we'd advocate for hardest, since it closes the iterate loop
  (story 3) with zero new wire.
- Whether generation is a distinct user intent ("Make me a…" affordance)
  or purely emergent from chat.
- Progress UX: generation runs longer than a chat turn (tens of seconds
  to minutes). Timing-hints / SSE heartbeat patterns from the honest-
  progress work apply.

## 8. Phasing

- **2a — generate from content** (no input file): meeting/project context
  → xlsx/docx/pptx out. Smallest plumbing (no input upload), full new
  surface (store, serve, wire, UX). Proves the economics.
- **2b — transform attached files**: phase-1 `documents` input rides into
  the sandbox raw. Retires the office extraction stopgap (raw xlsx/pptx
  reading arrives here for free).
- **2c — iterate loops**: returned file → Reference → re-attach. Mostly
  client UX (save-as-Reference) + regression coverage.

## 9. Open decisions for approval

1. Architecture: sandbox + provider files (recommended) vs GP-local
   generation vs hybrid (local template-edit fast path + sandbox general
   path).
2. Launch formats: xlsx + pptx + docx + pdf all at once, or xlsx-first
   (highest fidelity gap today)?
3. Retention window (proposed 30 days) and per-user quota (proposed 500MB).
4. Gate: Pro tier vs entitlement-matrix cell from day one (depends on
   entitlements design timing).
5. Phasing confirmation: 2a → 2b → 2c.
6. SS's §7 UX decisions — theirs, gathered in their design pass.
