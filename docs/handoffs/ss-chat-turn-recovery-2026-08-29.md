# Project chat: three paid answers the phone never saw (2026-08-29)

Joint GP/SS brief. Written by GP after reading prod logs, the edge access log,
GP source and SS source. Every claim below names the side it was proved on
(rule 5).

---

## What actually happened

Scott attached two business plans (400,653 bytes total) in Project Chat on
build 1306 over 5G at two bars, and asked for a comprehensive review. He got
"Error: The network connection was lost" twice, a message saying his file was
being built once, and eventually an answer telling him the uploads never came
through.

**The uploads came through every time.** GP built the full review three times
and delivered none of them.

| when (UTC) | GP request_id | docs on the turn | GP LLM time | in / out tokens | edge status | user saw |
|---|---|---|---|---|---|---|
| 16:14:39 → 16:15:46 | `58aa0d200bca` | 2 files, 400653 B | **66.4 s** | 10798 / 2240 | **499** | "network connection was lost" |
| 16:16:13 → 16:17:09 | `af7ac89ee8ef` | 2 files, 400653 B | **56.3 s** | 10937 / 2107 | **499** | "network connection was lost" |
| 16:21:43 → 16:21:45 | `081c7ec01cc6` | **none** | 2.5 s | 524 / 52 | 200 | delivered |
| 16:22:25 → 16:23:22 | `23cdd4415229` | 2 files, 400653 B | **56.7 s** | 11242 / 2114 | **499** | "your file is still being built" |

Proved on GP's side, from `usage_log` (`metadata.documents.raw_bytes` = 400653
on all three) and from the Bifrost access log for `cz.shouldersurf.com`
(`499`, `Length 0`, meaning the client was gone before nginx wrote a body byte).

Two things fall straight out of that table:

1. **Every turn that carried the documents took ~60 seconds and was lost.
   The one turn that carried no documents took 2.5 seconds and landed.**
   Delivery correlates perfectly with duration, not with anything about the
   files.
2. **GP's "the files didn't come through" reply was correct about the turn it
   was answering and wrong about every other turn.** At 16:21 the documents
   genuinely were not attached, so the model said so honestly. At 18:41 it
   generalised that to all four attempts, which the usage rows contradict.

Cost of the three undelivered answers: **$0.7995**, all of it identical work,
plus 400 KB uploaded three times over a weak uplink. GP kept the upstream call
running for 60 s, 55 s and 30 s **after** nginx had already recorded that the
client was gone.

## What was NOT wrong

- **The edge.** `proxy_buffering off`, `proxy_cache off`,
  `chunked_transfer_encoding on`, `proxy_read_timeout 120s` all confirmed live
  in `/data/nginx/custom/server_proxy.conf`, and `conf.d/include/proxy.conf`
  does not override them. Chunks are forwarded immediately. The 2.5 s turn
  landing through the same path is the positive control.
- **The 400 KB payload.** GP received, extracted and cached it every time
  (55,426 cache-creation tokens on the first turn).
- **Artifact creation.** `generations` holds **zero rows for Scott, ever**. No
  file was created, offered-and-built, or half-built. The artifact lane already
  requires an explicit yes, and it was never entered.

## What I could not prove from GP's side

Why the device stopped receiving. GP emits an SSE `progress` heartbeat every 10 s
(`_STREAM_HEARTBEAT_SECONDS`, `app/routers/chat.py:89`), and the edge passes
chunks straight through, so the phone should have been getting a byte every
10 s. Two of the three drops surfaced as `NSURLErrorNetworkConnectionLost`
(a dead socket, consistent with 5G at two bars right after a 400 KB uplink
burst) and the third surfaced as SS's own armed-generation stale-event guard
firing after 30 s of silence.

**Only the device log can settle it.** This is the request-side hole rule 3
describes: GP holds a complete, successful response and SS holds a dead socket,
and neither endpoint can see the middle. See ask 3 below.

---

## GP will build

**1. A turn id, so a retry is free and instant.**
SS mints a UUID per user-authored turn and resends it verbatim on every retry.
GP keys on `(user_id, turn_id)`: in flight, attach to it; already finished,
return the stored answer immediately. The retry stops costing a model call,
stops costing a 400 KB upload, and comes back in milliseconds instead of a
minute. This is the whole fix for "don't redo work we already paid for".

**2. Stage the completed answer even when the client is gone.**
Today a finished $0.27 answer is dropped on the floor when the socket dies.
GP already knows how to stage a result and let a client collect it later: that
is exactly what the `generations` table and the rescue pass do for files. The
same pattern generalised to ordinary chat turns converts a drop from a loss
into a delay.

**3. Notice the client left.**
GP polls `request.is_disconnected()` in the heartbeat loop. Combined with 2 we
finish and stage the turn rather than abort it (the answer becomes the cached
retry), but we never start a *new* upstream call for a request that is already
gone, and we record the disconnect so this is measurable instead of invisible.

**4. Tell the model what it actually received.**
GP knows `document_count` and `raw_bytes` on the turn. Putting that in the
context stops the model inventing a delivery failure, which is the single most
misleading thing Scott was told all day.

**5. `generation_intent` is truncating.**
Three of four classifier calls today hit `finish_reason: max_tokens` at the
150-token cap and failed open, burning a Haiku call and silently dropping the
offer each time. The cap was raised from 50 to 150 in July for this same
reason and is still too small, because the free-text `gist` is unbounded.
Raise it or take the gist out of the classifier's job.

## SS asks

**1. A retry affordance. Scott's ask, and the headline.**
Today a failed turn is a dead-end text bubble with nothing to tap. He wants a
retry control on the bubble, auto-retrying at **10 s, then 30 s, then 60 s**,
then stopping and leaving it to a manual tap. With GP's turn id (ask: mint it
and resend it unchanged) each of those retries is a cheap lookup, not a
re-upload and a re-run, so an aggressive schedule costs nothing.

**2. "The connection dropped, but your file is still being built" is false, and
it was shown on a turn where nothing was being built.**
`ProjectChatSection.swift:3852` and `:4201`, `MeetingChatSection.swift:2868`
render it when `didClientTimeout && generationArmed`, and `generationArmed` is
`(offerId != nil) || generationConfirmed`, which arms optimistically. GP has
never created a generation row for this user, so the app promised a file that
did not exist and then polled `rescuePendingGenerations()` on a 30/60/120 s
schedule that could never return anything, silently. Two fixes: only claim a
build when GP has confirmed a generation id, and make the rescue poll's
give-up visible rather than silent.

**3. The device log for `58aa0d200bca`, `af7ac89ee8ef` and `23cdd4415229`.**
Specifically `chunkCount` and whether any `{"type":"progress"}` heartbeat
arrived before the socket died. That is the one instrument GP does not have,
and it decides whether we are looking at a genuinely flaky radio or at
heartbeats that never reach the device.

**4. Two stale numbers in `CloudZapProvider.swift:715`.**
The comment says GP ships a 90 s server-side cap and heartbeats every ~5 s.
GP's actual values are a **180 s** wall clock (`_CHAT_STREAM_WALL_CLOCK_SECONDS`)
and a **10 s** heartbeat. The armed guard needs 30 s of silence to fire, so the
real margin is three missed beats, not six. Nothing is broken by this today,
but it is a comment that is wrong out loud and it feeds the timeout maths.

**5. Do not re-upload the documents on a retry.**
Once the turn id lands, a retry should carry the id and no bytes. 400 KB over a
two-bar uplink is most of the window in which this fails.
