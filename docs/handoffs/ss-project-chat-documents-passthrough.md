# Project Chat documents passthrough — wire spec (phase 1)

Status: PROPOSED 2026-07-08. Answers SS's four spec questions from their
acceptance note. Phase 1 is input fidelity only; returning generated files
is a separate phase 2 design.

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
level: `document_too_large` (raw bytes over the served cap) and
`document_unreadable` (bytes that do not parse as the declared type), both
of which the client can prevent at attach time using the served caps.

A note on pinned models: requests with a user pinned model/provider that
carry `documents` anyway (stale client state, config race) also downgrade
to server side extraction. User files are never forwarded to user keyed
targets.

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
- Adding formats later (legacy ppt, docx) is a served list change on our
  side. No app update, no wire change.

## GP side notes (our work, listed for completeness)

- Tier gate enforced at /v1/chat, same pattern as the Project Chat
  gating config (#93 lineage).
- Server side extraction path: PDF text layer extraction; PPTX text via
  the XML. Used for every downgrade case.
- Metering: usage_log metadata gains document count and total raw bytes,
  alongside the existing image_count. Cost attribution flows through
  record_cost as usual.
- Ships behind the config key: `enabled: false` until the server work is
  deployed and verified, so SS can build against the spec immediately.

## Timing

SS plans to build this together with References v2 (same plus menu branch
point). This spec is stable for that purpose; if anything moves during
implementation we version it here first.
