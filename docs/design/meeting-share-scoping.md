# Meeting share via iMessage: GP scoping (2026-08-21, no build yet)

Scott's ask via CQ. SS builds the Messages extension and the share entry;
GP hosts the page and serves the preview and the universal-link file; CQ
has no role, by design: the shared object is SS's meeting record, never
quilt memory. This document is effort and cost per piece, two product
variants priced, and the things above the ask that change its shape.
Nothing here is built.

## Effort, by piece (GP only)

| # | Piece | Effort | Notes |
|---|---|---|---|
| 1 | Share endpoint: `POST /v1/shares`, `DELETE /v1/shares/{share_id}`, `GET /v1/shares/{share_id}/stats` | 1 day | SQLite table `meeting_shares` (share_id, owner user_id, token, payload JSON, transcript_included, created_at, expires_at, revoked_at, view_count). Token 128-bit random, base64url, no user id in it. Ownership guard on revoke and stats. Per-tier creation rate limit via the existing `rate_limiter` and a tier dial. Echo: returns `{share_id, url, expires_at}` where `url` is built from the served share host, and a test asserts the page answers on exactly that URL. |
| 2 | Hosted page: `GET /s/{token}` on the share host | 1 day | Server-rendered HTML from a template (same mechanism as `report_template.html`), readable on any phone, no sign-in, `noindex`, Open Graph and Twitter card tags (title, date, first line of summary, SS mark). The SS mark is an asset SS supplies. Expired or revoked: a 410 page with the SS card, never the content. |
| 3 | Universal link: `/.well-known/apple-app-site-association` on the share host | 2 hours GP, plus two human steps | JSON with SS's Team ID and bundle id and the `/s/*` path, served as `application/json` with no redirect. SS owns the Associated Domains entitlement and the in-app route. Human steps: DNS record and an nginx proxy host with a cert for the share host (Scott, via the bifrost dashboard, about 15 minutes). |
| 4 | Controls, each a served dial | 0.5 day | `shares.default_expiry_days` (propose 30), `shares.max_expiry_days`, `shares.creations_per_day` per tier, `shares.transcript_allowed` per tier. Revoke is immediate. View count is incremented on page render and served back on the stats route. Expired rows are DELETED by the existing retention sweep (`_retention_sweep_loop`, periodic), payload and all; nothing archived. |
| 5 | Privacy line (below) | 0.5 day | Tests and the doc line, not code. |
| 6 | Day-one test: no share payload ever reaches `/v1/memory` | 1 hour | The share routes import nothing from `context_quilt` and a test patches `cq.capture` and `cq.recall` and asserts zero calls across create, render, stats, revoke. |

Total: about 3.5 GP days, plus the two human steps, plus SS's AASA ids and
the mark asset. No LLM call anywhere in the flow, so no prompt work.

## Host: a share subdomain, from day one

`share.shouldersurf.com` (or shorter), not `cz.shouldersurf.com`. Reasons:
the universal-link association is per host and changing it later is a
coordinated two-sided migration (AASA on the new host, a new build, and
every old link still pointing at the old host); `cz.` is the API host
SS's client pins and rate-limits against, and a public page that strangers
open from iMessage should not share an origin with it; and the URL is
user-facing, it will be read aloud and typed. Cost of the choice: the two
human steps above, once.

## Cost per share

There is no model call: the payload is already generated text. GP cost is
storage and egress. A share with summary and action items is about 3 to
5 KB; with a transcript, 20 to 60 KB (the 150 meetings in Scott's last 30
days average about 7.5K tokens of transcript). One page view is one HTML
response of that size plus the OG fetch iMessage makes once per send.

| | Variant A (full read, no app) | Variant B (summary + items, card for the rest) |
|---|---|---|
| Storage per share, 30-day retention | under $0.0001 | same (the transcript is stored either way if the sender included it; B only withholds it from the page) |
| Egress per view | under $0.0001 at 60 KB | lower, about 5 KB |
| GP build difference | none | one extra template branch and one tier check at render |
| Product difference | recipient gets everything | recipient gets the useful half and a reason to install |

Either variant rounds to zero on GP's side at any plausible volume; the
decision is product, not cost. Free vs Plus for share creation is one
entitlement switch (`shares` in the matrix) and costs nothing either way.

## Privacy line

- Stored in the same SQLite database as everything else, table
  `meeting_shares`, on the GP data volume. Litestream replicates it to
  the DR bucket, so the hosted copy exists in the DR replica for as long
  as the row does; a purge is a delete and replicates as one. The bucket's
  own retention of older snapshots applies (check before promising
  "gone", and say what it is).
- Readable on GP's side by anyone with container or volume access, the
  same set as for transcripts today. Not in `usage_log` (no LLM call), not
  in any log line: the share routes log share_id, owner id, outcome and
  sizes, never content. The nginx access log records the URL, which
  carries the token; treat the token as a credential in that log.
- If the transcript is included: yes, tap-to-reveal on the page (the
  summary renders, the transcript is behind a button), and the sender's
  default is off as specified. Recommend also a visible "shared by" line
  and the expiry date on the page so the recipient knows what they hold.
- A revoked or expired share returns 410 with no content, and the row is
  deleted by the sweep; there is no archive.

## Above the ask, things that change the shape

1. **The token is in the URL and the URL goes through Apple's and every
   messenger's link-preview fetchers.** That is inherent to the feature;
   it means the page must be safe to fetch unauthenticated by bots, and
   view counts will include preview fetches unless filtered by user agent
   (propose counting only non-preview fetches and saying so).
2. **The retention sweep is periodic, not startup-only** (`_retention_sweep_loop`),
   so expiry is enforced within the sweep interval; fine. But a share
   created from a meeting that is later purged by the 30-day transcript
   retention keeps its own copy until its own expiry. State that to the
   sender: sharing makes a copy with its own clock.
3. **Revocation after a preview has rendered** cannot unsend the card
   iMessage already drew (title, first line); only the page goes dark.
   Worth one line in the share sheet copy.
4. **Edits**: if the sender edits the meeting in SS after sharing, the share
   does not follow. Either document it or add `PUT /v1/shares/{id}` later.
5. **Rate limiting the public page** by IP at nginx, not just creation by
   tier, so a leaked link cannot be used to hammer the host.
6. **The SS mark and the OG image** need to be real assets served from the
   share host; a broken image in the iMessage card is worse than none.

## Sequence, per the 08-10 rule

GP deploys the routes, the page and the AASA first and proves them with a
curl and Apple's AASA validator; SS builds against what arrives; Scott
taps a real share from his phone to a second device; the echo check is
that the URL SS received is the URL the page answered on.
