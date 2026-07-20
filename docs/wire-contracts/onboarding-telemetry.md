# Onboarding funnel telemetry — wire contract

Instruments the first-run onboarding funnel so GP (which holds the
subscription side) can correlate onboarding behavior with conversion and
retention, and feed the result into CTA/promo targeting. One event on the
existing anonymous ping. GP defines the wire, the app emits it, same
pattern as the distribution signal.

Last updated: 2026-07-20. Status: GP ingestion live; step vocabulary
proposed below, pending SS confirmation against their actual screens.

## Join key and privacy

- **Join key is `device_id`** (identifierForVendor), which exists pre-login
  where onboarding runs. The ping carries `user_id` once signed in, so the
  onboarding row bridges device -> account -> subscription. No new identity
  plumbing.
- **Behavioral only, no PII.** The user's name and the voice-enrollment
  audio never leave the device. We carry booleans (`name_provided`,
  `voice_enrolled`), never the name or the voiceprint. This is a hard line.

## Wire

`POST /v1/events/ping` (no auth, per-IP rate limited, same as the other
lifecycle pings), with `event_type: "onboarding_completed"` and an
`onboarding` block alongside the normal envelope
(`device_id`, `app_version`, `os_version`, `device_model`, `app_locale`,
`distribution`):

```json
{
  "event_type": "onboarding_completed",
  "device_id": "<uuid>",
  "app_version": "1.15",
  "distribution": "sandbox",
  "onboarding": {
    "total_duration_ms": 42000,
    "completed": true,
    "tour_skipped": false,
    "name_provided": true,
    "voice_enrolled": true,
    "auth_choice": "apple",
    "abandoned_at_step": null,
    "steps": [
      {"step": "welcome", "dwell_ms": 3000},
      {"step": "name_entry", "dwell_ms": 12000},
      {"step": "voice_enrollment", "dwell_ms": 20000}
    ]
  }
}
```

Emit once at the end of onboarding, and flush on background if onboarding
is still in progress (so drop-off is captured, not just completers). The
`onboarding` block is required on this event and rejected on the lifecycle
events (422 either way).

## Fields (`onboarding`)

- **`total_duration_ms`** (int, optional) — wall-clock across onboarding,
  dwell paused on backgrounding.
- **`completed`** (bool, required) — finished vs abandoned.
- **`tour_skipped`** (bool) — user tapped skip on the intro tour.
- **`name_provided`** (bool) — a name was entered. The name itself is not sent.
- **`voice_enrolled`** (bool) — voice enrollment finished. The audio is not sent.
- **`auth_choice`** (`"apple"` | `"on_device"` | null) — sign-in path chosen.
- **`abandoned_at_step`** (string | null) — the step id they dropped on;
  null when `completed` is true.
- **`steps`** (array of `{step, dwell_ms}`) — per-page dwell, in order.
  `step` is a canonical id (below); `dwell_ms` is milliseconds on that page.

## Step vocabulary (PROPOSED — confirm against real screens)

Canonical `step` ids so the funnel is consistent across builds. GP does
not hard-validate these at ingestion (any id is stored, so the vocabulary
can evolve without a GP deploy), but both sides agree the canonical set
here so analysis lines up. The semantically important ones we correlate
on are fixed; the informational pages should use stable snake_case ids
that SS sends from their actual screen inventory.

Fixed (we correlate on these):
- `name_entry`
- `voice_enrollment`
- `auth_choice`

Proposed for the rest (SS to confirm/replace with real screen ids):
- `welcome`
- `value_prop` (or the actual per-screen ids if there are several)
- `permissions` (notifications / mic, if a distinct page)

**SS action:** send the real ordered screen list so we lock the final
vocabulary in this doc.

## Storage

`onboarding_events` table, one row per event, keyed by `device_id`
(indexed). Flat columns for the cohort dimensions (the booleans,
`auth_choice`, `total_duration_ms`, `completed`, `abandoned_at_step`) plus
`steps` as a JSON array for the per-page funnel. Correlation queries /
dashboard are a follow-up once data accrues; this contract covers capture.
