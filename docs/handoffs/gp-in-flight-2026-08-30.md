# GhostPour: what is in flight (written 2026-08-30 ~08:00Z, session cloudzap-66 [008f4e])

Prod and main are both at **3c37ad1**, healthy, verified on `/health`. Working
tree clean (untracked: `Subscription Info/`, older handoffs, `tmp/`). Five PRs
merged tonight, all deployed. Nothing of mine is half-applied.

> Supersedes `gp-in-flight-2026-08-29.md`. That file's open items are still
> open and restated at the bottom; nothing in it was retracted.

---

## What happened

Scott attached two business plans (400,653 bytes) in Project Chat over 5G at
two bars and asked for a comprehensive review. He got two network errors, a
message claiming a file was being built, and eventually an answer telling him
the uploads never came through.

**The uploads came through every time.** GP built the full review three times,
at $0.2738, $0.2623 and $0.2633, and delivered none of them.

| UTC | request_id | docs | GP time | edge | user saw |
|---|---|---|---|---|---|
| 16:14:39 -> 16:15:46 | `58aa0d200bca` | 400653 B | 66.4s | **499** | network error |
| 16:16:13 -> 16:17:09 | `af7ac89ee8ef` | 400653 B | 56.3s | **499** | network error |
| 16:21:43 -> 16:21:45 | `081c7ec01cc6` | none | 2.5s | 200 | delivered |
| 16:22:25 -> 16:23:22 | `23cdd4415229` | 400653 B | 56.7s | **499** | "your file is still being built" |

Delivery tracked DURATION, not the files. **$0.7995 delivered zero times.**

## Root cause, and it was one line

`app/routers/chat.py`, `should_stream` carried `and not is_project_chat`. That
exclusion is correct and stays: it is what kept Project Chat immune to the
07-13 bug where a confirmed Gantt turn token-streamed raw task-graph JSON into
the bubble and built no file.

But it also took Project Chat off the ONLY path that emits heartbeats, so a
turn sent nothing at all, not one byte and not even HTTP response headers,
until the whole JSON was built. On a document turn that is ~60s of silence.

SS's device log proved the other half: turn 4 died in the FOREGROUND after 36s
with `request_id=none`, so headers never arrived. My "flaky 5G" reading was
wrong and is withdrawn.

---

## Shipped tonight, all live

**#820 `cc41c4e`** Project Chat rides an SSE envelope. Model output stays
buffered internally (renderer and generation diversion untouched, nothing
token-streams); only the transport changed. Heartbeat every 5s, then the
byte-identical JSON body in `generation_result`, the frame the client already
substitutes for a non-stream body. Zero client changes.

NOT `generation_started`/`generation_progress`: any `generation_*` event sets
`sawGenerationEvents` on the client, which gates its "your file is still being
built" copy. A plain chat turn must never arm that.

**Gated on `chat_sse_min_build: 695`, FAILING CLOSED**, unlike every other gate
in `version_gate` (below the floor the envelope renders an EMPTY answer, which
is worse than the silence). SS verified 695 = `c08d89c` and 803 = `4661176`
line by line.

**#821 `dc28f61`** Test pinning that the gate OPENS on a User-Agent-only build.
SS believed the gate refused every device because the chat socket sends no
`X-App-Build`. It does not: `build_number` falls back to the default URLSession
UA. Measured: 630 of 793 `POST /v1/chat` carry a readable token.

**#822 `4ae9be3`** t=0 frame. The head flushes WITH the first body chunk, so
+10.34s to first byte was 5.32s pre-flight plus 5.00s waiting for a tick.
Killed the second half. **The 5.32s of pre-flight is still open** and matters:
this morning's two lost turns died at 3s and 9s.

**#823 `f768bb9`** `turn_id`. Client mints one per user-authored turn; GP keys
on `(user_id, turn_id)`. New `chat_turns` table, `app/services/chat_turns.py`,
`GET /v1/chat/turns/{id}`, 6h TTL, 404 for unknown. **DORMANT until SS ships
the client half.**

**#824 `3c37ad1`** CQ proxy stops dropping keys. See below.

