# Prompt envelope — composition spec

Which sections exist in a prompt, what order they appear in, and which of
them land in the `system` block versus the `user` message. Served as
`GET /v1/config/prompt-envelope`.

Last updated: 2026-08-02.

## The split this rests on

Two things were tangled together under "Global System Instructions" and
most of the confusion came from treating them as one.

**The envelope** is structure: which sections exist, their order, and
system versus user placement. It is a function of the model and the
provider API, it moves response quality, and it decides how much caching
benefit we get. GP owns it. It is not user-editable on any lane,
including BYOK, because we need to change it in a later version without
renegotiating.

**The contents** of the global instructions section is text. GP ships the
starting version. On the managed and on-device lanes it stays fixed. On
the user's own key they can rewrite it, because it is their key and their
bill.

This document specifies the envelope. The contents live in
`protected-prompts.defaultGlobalSystemInstructions`.

## Why it exists

`globalSystemInstructions` had exactly one consumer in the client,
covering the live session query path and post-session analysis. Neither
chat surface went near it, so a standing rule the user set held in one
place and evaporated everywhere else. On the managed lane it held nowhere
at all, because the locked path substitutes GP's default at the point of
use.

The client was not going off-script. We had never specified where the
section belonged, so each surface made its own decision. This is that
specification.

## Sections

Each section has a stable id, a placement, and a stability class. The
stability class is what drives ordering: the more often a section
changes, the later it goes, so that everything above it stays cacheable.

| id | placement | stability | editable |
|---|---|---|---|
| `global_system_instructions` | system | per user | BYOK only |
| `prompt_system_instructions` | system | per call type | never |
| `output_constraints` | system | static | never |
| `project_context` | system | per project | never |
| `meeting_context` | system | per selection | never |
| `rolling_summary` | system | periodic | never |
| `context_quilt` | system | per turn | never |
| `reference_text` | user | per send | never |
| `transcript` | user | per turn | never |
| `user_query` | user | per turn | never |

Placement is not negotiable per surface. A section that says `system`
never appears in the user turn, and the reverse. `transcript` is in the
user turn specifically because it grows on every turn, and anything that
grows must stay out of the cached prefix.

## Surfaces

Four surfaces, keyed by the call types the client already sends.

| surface | call types |
|---|---|
| `copilot_session` | `query`, `analysis` |
| `post_session_analysis` | `analyze_session` |
| `meeting_chat` | `meeting_chat`, `meeting_chat_follow_up` |
| `project_chat` | `project_chat`, `project_chat_follow_up` |

The served file lists, per surface, the ordered `system` sections, the
ordered `user` sections, and `cache_stable_prefix_ends_after`: the last
section that is still expected to be byte-identical across turns.

A surface may also carry `stability_overrides` where it genuinely freezes
a section that is otherwise allowed to move. `post_session_analysis` is
the one case today: the session is over, so `rolling_summary` is final
rather than periodic, and it is large enough to be worth having inside
the cached prefix. An override has to name a section the surface actually
composes and has to carry its reasoning, so it stays a statement of fact
rather than a way to move the boundary wherever is convenient.

`global_system_instructions` is present on all four. That is the
substantive change here, and it is what makes a standing rule actually
standing.

## Precedence

`global_system_instructions` comes first, `prompt_system_instructions`
second. When the two disagree, GP's instruction is the more proximate one
and the position is the lever.

That is structural on purpose. Asking the model in prose to prefer one
section over another is weaker than ordering, and it would put the
enforcement in text the client assembles rather than in the spec. Where a
conflict genuinely needs stating in words, GP states it inside
`prompt_system_instructions`, which GP authors on every lane.

## Caching

Ordering is by stability, volatile last, so the prefix stays byte-stable
across turns.

Worth stating plainly because it came up as a suspected blocker: GP does
not cache a prefix shared between users. The chat prompts already carry
per-meeting and per-project context, so they are per conversation before
anything is added to them, and the benefit GP gets is across turns within
one conversation. A section that is stable per user therefore costs
nothing in the prefix. Do not design around a cross-user cache.

## Compatibility

This ships as a **new config name**. Build 803, the App Store release, is
frozen with the old all-or-nothing decoder and never requests a name it
does not know about, so it cannot be affected by this file existing.

Consuming it is additive on the client side too: new optional fields, no
restructuring of anything a shipped build requires. The three things that
break an older decoder are removing or renaming a required field,
changing a field's type, and restructuring a container. None of those
happen here.

See `reference: iOS config decode failure` for the full rules and for the
server-side detector that watches for a client stuck on its bundled
config.

## What GP does not do

GP does not enforce this. `system_prompt` arrives assembled and is passed
through, so the envelope is a contract the client composes to, in the
same spirit as `project-chat-prompt-assembly.md`. GP's server-side
assembly path only engages when a request arrives with no
`system_prompt` at all.

Two consequences. A client that ignores the spec still gets an answer, it
just gets a worse one with worse caching. And the version number on this
file is how GP finds out which envelope a given client is composing to,
which matters the first time we change the ordering.

## Known follow-up, deliberately not bundled

`copilot_session` reproduces the shipped `systemPromptTemplate` exactly,
including `context_quilt` sitting ahead of `rolling_summary` and
`project_context`. That ordering is not cache-optimal: recall changes
every turn, so everything after it falls outside the stable prefix.

It is left alone here so that adopting this spec changes no live
behavior on the surface that already works. Reordering it is a value
change to a string field, which is safe for the 803 decoder, but it is a
behavior change and belongs in its own decision with its own before and
after.
