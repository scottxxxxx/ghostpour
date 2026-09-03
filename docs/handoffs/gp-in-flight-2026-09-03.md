# GhostPour: session handoff (2026-09-03, session cloudzap-54 [f792a1])

Same session that wrote `gp-in-flight-2026-09-02-close.md`, continued past
midnight. **That file is now WRONG on its top item and this supersedes it.**

**Prod and main are BOTH `31f6343`**, container healthy, `material_kind`
confirmed live in the running process. **ZERO open PRs**, clean tree.
This is the first time today prod and main have matched, because everything
before #862 was docs and `docs/**` is in the deploy workflow's
`paths-ignore`.

---

## ✅ SCOTT CAN COMP PEOPLE AGAIN (the 09-02 close says otherwise; it is stale)

Batch **549815 is LOADED**. Pool went **10 → 510 available, 0 reserved**.
500 loaded, 0 skipped. Verified with raw SQL as a second instrument, not
just the loader's own return value: 500 rows under batch 549815,
production, all `available`, `product_id`
`com.weirtech.shouldersurf.sub.pro.monthly`, and distinct codes equal to
total rows so nothing duplicated. The pre-existing 10 from batch 550023 are
untouched. **No code strings were printed at any point.** Scripts removed
from host and container and confirmed gone.

⚠ **THE RECIPE IN `gp-in-flight-2026-08-31-close.md` IS WRONG.** It gives
`load_pool(offer_id=..., environment=..., batch_id=..., product_id=...)`.
The real signature needs a `db` connection as its FIRST POSITIONAL argument
and an explicit `codes` list (`app/services/offer_dispense.py:97`). Written
as documented it is a TypeError on the first call. Caught by opening the
file rather than trusting the note, which is rule 7's shape.

⚠ **THE CLOCK IS STILL THE PROBLEM AND IT IS FOUR WEEKS OUT.** Everything
unexpired dies **2026-10-01**. The second round Scott ruled, a generic comp
offer, still has to be created BY HAND in App Store Connect (Apple has no
API) and creating it forces a 500-code mint. Untouched.

## ✅ SHIPPED: `material_kind` (#862, prod `31f6343`), a real deploy

CQ doc 22 option C on Scott's ruling. **GP was the only gate**, found by
enumerating all 43 `cq_proxy` routes: `/v1/capture-transcript` is the one
non-verbatim forwarder, it hand-enumerates arguments into `cq.capture()`,
and its metadata passes only `CAPTURE_METADATA_ALLOWLIST`. So the flag was
accepted by pydantic and read by nothing.

⚠ **Scott's word was RELAYED first and the relay was refused.** CQ passed
his instruction verbatim and even said they would rather be asked than
assumed. Asked directly; he said go. Third refusal of relayed authority in
two days and the boundary held every time.

**Proved by execution in the container on the OUTBOUND call**, never off
the green tick:

    deployed allowlist has material_kind: True
    "listening"              -> 'listening'        exact
    "meeting"                -> 'meeting'          exact
    "  Listenting  "         -> '  Listenting  '   UNMODIFIED
    absent                   -> key absent (GP invents no default)
    unknown sibling key      -> dropped; material_kind still crosses

⚠ **GP DOES NOT VALIDATE THE VOCABULARY AND MUST NOT START.** CQ owns it.
A check here would be a second place to update and a new way to drop a kind
they add later. CQ's `from_metadata` strips and lowercases before
comparing, so `Listening` and `LISTENING` resolve; that behavior was
covered by NO CQ test until this evening and is pinned now. Our not
normalising is safe BECAUSE of that, not merely on principle.

**The tests assert the VALUE, not the key.** CQ reads absent AND
unrecognised as `meeting` alike, deliberately, so a misspelling is silently
a meeting and looks identical to the flag never arriving. A presence-only
test stays green while GP trims, lowercases or defaults, and all three look
like success downstream. Sabotage both directions: removing the allowlist
entry fails 3 of 4, making GP normalise fails EXACTLY 1, the identity test.

## ✅ CLOSED ON A DEVICE: Accept-Language / CQ #406

Scott set the app to Spanish; a person page served
`44 de 52 / otros 43 de 92 / temas abiertos que no han surgido...`.