---

## The CQ thread: third instance of one bug

CQ found `client_id` and `deadline_date` absent at their end. GP's request ring
showed both PRESENT on arrival from SS. `PatchCreateRequest` modelled five
fields and pydantic defaults to `extra="ignore"`.

    to_name        silent, found by a three-way audit
    project_name   LOUD, CQ 422'd naming the field we ate, 7 calls
    client_id +    SILENT, 200s and correctly rendered rows
    deadline_date

Both earlier fixes were "add the missing field". **The class is getting QUIETER
as fields get more additive**, so exposure rises as detectability falls. Rule 5
in CLAUDE.md describes this bug in advance and prevented none of the three: a
rule with no mechanism is memory.

Audit of all 9 typed body models found more: `patch_type` dropped on update (so
EVERY patch-type edit was a silent no-op), both `assign-project` handlers
dropping everything, and a dormant `relationship`/`label` trap with 0 calls.

**Fixed:** fields modelled by name, `extra="allow"` on every proxy body model,
and `tests/test_cq_proxy_passthrough.py` which asserts the property BY RUNNING
each route and enumerates routes off the app.

⚠ **CQ's insistence on a behavioural test over a config assertion paid on the
first run.** Both `assign-project` handlers built their payload BY HAND, so
`extra="allow"` did nothing and `model_config["extra"] == "allow"` would have
gone green while they kept dropping.

**PROVEN END TO END 07:40:16.** SS sent `client_id` + `deadline_date`, GP's
ring shows both forwarded, CQ's row has both with `deadline_history` absent and
`last_observed_at` unmoved. First manual patch in their entire database to
carry a `client_id`; every earlier one is null, and the column of nulls stops
at the deploy.

## ⚠ STILL OPEN: the MISNAMING half

`extra="allow"` closes the DROPPING half. **The misnaming half is wide open and
nobody has an instrument**: a name that is wrong on one side is a 200 on every
side. `patch_type` needed all THREE components corrected; any one alone leaves
it broken. A forwarding test cannot prove a key is UNDERSTOOD.

**SS's proposal, Scott's call, not cheap:** a field-name inventory each team
GENERATES FROM ITS OWN CODE (never from a doc, which is a fourth place to be
wrong), then diffs. All three instances would have shown as a name present on
one side only. Needs a generator per codebase plus a differ, and rots if nobody
reads its output.

---

## Two numbers that shrank twice, and why

CQ measured 135 of 345 meetings (39%) with no self-identification, flat over
five months. I measured 131 of 210 retained transcripts (62.4%) with no `(you)`
marker and reported them side by side without asking how both could be true.

**Correction 1 (mine):** the `(you)` marker and `owner_speaker_label` both come
from `speakerEngine.ownerProfileName` (SessionManager.swift:3041). They are not
primary and fallback, they fail together. So GP's drop caused no additional
loss: where the owner was nil there was nothing to drop, where it was set the
marker was there anyway. **"Up to 39% degraded because of us" is RETRACTED.**

**Correction 2 (CQ's):** identification also happens via ITEM OWNERSHIP, no
marker required. Roughly two thirds of identifications in my window came that
way. That reconciles 62.4% vs 37.7%, which could not both be true if the marker
were the only path.

**The real harm bucket** is meetings where the owner was KNOWN but the
transcript's labels never matched, so the field we ate was the only signal.
SS's `inline_replacements=0` case. **The history is genuinely gone** (their
breadcrumb ring rotates every launch; six pulls, zero lines), so it is
measurable going forward only. SS shipped persisted counters
(`unknownOwner` / `knownButNoMatch` / `matched`).

---

## Waiting on Scott

1. **End a meeting.** `owner_speaker_label` moving into `metadata` (SS `089820d`)
   is UNVERIFIED on the wire; no capture-transcript since the restart. One
   meeting end and I read the body.
2. **The field-name inventory decision** (above). Not urgent, is a real call.
3. Older, unchanged from 08-29: sign off the three redeem messages, test-account
   revocation (delete `ddc3df33` refresh_tokens), `enforce_admins` on branch
   protection, the 300-char research-notes cap, the 1.16 upload.
