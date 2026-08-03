# Prompt envelope — composition spec (v2)

Which sections exist in a prompt, what order they appear in, and which of
them land in the `system` block versus the `user` message. Served as
`GET /v1/config/prompt-envelope`.

Last updated: 2026-08-03.

Governed by `docs/decisions/prompt-composition-doctrine.md`. Read that
first: GP owns this recipe, the client executes it, and changing
composition must never require an app update.

## What changed in v2, and why

v1 said `copilot_session` "reproduces the shipped systemPromptTemplate
exactly," so adopting it would change no live behavior. That was true of
the template and false of the wire. The client blanks `{{summary}}` and
`{{project}}` in the system block and re-emits them in the user turn, so
implementing v1 as written would have moved two large blocks into the
system turn on the one surface the spec claimed to be leaving alone.

CQ caught it by diffing the spec against what the client actually sends.
Three fixes follow from it, and the third is the one that matters most.

**Placement is per surface now.** v1 declared placement once per section
and asserted it globally. Reality is that the same section legitimately
lands in different turns on different surfaces, and a model that cannot
express that will keep producing specs that cannot describe the wire.
Sections still carry a `default_placement`, but each surface states its
own lists.

**Sections exist for everything the client sends.** `conversation_history`
and the attached-photos note ride the wire today and had no entry at all.
An absent section reads as undefined, not unchanged.

**Every surface declares whether it has been checked.** `verification` is
`adopting_now`, `byte_diffed_against_wire`, or
`unverified_describes_intent`, with a note. The v1 failure was not a wrong
value, it was a confident sentence about something nobody had verified.
The two session surfaces are flagged unverified and stay that way until
someone diffs them.

## Lanes

The recipe has to work for the on-device foundation model, Shoulder Surf
AI, a user's own key, and models that do not exist yet.

`lane_resolution` maps the client's `apiFormat` to a lane and falls back
to `byok` for anything unlisted. It is data rather than code precisely so
that adding a lane never needs a build. **The `default` lane must never be
removed**; it is what makes an unknown future model work.

Each lane declares:

- `instructions_editable` — the editability rule, below.
- `instructions_variant` — which served instruction text this lane uses.
  `on_device` asks for `compact` because roughly 245 tokens of cloud-
  written instructions against about 3072 usable is 8% of that budget
  spent before any transcript arrives.
- `surface_overrides` — per-surface recipe changes for that lane, for
  cases like a small model that cannot carry every section.

## Editability

| lane | user can edit the instructions? |
|---|---|
| `on_device` | no |
| `managed` (Shoulder Surf AI) | no |
| `byok` | **yes** |

The asymmetry is deliberate and is the whole point. On BYOK the
**contents** of the global system instructions become editable, because
it is the user's key, model and bill and we hand them ours as a starting
point. The **recipe** never becomes editable on any lane. A BYOK user may
rewrite the instructions; they may not decide the meeting summary moves
into the user turn.

Editability is a property of the lane, not of the section, which is why it
lives under `lanes` rather than under `sections`.

## Sections

Each section has a stable id, a `default_placement`, and a stability
class. Stability drives ordering: the more often a section changes, the
later it goes, so everything above it stays cacheable.

| id | default | stability |
|---|---|---|
| `global_system_instructions` | system | per user |
| `prompt_system_instructions` | system | per call type |
| `output_constraints` | system | static |
| `project_context` | system | per project |
| `meeting_context` | system | per selection |
| `rolling_summary` | system | periodic |
| `context_quilt` | system | per turn |
| `conversation_history` | user | per turn |
| `attached_photos_note` | user | per send |
| `reference_text` | user | per send |
| `transcript` | user | per turn |
| `user_query` | user | per turn |

`transcript` and `conversation_history` both grow every turn and must
never appear in a system block on any surface.

## Surfaces

| surface | call types | verification |
|---|---|---|
| `copilot_session` | `query`, `analysis` | unverified |
| `post_session_analysis` | `analyze_session` | unverified |
| `meeting_chat` | `meeting_chat`, `meeting_chat_follow_up` | adopting now |
| `project_chat` | `project_chat`, `project_chat_follow_up` | adopting now |

`global_system_instructions` is present on all four. On the chat surfaces
that is the single deliberate wire change; everything else must byte-diff
identical.

**The session surfaces are not safe to implement from this file.** The
placement is corrected, but the ORDER within the user turn has not been
diffed against the client and is not asserted. Confirm against the wire
first.

## Precedence

`global_system_instructions` first, `prompt_system_instructions` second.
GP's instruction is the more proximate one when they disagree, and the
position is the lever. That is structural on purpose: asking a model in
prose to prefer one section over another is weaker than ordering, and it
would put enforcement in text the client assembles.

## Caching

Cache breakpoints are GP's and the client does not manage them. GP marks
the system block cacheable, splitting at the Context Quilt recall boundary
when recall is present. So anything changing per turn must sit after
`cache_stable_prefix_ends_after`, or in the user turn.

That is the real reason summary and project ride the user turn on the
session surfaces. In the system block they would bust the cached prefix on
every turn, since both change per meeting and per turn.

Stated plainly because it came up as a suspected blocker: **GP does not
cache a prefix shared between users.** The chat prompts already carry
per-meeting and per-project context, so they are per conversation before
anything is added, and the benefit is across turns within one
conversation. A section stable per user costs nothing. Do not design
around a cross-user cache.

## Compatibility

Served under its own config name, so a build that does not know about it
never requests it and cannot be affected by its existence or its shape.

Consuming it is additive: new optional fields, no restructuring of
anything a shipped build requires. See the tier-catalog incident on
2026-08-03 for what the other kind of change costs.

## What GP does not do

GP does not enforce this. `system_prompt` arrives assembled and is passed
through, so the envelope is a contract the client composes to. GP's
server-side assembly only engages when a request arrives with no
`system_prompt` at all.

Two consequences. A client that ignores the spec still gets an answer, it
just gets a worse one with worse caching. And the version number is how GP
finds out which envelope a client is composing to, which matters the first
time we change the ordering.
