# Prompt composition doctrine

Scott, 2026-08-03. Standing, not a one-off call. Read this before touching
the prompt envelope, `protected-prompts`, or anything that decides where a
piece of context lands on the wire.

## The rule

**GhostPour owns the recipe. The client executes it.**

The recipe is: which pieces of Shoulder Surf's information go into the
system instructions, which go into the user message, in what order. That
composition is dictated by GP, served as config, and must be changeable
from GP alone.

Three consequences, all load bearing:

1. **No app update to change composition.** If moving a section from the
   system block to the user turn requires shipping a build, the pattern is
   broken. That is the entire point.

2. **The recipe is never baked into the client.** A client that hardcodes
   placement is not implementing the recipe, it is holding a private copy
   of one. Existing hardcoded placement is a migration problem to be
   retired, never a behavior to preserve on the grounds that it currently
   works.

3. **Every model, including ones that do not exist yet.** On-device
   foundation models, Shoulder Surf AI, and any BYOK model the user
   picks. Models change constantly, so the recipe has to be expressible
   per model or per lane, with a default that applies to a model we have
   never heard of. A composition spec that only covers the models we ship
   today fails the requirement.

## Global system instructions

The global system instructions are one **component inside** the recipe,
not a separate system. GP authors and serves the text.

Editability depends entirely on whose model is paying:

| lane | editable by the user? |
|---|---|
| on-device foundation model | **no** |
| Shoulder Surf AI (managed) | **no** |
| user's own key (BYOK) | **yes** |

On the two lanes we run, the served text is what is used, full stop. On
BYOK it is their key, their model and their bill, so we hand them our
instructions as the starting point and they are free to override them.
That flexibility is deliberate and is offered only there.

Note the asymmetry: the *contents* of the instructions become editable on
BYOK, the *recipe* never does. A BYOK user may rewrite the system
instructions; they may not decide that the meeting summary moves into the
user turn.

## What this rules out

- Endorsing client-side hardcoded placement because changing it would be
  disruptive. Correct sequencing is to describe current behavior
  accurately first so adoption is a no-op, then move things by config.
- Treating a composition question as the client's to answer. If the
  question is "where does this go on the wire", the answer is ours.
- Shipping a composition spec keyed only to surfaces, with no way to vary
  by model or lane.
- Letting the user edit system instructions on the managed or on-device
  lanes because it seems generous.

## How this got written

The 2026-08-03 prompt envelope described `copilot_session` as putting
`summary` and `project` in the system block. The shipped client blanks
both there and re-emits them in the user turn, so the spec described the
template rather than the wire. When that was raised, the first instinct
was to endorse the client's blanking as load bearing and keep it.

That instinct is the violation. Preserving hardcoded client placement
because it currently produces the right bytes concedes exactly the
control this doctrine exists to hold. The right answer is that the
envelope must state where those sections go today (the user turn), so
adopting it changes nothing, and from then on that placement moves when
GP says so and without a build.

## Sibling coverage (SS's rule 2, generalized)

SS's rule: every entry in a collection carries every key its siblings
carry. The missing Japanese instruction file is the same rule one level
up, at file granularity instead of key granularity, and it hid for the
same reason: nothing compared a config against its peers.

So the rule generalizes in both directions:

- **Keys.** Every entry in a collection carries every key its siblings
  carry, even when semantically empty.
- **Locales.** Every served config covers the same locale set its
  siblings cover, or declares which locales it does not and why.

A gap is allowed. A silent gap is not. Both are enforced in
`tests/test_served_config_shape.py`, with exemptions that must carry a
reason and that fail once they stop applying, so the checks cannot rot
into rubber stamps.

Standing corollary, from SS: **treat quiet as no signal for anything
older than 2026-08-02.** Those builds cannot report a decode failure. If
you want evidence a change landed safely on them, it has to come from
something other than the absence of alerts.

## Placement has one owner

`model-capabilities.promptPlacement` is deprecated in place and must be
ignored. It cannot be deleted, because removing a field is never safe for
any build ever shipped, which is what makes an explicit precedence rule
mandatory rather than tidy-up. The envelope wins.

Open assumption, flagged rather than assumed away: we believe no shipped
build reads that value. SS confirmed the type declares it non-optional,
which proves the key must be *present*, not that anything consumes it.
Their per-build manifest settles it cheaply and they are generating it
anyway.

## Presence is not consumption

CQ's rule, added 2026-08-04 after the third instance of the same failure
in a week. Their doc 17 carries it too.

**Presence in a payload is not evidence of consumption. A field is
integrated only when every hop that must read it is shown to read it.**

The three instances, which looked unrelated and were not:

- A `people` entry was given only the keys it needed to **render**. It was
  a valid entry, and the shipped decoder required two fields it lacked, so
  the whole tier catalog was discarded on every build in the field.
- The envelope described the **template** rather than the wire. The
  sentence was true about `systemPromptTemplate` and false about what the
  client actually sent, because the client blanked two sections at render
  time.
- A capture metadata key was **accepted** by pydantic, sat in the request
  object, and was read by nothing, because the outbound body was rebuilt
  from a closed parameter list.

Every time, the artifact in front of us looked right and the payload was
not, and every time it failed silently. The habit that produces it is
checking the thing you are holding instead of the thing that travels.

What this means in practice:

- **Test the outbound body, not the inbound request.** The inbound side is
  rarely the broken half. Asserting what we put on the wire localises a
  break to one hop; asserting that a partner received something tests
  their leg and ours together and tells you neither.
- **A new field is not done when it validates.** It is done when the hop
  that consumes it is demonstrated to consume it, with a test that fails
  if the wiring is removed.
- **Prefer one auditable list to several enumerations.** A key enumerated
  in three places fails silently when one is missed; an allowlist fails
  visibly, because there is somewhere to look.
- **Quiet is not health.** Already recorded above for pre-2026-08-02
  builds; this is the same rule pointed at our own code rather than at a
  frozen client.

## Open work this implies

- Envelope v2: correct `copilot_session` to the real wire, add sections
  for `conversation_history` and the attached-photos note, and add a
  per-model or per-lane dimension with a default for unknown models.
- Serve the per-surface override for global system instructions. Today
  `contextOverrides` is a client-side construct and GP neither serves nor
  reads it, which means that knob is currently the client's, not ours.
- A shorter on-device variant of the instruction text. The current default
  is roughly 245 tokens against about 3072 usable on the on-device lane,
  so 8% of that budget goes to instructions written for cloud models.