4. Local hygiene: `data/remote-config/` has a runaway `remote-config.aside/`
   recursion ~46 levels deep spraying errors through every local test run.
   Local only, nothing to do with prod.

## Waiting on SS

Client half of `turn_id` (mint, resend, `turn_in_progress`/`turn_unknown`
branches) and the retry card against the served `chat.retry` config. Until then
`turn_id` is live and dormant.

## My next task

**Populate `offer_code_pool`.** Still completely empty. Root cause of the Steven
code mixup, unmoved since the 08-29 handoff.

Then: close the 5.32s pre-flight window by returning the response BEFORE
document extraction and the classifier, registering the turn FIRST so a lookup
during pre-flight returns `in_progress` rather than nothing. That ordering is a
CONTRACT with SS: a frame means a row exists.

---

---

# ⚠ OPEN AND UNRESOLVED: cross-project recall leak

Found by Scott at 21:36Z, AFTER everything above shipped. Nothing below is
fixed. This is the top item for the next session.

## What he saw

A 31 second tech-podcast recording tagged to his **Twit** project produced a
summary that read:

  "...does not contain meeting content related to the projects, decisions,
   action items, or participants listed in the context (CTS, Don, Sai,
   ABM/A2A Integration Platform team members, etc.)"

**ABM / A2A Integration Platform is a real customer engagement of his. CTS is
a real client system.** The recall block injected into that turn carried named
individuals, an overdue commitment with an owner, and a production-promotion
decision from that engagement, plus Camino Caseworks material from a third
project.

Stored verbatim in `usage_log`, request_id `4b0617fb7270`, summary 21:36:41Z.

## NOT a cross-user leak. This matters for how it reads.

Recall is keyed per user (`subject_for(app_id, user_id)`). Measured across the
30 day window: **12 users had context blocks; the customer's names appear in
exactly ONE user's blocks, Scott's.** Nobody saw anyone else's data. It is his
own data surfacing in his own unrelated meeting.

The risk is a summary he forwards, shares or pastes carrying a customer's
people and decisions. The share lane makes that concrete rather than
theoretical.

## Scale

    calls carrying a CQ context block (30d)      : 1196
    of those, block mentions ABM / A2A / CTS     :  758
    distinct users with any context block        :   12
    distinct users whose block named the customer:    1

Runs 2026-08-15 through tonight, mostly summaries, 5 to 16 a day. Steady
state, not an edge case.

⚠ **I could NOT split "legitimately an ABM meeting" from "leaked into an
unrelated project", because `usage_log` does not store the call's project.**
758 is an upper bound on the leak. Storing project on the usage row is the gap
that prevented the audit and is worth closing on its own.

## Both halves proved, so nobody re-derives them

GP's half, proved on GP's side:
  - inbound `/v1/chat` at 21:36:42 carried `project: 'Twit'`,
    `project_id: '8DEBE602-7D07-4F83-8AB0-7DCBA4EDBEC2'`, `context_quilt: True`
  - GP forwards it: ran `_build_recall_metadata` against that exact body
    INSIDE the prod container, output
    `{"locale":"en","project":"Twit","project_id":"8DEBE602-..."}`
  - GP log: `cq_recall_ok matched=3 patch_count=38 recall_scope=full`

So the scope reached CQ and 38 patches came back, none of them Twit.

## CQ's diagnosis (contextquilt-26), three always-on cross-project legs

`/v1/recall` DOES honour `project_id`, on the legs designed for it. Not a flat
ignore and not a fallback-to-global. The exposure comes from three others:

1. **The cue leg** (`main.py:1256`), no project predicate BY DESIGN, documented
   in a comment, built for associative recall. Its matcher is a bare substring
   test (`main.py:940`, `if cue in text_lower`), no word boundary, and the cue
   index holds three-letter cues. **`cts` matches the word "projects"**, also
   "products", "aspects", "effects". `api` matches "capital", "rapidly". Any
   meeting containing those words pulls up to 10 ABM patches into any scope.
