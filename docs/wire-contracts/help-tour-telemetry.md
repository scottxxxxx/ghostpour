# Help and tour telemetry: wire contract

Two events on the existing anonymous ping, so the owner can see when people
use Help and the "How Shoulder Surf Works" tour, what they read, and what
they search for. Same pattern as `onboarding-telemetry.md`. GP defines the
wire, Shoulder Surf (iOS 1.21) emits it.

Last updated: 2026-11-25. Status: GP side built, no client sends it yet.

## Wire

`POST /v1/events/ping` (no auth, per-IP rate limited, 204 on success), on
the same envelope as `app_start`: `device_id` (UUID, required), `user_id`,
`app_version`, `app_build`, `os_version`, `device_model`, `app_locale`.

### `help_viewed`, sent once when the Help screen closes

```json
{
  "event_type": "help_viewed",
  "device_id": "5B0C1D8E-3F2A-4E6B-9C7D-1A2B3C4D5E6F",
  "user_id": "0f8b7c1e-user",
  "app_version": "1.21",
  "app_build": "912",
  "os_version": "26.1",
  "device_model": "iPhone17,3",
  "app_locale": "pt_BR",
  "help": {
    "entry": "settings",
    "duration_ms": 95000,
    "articles": [{"id": "acct-plans", "dwell_ms": 41000}],
    "searched": true,
    "searches": [{"q": "how do i delete", "opened": true},
                 {"q": "portuguese", "opened": false}],
    "asked": false,
    "whats_new_opened": true
  }
}
```

| Field | Rule |
|---|---|
| `entry` | required, `"settings"`, `"listen_mode"` or `"other"` |
| `duration_ms` | required, integer 0 to 86400000 (24 h) |
| `articles` | optional, at most 50 of `{id, dwell_ms}` |
| `articles[].id` | slug, `^[a-z0-9][a-z0-9._-]{0,63}$` |
| `articles[].dwell_ms` | integer 0 to 86400000 |
| `searched`, `asked`, `whats_new_opened` | required booleans |
| `searches` | optional, at most 20 (counted as sent) of exactly `{q, opened}` |
| `searches[].q` | string; see below |
| `searches[].opened` | required boolean: an article was opened from this search |

### `tour_viewed`, sent when the tour closes from Settings

```json
{
  "event_type": "tour_viewed",
  "device_id": "5B0C1D8E-3F2A-4E6B-9C7D-1A2B3C4D5E6F",
  "user_id": "0f8b7c1e-user",
  "app_version": "1.21",
  "app_build": "912",
  "os_version": "26.1",
  "device_model": "iPhone17,3",
  "app_locale": "pt_BR",
  "tour": {
    "entry": "settings",
    "duration_ms": 64000,
    "slides_total": 12,
    "slides_seen": 3,
    "last_slide": "tour-03",
    "completed": false,
    "narration_on": true,
    "language": "pt-BR"
  }
}
```

| Field | Rule |
|---|---|
| `entry` | required, `"settings"` or `"other"`. `"onboarding"` is rejected: the first-run tour stays on `onboarding_completed` |
| `duration_ms` | required, integer 0 to 86400000 |
| `slides_total`, `slides_seen` | required, integers 0 to 200, `slides_seen <= slides_total` |
| `last_slide` | optional, slug as above |
| `completed`, `narration_on` | required booleans |
| `language` | optional, at most 16 chars, `^[A-Za-z]{2,3}([-_][A-Za-z0-9]{1,8})*$` (`pt-BR`; `pt_BR` is accepted and read as the same language) |

## Search text (`help.searches[].q`)

The one free-text field. GP normalizes it even though the client does too,
and stores the normalized form:

1. Rejected (422) if it holds a control character, a tab or a newline
   (Unicode categories Cc, Zl, Zp), checked on the text as sent.
2. Lowercased, trimmed, runs of whitespace collapsed to one space.
3. Rejected (422) unless the result is 1 to 100 characters. The raw string
   is capped at 1000.
4. Silently dropped (the event is still stored) when it looks personal:
   - email: `[^\s@]+@[^\s@]+\.[^\s@]+`
   - URL: `[a-z][a-z0-9+.-]*://|\bwww\.|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b`
   - phone: `\d{7,}` after removing `[\s\-.()]`
5. Deduplicated by `q`; `opened` is true if any copy was.

`searches` not empty with `searched: false` is rejected (422), judged on
what was sent, before any drop.

GP never writes `q` to its application log. The dashboard's live-log ring
(in memory, the last 1000 requests, admin key only) does capture this
endpoint's request bodies, raw `q` included; see the PR for that finding.

## Rejections

All shape errors are FastAPI's standard 422 with
`{"detail": [{"type", "loc", "msg", "input", ...}]}`. A block on the wrong
event (`help` on anything but `help_viewed`, `tour` on anything but
`tour_viewed`) and an event without its block are 422 too, the same as
`onboarding`. A `device_id` that is not a UUID is 400. Over the rate limit
is 429.

Unknown keys inside `help` or `tour` are accepted and not stored (what the
`onboarding` block does). An unknown key inside a `searches` item is a 422.

## Storage and retention

`help_tour_events`, one row per event, apart from `telemetry_events` so the
lifecycle counts do not include them. The block is stored as JSON in
`payload`. Rows are purged at 180 days, search text included, and a
signed-in user's rows go with their account on deletion. Accounts flagged
`exclude_from_stats` are left out of the dashboard numbers.

## Read side

`GET /webhooks/admin/telemetry/help-tour?days=N&app=&app_version=&os_version=&device_model=`
(admin key), one response for the dashboard's "Help and learning" section.
See `app/services/help_tour_stats.py` for how each number is computed.
