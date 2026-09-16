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
import re
import subprocess
import sys
import time
import urllib.request
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
AUDITOR_QA = Path("/Users/scottguida/N400 App/qa")
V35_EDITS = Path("/Users/scottguida/N400 App/contracts/partial-date-ask-once-prompt-v35-edits.md")
# v34d merged to main in #987 (57569e40). The PR branch it used to read is
# gone or stale; main IS the v34 arm now.
V34_REF = "origin/main"
SLUG = "n400/interviewer-turn"
CONTEXT = "interpreter: no, filing for self: yes"
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


def configs_for(version: int, revision: str = "e") -> dict:
    """v34 from main (v34d, merged in #987). revision "b" (the default, 2026-09-15 second
    cut) applies v34b's one sentence to v34, and for 35 applies v35b's four
    edits on top of that; v35b's Edit A anchors on the ORIGINAL sentence, so it
    REPLACES v35 rather than building on it. revision "a" reproduces the first
    run (v34 as branched, v35 = v34 + the first v35 edit)."""
    names = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-tree", "--name-only", V34_REF, "config/remote/n400/"]).decode().split()
    out = {"n400/" + Path(n).stem: git_json(V34_REF, n) for n in names if n.endswith(".json")}
    cfg = out[SLUG]
    assert cfg["version"] == 34 and "A PLAIN ANSWER GETS NO ECHO" in cfg["systemPrompt"], "v34 branch is not v34"
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
        # The v34 chain is applied ONLY while V34_REF still carries the pre-cut
        # text. Once v34d lands on the branch (or on main) the base already IS
        # the final v34 arm, and re-applying b, c and d would die on anchors
        # that no longer exist. The v35 chain is unaffected: its anchors are in
        # the date and deferral rules, not the reply shape.
        v34_done = "it never means no read-back" in sp
        if v34_done and not d_or_later:
            raise SystemExit(
                f"V34_REF already carries v34d, so revision {revision!r} cannot be "
                "rebuilt from it. Use --revision d or e, or point V34_REF at the "
                "commit that cut it.")
        steps = []
        if v34_done:
            print(f"   v34 arm: taken from {V34_REF} as is (already v34d)")
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
          locale: str = "en") -> dict:
    """⚠ locale is a RUN-level setting in the auditor's harness, not per turn:
    `state["locale"]` feeds every ask, every reply pick and every question_text.
    So a Spanish rep is its OWN RUN, not a Spanish utterance inside an English
    one. This argument was hardcoded "en" until v36 needed Spanish reps, and
    without it those reps would have run in English and scored as passes."""
    seed_path = None
    if seed:
        seed_path = runs_dir / f"{run}.seed.json"
        seed_path.write_text(json.dumps(seed))
    q.cmd_start(Namespace(run=run, lane="interviewer", locale=locale, persona=None, context=CONTEXT,
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
        "step2_asks_moved_out_day": asks_day(r2) and bool(re.search(r"out|left|leave", r2, re.I)),
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", choices=["v34", "v35", "v36"])
    # Which cut to test. "e" is the current one (v34d unchanged, v35e); the
    # earlier letters reproduce earlier probes exactly. The default is the
    # NEWEST, because the harness once defaulted to "b" while "d" was the live
    # cut and a dry run quietly assembled the previous text. The default is
    # what a run gets when nobody is thinking about it, so it has to be the
    # answer that cannot be silently wrong.
    ap.add_argument("--revision", choices=["a", "b", "c", "d", "e"], default="e")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    sys.path.insert(0, str(AUDITOR_QA))
    import n400_qa as q
    from app.config import get_settings

    runs_dir = Path("/private/tmp/claude-501/-Users-scottguida-cloudzap/e310dc06-602b-43e8-b44f-dc89a8b6ba0f/scratchpad/qa_runs")
    runs_dir.mkdir(parents=True, exist_ok=True)
    q.RUNS = runs_dir
    q.token = lambda: "probe-no-token"
    q.served_build = lambda: {"probe": "direct API, no health read"}

    key = "" if args.dry else get_settings().anthropic_api_key
    if not args.dry:
        assert key, "no Anthropic key in settings"

    plan = []
    if args.only in (None, "v34"):
        plan.append(("v34", 34, "q_p2_country_of_birth", None, V34_TURNS, score_v34, "en"))
    if args.only in (None, "v35"):
        plan.append(("v35", 35, "q_p4_prior_address1", {"p4.has_prior_address1": "yes"},
                     V35_TURNS, score_v35, "en"))
    if args.only in (None, "v36"):
        # THREE arms, because locale is a RUN-level setting in the auditor's
        # harness: a Spanish rep is its own run, not a Spanish utterance inside
        # an English one. Seeded at the eligibility question, one turn each.
        plan.append(("v36-en", 36, "q_p1_eligibility_basis", None, V36_TURNS_EN, score_v36, "en"))
        plan.append(("v36-es", 36, "q_p1_eligibility_basis", None, V36_TURNS_ES, score_v36, "es"))
        plan.append(("v36-spouse", 36, "q_p1_eligibility_basis", None, V36_TURNS_SPOUSE,
                     lambda s: score_v36(s, spouse=True), "en"))

    results = []
    stamp = time.strftime("%H%M%S")
    for name, version, cursor, seed, turns, scorer, locale in plan:
        configs = configs_v36() if version == 36 else configs_for(version, args.revision)
        # At revision "e" the v34 arm is v34d unchanged, so it is labelled d.
        # A results file naming a "v34e" would invent a cut that never existed,
        # and these files are read months later as the record of what ran.
        # v36 has no cuts at all, so it carries no letter.
        cut = "" if version == 36 else (
            "d" if (version == 34 and args.revision == "e") else args.revision)
        print(f"== {name}: prompt v{configs[SLUG]['version']}{cut} "
              f"{len(configs[SLUG]['systemPrompt'])} chars, cursor {cursor}, locale {locale}")
        q.post = make_post(configs, key, args.dry)
        # The auditor asked for 3 en + 3 es + ONE spouse rep, so --reps must not
        # silently triple the spouse arm.
        reps = 1 if (args.dry or name == "v36-spouse") else args.reps
        for rep in range(reps):
            run = f"probe-{name}-{stamp}-r{rep}"
            try:
                state = drive(q, run, cursor, seed, turns, runs_dir, locale)
            except SystemExit as e:
                if str(e) == "dry":
                    continue
                raise
            s = scorer(state)
            s.update({"probe": name, "rep": rep, "run": run})
            results.append(s)
            print(f"   {name} rep{rep}: {'PASS' if s['pass'] else 'FAIL'} {s.get('checks') or s.get('why')}")

    if args.dry:
        return 0
    cost = sum(c["in"] * PRICE["in"] + c["out"] * PRICE["out"] + c["cache_read"] * PRICE["cache_read"]
               + c["cache_write"] * PRICE["cache_write"] for c in CALLS) / 1e6
    for name in ("v34", "v35", "v36-en", "v36-es", "v36-spouse"):
        rs = [r for r in results if r["probe"] == name]
        if rs:
            print(f"{name}: {sum(r['pass'] for r in rs)} of {len(rs)} reps PASS")
    print(f"calls {len(CALLS)}, measured cost ${cost:.2f}, stop reasons {sorted(set(c['stop'] for c in CALLS))}")
    if args.out:
        Path(args.out).write_text(json.dumps({"revision": args.revision, "results": results, "calls": CALLS,
                                              "cost_usd": round(cost, 4)}, ensure_ascii=False, indent=1))
        print("written", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