2. **The entity/relationship header** (`main.py:1023`, `:1050`) filters on
   user_id only and has NO project concept. Source of the "Projects: CTS",
   "People: Don, Sai" and every "works_on ABM / A2A" line.
3. **The flat leg's null-permissive arm**: `project_id = $2 OR project_id IS
   NULL OR ...`, and the corpus is mostly unstamped (behavior 1407/1486 null,
   person 422/423, insight 95/95, org 44/44).

CQ has NOT re-run that recall (it would write `last_accessed_at` and perturb
decay), so per-line attribution is elimination plus cue evidence, not direct
observation. They flagged that themselves.

## GP'S OWN HALF, and it is ours regardless of scoping

The summary prompt let the model **enumerate the injected context back to the
user** when it could not summarize. Scoping shrinks the blast radius; it does
not make reciting safe. A refusal must never recite the context block.

This is the piece I would ship first: small, GP-only, no recall change.

## THREE DECISIONS WAITING ON SCOTT

1. Ship the GP recital fix now as a same-night mitigation? (my recommendation:
   yes, it is small and independent)
2. Does CQ tighten the cue matcher to word boundaries? One line, kills the
   "projects" collision, does not touch the design intent. (recommend yes)
3. Does the deeper scoping problem become real work? The global entity header
   and the 94.7% unstamped corpus are DESIGN, not defect, and need a posture
   call from him.

CQ has correctly refused to change recall behaviour on their own judgment: it
is the hot path, shared by every app, and byte-stability within a UTC day is a
documented upstream prompt-caching dependency.

## Lessons filed tonight

- **[[feedback-typed-hop-drops-and-misnames]]** the two halves of the class.
- **[[reference-local-venv-off-the-pin]]** local fastapi 0.135 vs the `0.115.*`
  pin. A 401 sabotage came back GREEN because the edit was semantically a no-op
  on the local version. Auth status codes differ; CI is the only honest test.
- **A test can lose a race it never knew it was in.** #824 failed CI twice on a
  recall retention test that does not touch anything I changed. `main.py`'s
  sweep loop calls `purge_observations` at startup as a concurrent task, and the
  `client` fixture arms it. Proven by probe: a row inserted after startup
  disappeared 0.05s later. It had ALWAYS been racy and surfaced only because my
  branch added ~17 tests and shifted the timing, which is the worst way for a
  race to announce itself: it frames a new branch as breaking what it never
  touched, and the tempting reading is "flaky, re-run it".
- **Three wrong hypotheses died before the right one**, each killed by a check
  rather than an argument. What cracked it was noticing my local run used
  `--ignore=tests/integration` while CI runs everything, which CLAUDE.md warns
  about by name.
- **A harness can lie in the shape of the defect.** My passthrough test's sample
  builder unwrapped `Optional[X]` and `list[X]` with one line, so
  `list[FromLabel]` produced a string and reassign-speaker 422'd. I nearly
  EXCLUDED a real route to make the suite pass.
- **Sabotage caught a decorative test of mine.** Predicted 3 red on disabling the
  replay, got 2: the stored-body test compared two LIVE responses and the mock
  returns the same answer every time, so it passed with the feature off.
- **Agreement between two teams proves less than it looks** if both read the same
  doc. SS's client DECODING `label` while ENCODING `relationship` was evidence
  from a third place neither GP nor CQ controls.

## Standing gotchas

- This worktree is shared. Check `git branch --show-current` before believing a diff.
- Deploy is automatic ~3 min after merge; check `/health` `git_sha`.
- CI is the authority. Do NOT `--ignore=tests/integration`.
- GP's request ring (`GET /webhooks/admin/live-log`, `X-Admin-Key` =
  `$CZ_ADMIN_KEY` inside the container) is the ONLY place to see what a client
  actually sent. ~160 entries, turns over fast, resets on deploy. **Dump it
  first, deliberate after.**
- The live config overlay can drift AHEAD of the repo. Hydration bumps the
  OVERLAY's own version when it adds a key; the repo's `version` never reaches
  clients. Do not rely on bumping it to signal a change.
