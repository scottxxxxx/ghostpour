# Project Chat documents passthrough — wire spec (phase 1)

Status: FROZEN 2026-07-08 (server shipped #360, dark behind config; format
roadmap #363; References addendum below). Answers SS's four spec questions
from their acceptance note. Phase 1 is input fidelity only; returning
generated files is a separate phase 2 design.

## References addendum (2026-07-09)

SS extends document attachments to their saved **References library**: a
file imported once and attached to many sends. Decisions:

- **The wire is origin-agnostic.** A saved reference and a one-off chat
  attachment send the identical `documents` entry; GP neither knows nor
  cares which it was. No new fields.
- **Launch contract is RESEND**: the client sends the base64 bytes on every
  request that attaches the reference, under the served caps
  (per_file_max_mb / max_files apply **per request**, references and
  one-off attachments combined). No upload-once / reference-by-id shape in
  phase 1 — that would make GP a store of user files (ownership, retention,
  deletion lifecycle) which phase 2's returned-files design will justify
  properly; designing it twice is worse than once.
- **Repeat-send cost is handled on our side**: resending the same document
  within a short window bills its tokens at a small fraction of full input
  price (server-side caching, transparent, no wire impact). SS does not
  need to build anything for this.
- One product note for SS: a sticky reference attached to EVERY send of a
  long session still re-reads the document each turn. Attach-per-message
  (user intent) will feel better on budgets than attach-per-session
  (ambient), whatever the UI ends up being.

## Behavior summary

Users can attach a PDF or PPTX to a Project Chat message. When the chat is
routed through Shoulder Surf AI **and** the user tier is Pro, the client
sends the raw file bytes and GhostPour owns everything downstream:
interpretation on the passthrough path, extraction on the fallback path.
In every other case (BYOK or user pinned model, on device, Plus/free tier),
the client keeps today's behavior: extract text client side and inline it
under the `--- Attached: "name" ---` framing. Nothing that works today is
removed.

## Wire shape

New optional field on `POST /v1/chat`:

```json
{
  "documents": [
    {
      "name": "ABM Summary_Jul02.pdf",
      "media_type": "application/pdf",
      "data": "<base64 of raw file bytes>"
    }
  ]
}
```

- `name`: user visible filename. GP reuses it in the same
  `--- Attached: "name" ---` framing the client uses today, so responses
  read identically regardless of which side did the inlining.
- `media_type`: declared MIME type. v1 accepts
  `application/pdf` and
  `application/vnd.openxmlformats-officedocument.presentationml.presentation`.
- `data`: base64 of the file exactly as picked. No client conversion.
- Field absent or empty array = no documents. No null semantics needed.
- `documents` and the existing `images` array can coexist on one request.

## The four answers

### 1. Gauge accounting

Documents follow the images precedent: **outside the char gauge**, with
caps enforced server side. The client never counts document bytes against
the context gauge. Caps the client needs for attach time UX are served in
config (below), so the UI can present them the same way it presents the
image cap today.

### 2. Downgrade semantics

When GP declines passthrough for any reason (tier below Pro, media type
not in the accepted list, over an internal server policy limit such as PDF
page count), GP **extracts text server side from the bytes it already
has** and inlines it with the standard framing. The request succeeds in
one round trip and the response is indistinguishable from today's
client extracted flow. "Falls back to today's behavior" means exactly
this; it never means a client retry. The only hard errors are transport
level: `document_too_large` (raw bytes over the served cap),
`too_many_documents` (over the served max_files), and
`document_unreadable` (bytes that do not parse as the declared type), all
of which the client can prevent at attach time using the served caps.

A note on pinned models: requests with a user pinned model/provider that
carry `documents` anyway (stale client state, config race) also downgrade
to server side extraction. User files are never forwarded to user keyed
targets.

**Scanned PDFs are in scope on the passthrough branch.** A PDF with no
text layer (a scan or a photo export) that rides passthrough is read
visually and just works; the client should NOT pre reject it with a "no
readable text" error when the passthrough branch condition holds. On the
extraction fallback (Plus tier, pinned model, flag off) a scanned PDF
yields no extractable text: the request still succeeds (never an error),
the model is told the attachment had no readable text, and answer quality
reflects that. So keep the "no readable text" pre check on the client
extraction path where it genuinely protects the user, and drop it on the
passthrough branch.

### 3. Size caps

Caps are defined against **raw file size before base64**, served in config
alongside the accepted formats:

- `per_file_max_mb`: 25 (launch value)
- `max_files`: 2 (launch value)

Client enforces at attach time with a clear too large message (same
pattern as reference imports). GP enforces the same values
authoritatively; base64 inflation is GP's problem to account for, not the
client's. Infra note for our side: proxy body limit is 2000m, so 25MB
raw (~34MB encoded) clears with wide margin.

### 4. Config vehicle

A new top level key in **client-config** (an addition, so it auto
hydrates and old clients ignore it per the decoder contract):

```json
"documents": {
  "enabled": true,
  "min_tier": "pro",
  "accepted_types": [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  ],
  "per_file_max_mb": 25,
  "max_files": 2
}
```

Client rule set:
- Offer the passthrough branch only when `enabled` is true, the chat is
  SS AI routed, and tier meets `min_tier`.
- Drive the picker's format list and attach time size checks from this
  key.
- Missing, stale, or malformed key = extraction path, exactly as SS
  proposed. A config hiccup degrades to today's behavior, never breaks
  attach.
- `allowed_users` (server-read only, ships empty): identities (user id or
  email) listed here get the passthrough path even while `enabled` is
  false and regardless of tier — the per-account e2e/canary hook, meant to
  pair with the client's debug override that forces the gate open. The
  client never reads this key. Dark-server behavior for everyone else is
  unchanged: documents that arrive while `enabled` is false are extracted
  server side and inlined (never ignored, never an error).
- Adding formats later (legacy ppt, docx) is a served list change on our
  side. No app update, no wire change.

## GP side notes (our work, listed for completeness)

- Tier gate enforced at /v1/chat, same pattern as the Project Chat
  gating config (#93 lineage).
- Documents upgrade the turn (server routing policy, 2026-07-10): a
  document-carrying send resolves through the chat surface's first-send
  model lane even when the client marks it a follow-up. Invisible on the
  wire; it keeps the served passthrough ceilings coherent on every turn.
- Server side extraction path: PDF text layer extraction; PPTX text via
  the XML. Used for every downgrade case.
- Provider ceilings, now SERVED for client pre-check (addendum
  2026-07-09): the model side caps requests at 32MB and PDFs at 600
  pages, which the wire caps (25MB raw x 2) can exceed once encoded. The
  documents config gains a `passthrough` subkey the client should read:

  ```json
  "passthrough": {"max_pdf_pages": 600, "max_total_mb": 22}
  ```

  Semantics: these bound the NATIVE path only. `max_total_mb` is the
  combined RAW size (plain file-size math, no base64 accounting) of
  documents that can ride natively in one request; `max_pdf_pages` is the
  per-PDF page cap. Both are cheap on-device checks (PDFKit gives page
  count) — pre-check at attach so the user learns BEFORE a round trip
  that a file will be sent as extracted text rather than read natively.
  Over-limit attachments still send fine within the wire caps; the server
  enforces the same served values and DOWNGRADES them to extraction —
  never an error. The values change when our routing changes (a config
  edit, no app release), which is the point: the client should never
  hardcode them.
- Metering: usage_log metadata gains document count and total raw bytes,
  alongside the existing image_count. Cost attribution flows through
  record_cost as usual.
- Ships behind the config key: `enabled: false` until the server work is
  deployed and verified, so SS can build against the spec immediately.

## Format roadmap (agreed with SS 2026-07-08)

The wire is bytes plus MIME and the picker list is served, so format
growth is a config flip. Positions per format:

- **pdf, pptx** — launch pair, as shipped.
- **docx** — server extractor already implemented (structured OOXML text,
  tables included, same approach as pptx); joins `accepted_types` as an
  early config addition once the launch pair is verified e2e. Extraction
  path only for now (no native docx document block on the model side);
  chart and layout fidelity for docx arrives if or when a server side
  convert to PDF step ever lands.
- **xlsx** — agreed it is extraction hostile (structure IS the content).
  Early config addition, not launch: we will build a structured
  sheet-to-CSV-text extraction with row caps before adding it to the
  served list. Not vision; structured text.
- **iWork (key / pages / numbers)** — NOT on our roadmap; parsing these
  server side is not reasonable. Build the picker copy for "export to
  PDF" messaging.
- **Text native (txt, md, csv, rtf, html)** — stay client extracted
  permanently, per SS. They lose nothing in extraction, work on every
  path including on device, and cost fewer tokens. No bytes for these.
- **Legacy doc / ppt** — future config additions at most, no commitment.

## Timing

SS plans to build this together with References v2 (same plus menu branch
point). This spec is stable for that purpose; if anything moves during
implementation we version it here first. Phase 2 (returned files) will be
designed with SS at the table from the start — it shapes their attachment
UX.
