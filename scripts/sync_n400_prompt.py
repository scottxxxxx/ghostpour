"""Sync one leaf of a served n400 config from the DEPLOYED bundle, then prove it.

Runs INSIDE the prod container:

    scp -i ~/.ssh/gcp_deploy_key scripts/sync_n400_prompt.py \
        scottguida@35.239.227.192:/tmp/
    ssh -i ~/.ssh/gcp_deploy_key scottguida@35.239.227.192 \
        'docker cp /tmp/sync_n400_prompt.py ghostpour:/tmp/ && \
         docker exec ghostpour python /tmp/sync_n400_prompt.py --expect-sha <merge-commit>'

Reads the admin key from the environment and never prints it.

WHY THIS IS IN THE REPO AND NOT IN /tmp
---------------------------------------
It used to live only in /tmp on two machines, one container recreate away from
the fate of ste_full.json. Worse, the check that matters most was not in it at
all: the operator was expected to confirm the deploy had landed BEFORE running
the sync, and that instruction lived in a chained shell command and in prose.
That is the #948 shape, a command that exists only in chat and is therefore
never run. `--expect-sha` puts the guard inside the thing it guards.

THE TRAP IT EXISTS FOR
----------------------
`sync-from-bundle` copies from the bundle INSIDE THE RUNNING CONTAINER. Run it
before the deploy carrying your change has landed and it pushes the OLD text and
reports success doing it. A sync REPORT and a SERVED VALUE are different events.
So this prints the report, then reads the document back and checks actual
strings.

⚠ `PUT /webhooks/admin/config/{slug}` is a FULL DOCUMENT REPLACE that rejects a
body with no `version`. Never "just PUT systemPrompt": a partial PUT succeeds and
silently deletes the rest of the document. This script never PUTs; it syncs one
leaf pointer, so a dashboard edit to any other key survives.

⚠ PER-VERSION: `PHRASES` and `BLOCK_LINE` below describe ONE prompt version.
They are the v30 (#966) deferral rule and its counterweight. When you ship a new
prompt version, update them to phrases from THAT version, or this reports success
against strings nobody changed. A check that cannot fail is decoration.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SLUG = "n400/interviewer-turn"
ADMIN = "http://localhost:8000/webhooks/admin/config"
HEALTH = "http://localhost:8000/health"

# --- what the served copy must say, PER VERSION -----------------------------
#
# Each version names the ONE phrase that must appear exactly once, the phrases
# that must all be present, and a substring that locates the block the rule
# lives in (a substring, not a line number: v30's check indexed line 83 and a
# reflow would have moved it silently). A sync run against a version whose
# phrases are not listed here refuses, because verifying a new prompt against
# strings nobody changed is a check that cannot fail.
VERSIONS = {
    30: {
        "block_anchor": "DEFERRALS",
        "once": "AND THE CLAUSE MUST NOT BE CONDITIONAL",
        "phrases": [
            "to firm up later IF NEEDED",
            "we can pin the exact day later IF IT MATTERS",
            "to verify the exact days IF NEEDED",
            "Say the return as a fact, not a possibility",
            "if you can find it",
            "when you have your mail in front of you",
            "A condition on WHETHER she has to come back is not",
            "por verificar",
            "EVERY DEFERRED FIELD, NAMED, NOT ONE OF THEM",
        ],
    },
    31: {
        # The schema block, where the intent line lives. #972: intent is an
        # object, always. The v30 rule must SURVIVE the edit, so its once
        # phrase is asserted present here too.
        "block_anchor": '{\n  "schema_version": 1,',
        "once": '"intent": an OBJECT, never a bare string, {"type": string',
        "phrases": [
            "Write `intent` as an object, always, even when `type` is the only",
            "The bare string is the old shape and is being retired.",
            "`intent.type` is exactly one of:",
            '"target": omitted',
            '"confidence": omitted',
            '"disambiguation": omitted',
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            "if you can find it",                         # and its counterweight
        ],
    },
    32: {
        # The schema block, where the `deferred` line lives. deferred.origin:
        # a capture gap is owed TO her. Edit B REPLACED v31's "goes NOWHERE"
        # passage, so its absence is part of the claim; v30's rule and v31's
        # object intent must survive.
        "block_anchor": '{\n  "schema_version": 1,',
        "once": '"origin": "applicant" or "capture_gap"',
        "phrases": [
            "A PASSING MENTION OF A FACT YOU CANNOT RECORD",
            "said earlier, not yet recorded, ask again",
            "A capture gap NEVER closes a slot",
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        # Absence is checked only beside the presence phrases above: on its own
        # it is true of text that never had the rule.
        "absent": [
            "goes NOWHERE",
        ],
    },
    33: {
        # DEFERRALS names Part 9: v32's capture gap measured 0 of 4 live,
        # with the model's own raw output empty. Edit A replaced the worked
        # example and the "nearest field id" sentence, so the old example's
        # absence is part of the claim. v30, v31 and v32 must survive.
        "block_anchor": "When the applicant cannot give a value",
        "once": "The entry is the record and the reply is not",
        "phrases": [
            "p9.selective_service_registered",
            "the boundary never empties `deferred`",
            "nearest field id you can name",
            "a capture gap is not that decision",
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            "I will ask about your address when we get to that part",
            "had my green card since 2019",
            "goes NOWHERE",
        ],
    },
    34: {
        # The reply-shape rule: every reply opened "Got it, <her answer>"
        # (Scott, 2026-09-15). Edit A replaced the one-line acknowledgement
        # rule, so its absence is part of the claim. v30 to v33 must survive.
        #
        # Shipped as four cuts (v34, v34b, v34c, v34d), each anchored on the
        # last one's replacement text. v34b's vary-the-opener sentences were
        # REPLACED by v34c's "no opener at all", so they are in `absent`: a
        # served copy carrying them is a stale cut, not a new version.
        "block_anchor": "Keep each reply to one to three short spoken sentences",
        "once": "A PLAIN ANSWER GETS NO ECHO",
        "phrases": [
            "ECHO ONLY WHAT COULD HAVE BEEN MISHEARD",
            'Never "Got it, no"',   # sentence-initial since v34b/c reflowed it
            "it begins with the next question itself",    # v34c: no opener
            "it never means no read-back",                # v34d: but still echo
            "a value you do not echo is still minted",
            "The entry is the record and the reply is not",  # v33, must survive
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            "acknowledge what landed, then ask the next thing",
            "Never the same opener two turns running",          # v34b, replaced
            "look at the first word of your own previous line",  # v34b, replaced
            "had my green card since 2019",
            "goes NOWHERE",
        ],
    },
    35: {
        # ASK THE DAY ONCE, with a way out. Scott, build 52: "I was never
        # prompted to provide the exact days." Shipped as five cuts (v35, v35b,
        # v35c, v35d, v35e) after the first probed 0 of 3; each anchors on the
        # previous cut's replacement text. v30 to v34 must survive.
        "block_anchor": "When the applicant cannot give a value",
        "once": "A MONTH AND A YEAR IS ASKED, NOT DEFERRED",
        "phrases": [
            "ONE DAY PER QUESTION",
            "THE MOVED-OUT DAY IS THE VERY NEXT QUESTION",
            "AND THE DAY SHE GAVE IS SAID BACK FIRST",   # v35e
            "a day she gave and never heard back",       # v35e
            "A promise to ASK later is not a promise to verify",
            "as a FLOOR under you and never a move to imitate",
            # Present TWICE (the rule and its worked example), so this is a
            # presence check and must never become a `once`.
            "If you don't know it offhand, we can check it later",
            "A PLAIN ANSWER GETS NO ECHO",               # v34, must survive
            "it never means no read-back",               # v34d, must survive
            "The entry is the record and the reply is not",  # v33, must survive
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            # v35b replaced this; it sat beside the ask-once rule and
            # contradicted it, which is why v35's first cut probed 0 of 3.
            "A partial date is always a deferral",
            "VERY NEXT QUESTION: the turn after",   # v35d's passage, v35e replaced it
            "I'll need the exact day",
            "never demand exact days",
            "acknowledge what landed, then ask the next thing",
            "had my green card since 2019",
            "goes NOWHERE",
        ],
    },
    36: {
        # NAME THE BASIS, NEVER JUDGE IT. From v34d's live receipt: "six years
        # on your own is the general five year path, so that fits" reads back a
        # DERIVED basis and then rules on it, presenting an inference as
        # something the form has checked. The lane did not invent "that fits";
        # our own worked example taught it, in English and in Spanish.
        #
        # MINT THAT BASIS IN THIS SAME RESPONSE is untouched and is in the
        # phrase list on purpose: the blank eligibility box (conf-v20) is the
        # defect that rule exists for, and an edit aimed at the verdict word
        # has no business weakening the mint.
        "block_anchor": "ELIGIBILITY",
        "once": "NAME THE BASIS, NEVER JUDGE IT",
        "phrases": [
            "that's the general five year path",
            "esa es la vía general de cinco años",   # the Spanish half, same rule
            "A reply that names a basis with an empty facts array",
            "MINT THAT BASIS IN THIS SAME RESPONSE",     # must SURVIVE v36
            "AND THE DAY SHE GAVE IS SAID BACK FIRST",   # v35e, must survive
            "A MONTH AND A YEAR IS ASKED, NOT DEFERRED",  # v35, must survive
            "ONE DAY PER QUESTION",                      # v35c, must survive
            "A PLAIN ANSWER GETS NO ECHO",               # v34, must survive
            "it never means no read-back",               # v34d, must survive
            "The entry is the record and the reply is not",  # v33, must survive
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            # The verdict, in both languages. Edit A rewrote the worked example
            # and Edit B the defect sentence, so NO older sentence is left
            # teaching the phrase.
            "that fits",
            "encaja",
            "A partial date is always a deferral",
            "acknowledge what landed, then ask the next thing",
            "had my green card since 2019",
            "goes NOWHERE",
        ],
    },
    38: {
        # THE ORDER IS A REQUIREMENT, NOT A PREFERENCE. GP streams the reply
        # sentence by sentence for TTS (PR #1003) and its release check runs
        # the real checkpoint refusal on the PARTIAL object, which it can only
        # do once facts, deferred and section_checkpoint are parsed; without
        # them it buffers the whole turn, on exactly the read-back turns
        # streaming is for. The served v36 already ordered them; this makes
        # the order something the model is TOLD rather than something it
        # happens to do (16 of 16 real responses complied before the cut).
        # One sentence, no moved blocks, cut from v36 by the auditor. v37 and
        # v37b stay shelved; v38 skips the number so two texts never share one.
        #
        # The ORIGINAL reason for the order must survive word for word: the
        # edit extends that sentence rather than replacing its argument.
        "block_anchor": "the read-back is the `reply` string of an object, never prose on its own",
        "once": "THE ORDER IS A REQUIREMENT, NOT A PREFERENCE",
        "phrases": [
            "`facts`, `deferred` AND `section_checkpoint` ARE ALWAYS WRITTEN BEFORE `reply`",
            "starts speaking each sentence of `reply` the moment that sentence is complete",
            "A reply written before them is not wrong, it simply cannot be spoken until the whole turn has arrived",
            "the reply LAST, because each later field must follow the earlier ones",  # the original reason, must survive
            "`asking` written after `facts` can never name a node you just minted",
            "No prose, no markdown, no code fences.",
            "NAME THE BASIS, NEVER JUDGE IT",             # v36, must survive
            "MINT THAT BASIS IN THIS SAME RESPONSE",     # v36 kept it, must survive
            "AND THE DAY SHE GAVE IS SAID BACK FIRST",   # v35e, must survive
            "A MONTH AND A YEAR IS ASKED, NOT DEFERRED",  # v35, must survive
            "ONE DAY PER QUESTION",                      # v35c, must survive
            "A PLAIN ANSWER GETS NO ECHO",               # v34, must survive
            "it never means no read-back",               # v34d, must survive
            "The entry is the record and the reply is not",  # v33, must survive
            '"origin": "applicant" or "capture_gap"',     # v32's key, must survive
            "AND THE CLAUSE MUST NOT BE CONDITIONAL",   # v30's rule, must survive
            '"intent": an OBJECT, never a bare string',  # v31's shape, must survive
        ],
        "absent": [
            # v36's verdict must stay gone; a v38 sync that brought it back
            # would be a stale cut wearing a new number.
            "so that fits",
            "así que encaja",
        ],
    },
}
# Kept as names for the v30 tests and any caller that imported them.
BLOCK_LINE = 83
MUST_APPEAR_ONCE = VERSIONS[30]["once"]
PHRASES = [
    "to firm up later IF NEEDED",
    "we can pin the exact day later IF IT MATTERS",
    "to verify the exact days IF NEEDED",
    "Say the return as a fact, not a possibility",
    "if you can find it",                            # the counterweight
    "when you have your mail in front of you",       # the counterweight
    "A condition on WHETHER she has to come back is not",
    "por verificar",                                 # the anchor it follows
    "EVERY DEFERRED FIELD, NAMED, NOT ONE OF THEM",  # the rule it must not cost
]


def _key() -> str:
    key = os.environ.get("CZ_ADMIN_KEY", "")
    if not key:
        raise SystemExit("CZ_ADMIN_KEY is empty in this container; refusing to guess")
    return key


def call(method: str, url: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-Admin-Key": _key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def running_sha() -> str:
    with urllib.request.urlopen(HEALTH, timeout=15) as r:
        return json.loads(r.read().decode()).get("git_sha", "")


def deploy_has_landed(running: str, expected: str) -> tuple[bool, str]:
    """(may we sync, why not).

    Pure so it can be tested without a server. An empty running sha is NOT
    treated as a match: unreachable and unknown are different from equal, the
    same distinction #962 drew for the admin page's build badge.
    """
    if not expected:
        return False, "no --expect-sha given; refusing to sync blind"
    if not running:
        return False, "the running server did not report a git_sha"
    if not (running.startswith(expected) or expected.startswith(running)):
        # A mismatch is refused in BOTH directions. Older means the deploy has
        # not landed and a sync would push the old bundle. Newer means a later
        # merge deployed on top (2026-09-14: #977 landed between the /health
        # check and the sync); the bundle is probably right but "probably" is
        # not the standard, so confirm the running bundle's version and re-run
        # with the sha that is actually running.
        return False, ("the running image is %s, not %s. If the running image is "
                       "OLDER the deploy has not landed and syncing would push the "
                       "old bundle; if it is NEWER, confirm the running bundle's "
                       "version and re-run with --expect-sha %s"
                       % (running[:12], expected[:12], running[:12]))
    return True, ""


def served() -> tuple[dict, str]:
    _, doc = call("GET", "%s/%s" % (ADMIN, SLUG))
    body = doc.get("data", doc)
    return body, body.get("systemPrompt", "")


def verify(sp: str, version: int) -> bool:
    """Read the SERVED string back by phrase, for one version. Returns whether
    it is right. An unlisted version is a refusal, not a pass."""
    spec = VERSIONS.get(version)
    if spec is None:
        print("  *** no phrase list for version %s; add one before syncing ***" % version)
        return False
    ok = True

    count = sp.count(spec["once"])
    ok &= count == 1
    print("  %-52s %s" % (spec["once"][:52],
                          "once  OK" if count == 1 else "*** %d ***" % count))

    for phrase in spec["phrases"]:
        present = phrase in sp
        ok &= present
        print("  %-52s %s" % (phrase[:52], "OK" if present else "*** MISSING ***"))

    for phrase in spec.get("absent", []):
        count = sp.count(phrase)
        ok &= count == 0
        print("  %-52s %s" % (phrase[:52], "absent  OK" if count == 0
                              else "*** STILL PRESENT x%d ***" % count))

    # Present SOMEWHERE in the document is not the claim; it has to be in the
    # block. The block is located by its own text and the once phrase must
    # appear AFTER it, so a reflow cannot move the check onto the wrong line.
    start = sp.find(spec["block_anchor"])
    in_block = start != -1 and spec["once"] in sp[start:]
    detail = "OK" if in_block else ("*** BLOCK NOT FOUND ***" if start == -1 else "*** WRONG SECTION ***")
    ok &= in_block
    print("  %-52s %s" % ("inside the block after %r" % spec["block_anchor"][:20], detail))
    return bool(ok)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--expect-sha", default="",
                    help="the merge commit carrying the prompt change. The sync "
                         "REFUSES unless the running image is that commit.")
    ap.add_argument("--force", action="store_true",
                    help="sync even if the running image is not --expect-sha. "
                         "Only for re-syncing text already in the running image.")
    args = ap.parse_args(argv)

    running = ""
    try:
        running = running_sha()
    except (urllib.error.URLError, OSError) as exc:
        print("could not read /health: %s" % exc)

    may, why = deploy_has_landed(running, args.expect_sha)
    print("RUNNING %s   EXPECTED %s" % (running[:12] or "unknown",
                                        args.expect_sha[:12] or "none given"))
    if not may:
        if not args.force:
            print("\nREFUSING TO SYNC: %s" % why)
            return 2
        print("\n--force: syncing anyway (%s)" % why)

    body, sp = served()
    print("BEFORE  version=%s  chars=%d" % (body.get("version"), len(sp)))

    status, rep = call("POST", "%s/%s/sync-from-bundle" % (ADMIN, SLUG),
                       # /version too, so the served number names the bundle it
                       # came from rather than counting writes. Without it v38
                       # served as "37", the shelved cut's number (2026-09-20).
                       {"keys": ["/systemPrompt", "/version"]})
    print("SYNC    HTTP %s  version=%s" % (status, rep.get("version")))
    for c in rep.get("changes", []):
        print("    %-20s %s" % (c.get("key"), c.get("status")))

    body, sp = served()
    print("\nAFTER, read back from the running server by STRING")
    print("  version=%s  systemPrompt chars=%d" % (body.get("version"), len(sp)))
    ok = verify(sp, int(body.get("version") or 0))

    print("\nRESULT:", "the served N-400 prompt carries the rule and its counterweight"
          if ok else "*** THE SERVED PROMPT IS NOT RIGHT, DO NOT CLOSE THIS OUT ***")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
