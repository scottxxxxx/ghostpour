# Onboarding funnel telemetry — wire contract

Instruments the first-run onboarding funnel so GP (which holds the
subscription side) can correlate onboarding behavior with conversion and
retention, and feed the result into CTA/promo targeting. One event on the
existing anonymous ping. GP defines the wire, the app emits it, same
pattern as the distribution signal.

Last updated: 2026-07-20. Status: LIVE end to end. GP ingestion deployed;
SS client emit built and verified against the live endpoint; step
vocabulary finalized below from SS's screen list.

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

## Step vocabulary (FINALIZED 2026-07-20 from SS's screen list)

Canonical `step` ids, in order. GP does not hard-validate these at
ingestion (any id is stored, so the vocabulary can evolve without a GP
deploy), but both sides agree the set here so analysis lines up.

1. `highlights`
2. `tour_1` … `tour_N` — the intro tour slides, positional. Currently
   ~12 core slides, but the count varies by release, so these ids are
   dynamic. Permissive ingestion is exactly why this is fine: N can move
   release to release with no GP change. Treat `tour_*` as one funnel
   stage when aggregating unless a specific slide matters.
3. `name_entry` — fixed, correlated
4. `auth_choice` — fixed, correlated
5. `voice_enrollment` — fixed, correlated
6. `complete` — terminal screen

The three we correlate on (`name_entry`, `auth_choice`,
`voice_enrollment`) map exactly to SS's screens. `complete` is the final
"Get Started" page. For this build the tour is exactly 12 slides
(`tour_1`..`tour_12`), count and order stable, though the slide set can
change release to release.

**Conditional steps (critical for analysis):** `name_entry`,
`auth_choice`, and `voice_enrollment` are dropped up front for a
returning user who has already done them, so their step list is shorter.
Absence of one of these from `steps` means "not shown," NOT "abandoned"
or "declined". Do not infer a skip from a missing step. Read the outcome
from the booleans (`name_provided`, `voice_enrolled`) and `auth_choice`,
not from step presence. `abandoned_at_step` carries whichever id was on
screen at background if they never reached `complete`.

**Not instrumented:** the Terms & Privacy consent gate runs before this
flow as a separate screen and is out of the funnel today. It's a
mandatory gate (everyone passes it or doesn't use the app), so dwell
there is low-signal; left out on purpose. If we ever want it, SS adds a
`terms` step ahead of `highlights`, no GP change needed (permissive
ingestion).

## Storage

`onboarding_events` table, one row per event, keyed by `device_id`
(indexed). Flat columns for the cohort dimensions (the booleans,
`auth_choice`, `total_duration_ms`, `completed`, `abandoned_at_step`) plus
`steps` as a JSON array for the per-page funnel. Correlation queries /
dashboard are a follow-up once data accrues; this contract covers capture.
