"""Multi-turn pre-ship probe for the N-400 interviewer lane: v34 and v35.

    .venv/bin/python qa/n400_multiturn_probe.py --dry        # assemble opening turns only, no API calls
    .venv/bin/python qa/n400_multiturn_probe.py --reps 3     # run both checks, direct API

Ruled by fable-auditor-f5 (2026-09-15): one harness, each check against its own
prompt version, so each probe measures one change.

  v34 (the acknowledgement rule): six plain answers in a row, the exact questions
      from Scott's build 52 screenshot (birth country, nationality, gender, the
      permanent resident date, parent citizen, impairment).
      PASS per rep: no two consecutive replies open with the same word; the replies
      to "Male." and "No." do not echo them; the date is echoed exactly once; all
      six facts are minted.
  v35 (ask the day once): the prior address block.
      PASS per rep: the first reply asks the moved-in day WITH the way out and
      defers nothing; "the 15th" mints p4.prior_address1.from = 2019-01-15 and the
      next reply OPENS with that date said back in full and then asks the
      moved-out day and nothing else about the address; "I'm not sure" defers
      p4.prior_address1.to with partial 2020-01 and the reply does not ask for a
      day again.

CLIENT LOGIC is the auditor's own harness, `N400 App/qa/n400_qa.py`, imported and
driven unchanged (graph, agenda, known facts, minting, deferrals, cursor). Four
things are replaced and only these: `post` (assembles the prompt locally for the
chosen version and calls the Anthropic API directly, then applies GP's
guard_response_text, the way the route does), `token` (a dummy: no session is
minted), `served_build` (no health read), and `RUNS` (the scratchpad, so nothing
lands in the auditor's qa/runs).

Prompts: v34 is read from the v34 PR branch in git. v35 is v34 with the auditor's
edit (N400 App/contracts/partial-date-ask-once-prompt-v35-edits.md) applied in
memory, the anchor asserted exactly once.

NOT exercised: the route's retry guards (checkpoint refusal, oath modification,
closing while open) and the envelope retry. `thinking` is sent explicitly as
disabled, matching the served config.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date
import urllib.request
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
AUDITOR_QA = Path("/Users/scottguida/N400 App/qa")
V35_EDITS = Path("/Users/scottguida/N400 App/contracts/partial-date-ask-once-prompt-v35-edits.md")
# ⚠ NOT A REF, A STARTING POINT. This was `V34_REF = "origin/main"` and it was
# BROKEN for hours: v34d merged to main in #987, then v35 and v36 merged on top,
# so main's interviewer-turn stopped being version 34 and `configs_for` died on
# its own assert. The v34 and v35 arms were unrunnable and nobody noticed,
# because the authorized work was the v37 arm, which uses configs_v37().
#
# A ref that names a BRANCH is a claim about that branch's contents at some
# past moment, and it keeps being read as if it were still true. The same
# defect ate the original V34_REF when its PR branch merged, and _v36_ref()
# was written to dodge it. This is that fix generalized: ask history which
# commit really carries the version you want, rather than asserting that a
# moving name still does.
V34_HISTORY_FROM = "origin/main"
SLUG = "n400/interviewer-turn"
# ⚠ THE APP'S EXACT BASE STRING, read from OpeningQuestions.swift rather than
# paraphrased. It joins pieces with "; " (not ", ") and carries state and
# language, which this harness omitted. Wrong separator and two missing fields,
# on EVERY arm of every probe from v34 to v37: the lane was being graded on an
# input production never sends. Found only because the auditor sent me to check
# the app for a DIFFERENT string.
CONTEXT = "state: Texas; language: English; interpreter: no; filing for self: yes"
CONTEXT_ES = "state: Texas; language: Spanish; interpreter: no; filing for self: yes"
PRICE = {"in": 2.0, "out": 10.0, "cache_read": 0.20, "cache_write": 2.50}  # Sonnet 5, $ per million

V34_TURNS = ["I was born in Monterrey, Mexico.", "Mexico.", "Male.", "January 1st, 2021.", "No.", "No."]
V34_FIELDS = ["p2.country_of_birth", "p2.country_of_nationality", "p2.gender", "p2.lpr_date",
              "p2.parent_citizen_before_18", "p2.disability_exemption"]
V35_TURNS = ["I lived there between January of 2019 to January of 2020, at 335 Gonzales Road, "
             "Santiago, Chile, postal code 12345", "the 15th", "I'm not sure"]

CALLS: list[dict] = []


def git_json(ref: str, path: str) -> dict:
    return json.loads(subprocess.check_output(["git", "-C", str(ROOT), "show", f"{ref}:{path}"]))


V35B_EDITS = Path("/Users/scottguida/N400 App/contracts/partial-date-ask-once-prompt-v35b-edits.md")
V34C_V35C_EDITS = Path("/Users/scottguida/N400 App/contracts/prompt-v34c-v35c-edits.md")
V34D_V35D_EDITS = Path("/Users/scottguida/N400 App/contracts/prompt-v34d-v35d-edits.md")
# v35e touches the v35 arm only; at revision "e" the v34 arm is v34d unchanged.
V35E_EDIT = Path("/Users/scottguida/N400 App/contracts/prompt-v35e-edit.md")


def _edit_block(md: list[str], line: int) -> str:
    s = md[line - 1]
    assert s.startswith("    "), f"edits file line {line} is not an indented block"
    return s[4:]


# v36 is COMMITTED IN FULL on its PR branch, so its arm reads that ref AS IS.
# There is no edit stack to assemble, unlike every cut from v34b to v35e, which
# is why this needs none of configs_for's ordering machinery.
V36_REF = "origin/feat/n400-v36-name-the-basis"
V36_TURNS_EN = ["on my own, about six years"]
V36_TURNS_ES = ["por mi cuenta, unos seis años"]
V36_TURNS_SPOUSE = ["my husband is a citizen, we've been married four years, "
                    "green card three and a half"]


def _v36_ref() -> str:
    """main once v36 has merged, the PR branch until then.

    ⚠ THIS IS THE DEFECT THAT BROKE THE v34 ARM, fixed before it bites rather
    than after. V34_REF pointed at a PR branch, and the moment that branch
    merged the anchors it rebuilt from stopped existing, so every run died. A
    ref pinned to a branch that is about to be merged and deleted is a run that
    works until the day the thing it tests ships."""
    for ref in ("origin/main", V36_REF):
        try:
            doc = git_json(ref, "config/remote/n400/interviewer-turn.json")
        except subprocess.CalledProcessError:
            continue
        if doc.get("version") == 36:
            return ref
    raise SystemExit(
        f"no v36 config found: neither origin/main nor {V36_REF} is version 36. "
        "If v36 has merged and the branch is gone, fetch main; if it has not, "
        "fetch the PR branch.")


_REF_CACHE: dict[tuple[int, bool], str] = {}
# The marker for "the v34 chain has already been applied at this commit".
V34D_MARKER = "it never means no read-back"


def ref_carrying_version(version: int, *, pre_cut: bool = False) -> str:
    """The newest commit on `V34_HISTORY_FROM` whose n400/interviewer-turn
    really IS `version`, found by walking that file's history.

    `pre_cut=True` additionally requires the commit to predate the v34 chain,
    which is what revisions "a", "b" and "c" rebuild from. Without it they can
    only be assembled from a base that already contains their own output, and
    the anchors they replace no longer exist there.

    ⭐ WHY A WALK RATHER THAN A PINNED SHA. A pinned sha would work and would be
    one line. It would also be a number nobody can check without running the
    same walk, and the last two times this harness named a ref by hand (the
    original V34_REF on a PR branch, then "origin/main" after v35 merged) the
    name went stale silently and the run died at the moment somebody needed it.
    The walk states the PROPERTY the arm requires and fails loudly naming it.
    """
    key = (version, pre_cut)
    if key in _REF_CACHE:
        return _REF_CACHE[key]
    path = f"config/remote/n400/{Path(SLUG).name}.json"
    shas = subprocess.check_output(
        ["git", "-C", str(ROOT), "log", "--format=%H", V34_HISTORY_FROM, "--", path],
    ).decode().split()
    if not shas:
        raise SystemExit(
            f"no history for {path} on {V34_HISTORY_FROM}. Fetch it first: "
            f"git fetch origin")
    for sha in shas:
        try:
            doc = git_json(sha, path)
        except subprocess.CalledProcessError:
            continue
        if doc.get("version") != version:
            continue
        if pre_cut and V34D_MARKER in doc.get("systemPrompt", ""):
            continue
        _REF_CACHE[key] = sha
        return sha
    raise SystemExit(
        f"no commit on {V34_HISTORY_FROM} carries interviewer-turn version "
        f"{version}{' before the v34 chain' if pre_cut else ''}, across "
        f"{len(shas)} commits that touched it. Either the history has been "
        f"rewritten or that version never landed on this branch.")


def configs_v36() -> dict:
    """The v36 prompt, read whole. Nothing is assembled, so the only things
    worth asserting are that the ref really is v36 and that the verdict is
    actually gone: absence IS the version for this cut, in both languages, so a
    ref still carrying it would probe as a pass it did not earn."""
    ref = _v36_ref()
    names = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-tree", "--name-only", ref, "config/remote/n400/"]).decode().split()
    out = {"n400/" + Path(n).stem: git_json(ref, n) for n in names if n.endswith(".json")}
    cfg = out[SLUG]
    assert cfg["version"] == 36, f"{ref} is v{cfg['version']}, not v36"
    sp = cfg["systemPrompt"]
    assert "NAME THE BASIS, NEVER JUDGE IT" in sp, f"{ref} lacks v36's once phrase"
    assert "that fits" not in sp, f"{ref} still carries the English verdict"
    assert "encaja" not in sp, f"{ref} still carries the Spanish verdict"
    print(f"   v36 arm: taken from {ref} as is ({len(sp)} chars)")
    return out


V37_EDITS = Path("/Users/scottguida/N400 App/contracts/prompt-v37-covered-window-closes-gate-edit.md")
# Scott's 2:17 turns, verbatim from his screenshot. C and D swap ONLY the dates
# sentence; the first turn must still answer the employer question the lane
# actually asks, which is why they are not single-utterance arms.
V37_T1_EN = "Yes, I worked at Weird Tech. The address there is 1001 Main Street, Austin, Texas, 78777."
V37_T2_EN = "Sales person. I worked there from January 1st, 2012 to January 1st, 2025."
V37_T2_INSIDE = "Sales person. I worked there from March 3rd, 2023 to January 1st, 2025."
V37_T2_YEARONLY = "Sales person. I worked there from 2012 to 2025."
V37_T1_ES = ("Sí, trabajé en Weird Tech. La dirección es 1001 Main Street, Austin, Texas, 78777.")
V37_T2_ES = "Vendedor. Trabajé allí desde el 1 de enero de 2012 hasta el 1 de enero de 2025."
# ⚠ ONE piece carrying BOTH languages, appended with "; ", exactly as
# OpeningQuestions.swift:239 builds it. There is NO locale branch: its own
# comment says "One sentence, en and es (the wire locale picks)". So the es arm
# gets the SAME string as the en arm, not a Spanish translation of it.
#
# The date is a full ISO day from DerivedFacts.historyWindowStart, today minus
# five years, NOT the spoken "September 16, 2021" of the spec prose. Computed
# rather than hardcoded so a run tomorrow does not quietly grade a stale window.
def _history_window_piece(today: date | None = None) -> str:
    d = today or date.today()
    start = d.replace(year=d.year - 5).isoformat()
    return ("history window: questions cover the last five years, since "
            f"{start} (preguntas de los últimos cinco años, desde {start})")


V37_WINDOW = _history_window_piece()
V37_SEED_JOB = {"p7.employer1.employer_name": "unemployed", "p7.has_job2": "yes"}
V37_F_TURN = "I've lived at 12 Oak Street, Austin, Texas 78701 since June 3rd, 2015"
V37_G_TURN = "He was born here, in Chicago."
# The minimum current-marriage facts the graph needs to reach
# q_p5_spouse_citizen_how legally: without these the walk cannot get there and
# the arm would test nothing while looking like it ran.
# v38: the three keys the stream depends on come before the reply. ONE edit
# on v36 as served, no moved blocks. The cut is about the SHAPE of the object,
# so its scorer reads RAW model text with an order-preserving parser: a dict
# that has been parsed and re-serialised has lost the only thing under test.
V38_EDIT = Path("/Users/scottguida/N400 App/contracts/prompt-v38-keys-before-reply-edit.md")
V38_KEYS_BEFORE_REPLY = ("facts", "deferred", "section_checkpoint")
# Raw model text per turn_id, captured in make_post BEFORE fence stripping,
# envelope extraction and guards. Cleared before each drive() so that after a
# rep it holds exactly that rep's turns, in order.
RAW_TEXTS: dict[str, str] = {}
# A turn that mints nothing: a question back instead of an answer.
V38_D_TURN = "Sorry, what do you mean by that exactly?"
V38_D_TURN_ES = "Perdón, ¿a qué se refiere con eso exactamente?"

V37_SEED_SPOUSE = {
    # ⚠ THE ELIGIBILITY BASIS IS LOAD-BEARING AND I LEFT IT OUT TWICE.
    # p5.spouse_has_a_number is required only when
    # p5.current_marriage_block_applies derives "yes", which happens only for
    # basis spouse_usc or spouse_employed_abroad. Without it Part 5 is genuinely
    # COMPLETE, the checkpoint is CORRECT, and arm G "fails" having never
    # reached the condition it exists to test. I reported that as a live
    # production defect the first time. The auditor reproduced it both ways on
    # live v36 and told me the spec now carries this seed; I then ran the
    # re-probe without adding it.
    "p1.eligibility_basis": "spouse_usc",
    "p5.marital_status": "married",
    "p5.marriage_date": "2019-06-15",
    "p5.spouse_first_name": "Daniel",
    "p5.spouse_last_name": "Reyes",
    "p5.spouse_address_same": "yes",
}


def configs_v37() -> dict:
    """v37 assembled IN MEMORY from main's v36 plus the auditor's five edits,
    the way the v34 and v35 arms worked. v36 is the base because v37 is cut
    from it, and each anchor is asserted exactly once before it is applied, so
    an edit that no longer fits fails here rather than probing as a pass."""
    names = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-tree", "--name-only", "origin/main", "config/remote/n400/"]).decode().split()
    out = {"n400/" + Path(n).stem: git_json("origin/main", n) for n in names if n.endswith(".json")}
    cfg = out[SLUG]
    assert cfg["version"] == 36, f"origin/main is v{cfg['version']}, not the v36 v37 is cut from"
    # ⚠ PAIR BY HEADER, NEVER BY LINE NUMBER. This read (16,20),(24,28)... until
    # the auditor revised the edit file IN PLACE and every block moved down two
    # lines to (18,22),(26,30)... Hardcoded positions then pointed at the wrong
    # text, and the failure mode is not always a clean assertion: if a shifted
    # line happens to be another indented block, the arm assembles a prompt
    # nobody wrote and grades it as v37. I caught this exact trap while
    # verifying v37b by header and left it standing here, in the same file, in
    # the same session. Same shape as the sync script indexing line 83.
    lines = V37_EDITS.read_text(encoding="utf-8").split("\n")
    edits, cur = [], None
    for l in lines:
        if l.startswith("## Edit"):
            cur = (l.split()[2].rstrip(":"), [])
            edits.append(cur)
        elif cur is not None and l.startswith("    ") and l.strip() and len(cur[1]) < 2:
            cur[1].append(l[4:])
    assert len(edits) == 5, f"v37 edit file has {len(edits)} edits, expected 5"
    sp = cfg["systemPrompt"]
    for label, blocks in edits:
        assert len(blocks) == 2, f"v37 Edit {label} has {len(blocks)} blocks, expected 2"
        old, new = blocks
        assert sp.count(old) == 1, f"v37 Edit {label} anchor count {sp.count(old)}"
        sp = sp.replace(old, new)
    assert sp.count("A COVERED WINDOW CLOSES ITS GATE") == 4
    assert "October 2017" not in sp
    # v36, v35 and v34d must ride along untouched.
    for ph in ("NAME THE BASIS, NEVER JUDGE IT", "MINT THAT BASIS IN THIS SAME RESPONSE",
               "AND THE DAY SHE GAVE IS SAID BACK FIRST", "A PLAIN ANSWER GETS NO ECHO"):
        assert sp.count(ph) == 1, ph
    print(f"   v37 arm: assembled from origin/main v36 + 5 edits ({len(sp)} chars)")
    return {**out, SLUG: {**cfg, "systemPrompt": sp, "version": 37}}


def configs_v38() -> dict:
    """v38 assembled in memory from main's v36 plus the auditor's ONE edit.
    Header-paired like v37 (never line numbers). Every string check the edit
    file lists is asserted here against the ASSEMBLED text, not the file."""
    names = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-tree", "--name-only", "origin/main", "config/remote/n400/"]).decode().split()
    out = {"n400/" + Path(n).stem: git_json("origin/main", n) for n in names if n.endswith(".json")}
    cfg = out[SLUG]
    assert cfg["version"] == 36, f"origin/main is v{cfg['version']}, not the v36 v38 is cut from"
    lines = V38_EDIT.read_text(encoding="utf-8").split("\n")
    edits, cur = [], None
    for l in lines:
        if l.startswith("## Edit"):
            cur = (l.split()[2].rstrip(":"), [])
            edits.append(cur)
        elif cur is not None and l.startswith("    ") and l.strip() and len(cur[1]) < 2:
            cur[1].append(l[4:])
    assert len(edits) == 1, f"v38 edit file has {len(edits)} edits, expected 1"
    label, (old, new) = edits[0]
    sp = cfg["systemPrompt"]
    assert sp.count(old) == 1, f"v38 Edit {label} anchor count {sp.count(old)}"
    sp = sp.replace(old, new)
    for ph in ("THE ORDER IS A REQUIREMENT, NOT A PREFERENCE",
               "starts speaking each sentence of `reply`",
               "the reply LAST, because each later field must follow",
               "`asking` written after `facts` can never name a node",
               "No prose, no markdown, no code fences.",
               "NAME THE BASIS, NEVER JUDGE IT", "AND THE DAY SHE GAVE IS SAID BACK FIRST",
               "A PLAIN ANSWER GETS NO ECHO", "MINT THAT BASIS IN THIS SAME RESPONSE"):
        assert sp.count(ph) == 1, f"v38 string check: {ph!r} count {sp.count(ph)}"
    assert not re.search("[\u2013\u2014]", sp), "v38 carries an en or em dash"
    print(f"   v38 arm: assembled from origin/main v36 + 1 edit ({len(sp)} chars)")
    return {**out, SLUG: {**cfg, "systemPrompt": sp, "version": 38}}


def _ordered_keys_from_raw(text: str) -> list[str] | None:
    """Top-level keys of the object in the RAW model text, in the order the
    model wrote them. Strips a whole-response fence the way production does,
    then takes first-brace to last-brace like extract_envelope. Returns None
    when there is no object."""
    from collections import OrderedDict
    t = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if m:
        t = m.group(1)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(t[i:j + 1], object_pairs_hook=OrderedDict)
    except ValueError:
        return None
    if not isinstance(obj, dict) or "reply" not in obj:
        return None
    return list(obj.keys())


def score_v38(state: dict, kind: str, raw: dict[str, str]) -> dict:
    """The cut is about SHAPE, so every check reads the raw text's key order.
    kind A/C: an ordinary answer turn, scored as its v36 arm plus order.
    kind B: the read-back turn; section_checkpoint must carry a part.
    kind D: a turn that mints nothing; facts and deferred must be PRESENT and
    empty, section_checkpoint PRESENT and null, all still before reply."""
    entries = [e for e in state["transcript"] if e.get("applicant")]
    if not entries or any(e.get("error") for e in entries):
        return {"pass": False, "why": f"incomplete run: {len(entries)} replies",
                "replies": [e.get("interviewer") for e in entries]}
    texts = list(raw.values())
    if not texts:
        return {"pass": False, "why": "no raw text captured"}
    checks: dict[str, bool] = {}
    orders = []
    for n, text in enumerate(texts):
        keys = _ordered_keys_from_raw(text)
        orders.append(keys)
        if keys is None:
            checks[f"t{n}_is_object"] = False
            continue
        r = keys.index("reply")
        for k in V38_KEYS_BEFORE_REPLY:
            checks[f"t{n}_{k}_present"] = k in keys
            checks[f"t{n}_{k}_before_reply"] = k in keys and keys.index(k) < r
    last = texts[-1]
    obj = None
    try:
        t = last.strip()
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
        t = m.group(1) if m else t
        obj = json.loads(t[t.find("{"):t.rfind("}") + 1])
    except (ValueError, AttributeError):
        pass
    if kind in ("A", "C"):
        base = score_v36(state)
        checks["v36_checks_unchanged"] = bool(base.get("pass"))
        # Original names, NOT prefixed: the second scorer maps text checks by
        # name (TEXT_CHECKS in typesafe_rescore_arms.py), and a renamed check
        # is a check it silently never grades.
        checks.update(base.get("checks") or {})
    elif kind == "B":
        cp = (obj or {}).get("section_checkpoint")
        checks["section_checkpoint_carries_a_part"] = isinstance(cp, dict) and isinstance(cp.get("part"), int)
    elif kind == "D":
        checks["facts_present_and_empty"] = obj is not None and "facts" in obj and not obj["facts"]
        checks["deferred_present_and_empty"] = obj is not None and "deferred" in obj and not obj["deferred"]
        checks["section_checkpoint_present_and_null"] = obj is not None and "section_checkpoint" in obj and obj["section_checkpoint"] is None
    reply = entries[-1].get("interviewer") or ""
    return {"pass": all(checks.values()), "checks": checks, "reply": reply,
            "key_orders": orders}


def configs_for(version: int, revision: str = "e") -> dict:
    """v34 from main (v34d, merged in #987). revision "b" (the default, 2026-09-15 second
    cut) applies v34b's one sentence to v34, and for 35 applies v35b's four
    edits on top of that; v35b's Edit A anchors on the ORIGINAL sentence, so it
    REPLACES v35 rather than building on it. revision "a" reproduces the first
    run (v34 as branched, v35 = v34 + the first v35 edit)."""
    # Resolved from history rather than asserted about a branch name. Revisions
    # a, b and c rebuild the v34 chain, so they need a base from BEFORE it.
    ref = ref_carrying_version(34, pre_cut=revision in ("a", "b", "c"))
    names = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-tree", "--name-only", ref, "config/remote/n400/"]).decode().split()
    out = {"n400/" + Path(n).stem: git_json(ref, n) for n in names if n.endswith(".json")}
    cfg = out[SLUG]
    # ⚠ The version is true BY CONSTRUCTION here (the resolver selected on it),
    # so re-asserting it would be a check that cannot fail. And the obvious
    # marker cannot fail either: "A PLAIN ANSWER GETS NO ECHO" is still present
    # in v35 AND v36, so a resolver that handed back main would sail straight
    # through it. Checked, not assumed. The discriminating assertion is the
    # ABSENCE of the thing v36 exists to add.
    sp_ = cfg["systemPrompt"]
    assert "A PLAIN ANSWER GETS NO ECHO" in sp_, (
        f"{ref[:8]} is interviewer-turn v34 but does not carry the v34 base text")
    assert "NAME THE BASIS, NEVER JUDGE IT" not in sp_, (
        f"{ref[:8]} was resolved as the v34 base but carries v36's name-the-basis "
        f"rule, so the resolver returned a later cut")
    print(f"   v34 base: {ref[:8]} (resolved as the newest v34 on {V34_HISTORY_FROM})")
    sp = cfg["systemPrompt"]
    if revision == "a":
        if version == 35:
            md = V35_EDITS.read_text().split("\n")
            old, new = md[17][4:], md[21][4:]
            assert sp.count(old) == 1, "v35 anchor not unique in v34"
            sp = sp.replace(old, new)
            assert "the day is ASKED FOR ONCE" in sp
    else:
        mb = V35B_EDITS.read_text().split("\n")
        mc = V34C_V35C_EDITS.read_text().split("\n")
        # In order: v34b's sentence, then (revision c) v34c replacing it, then
        # for the 35 arm v35b's four edits and v35c's three on top. Edits whose
        # anchor is another edit's replacement text only exist at their stage,
        # so the order is part of the check.
        md_ = V34D_V35D_EDITS.read_text().split("\n")
        me = V35E_EDIT.read_text().split("\n")
        later = revision in ("c", "d", "e")
        d_or_later = revision in ("d", "e")
        # The v34 chain is applied ONLY while the resolved base still carries
        # the pre-cut text. Once v34d has landed the base already IS the final
        # v34 arm, and re-applying b, c and d would die on anchors that no
        # longer exist, which is why a/b/c resolve with pre_cut=True. The v35
        # chain is unaffected: its anchors are in the date and deferral rules,
        # not the reply shape.
        v34_done = V34D_MARKER in sp
        if v34_done and not d_or_later:
            raise SystemExit(
                f"the resolved v34 base {ref[:8]} already carries v34d, so revision "
                f"{revision!r} cannot be rebuilt from it. This should be unreachable: "
                "a, b and c resolve with pre_cut=True. If you see it, the marker "
                "moved.")
        steps = []
        if v34_done:
            print(f"   v34 arm: taken from {ref[:8]} as is (already v34d)")
        else:
            steps += [(mb, "v34b", 70, 74)]
            if later:
                steps += [(mc, "v34c", 13, 17)]
            if d_or_later:
                steps += [(md_, "v34d", 11, 15)]
        if version == 35:
            steps += [(mb, "v35b-" + n, o, w) for n, o, w in
                      [("A", 16, 20), ("B", 24, 28), ("C", 32, 36), ("D", 40, 44)]]
            if later:
                steps += [(mc, "v35c-" + n, o, w) for n, o, w in
                          [("A", 21, 25), ("B", 29, 33), ("C", 37, 41)]]
            if d_or_later:
                steps += [(md_, "v35d", 19, 23)]
            if revision == "e":
                # Anchored on the v35d replacement, so it only exists after it.
                steps += [(me, "v35e", 10, 14)]
        for md, name, old_line, new_line in steps:
            old, new = _edit_block(md, old_line), _edit_block(md, new_line)
            assert sp.count(old) == 1, f"{name} anchor count {sp.count(old)}"
            sp = sp.replace(old, new)
        if later:
            assert "it begins with the next question itself" in sp
            assert "look at the first word of your own previous line" not in sp
        else:
            assert "look at the first word of your own previous line" in sp
        if d_or_later:
            assert "it never means no read-back" in sp
        if version == 35:
            assert "A MONTH AND A YEAR IS ASKED, NOT DEFERRED" in sp
            assert "A partial date is always a deferral" not in sp
            if later:
                assert "ONE DAY PER QUESTION" in sp
                assert "A promise to ASK later is not a promise to verify" in sp
                assert "as a FLOOR under you and never a move to imitate" in sp
            if d_or_later:
                assert "THE MOVED-OUT DAY IS THE VERY NEXT QUESTION" in sp
            if revision == "e":
                assert sp.count("AND THE DAY SHE GAVE IS SAID BACK FIRST") == 1
                assert sp.count("a day she gave and never heard back") == 1
    cfg = {**cfg, "systemPrompt": sp, "version": version}
    return {**out, SLUG: cfg}


def make_post(configs: dict, key: str, dry: bool):
    from app.services.n400_envelope import extract_envelope, is_envelope
    from app.services.n400_interviewer_guard import guard_response_text
    from app.services.prompt_assembly import assemble_prompt
    from app.services.spanish_numerals import numeral_variables

    cfg = configs[SLUG]

    def post(body, tok):
        md, utt = body["metadata"], body["user_content"]
        variables = {**md, **numeral_variables(md["call_type"], md.get("locale"), utt)}
        assembled = assemble_prompt(md["call_type"], utt, configs,
                                    jurisdiction=md.get("jurisdiction"), variables=variables)
        assert assembled, "assemble_prompt returned None"
        max_tokens = assembled.get("max_tokens") or cfg.get("maxTokens") or 2048
        if dry:
            print(f"    DRY v{cfg['version']}: system {len(assembled['system_prompt'])} chars, "
                  f"user {len(assembled['user_content'])} chars, max_tokens {max_tokens}, "
                  f"agenda lines {len((md.get('agenda') or '').splitlines())}, utterance {utt!r}")
            raise SystemExit("dry")
        payload = {
            "model": cfg["recommendedModel"], "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
            "system": [{"type": "text", "text": assembled["system_prompt"], "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": assembled["user_content"]}],
        }
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json", "x-api-key": key,
                                              "anthropic-version": "2023-06-01"}, method="POST")
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read())
        secs = round(time.monotonic() - t0, 1)
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        RAW_TEXTS[str(md.get("turn_id"))] = text     # raw, before anything touches it
        u = resp.get("usage", {})
        CALLS.append({"version": cfg["version"], "turn_id": md.get("turn_id"), "stop": resp.get("stop_reason"),
                      "secs": secs, "in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
                      "cache_read": u.get("cache_read_input_tokens", 0) or 0,
                      "cache_write": u.get("cache_creation_input_tokens", 0) or 0})
        obj_text = text if is_envelope(text) else (extract_envelope(text) or text)
        guarded = guard_response_text(obj_text, md.get("agenda"), md.get("turn_id"),
                                      user_content=utt, conversation=md.get("conversation"))
        return 200, json.dumps({"text": guarded}), secs

    return post


def drive(q, run: str, cursor: str, seed: dict | None, turns: list[str], runs_dir: Path,
          locale: str = "en", context: str = CONTEXT) -> dict:
    """⚠ locale is a RUN-level setting in the auditor's harness, not per turn:
    `state["locale"]` feeds every ask, every reply pick and every question_text.
    So a Spanish rep is its OWN RUN, not a Spanish utterance inside an English
    one. This argument was hardcoded "en" until v36 needed Spanish reps, and
    without it those reps would have run in English and scored as passes."""
    seed_path = None
    if seed:
        seed_path = runs_dir / f"{run}.seed.json"
        seed_path.write_text(json.dumps(seed))
    q.cmd_start(Namespace(run=run, lane="interviewer", locale=locale, persona=None, context=context,
                          volunteer=True, cursor=cursor, seed=str(seed_path) if seed_path else None))
    for utt in turns:
        q.cmd_step(Namespace(run=run, say=utt))
    return json.loads((runs_dir / f"{run}.json").read_text())


def first_word(s: str) -> str:
    m = re.match(r"\W*([A-Za-z']+)", s or "")
    return m.group(1).lower() if m else ""


def score_v34(state: dict) -> dict:
    entries = [e for e in state["transcript"] if e.get("applicant")]
    replies = [e.get("interviewer") or "" for e in entries]
    checks = {}
    if len(replies) < 6 or any(e.get("error") for e in entries):
        return {"pass": False, "why": f"incomplete run: {len(replies)} replies, errors {[e.get('error') for e in entries if e.get('error')]}", "replies": replies}
    openers = [first_word(r) for r in replies]
    checks["openers_vary"] = all(openers[i] != openers[i + 1] for i in range(5))
    # v34c: after a plain answer the reply has NO opener; it begins with the
    # next question itself. Replies 1, 2, 3, 5 and 6 follow a country, a
    # country, a sex, a no and a no; reply 4 follows the date and may carry an
    # echo with a bridge in front of it.
    bridges = ("thanks", "thank", "okay", "ok", "got", "great", "understood", "alright",
               "noted", "perfect", "sure", "good", "right")
    plain = [0, 1, 2, 4, 5]
    checks["plain_answers_open_with_the_question"] = all(openers[i] not in bridges for i in plain)
    checks["no_male_echo"] = not re.search(r"\bmale\b", replies[2], re.I)
    no_echo = lambda r: not re.match(r"\W*(got it,?\s*)?no\b", r, re.I) and "got it, no" not in r.lower()
    checks["no_no_echo"] = no_echo(replies[4]) and no_echo(replies[5])
    date_hits = [len(re.findall(r"January (1st|1|first),? 2021", r, re.I)) for r in replies]
    checks["date_echoed_once"] = date_hits[3] == 1 and sum(date_hits) == 1
    # v34d: no opener means no "Got it", never no read-back. After the date the
    # reply LEADS with the value itself, then the next question (v34c killed
    # the opener and took the read-back with it, 3 of 3).
    checks["date_read_back_leads_the_reply"] = bool(
        re.match(r"\W*january\s+(1st|1|first),?\s*2021", replies[3], re.I))
    facts = state["facts"]
    checks["all_six_minted"] = all(facts.get(f) not in (None, "") and not (isinstance(facts.get(f), dict) and facts[f].get("deferred")) for f in V34_FIELDS)
    got_it = sum(r.lower().count("got it") for r in replies)
    return {"pass": all(checks.values()), "checks": checks, "openers": openers, "got_it": got_it,
            "date_hits": date_hits, "replies": replies, "facts": {f: facts.get(f) for f in V34_FIELDS}}


def score_v35(state: dict) -> dict:
    entries = [e for e in state["transcript"] if e.get("applicant")]
    if len(entries) < 3 or any(e.get("error") for e in entries):
        return {"pass": False, "why": f"incomplete run: {len(entries)} replies", "replies": [e.get("interviewer") for e in entries]}
    r1, r2, r3 = (e.get("interviewer") or "" for e in entries[:3])
    way_out = lambda r: bool(re.search(r"don.t know|not sure|check (it|that) later|offhand|later", r, re.I))
    # Only the QUESTIONS in a reply count as asking. The first scorer matched
    # "exact day" inside statements ("I'll leave that exact day open"), which
    # ask nothing, and failed two reps that did the right thing.
    questions = lambda r: [q for q in re.split(r"(?<=[.!])\s+", r) if "?" in q]
    asks_day = lambda r: any(re.search(r"\bday\b|\bdate\b", q, re.I) for q in questions(r))
    date_fids = ("p4.prior_address1.from", "p4.prior_address1.to")
    minted1 = {m.get("field_id") for m in (entries[0].get("minted") or [])}
    minted2 = {m.get("field_id"): m.get("value") for m in (entries[1].get("minted") or [])}
    deferred_all = state.get("deferred") or []
    # v35c: ONE DAY PER QUESTION. The step 1 question asks the moved-in day
    # only; "and what day did you move out" in the same breath is two
    # questions however it is punctuated.
    step1_qs = questions(r1)
    asks_moved_out = any(re.search(r"\b(out|left|leave)\b", q, re.I) for q in step1_qs)
    day_mentions = sum(len(re.findall(r"\bday\b", q, re.I)) for q in step1_qs)
    checks = {
        "step1_asks_day_with_way_out": asks_day(r1) and way_out(r1) and bool(re.search(r"move|moved|in\b", r1, re.I)),
        # v35c ruling: a deferral on first hearing is legal ONLY when the same
        # reply asks for that day (the entry marks the question as owed).
        "step1_deferral_only_if_it_asked": (not entries[0].get("deferred")) or asks_day(r1),
        "step1_asks_one_day_only": day_mentions == 1 and not asks_moved_out,
        "step1_mints_neither_date": not (minted1 & set(date_fids)),
        "step2_mints_from_2019_01_15": minted2.get("p4.prior_address1.from") == "2019-01-15"
            or state["facts"].get("p4.prior_address1.from") == "2019-01-15",
        # v35b: the date said back with ITS year (reps 1 and 2 of the first run
        # said "January 15th, 2020" while minting 2019-01-15).
        "step2_says_2019_not_2020": bool(re.search(r"\b15(th)?\b.{0,12}2019|2019.{0,12}\b15(th)?\b", r2))
            and not re.search(r"\b15(th)?,? 2020\b", r2),
        # ⚠ FALSE PASS, found by the auditor's second scorer (Jev) rescoring
        # recorded arms, not by this harness. This was
        # `asks_day(r2) and re.search(r"out|left|leave", r2)`, matching ANYWHERE
        # in the reply. "January 15th, 2020, noted, and I've kept the move-out
        # date to verify. Was there an apartment or unit number...?" credited a
        # QUESTION THAT WAS NEVER ASKED: "move-out" supplied the "out" from a
        # statement while asks_day matched elsewhere. Same class as step3, which
        # I narrowed to questions-only and then left this one behind.
        # ⭐ A false pass is worse than a false fail: it certifies behaviour that
        # did not happen. v35e's 3 of 3 was re-scored under the corrected check
        # and STANDS (0 flips), so the verdict was never affected; it was
        # latent and would have bitten the next v35-arm run.
        "step2_asks_moved_out_day": (
            any(re.search(r"\bday\b|\bdate\b", q, re.I) for q in questions(r2))
            and any(re.search(r"\b(out|left|leave)\b", q, re.I) for q in questions(r2))),
        # ⚠ A CLASS NEITHER SCORER WAS LOOKING FOR. On v35b-era reps the lane
        # said back "January 15th, 2020" while MINTING 2019-01-15: the read-back
        # and the record disagreed by a year, on the exact turn v35e exists to
        # get right. Every date check here compared only the MINT, so they all
        # passed. The reply is what she hears; if it disagrees with what was
        # filed, the read-back is worse than useless because it invites her to
        # confirm a value that is not the one on the form.
        "says_back_the_year_it_filed": (
            lambda said, filed: (not said) or (not filed) or said[0] in filed)(
                re.findall(r"January\s+15(?:th)?,?\s*(\d{4})", r2),
                minted2.get("p4.prior_address1.from")
                or state["facts"].get("p4.prior_address1.from") or ""),
        # v35e: the day she gave is said back FIRST. Probe 4 rep 2 minted
        # 2019-01-15 correctly and asked the moved-out day next, but never said
        # the date back, so the one value she had just spoken was the one value
        # she could not catch a mishear on. "Begins with" is literal: the reply
        # opens with the date, no bridge word in front of it, the same shape
        # v34d put back on the other arm.
        "step2_reply_opens_with_the_date": bool(re.match(r"\s*January\s+15(th)?,?\s+2019\b", r2)),
        # v35d: the moved-out day is the VERY NEXT question, before the unit,
        # the state or anything else about that address (probe 3: all three
        # reps went to apartment and state instead, so `to` was never asked).
        "step2_asks_only_the_moved_out_day": asks_day(r2)
            and bool(re.search(r"\b(out|left|leave)\b", r2, re.I))
            and not any(re.search(r"apartment|unit|state|province|region|postal|zip", q, re.I)
                        for q in questions(r2)),
        # A `to` deferral STANDS after step 3, whether it was written here or
        # at step 1 and left alone. Demanding a fresh entry on this turn was my
        # scorer being stricter than the rule (probe 3 reps 1 and 2 failed on
        # it while behaving correctly); the auditor confirms both entries stand
        # until each day is answered or declined.
        "step3_to_deferral_stands": any(d.get("field_id") == "p4.prior_address1.to" for d in deferred_all),
        "step3_partial_2020_01": any(d.get("field_id") == "p4.prior_address1.to" and str(d.get("partial_value", "")).startswith("2020-01")
                                     for d in deferred_all),
        "step3_does_not_ask_day_again": not any(re.search(r"\b(what|which)\b.{0,20}\bday\b|\bexact day\b", q, re.I)
                                                for q in questions(r3)),
    }
    return {"pass": all(checks.values()), "checks": checks, "replies": [r1, r2, r3],
            "minted_step2": minted2, "deferred": deferred_all}


# The verdict, both languages, from the auditor's spec. Checked only BEFORE the
# reply's final question: "good" can legitimately open a question ("good, and
# what is your A-Number?" is not the defect v36 is about).
VERDICT_WORDS = (r"\b(fits?|works?|qualif(?:ies|y)|enough|sounds right|good"
                 r"|encaja|sirve|califica|suficiente|bien)\b")


def _before_final_question(reply: str) -> str:
    qs = [s for s in re.split(r"(?<=[.!?])\s+", reply) if "?" in s]
    return reply[:reply.index(qs[-1])] if qs else reply


def score_v36(state: dict, spouse: bool = False) -> dict:
    """v36: name the basis, never judge it. One applicant turn, seeded at the
    eligibility question.

    ⚠ The year check keys on seven/eight/nine/ten, NOT on "five": the reply is
    SUPPOSED to say "the general five year path", so a naive year-count regex
    would flag the correct behaviour. That is the scorer-bug shape that failed
    two correct reps in probe 3.

    ⚠ The spouse rep asserts the basis is merely NOT general_provision rather
    than naming the three-year enum, because I have not verified that enum's
    spelling against the served prompt. Narrow and true beats precise and
    guessed."""
    entries = [e for e in state["transcript"] if e.get("applicant")]
    if not entries or any(e.get("error") for e in entries):
        return {"pass": False, "why": f"incomplete run: {len(entries)} replies",
                "replies": [e.get("interviewer") for e in entries]}
    e1 = entries[0]
    reply = e1.get("interviewer") or ""
    minted = {m.get("field_id"): m.get("value") for m in (e1.get("minted") or [])}
    basis = minted.get("p1.eligibility_basis") or state["facts"].get("p1.eligibility_basis")
    before = _before_final_question(reply)
    checks = {
        # (1) the mint must not regress to conf-v20's blank box.
        "basis_minted_on_this_turn": bool(basis),
        # (3) the whole point of the cut.
        "no_verdict_word": not re.search(VERDICT_WORDS, before, re.I),
        # (5) nothing claimed that she did not say.
        "claims_no_military": not re.search(r"\bmilitar", reply, re.I),
        "claims_no_other_year_count": not re.search(
            r"\b(seven|eight|nine|ten|siete|ocho|nueve|diez)\b", reply, re.I),
    }
    if spouse:
        checks["basis_is_not_the_five_year_path"] = bool(basis) and basis != "general_provision"
    else:
        checks["basis_is_general_provision"] = basis == "general_provision"
        # (2) names the path.
        checks["names_the_five_year_path"] = bool(
            re.search(r"five[- ]year|cinco años", reply, re.I))
        # (4) the next question is the A-Number.
        checks["next_question_is_the_a_number"] = bool(
            re.search(r"a-?number|número\s+a\b", reply, re.I))
        # (5) she said nothing about a spouse in this arm.
        checks["claims_no_spouse"] = not re.search(
            r"\b(spouse|husband|wife|esposo|esposa|cónyuge)\b", reply, re.I)
    return {"pass": all(checks.values()), "checks": checks, "reply": reply,
            "minted": minted, "basis": basis}


ASKS_MORE_JOBS = r"anything else|another job|any other job|before that|otro (trabajo|empleo)|algo más"
FULL_DAY = r"^\d{4}-\d{2}-\d{2}$"
SAYS_COVERED = r"covers? the (last |past )?five years|covers the window|cubre los (últimos )?cinco años|cubre los últimos 5"


def derived_view(state: dict):
    """(merged facts, graph) as the CLIENT would compute them after this turn.

    ⚠ Derived values are NEVER written into state["facts"] and never appear in a
    transcript entry; the harness computes them ON READ. So "the gate closed" is
    not a thing to look for in `minted`, which is what v37's first cut assumed
    and what cost probe 1.

    ⚠ g.closed must be set from the run before asking for an agenda, or the
    agenda is not the one the client would have computed and the check passes or
    fails for the wrong reason."""
    import n400_qa as q
    g = q.Graph()
    g.closed = set(state.get("closed") or [])
    return q.with_derived(g, state.get("facts") or {}), g


def gate_is_derived_not_minted(state: dict, gate: str) -> tuple[bool, bool]:
    """(derived "no", its node is off the agenda).

    The second half of the first tuple element is what proves DERIVATION: a
    value present in the merged view and ABSENT from state["facts"] was computed
    by the client, not minted by the lane. A value in both would mean the lane
    minted it, which is the thing v37b stopped asking for."""
    wd, g = derived_view(state)
    derived = wd.get(gate) == "no" and gate not in (state.get("facts") or {})
    # Resolve the gate's node from the graph rather than hardcoding an id: the
    # id seen in probe 1 appeared beside a harness note about the client falling
    # back to it, so it may not be the gate's own node.
    node_of = {fid: nid for nid, n in g.nodes.items() for fid in n["field_ids"]}
    nid = node_of.get(gate)
    off_agenda = nid is not None and nid not in g.agenda(state.get("cursor") or "", wd)
    return derived, off_agenda


def score_v37(state: dict, arm: str) -> dict:
    """v37: a covered history window closes its gate.

    ⚠ Four of the seven arms assert the gate does NOT close. That half is the
    point: a rule that closes gates is only safe if it declines to close them
    on a partial date (D), inside the window (C), with no window sentence (E),
    and on a gate that is not a history gate at all (G)."""
    entries = [e for e in state["transcript"] if e.get("applicant")]
    if not entries or any(e.get("error") for e in entries):
        return {"pass": False, "why": f"incomplete run: {len(entries)} replies", "arm": arm,
                "replies": [e.get("interviewer") for e in entries]}
    last = entries[-1]
    reply = last.get("interviewer") or ""
    minted = {m.get("field_id"): m.get("value") for m in (last.get("minted") or [])}
    facts = state.get("facts") or {}
    asking = last.get("asking") or ""
    checks: dict = {}
    if arm in ("A", "B"):
        # ⚠ REVISED after probe 1. The first cut asserted the LANE minted
        # p7.has_job3, which the client data model makes impossible: a fact
        # outside the asked node's field_ids is filed tentative regardless of
        # the marker. The app owns the gate; the prompt only stops asking.
        derived, off_agenda = gate_is_derived_not_minted(state, "p7.has_job3")
        checks["from_minted_as_the_full_day"] = minted.get("p7.employer2.from") == "2012-01-01"
        checks["says_the_window_is_covered"] = bool(re.search(SAYS_COVERED, reply, re.I))
        checks["does_not_ask_for_an_earlier_job"] = not re.search(ASKS_MORE_JOBS, reply, re.I)
        checks["asking_is_not_the_gate"] = asking != "q_p7_more_jobs2"
        checks["gate_derived_no_not_minted"] = derived
        checks["gate_node_off_the_agenda"] = off_agenda
    elif arm in ("C", "E"):
        checks["gate_not_closed"] = "p7.has_job3" not in minted
        checks["gate_is_still_asked"] = bool(re.search(ASKS_MORE_JOBS, reply, re.I)) or "job3" in asking
    elif arm == "D":
        # v35 on job dates, never receipted on Part 7: a year-only answer must
        # not become a day-precision fact.
        frm = minted.get("p7.employer2.from") or facts.get("p7.employer2.from") or ""
        checks["no_day_precision_from_a_year"] = not re.match(FULL_DAY, str(frm))
        checks["gate_not_closed"] = "p7.has_job3" not in minted
    elif arm == "F":
        # Same revision as A and B: the client derives it, the lane stops
        # asking. Probe 1 showed the lane emitting p4.has_prior_address1 = "no"
        # and the client parking it as TENTATIVE, with the reply explaining
        # itself ("Since that covers the last five years...").
        derived, off_agenda = gate_is_derived_not_minted(state, "p4.has_prior_address1")
        checks["says_the_window_is_covered"] = bool(re.search(SAYS_COVERED, reply, re.I))
        checks["does_not_ask_the_prior_address"] = not re.search(
            r"before (that|this)|previous address|another address|prior address", reply, re.I)
        checks["gate_derived_no_not_minted"] = derived
        checks["gate_node_off_the_agenda"] = off_agenda
    elif arm == "G":
        checks["citizen_how_minted_by_birth"] = minted.get("p5.spouse_citizen_how") == "by_birth"
        checks["a_number_not_minted_silently"] = "p5.spouse_has_a_number" not in minted
        checks["a_number_asked_in_passing"] = bool(
            re.search(r"a-?number", reply, re.I)) and bool(
            re.search(r"right\?|correct\?|isn't (he|that)|verdad\?", reply, re.I))
    return {"pass": all(checks.values()), "checks": checks, "arm": arm,
            "reply": reply, "minted": minted, "asking": asking}


SECOND_SCORER = AUDITOR_QA / "typesafe_rescore_arms.py"


def _second_scorer_gate(out_path: str, advisory: bool = False) -> bool:
    """Rescore this run's reply-TEXT checks with a second, differently built
    scorer. Returns True if the verdicts are HELD (unverified or disagreed on).

    Standing rule, Scott 2026-09-17: "make it standing, run it on every arm
    before shipping." Rule doc, questions and thresholds live with the tool at
    N400 App/qa/JEV-SECOND-SCORER-STANDING-RULE.md. That file is the authority
    and this function deliberately owns no copy of the questions, because two
    copies of a rule drift and the drift is invisible from either side.

    ⭐ WHY THIS IS A GATE AND NOT A README LINE. Our hand written regexes have
    been wrong three times, and the third was a FALSE PASS:
    step2_asks_moved_out_day required asks_day(r2) AND /out|left|leave/, and
    the phrase "move-out date" in a STATEMENT supplied the "out" while asks_day
    matched somewhere else entirely. It credited a question that was never
    asked, and it had already helped certify v35e at 3 of 3, which is what
    serves. A false pass certifies behaviour that did not happen, which is the
    dangerous direction. A rule whose only carrier is "whoever runs the probe
    remembers to also run the rescore" has no carrier at all.

    ⚠ NOT BEING ABLE TO RUN IT IS A HOLD, NOT A SKIP. No run file, no tool on
    disk, no TYPESAFE_API_KEY: each of those leaves the verdicts exactly as
    unverified as a disagreement does, and an absent check reads as a pass to
    anybody scrolling past. Cost is never the reason to skip: 32 checks, about
    $0.0004, roughly 0.3s each.
    """
    if not out_path:
        print("\n⚠ SECOND SCORER: no --out file, so there is nothing to rescore. "
              "A run with no artifact cannot produce a trusted verdict.")
        return not advisory
    if not SECOND_SCORER.exists():
        print(f"\n⚠ SECOND SCORER: {SECOND_SCORER} is not on disk.")
        return not advisory

    # ABSOLUTE, because the tool runs with cwd set to the auditor's directory
    # and resolves a relative path there. Found live: `--out qa/runs/x.json`
    # produced "not on disk", exit 1, and a HOLD that read as the scorer
    # failing when it was the path. Every earlier gate check used absolute
    # paths, which is exactly why none of them could see it.
    cmd = [sys.executable, str(SECOND_SCORER), str(Path(out_path).resolve())]
    if advisory:
        cmd.append("--advisory")
    print(f"\n== second scorer (Jev) over {out_path} ==")
    r = subprocess.run(cmd, cwd=str(AUDITOR_QA), capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode == 0:
        # ⚠ "0/0 agree" is NOT agreement. The tool skips arms it does not
        # know (ARMS) and checks it cannot map (TEXT_CHECKS), silently, so a
        # new arm nobody registered rescored NOTHING and would have read as
        # clean. Caught while adding v38, before it ran. Zero graded is a hold.
        m = re.search(r"(\d+)/(\d+) agree", r.stdout)
        graded = int(m.group(2)) if m else 0
        if graded == 0:
            print("== second scorer GRADED NOTHING: every arm or check was skipped. "
                  "Register the arm in ARMS and its text checks in TEXT_CHECKS "
                  "(typesafe_rescore_arms.py). An ungraded verdict is unverified. ==")
            return not advisory
        print(f"== second scorer agrees on every reply-text check ({graded} graded) ==")
        return False
    if r.returncode == 2:
        print("== second scorer DISAGREES. A disagreement is a hold that a human "
              "rules, and the ruling gets recorded with the arm. Read the reply "
              "text before believing either scorer: the first failure mode of "
              "this method is a check wired to the wrong reply index. ==")
        return not advisory
    print(f"== second scorer could not run (exit {r.returncode}) ==")
    return not advisory


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", choices=["v34", "v35", "v36", "v37", "v38"])
    # Which cut to test. "e" is the current one (v34d unchanged, v35e); the
    # earlier letters reproduce earlier probes exactly. The default is the
    # NEWEST, because the harness once defaulted to "b" while "d" was the live
    # cut and a dry run quietly assembled the previous text. The default is
    # what a run gets when nobody is thinking about it, so it has to be the
    # answer that cannot be silently wrong.
    ap.add_argument("--revision", choices=["a", "b", "c", "d", "e"], default="e")
    ap.add_argument("--out", default="")
    # ⚠ THE GATE, not a flag to reach for. Scott, 2026-09-17: "make it
    # standing, run it on every arm before shipping." --advisory downgrades
    # the hold to a print, and stamps every verdict line UNVERIFIED so a
    # pasted summary carries its own caveat.
    ap.add_argument("--advisory", action="store_true",
                    help="rescore but do not hold on disagreement (stamps output UNVERIFIED)")
    args = ap.parse_args()

    sys.path.insert(0, str(AUDITOR_QA))
    import n400_qa as q
    from app.config import get_settings

    # ⚠ THIS WAS A DEAD PATH. It was hardcoded to one session's scratchpad
    # (.../e310dc06-.../scratchpad/qa_runs), which stops existing the moment
    # that session does. mkdir(parents=True) would have RECREATED it happily
    # under a stale uuid, so the failure would not even have been loud: the run
    # artifacts would just land somewhere nobody would look for them again.
    # A per-session path is the wrong default for a directory whose whole job
    # is to survive between runs. --out is what promotes a run into qa/runs/.
    runs_dir = Path(os.environ.get("N400_PROBE_RUNS")
                    or Path(tempfile.gettempdir()) / "n400-probe-runs")
    # Both halves, because they fail differently: a path that cannot be MADE
    # (N400_PROBE_RUNS pointing through a file) raised a bare NotADirectoryError
    # traceback that never named the variable responsible, while a dir that
    # exists and is read only got a clean message. Same outcome, and only one
    # of them told you where to look.
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SystemExit(f"runs dir {runs_dir} cannot be created: {e}. "
                         "Set N400_PROBE_RUNS to a writable directory.")
    probe = runs_dir / ".writable"
    try:
        probe.write_text("x")
        probe.unlink()
    except OSError as e:
        raise SystemExit(f"runs dir {runs_dir} is not writable: {e}. "
                         "Set N400_PROBE_RUNS to a writable directory.")
    print(f"   runs dir: {runs_dir}")
    q.RUNS = runs_dir
    q.token = lambda: "probe-no-token"
    q.served_build = lambda: {"probe": "direct API, no health read"}

    key = "" if args.dry else get_settings().anthropic_api_key
    if not args.dry:
        assert key, "no Anthropic key in settings"

    # Plan entries are DICTS, not tuples. v37 needs a per-arm context string and
    # a per-arm rep count, which would take the tuple to nine positional fields;
    # at that width the commas are where mistakes live.
    def arm(name, version, cursor, turns, scorer, seed=None, locale="en",
            context=CONTEXT, reps=None):
        return dict(name=name, version=version, cursor=cursor, seed=seed, turns=turns,
                    scorer=scorer, locale=locale, context=context, reps=reps)

    plan = []
    if args.only in (None, "v34"):
        plan.append(arm("v34", 34, "q_p2_country_of_birth", V34_TURNS, score_v34))
    if args.only in (None, "v35"):
        plan.append(arm("v35", 35, "q_p4_prior_address1", V35_TURNS, score_v35,
                        seed={"p4.has_prior_address1": "yes"}))
    if args.only in (None, "v36"):
        # THREE arms, because locale is a RUN-level setting in the auditor's
        # harness: a Spanish rep is its own run, not a Spanish utterance inside
        # an English one. Seeded at the eligibility question, one turn each.
        plan.append(arm("v36-en", 36, "q_p1_eligibility_basis", V36_TURNS_EN, score_v36))
        plan.append(arm("v36-es", 36, "q_p1_eligibility_basis", V36_TURNS_ES, score_v36, locale="es"))
        plan.append(arm("v36-spouse", 36, "q_p1_eligibility_basis", V36_TURNS_SPOUSE,
                        lambda s: score_v36(s, spouse=True), reps=1))
    if args.only in (None, "v37"):
        # Seven arms. FOUR of them (C, D, E, G) assert the gate does NOT close:
        # a rule that closes gates is only safe if it declines to on a partial
        # date, inside the window, with no window sentence, and on a gate that
        # is not a history gate at all.
        # Base + "; " + the one combined piece, the way the app joins them. The
        # es arm differs only in its base (language: Spanish), because the
        # window piece itself carries both languages.
        win_en = CONTEXT + "; " + V37_WINDOW
        win_es = CONTEXT_ES + "; " + V37_WINDOW
        sc = lambda a: (lambda s: score_v37(s, a))
        plan.append(arm("v37-A", 37, "q_p7_job2", [V37_T1_EN, V37_T2_EN], sc("A"),
                        seed=V37_SEED_JOB, context=win_en))
        plan.append(arm("v37-B", 37, "q_p7_job2", [V37_T1_ES, V37_T2_ES], sc("B"),
                        seed=V37_SEED_JOB, locale="es", context=win_es, reps=1))
        plan.append(arm("v37-C", 37, "q_p7_job2", [V37_T1_EN, V37_T2_INSIDE], sc("C"),
                        seed=V37_SEED_JOB, context=win_en, reps=1))
        plan.append(arm("v37-D", 37, "q_p7_job2", [V37_T1_EN, V37_T2_YEARONLY], sc("D"),
                        seed=V37_SEED_JOB, context=win_en, reps=1))
        # E: the build 57 and older case. Base context, NO window sentence.
        plan.append(arm("v37-E", 37, "q_p7_job2", [V37_T1_EN, V37_T2_EN], sc("E"),
                        seed=V37_SEED_JOB, context=CONTEXT, reps=1))
        plan.append(arm("v37-F", 37, "q_p4_current_address_from", [V37_F_TURN], sc("F"),
                        context=win_en, reps=1))
        # G: regression. The window sentence IS present, so a leak into a
        # non-history gate would be caught rather than merely absent.
        plan.append(arm("v37-G", 37, "q_p5_spouse_citizen_how", [V37_G_TURN], sc("G"),
                        seed=V37_SEED_SPOUSE, context=win_en, reps=1))

    if args.only in (None, "v38"):
        # Four arms from the edit file. A and C are the v36 arms scored as
        # before PLUS key order, so the cut is proved to change nothing but
        # order. B is the arm the cut exists for: the read-back turn that
        # buffers today. Its seed is the auditor's own documented reproduction
        # (arm G's comment): with a NON-spouse basis the spouse seed's Part 5 is
        # genuinely complete after "He was born here, in Chicago", so the
        # checkpoint is correct and the reply reads the part back. D mints
        # nothing and pins that the three keys are still PRESENT (empty, empty,
        # null) and still before reply.
        v38_seed_b = {**V37_SEED_SPOUSE, "p1.eligibility_basis": "general_provision"}
        plan.append(arm("v38-A", 38, "q_p1_eligibility_basis", V36_TURNS_EN,
                        lambda s: score_v38(s, "A", dict(RAW_TEXTS)), reps=3))
        plan.append(arm("v38-B", 38, "q_p5_spouse_citizen_how", [V37_G_TURN],
                        lambda s: score_v38(s, "B", dict(RAW_TEXTS)), seed=v38_seed_b, reps=1))
        plan.append(arm("v38-C", 38, "q_p1_eligibility_basis", V36_TURNS_ES,
                        lambda s: score_v38(s, "C", dict(RAW_TEXTS)), locale="es", reps=1))
        plan.append(arm("v38-D", 38, "q_p1_eligibility_basis", [V38_D_TURN],
                        lambda s: score_v38(s, "D", dict(RAW_TEXTS)), reps=1))

    results = []
    stamp = time.strftime("%H%M%S")
    for p in plan:
        name, version, cursor, locale = p["name"], p["version"], p["cursor"], p["locale"]
        configs = (configs_v38() if version == 38 else
                   configs_v37() if version == 37 else
                   configs_v36() if version == 36 else
                   configs_for(version, args.revision))
        # At revision "e" the v34 arm is v34d unchanged, so it is labelled d.
        # A results file naming a "v34e" would invent a cut that never existed,
        # and these files are read months later as the record of what ran.
        # v36 and v37 have no cuts at all, so they carry no letter.
        cut = "" if version in (36, 37, 38) else (
            "d" if (version == 34 and args.revision == "e") else args.revision)
        print(f"== {name}: prompt v{configs[SLUG]['version']}{cut} "
              f"{len(configs[SLUG]['systemPrompt'])} chars, cursor {cursor}, locale {locale}")
        q.post = make_post(configs, key, args.dry)
        # A per-arm rep count, so --reps cannot silently multiply an arm the
        # auditor specified as a single run.
        reps = 1 if args.dry else (p["reps"] or args.reps)
        for rep in range(reps):
            run = f"probe-{name}-{stamp}-r{rep}"
            try:
                RAW_TEXTS.clear()
                state = drive(q, run, cursor, p["seed"], p["turns"], runs_dir, locale, p["context"])
            except SystemExit as e:
                if str(e) == "dry":
                    continue
                raise
            s = p["scorer"](state)
            s.update({"probe": name, "rep": rep, "run": run})
            results.append(s)
            print(f"   {name} rep{rep}: {'PASS' if s['pass'] else 'FAIL'} {s.get('checks') or s.get('why')}")

    if args.dry:
        return 0
    cost = sum(c["in"] * PRICE["in"] + c["out"] * PRICE["out"] + c["cache_read"] * PRICE["cache_read"]
               + c["cache_write"] * PRICE["cache_write"] for c in CALLS) / 1e6
    if args.out:
        Path(args.out).write_text(json.dumps({"revision": args.revision, "results": results, "calls": CALLS,
                                              "cost_usd": round(cost, 4)}, ensure_ascii=False, indent=1))
        print("written", args.out)

    # The second scorer runs BEFORE the per-arm verdict lines are printed,
    # because those lines are the thing that must not be trusted unverified.
    held = _second_scorer_gate(args.out, advisory=args.advisory)
    stamp_v = " [UNVERIFIED]" if held else ""

    for name in ("v34", "v35", "v36-en", "v36-es", "v36-spouse",
                 "v37-A", "v37-B", "v37-C", "v37-D", "v37-E", "v37-F", "v37-G",
                 "v38-A", "v38-B", "v38-C", "v38-D"):
        rs = [r for r in results if r["probe"] == name]
        if rs:
            print(f"{name}: {sum(r['pass'] for r in rs)} of {len(rs)} reps PASS{stamp_v}")
    print(f"calls {len(CALLS)}, measured cost ${cost:.2f}, stop reasons {sorted(set(c['stop'] for c in CALLS))}")
    if held:
        print("\n⚠ HOLD: these verdicts are NOT second-scored. Nothing ships on them.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