⚠ **The device acceptance test earned its keep and this is the receipt to
remember.** CQ's #406 localized NOTHING on real data until CQ #413 (prod
`a6a23fb`), because they keyed on the wrong container and field name and
tested against a fixture INVENTED FROM A MODEL CLASS rather than a stored
row. **Had Scott tested two hours earlier he would have seen English, and
the first suspect would have been GP's hop, which was innocent throughout.**
Every circumstance would have supported that: our header work was newly
shipped and "GP drops the header" fits the observable perfectly. A 200 was
available all afternoon and would have been true. Both hops were correct.
Only the words were wrong.

## N-400: contract settled, and the gap is WIDER than #852 suggested

Settled with `fable-auditor-d5` (delegated reviewer on the N-400 client).
Detail in project memory `project_n400_contract_2026_09_02`.

**They believed the interview lane did not exist and that it blocked their
launch. It exists** (#852, `040b0c8`, deployed). They were two metadata
keys away: their envelope omitted `section_label` and `question_text`, both
declared required, so their call 422s today. Proved by execution.

⚠ **THE RULING IS ONE SET OF FIVE, NOT FIVE ITEMS.** Inputs `confirmed`,
`confirmed_components`, `device_intent_hint`; outputs `confirmation`,
`intent`. The deployed system prompt CLOSES the response schema at seven
fields (`schema_version, turn_id, reply, facts, clarification, conflict,
complete`), so `confirmation`, `policy_decision` and `feature_state` are
absent and the word `confirmation` does not occur in the prompt at all.
**I under-reported this earlier as input-side only; duty 2 (direct mint vs
confirm) is also unexpressible.** Piecemeal is useless: a client that can
send a hint and gets no label back is no better off.

⚠ **THE CLASSIFICATION LANE WOULD STARVE THE INTERVIEW.**
`app/services/app_budget.py:149` sums `estimated_cost_usd` over
`(user_id, app_id, month)` with **no call_type awareness, no sub-cap, no
reserve**. A fire-and-forget classification call draws the SAME $5 pool as
the interview. If it exhausts the cap, the next interview turn returns the
budget 200 with empty `text` and the applicant loses the interview for
calls they never saw. **The coupling runs the wrong way**: control
utterances cluster when someone is confused or on a hard section, so this
spend rises exactly when the interview is what they cannot afford to lose,
and fire-and-forget hides it so the first symptom is one layer from the
cause. **Mitigation is GP-side and does not exist: a reserve.** Ruling the
lane in without it IS ruling in the starvation path. N-400 has built the
seam dark and gated it on BOTH the ruling AND the reserve existing.

⚠ **CALIFORNIA CAN BUY AN APP WHOSE CORE CAPABILITY IS RESTRICTED THERE.**
Scott opened purchasing in every state (an App Review tester outside TX
could not complete the IAP, a rejection risk). The live matrix has
`populate_field` RESTRICT in CA against ALLOW in TX, and
`assess_moral_character_impact` BLOCK in CA against ESCALATE in TX. Sits on
top of #849, still `legal_review_status: PENDING` with two rows on
`basis: NEEDS_LEGAL`. Revenue and legal, not a rendering question. Raised
independently by GP and N-400, uncoordinated.

Also: a real device defect. **Nothing classifies an utterance before it
becomes an answer**, so "can you repeat that" and the phone hearing its own
TTS are filed as the applicant's answer to a federal form question. They
are building the on-device deterministic lane now.

## The `interview-turn.json` reconcile, and a WRONG FINDING OF MINE

Done: the local `data/remote-config/n400/interview-turn.json` now matches
prod byte for byte.

⚠⚠ **MY FIRST ACCOUNT OF THIS WAS WRONG AND HAD ALREADY HARDENED INTO A
RULE.** I reported two files claiming `version: 3` while differing, called
it UNFIXED, and said no PR could fix it because `data/` is gitignored. All
three were wrong, because I found two copies and stopped looking. **There
are THREE:**

1. `config/remote/n400/interview-turn.json` is **TRACKED, THE SOURCE**,
   byte-identical to prod, and GUARDED by
   `tests/test_n400_interview_turn_config.py`, which asserts exact set
   equality between the template's placeholders and a declared set.
2. The prod container's copy is correct.
3. `data/remote-config/...` on this machine is a gitignored RUNTIME BUNDLE,
   the only stale one.

So there was no repo defect and no version-marker failure in the tracked
contract. It is the config bundle sync trap: the runtime bundle drifts from
the tracked source locally and nothing notices, because the TEST reads
`config/remote/` while the APP reads `data/remote-config/`.

**What broke the story: I ran the suite against the stale copy EXPECTING IT
TO FAIL. It passed, which was only explicable if the test read a different
file.** Nothing in the two files could have revealed it. Corrected in
memory, including that the first account was wrong, because I had already
told a partner team not to read our repo on the strength of it.

**Answer config questions from `config/remote/` or the container. NEVER
from `data/remote-config/`.**

## Waiting on Scott (nothing is blocked on GP)

1. **N-400's five-item variable set as ONE ruling**, with the budget
   reserve attached as a sixth condition rather than a follow-up.
2. **California purchasing vs the RESTRICT matrix, bundled with #849.**
   Two teams independently called the legal review a launch blocker.
3. Server-side enforcement of verbatim evidence and the safety_legal facts
   ban, so both N-400 lanes are symmetric. Code change.
4. Prompt and model tuning for the N-400 lane, which he owns with them.
5. The **shared tier** across the three apps.
6. Unwinding Tech Rehearsal spend already recorded in ShoulderSurf
   allowances.
7. The per-app counter migration.
8. **The generic comp offer, by hand in ASC, before 2026-10-01.**

## Cross-team state

⚠ **Partner sessions were RETIRED AND REPLACED mid-exchange.**
`contextquilt-9d` → `contextquilt-af`, `shouldersurf-c7` →
`shouldersurf-35`, both in the same tmux panes. Run `ListAgents` before
addressing anyone; a name from earlier in a transcript may be gone. Their
handoffs DID carry context, so do not re-explain by default.

**CQ (`contextquilt-af`): nothing open.** Their #416 (`106d0f0`) adds an
`unrecognised_kind` WARNING with the value verbatim, silent when absent.
It exists because GP reported the VALUE rather than the key: CQ logged
nothing when a kind arrived and did not resolve, so a typo and a dropped
field were distinguishable on the wire and NOT in their log, which is where
acceptance is read. **That warning is now the acceptance test's NEGATIVE
CONTROL**: a correct first send is takeaways + zero behavior rows + zero
new person entities AND NO warning line.

**SS (`shouldersurf-35`): they are the LAST HOP** and have been told they
are clear to send `material_kind`. Flagged to them that a misspelling
errors nowhere at any hop, so the two literals belong in one place on their
side.

**N-400 (`fable-auditor-d5`): nothing blocking from GP.**

## In flight (not ours, but expected)

SS sending the flag; then CQ's step 3 acceptance, which is theirs to read
and which they will report either way, including a failure.

## Gotchas earned

⚠ **`gh pr checks <n> --watch` can exit 0 having proved nothing.** Started
before the workflow registers it prints "no checks reported on the branch"
and exits SUCCESSFULLY. **Read the words, not the exit code**, and
re-check independently before merging on a watch result.

⚠ **A sabotage can fail for the WRONG REASON and still look like proof.**
Removing the allowlist entry failed with `KeyError: 'metadata'`, not my
assert message, because an emptied metadata dict is omitted entirely and
the subscript raised before the assert could speak. Same defect as #838,
same fix (`.get()`), then re-sabotaged to confirm the messages print.
**The tell was that the failure message was not mine.**

⚠ **Stopping at two copies produced a confident wrong answer** that reached
Scott, a memory file and a partner team before anything checked it. See the
reconcile section. The instrument that broke it was a test run EXPECTING
failure.

⚠ **The tenth-rule candidate (a flattering sabotage surprise is a finding,
not a strong test) is HELD, unpromoted, NOT put to Scott.** Two of its
three receipts are GP's and CQ's from one evening, produced while
discussing sabotage with each other, which is one observation counted
twice. Promotion criterion, CQ's and better than mine: an instance where
somebody NOT in that conversation gets a flattering surprise and checks the
mutation BECAUSE THE RULE TOLD THEM TO. SS drafts if it recurs.
