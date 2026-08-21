# Meeting share via iMessage: the wire

Ruled 2026-08-21. GP stores SS's `.shouldersurf` archive as uploaded and
serves it back by share id; a recipient with the app opens it through a
universal link, everyone else reads the hosted page. Creation is free on
every tier. Nothing here reaches Context Quilt (tested).

Host: `https://share.shouldersurf.com` (served from client-config
`share.host`; the URL SS receives is the URL the page answers on).

## Create (authenticated, SS bearer JWT)

`POST /v1/shares`

Body: the raw archive bytes, exactly as SS builds them for the manual
share. `Content-Type`: the archive's type, named by SS (stored and served
back unchanged). No multipart, no JSON wrapper.

Headers carrying the card fields:

| header | required | meaning |
|---|---|---|
| `X-Share-Title` | yes | card title |
| `X-Share-Date` | no | meeting date, ISO or display string |
| `X-Share-Duration-Seconds` | no | integer |
| `X-Share-Summary-Line` | no | first line of the summary, card description |
| `X-Share-Transcript-Included` | no | `true` when the archive carries the transcript (sender's per-share choice, default off) |
| `X-Share-Expiry-Days` | no | 1..`share.max_expiry_days`; default `share.default_expiry_days` (30) |

Response 200: `{"share_id": "...", "url": "https://share.shouldersurf.com/s/<token>", "expires_at": "<ISO>"}`.
Errors, all with `detail.code`: 403 `share_disabled` (matrix switch off for
the tier), 403 `share_transcript_disabled` (tier dial), 413
`share_too_large` (over 25 MB), 422 `share_empty`, 429
`share_rate_limited` (per-tier creations per day, default 50).

The token is 128 random bits, base64url, carries no user id, and is the
credential. GP never writes it to a log line (the access log masks
`/s/<token>`); SS should not either.

## Revoke and stats (authenticated, owner only)

`DELETE /v1/shares/{share_id}` -> `{"share_id", "status": "revoked"}`.
Immediate: the page and the archive answer 410 from then on, and the
bytes are deleted. 404 for anyone but the owner.

`GET /v1/shares/{share_id}/stats` -> `{"share_id", "view_count",
"expires_at", "revoked", "live"}`. `view_count` excludes link-preview
fetchers (iMessage, Slack, WhatsApp and the rest announce themselves).

## Public (no sign-in)

`GET /s/{token}`: the hosted page. `noindex`, Open Graph and Twitter card
tags (title, description from the summary line). 410 with a neutral page
when expired, revoked or unknown; no distinction between the three.

`GET /s/{token}/archive`: the archive bytes with the stored content type.
For the app's universal-link handler, which then runs the same importer
as the manual flow. 410 under the same conditions. Split from the page by
path, not by Accept, so a preview fetcher never receives the archive.

`GET /.well-known/apple-app-site-association`: served by GP as
`application/json`, `{"applinks": {"details": [{"appIDs": [...],
"components": [{"/": "/s/*"}]}]}}`. 404 until SS supplies
`TEAMID.com.weirtech.shouldersurf` for client-config `share.aasa_app_ids`,
so Apple never caches an association with no app in it.

## Retention

Rows and bytes are deleted by the periodic retention sweep once expired or
revoked. A share is a copy with its own clock: the meeting's own 30-day
transcript retention does not shorten it, and revoking cannot unsend a
card iMessage already rendered. Both lines belong in SS's share sheet copy.

## What waits on SS

- The archive content type and format spec (for the full page renderer;
  today the page is the card plus title, date and summary line).
- `TEAMID.com.weirtech.shouldersurf` for the AASA.
- A mark asset for the card image.
