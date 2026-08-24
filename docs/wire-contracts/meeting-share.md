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
| `X-Share-Title` | yes | card title, UTF-8 **percent-encoded** (see below) |
| `X-Share-Date` | no | meeting date, ISO or display string |
| `X-Share-Duration-Seconds` | no | integer |
| `X-Share-Summary-Line` | no | first line of the summary, card description, UTF-8 **percent-encoded** (see below) |
| `X-Share-Transcript-Included` | no | `true` when the archive carries the transcript. A FACT about the bundle, not a choice: SS's exporter has no transcript toggle and never had one, so every bundle carries one and SS sends `true`. GP uses it to decide whether the page offers tap-to-reveal. |
| `X-Share-Expiry-Days` | no | 1..`share.max_expiry_days`; default `share.default_expiry_days` (30) |

Response 200: `{"share_id": "...", "url": "https://share.shouldersurf.com/s/<token>", "expires_at": "<ISO>"}`.
Errors, all with `detail.code`: 403 `share_disabled` (matrix switch off for
the tier), 413 `share_too_large`, 422 `share_empty`, 429
`share_rate_limited` (per-tier creations per day, default 50).

**There is NO size cap by default.** Scott ruled that 2026-08-22. The cap
is a per-tier dial, `tiers.{tier}.feature_definitions.share.max_archive_mb`,
with a dashboard card; absent or null means uncapped, and no tier sets it
today, so nothing 413s. The 413 body carries `size_bytes` and
`limit_bytes` so a client has true numbers to show. The nginx in front of
GP allows 2000m, so it is not a hidden second cap either.

An earlier draft of this document said "over 25 MB", from the #750
skeleton's hardcoded constant, which #754 deleted. That was stale for
about a day and CQ read it and relayed the opposite of the ruling in good
faith. Corrected 2026-08-22 against the deployed code rather than against
the previous draft. SS measured real bundles at 275 KB to 36.9 MB, median
about 2.1 MB, audio in eleven of twelve, so a two-hour meeting with audio
is around 29 MB and goes through.

`403 share_transcript_disabled` is GONE (2026-08-22). It was written on
the belief that the sender had a per-share transcript toggle; SS checked
their exporter and there is not one and never was. The dial could not
restrict a feature, only make every share from a tier fail with a message
telling the sender to do something the app cannot do. Withholding sharing
from a tier is the `share` entitlement's job. If a real per-share choice
is ever wanted it is SS exporter work plus an archive spec change, and the
gate then belongs next to the toggle that exists.

### Card text is percent-encoded UTF-8

`X-Share-Title` and `X-Share-Summary-Line` are user-generated text in an
HTTP header, which is not a place Unicode survives. A strict client cannot
put "四半期レビュー" in a header at all, and one that writes the raw UTF-8
bytes anyway gets them back latin-1-decoded, stored, and rendered onto the
card and the og:title. Nothing errors; the share just says something else.

So: UTF-8, percent-encoded (`%E5%9B%9B...`). ASCII on the wire, a no-op for
a plain English title, and a literal percent goes as `%25`. Malformed
sequences decode with replacement rather than 4xx, because a share that
fails to send is worse than a title missing a glyph.

The token is 128 random bits, base64url, carries no user id, and is the
credential. GP never writes it to a log line (the access log masks
`/s/<token>`); SS should not either.

## `client-config.share.host` is an ORIGIN, not a hostname

Served as `"https://share.shouldersurf.com"`: scheme plus host, no path,
no trailing slash (GP strips one if present). GP builds every share URL
as `{host}/s/{token}`, so the scheme is structural, not decorative, and
the value is stable. The key is named `host` and that name is misleading;
it is not being renamed, because a renamed key on a persisted client
struct is a two-sided deploy and the value is already correct.

Ruled 2026-08-23 after it reached a device. SS's client treated the value
as a bare hostname: the share sheet's fail-closed DNS probe ran
`getaddrinfo` on the URL string, failed, and showed "Sharing isn't
switched on yet" while the host was live, and the universal-link parser
compared it against `URL.host`, so a tapped link would have matched
nothing and done nothing. Nothing in that chain errors, which is why it
reached a phone. SS (their `feea62a`) now normalises either form to the
bare lowercase host for DNS and AASA matching, which is the right
posture on their side regardless of what this says.

So: **treat `share.host` as an origin.** Derive a bare host from it when
a hostname is what you need; never assume it is one.

