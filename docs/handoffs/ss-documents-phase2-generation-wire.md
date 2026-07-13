# Phase 2 generation wire — confirmation envelope + streaming (working doc)

Status: PINNED 2026-07-11 — SS confirmed Part 4 (+ honest-progress
refinement on 409/running bodies) and their UX positions
(docs/DOCUMENTS_PHASE2_UX.md, SS repo: confirm button, generating state,
four-state file card, stale card w/ pre-confirmed regenerate, spreadsheet
honesty state) conflict with nothing here. GP building the server
package. Changes from here: edit this doc.

Companion: `docs/design/documents-phase2-returned-files.md` (approved
phase-2 design; §10 is the envelope's origin). Phase-1 spec:
`ss-project-chat-documents-passthrough.md`.

Field evidence driving both parts: first live device generation
(2026-07-11) succeeded server-side in 124s and died on the client's then
120s timeout; the retest ran past 180s and died on GP's own provider
timeout. Generation turns have real runtime variance — the wire must stop
depending on anyone's fixed timeout.

---

## Part 1 — Confirmation envelope

### Flow

1. **Intent check (GP).** On generation-eligible turns (gate passes:
   surface, tier/allowed_users, managed routing, provider), GP runs a
   cheap intent classifier over the user ask (~$0.001, strict schema).
   Fail-open: classifier error or "not a file request" → the turn
   proceeds as normal chat. Nothing is armed.

2. **Offer.** On a detected file intent, GP does NOT run the main turn.
   It returns the standard feature-state envelope immediately:

   ```json
   {
     "feature_state": {
       "feature": "document_generation",
       "state": "confirmation_required",
       "cta": {
         "kind": "generation_offer",
         "text": "This looks like a file request. Generate a spreadsheet from this project? Takes about two minutes.",
         "action": "confirm_generation",
         "details": {
           "expected_format": "xlsx",
           "expected_seconds": 150
         }
       }
     }
   }
   ```

   `text` served + localized (3 locales). `expected_format` is the
   classifier's best guess — advisory, not binding on the model.
   `expected_seconds` is measured per call shape (honest-progress rule),
   not aspirational. `details` is add-only; a future `cost_credits`
   field slots here if consumable credits ever ship.

3. **Confirmed resend (SS).** The button resends the same request with
   `metadata.generation_confirmed = true`. The client now KNOWS this is
   a generation turn and switches to the generation transport (Part 2)
   and long-running progress UI — never a chat spinner.

4. **GP on the confirmed resend:** skips the classifier, arms
   generation. The flag alone grants nothing — gate still required; a
   spoofed flag on an ineligible account is a normal chat turn.

### Arming rule

Once this ships, generation arms ONLY on `generation_confirmed = true`.

- The un-signaled multi-minute turn — the entire timeout bug class —
  stops existing as a category.
- Casual phrasing can't trigger sandbox cost by accident.
- Classifier false negatives: SS's manual "generate as file" affordance
  sends the confirmed flag directly, no classifier involved.
- False positives: user ignores the button or answers normally; nothing
  ran, nothing billed.

### SS-owned surfaces (awaiting written positions)

- Confirm button rendering inside the existing feature-state CTA family.
- The manual "generate as file" affordance and its placement.
- Progress UX for the confirmed turn (Part 2 gives it real events).

---

## Part 1 v2 — Conversational confirmation (SS design revision, PINNED 2026-07-12)

Supersedes the button-primary flow. The offer IS a chat message; the
reply IS the confirmation. The manual generate-as-file path with
`generation_confirmed` stays exactly as designed (explicit fallback).

### Flow

1. Intent check unchanged — but the classifier now also returns a `gist`
   (short phrase, in the user's language) and the envelope's `cta.text`
   is composed conversationally from served, localized templates:
   "Sounds like you want a Word document for onboarding new people. Want
   me to build it?" The client renders that text VERBATIM as an assistant
   chat message and persists it in history as an assistant turn.
2. The envelope's `cta.details` now carries `offer_id` (and `gist`). GP
   remembers the live offer server-side for ONE reply (10-minute TTL).
3. **Echo fields (Q2):** the next send in that conversation carries
   `metadata.offer_id` (from the envelope) and `metadata.generation_id`
   (client-minted, as in Part 4). Field names exactly those.
4. **Reply interpretation (Q1):** GP judges the reply against the offer
   on that same send — no extra round trip. A yes (any language, any
   casual phrasing) arms generation on THAT turn: the minted
   generation_id becomes the rescue id, the turn re-resolves onto the
   first-send lane, and the response is the Part 2 SSE event stream. A
   no / unrelated reply is a normal chat turn; both ids are discarded.
   Either way the offer is dead after one reply — and a fresh file
   intent in the reply itself simply produces a fresh offer.
5. **Modification replies (Q4): confirm-with-revised-intent.** "Actually
   make it a spreadsheet" arms generation immediately with the revised
   format — no second offer, no extra round trip. The interpreter
   returns the revised format; the generation turn reads the
   conversation (original ask + offer + reply) for full context.
6. **Artifacts (Q5): nothing changes.** Chat-confirmed turns are the
   same armed turns: `generation_result` carries `generated_files`
   identically, download-on-land and file cards as agreed.

### Client contract notes

- A send that echoes an `offer_id` must be prepared for EITHER a normal
  JSON answer or the generation SSE stream — SS stated this property and
  it is load-bearing: the client never needs to know how GP judged.
- The offer store is in-memory (same argument as the running registry):
  a GP restart kills pending offers; the echoed id finds nothing and the
  turn is normal chat. The user asks again, or uses the manual path.
- The generation turn relies on the client's conversation assembly
  carrying the original ask and the persisted offer message — which SS's
  history-persistence position already guarantees.
- Interpreter cost ~$0.001 (same classifier model), metered as
  `generation_intent` usage rows. Fail-open: interpreter failure =
  non-confirm, never a broken turn.

## Part 2 — Streaming wire sketch (generation transport)

### Shape

The confirmed resend is answered as SSE — on every surface, including
Project Chat (which is JSON for normal turns). Event family:

```
event: generation_started
data: {"expected_seconds": 150, "expected_format": "xlsx"}

event: generation_progress        (every ~5s until done)
data: {"elapsed_seconds": 35, "expected_seconds": 150, "phase": "executing", "round": 1}

event: generation_result          (terminal, exactly one)
data: { ...full non-stream response body: text, generated_files[], usage... }

event: generation_error           (terminal, exactly one, instead of result)
data: {"code": "generation_timeout", "message": "..."}
```

- `generation_result.data` is byte-identical in shape to today's
  non-stream JSON response — the download-on-land path SS already built
  consumes it unchanged.
- Heartbeat cadence ~5s; the client treats "no event for 30s" as the
  only timeout it needs. No fixed whole-request ceiling anywhere.
- `phase` vocabulary (add-only): `starting`, `working`, `executing`,
  `collecting`. `round` counts provider continuation rounds.

### Phasing — A ships without provider streaming

**Phase A (fast):** GP runs the provider call exactly as today
(non-stream, internal) and emits timer-based heartbeats while waiting:
`generation_started` on arming, `generation_progress` with elapsed vs
expected every 5s, `generation_result` when the call returns and
artifacts are collected. Honest by the progress rules — elapsed against
a measured expectation, no fake precision. This alone kills the timeout
class end to end and needs no provider-side changes.

**Phase B (later):** GP consumes the provider's stream server-side and
upgrades `phase`/`round` to real signals — continuation-round boundaries
(pause_turn) are natural progress markers, and code-execution tool
events mark `executing`. Same wire events, better data; SS sees no
contract change.

### Meeting Chat unlock

Meeting Chat's normal turns keep streaming tokens exactly as today. A
confirmed generation resend on Meeting Chat rides the generation event
family instead of the token stream. That makes generation available on
the surface where a user most naturally asks for it — launch-relevant
per SS, and this sketch is the mechanism.

### GP-side reliability commitments (same build)

- Provider timeout for generation-armed legs raised well past the
  observed variance (Phase A removes the client-window constraint;
  internal ceiling ~400s, config-tunable).
- Generation-armed turns are EXCLUDED from the OR fallback: OR cannot
  arm the sandbox, so a fallback there silently converts "make me a
  file" into a text answer — and a timed-out provider leg may have
  completed and billed with nobody collecting the artifact. A failed
  generation leg surfaces as `generation_error`; the client retry (one
  tap, the confirm button again) is the recovery.

---

## Part 3 — Pinned wire facts (answered 2026-07-11, restated for one-doc reference)

- **Download auth:** GET `/v1/generated-files/{id}` requires the same
  JWT bearer as chat; owner-only; missing / expired / not-yours are one
  indistinguishable 404; `Cache-Control: private, no-store`.
- **Expiry:** exactly 6h from creation; purge sweeps hourly + startup;
  the serve path checks expiry itself. Interrupted download → restart
  the GET (≤25MB files, no resume protocol). Expired → gone by design
  (the URL is a fetch window, not storage); recovery is regenerate.
  Stale-card UX: SS proposes the regenerate affordance in writing.
- **Entry metadata (SHIPPED #385):** every `generated_files` entry
  carries `name`, `media_type`, `size_bytes`, `sha256`, `url`,
  `expires_at`. Card renders instantly; background download verifies
  against the checksum. Add-only.
- **Sync / share bundles:** never place the GP URL in an iCloud sync or
  cross-user share bundle — owner-bound, JWT-gated, 6h-lived. Ship the
  persisted bytes. Once persisted client-side, GP staging, expiry, and
  metering are unaffected; generation cost belongs to the generator at
  creation; shares cost nothing.
- **xlsx reference loop (2c honesty gap):** a generated spreadsheet
  saved as a Reference cannot ride the `documents` field today (xlsx is
  not in `accepted_types` — extraction-hostile). Full-circle re-attach
  for spreadsheets arrives with 2b sandbox reading. Until then the
  Reference card should message it honestly; SS proposes the copy.

---

## Part 4 — Mid-turn death rescue (answering SS's open question, 2026-07-11)

The question: the app dies mid-generation (user backgrounds out, iOS
kills it, phone dies), GP completes and stages the file, the response
lands on a dead connection. Pending-generations lookup, or idempotent
confirmed resend? **Answer: both, as one mechanism, keyed by one value —
and SS persists exactly one uuid of per-turn state.**

Field evidence this matters: the very first live generation already hit
this shape (completed server-side, client gone at response time — that
one was a timeout, but the wire outcome is identical to an app death).

### Mechanism

1. **Client mints a `generation_id`** (uuid) and sends it in
   `metadata.generation_id` on every confirmed resend. Required on
   confirmed sends once this ships.
2. **GP records the turn against that id**: staged artifacts, the model's
   text answer, and terminal status, all retained on the same 6h clock as
   the staging bytes (one expiry, one purge). This is new-but-tiny
   retention — confirmed generation turns only, gone in 6h.
3. **Rescue lookup:** `GET /v1/generations/{generation_id}` — same JWT
   bearer, owner-scoped, uniform 404 for not-yours / expired / never-
   arrived. Responses:
   - `200 {status: "done", text, generated_files: [...same entries as the
     live response, sha256 included...]}` — reconstruct the full turn:
     chat bubble + file cards, download as normal.
   - `200 {status: "failed", error: {...}}` — render the failure +
     regenerate affordance.
   - `200 {status: "running", started_at, elapsed_seconds,
     expected_seconds, poll_after_seconds}` — turn still in flight
     (in-memory registry; see caveat below). A relaunched client resumes
     the honest progress card from the TRUE elapsed time — never an
     elapsed-from-zero timer (SS refinement, 2026-07-11). Poll again
     after `poll_after_seconds`.
   - `404` — unknown here: never arrived, expired, or GP restarted
     mid-turn. After the client's own patience window: regenerate card.
4. **Idempotency falls out for free:** a confirmed resend whose
   `generation_id` is already terminal returns the stored result — no
   re-run, no second sandbox bill. Same id currently running → `409`
   whose body carries `{code: "generation_in_progress", started_at,
   elapsed_seconds, expected_seconds, poll_after_seconds}` — the same
   honest-progress fields as the running lookup, so the client resumes
   the correct progress card directly from the 409. Blind retry with the
   same id is therefore always safe.

### What SS persists per confirmed turn

The `generation_id` plus where the result belongs (meeting/project +
transcript position). Nothing else — no partial response state, no URL,
no metadata. On relaunch: for each unresolved persisted id, poll the
lookup; `done` reconstructs, `failed`/timeout offers regenerate.

### Caveats, stated honestly

- `running` status lives in memory: a GP deploy/restart mid-turn kills
  the in-flight turn anyway (its provider connection dies with the
  process), so post-restart those ids resolve 404 → regenerate. Rare and
  self-consistent; not worth durable in-flight state.
- The stored `text` makes the rescue reconstruct the WHOLE turn, not
  just the file. This is the one place GP briefly retains an SS chat
  answer; 6h, purged with staging, generation turns only.
- Interaction with Part 2: `generation_result` over SSE and the rescue
  lookup return the same body shape — one parser on the client.

### Status

**CONFIRMED by SS 2026-07-11 — FROZEN, building** (envelope + transport +
rescue as one server package; shared generation_id plumbing).

---

## Part 5 — Error contract: codes + typed details (SHIPPED 2026-07-12)

Codes are the contract; `message` is the developer-facing English
fallback; `details` carries every interpolated value as a TYPED field so
the client composes localized strings from data. Model and provider
identities never appear in any field.

HTTP error bodies: `{"detail": {"code", "message", "details?"}}`.
The `generation_error` SSE event carries the SAME family in its data:
`{"code", "message", "details?"}`.

| code | details fields |
|---|---|
| `document_too_large` | `file`, `size_mb`, `max_mb` |
| `too_many_documents` | `max_files` |
| `document_unreadable` | `file` (covers bad base64, unparseable bytes, and parse timeouts) |
| `generation_in_progress` (409) | `started_at`, `elapsed_seconds`, `expected_seconds`, `poll_after_seconds` |
| `invalid_request` | none today |
| `rate_limited` | none today (retry-after rides the header) |
| `provider_error` | none (deliberately opaque) |
| budget envelope codes | unchanged — the existing CTA family you already render |

Add-only promise: codes never disappear or change meaning; new codes and
new details fields may appear. Unknown fields are ignorable.

### Phase tokens (Part 2 heartbeats) — for your localization map

`phase` is a fixed enum of wire tokens, never display strings:
`starting`, `working`, `executing`, `collecting`. Map and localize
client-side; new tokens may appear (ignore unknown gracefully). Real
phase signals arrive with Phase B; the tokens are stable now.

### Rescue polling pattern (pinned from Q8)

Poll `GET /v1/generations/{id}` only while a surface holding an
unresolved id is in the FOREGROUND, at `poll_after_seconds` cadence;
stop when backgrounded. Open-time polling plus the in-band result is
the intended pattern — never a background timer.