## The unfurl image (2026-08-23)

`GET /s/{token}` serves `og:image`, `og:image:width/height` (1200x630),
`twitter:image`, `twitter:card: summary_large_image` and an
`apple-touch-icon`. Without these iMessage rendered a generic Safari
compass where the app mark should be (Scott's first real share).

The images are served by GP on the share origin, by name from an
allowlist of two, so there is nothing to walk:

| path | use | size |
|---|---|---|
| `/share-assets/card-1200x630.png` | `og:image` / `twitter:image` | 1200x630, the mark centred on the brand gradient |
| `/share-assets/icon-512.png` | `apple-touch-icon` | 512 square |

Both URLs are dials, `client-config.share.og_image_url` and
`client-config.share.icon_url`, defaulting to the share origin's own
paths so a bare config still unfurls with the mark. A CDN or a redesign
is a config edit, not a deploy.

**Edge:** `/share-assets/*` must be on the share host's path allowlist at
the edge (Bifrost), alongside `/s/*` and the AASA path. Asked 2026-08-23.
Until it is, the page serves the tags and the messenger's fetch of the
image 404s at the edge, which renders exactly as before: the compass.

Messengers fetch the preview at SEND time and cache it, so a bubble sent
before this deployed does not update; a newly sent link does.

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
`F22KGHDYAE.com.shouldersurf.ShoulderSurf` for client-config `share.aasa_app_ids`,
so Apple never caches an association with no app in it.

## Retention

Rows and bytes are deleted by the periodic retention sweep once expired or
revoked. A share is a copy with its own clock: the meeting's own 30-day
transcript retention does not shorten it.

**Revoking cannot withdraw what already left.** The earlier wording here
was "revoking cannot unsend a card iMessage already rendered", which SS
correctly called weaker than the truth. It reads as being about a card.
The true statement is that `X-Share-Summary-Line` is meeting content, it
becomes the Open Graph description, and at share time it is fetched and
CACHED by iMessage, Slack, WhatsApp and every other unfurler. So the first
line of a private meeting summary leaves our system into third-party
caches the moment the link is sent, and revoking reaches none of them.
Revoke stops the page and the archive; it does not reach a cache.

That is the line that belongs in SS's share-sheet copy, in those terms,
and it is a stronger claim than the one it replaces.

## What waits on whom (2026-08-22)

Done: the archive spec (SS, their 3471978), the Team ID (AASA live), and
the full page renderer (#754, live on `da76aca`).

- **Scott**: DNS. `share.shouldersurf.com` has no A record. Needs
  A -> 35.239.227.192, an nginx proxy host to `ghostpour:8000`, and a
  Let's Encrypt cert. Until it lands, `client-config.share.host` names a
  host that does not resolve, so a share created today mints a dead link
  and iMessage renders a card that never loads. Test against
  `cz.shouldersurf.com`, which serves every one of these routes now.
- **Scott**: a mark asset for the card image. SVG preferred, else PNG,
  transparent, 2x, 512px on the long edge. Cosmetic; gates nothing.
- **SS**: the client build. Read `share.host` from client-config rather
  than hardcoding either name, and treat "the configured host does not
  resolve" as a real error state rather than a hang, because that is the
  live state on every device until the DNS lands.

## Audio on the hosted page (2026-08-24)

Scott's ruling: the recording is playable on the web page and the
transcript follows it. Nothing changes for SS's exporter; this is read
off the bundle as a FACT (any `media/<origin>/audio/*.m4a` entry).

- `GET /s/{token}/audio/{n}` (public, GET/HEAD): the n-th audio entry
  of the bundle in name order across its meetings, `audio/mp4`, with
  HTTP Range (206 + Content-Range + Accept-Ranges: bytes). Safari's
  audio element probes `Range: bytes=0-1` and needs the 206. 404 when
  n is past the entries or the bundle has no audio; 410 on a dead share.
  Bifrost's edge admits `/audio/[0-9]{1,3}` only (n 0-999).
- The entry is inflated ONCE to a sidecar `<archive>.audio{n}.m4a`
  beside the archive (bounded at 64 MB per entry, streamed, no partial
  file on failure) and the sidecar dies with the share on purge.
- The page renders `transcriptSegments` as timed lines
  (`sessionTimeOffset`/`endTimeOffset`); playback highlights and scrolls
  the current line, a tap on a line seeks. Records without segments keep
  the plain transcript. The transcript panel opens itself when audio is
  present.
