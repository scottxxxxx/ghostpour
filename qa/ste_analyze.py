"""The A/B report: gate first, number second, disagreement set as its own line.

    python /tmp/ste_analyze.py /tmp/ste_full.json /tmp/ste_judged.json

Two scorers of different kinds on the same generated replies:
  - the LLM judge (validated today at 41/41 turn-level against the hand labels)
  - deferral_disclosure.py, mechanical, no model in the loop, also 41/41

⚠ PRE-REGISTERED BEFORE ANY RESULT WAS SEEN (2026-09-11):
    PRIMARY GATE: variant A's per-turn agreement with the gold labels, floor
    33 of 41. Below that, no A-versus-B number is published, because variant A
    IS v29 and a harness that cannot reproduce v29 cannot measure a change to it.
    SECONDARY, reported not gated: the A-versus-gold rate delta as a number.

⚠ Both degenerate winners are guarded:
    defer nothing      -> scores perfectly on zero fields. Excluded from the
                          rate, counted separately.
    disclose everything -> scores perfectly by naming every field in every
                          reply, while breaking the 35-word spoken cap and the
                          one-question ending. Reply length and question count
                          are reported beside the rate.

⚠ The mechanical scorer's hedge vocabulary was tuned on the CURRENT prompt's
phrasings, so it can under-report variant B BY CONSTRUCTION. A B-worse gap that
appears only there is evidence about the vocabulary, not about the prompt.
"""
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict

GATE_FLOOR = 33          # of 41, pre-registered
IN_VOCAB = ("date", "from", "to", "postal_code", "occupation", "employer_name")
WORD_CAP = {"en": 35, "es": 42}

spec = importlib.util.spec_from_file_location("dd", "/tmp/deferral_disclosure.py")
dd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dd)


def reply_for_locale(obj, locale):
    r = obj.get("reply")
    if isinstance(r, str):
        return r
    if not isinstance(r, dict):
        return ""
    return r.get(locale) or (next(iter(r.values()), "") if r else "")


def in_vocab(field_id):
    tail = (field_id or "").split(".")[-1]
    return any(tok in tail for tok in IN_VOCAB)


def majority(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


def main():
    gen = json.load(open(sys.argv[1]))
    judged = json.load(open(sys.argv[2]))
    labelled = json.load(open("/tmp/labelled-deferral-turns.json"))
    gold = {(t["run"], t["turn"]): t["label"] for t in labelled["turns"]}

    judge_by = {}
    for j in judged["judged"]:
        judge_by[(j["run"], j["turn"], j["variant"], j["rep"])] = j

    rows = defaultdict(dict)          # (run,turn) -> variant -> list of per-rep dicts
    oov_fields = Counter()
    parse_fail = Counter()

    for r in gen["results"]:
        key = (r["run"], r["turn"])
        try:
            obj = json.loads(r.get("envelope") or r["raw"])
        except Exception:
            parse_fail[r["variant"]] += 1
            continue
        deferred = [d for d in (obj.get("deferred") or []) if isinstance(d, dict)]
        reply = reply_for_locale(obj, r["locale"])

        mech = None
        if deferred:
            for d in deferred:
                if not in_vocab(d.get("field_id")):
                    oov_fields[(r["variant"], d.get("field_id"))] += 1
            mech = dd.score_turn({
                "run": r["run"], "turn": r["turn"], "locale": r["locale"],
                "reply": {r["locale"]: reply},
                # THE GENERATED array, with partial_value verbatim.
                "deferred": deferred,
            })

        jrec = judge_by.get((r["run"], r["turn"], r["variant"], r["rep"]), {})
        rows[key].setdefault(r["variant"], []).append({
            "rep": r["rep"], "locale": r["locale"],
            "deferred_n": len(deferred),
            "fields": [d.get("field_id") for d in deferred],
            "partial_n": sum(1 for d in deferred if d.get("partial_value")),
            "judge_told": jrec.get("turn_told"),
            "mech_told": (mech["predicted"] == "told") if mech else None,
            "words": len(reply.split()),
            "questions": reply.count("?") + reply.count("¿"),
            "over_cap": len(reply.split()) > WORD_CAP.get(r["locale"], 35),
        })

    variants = ["A_v29", "B_ste"]
    print("=" * 72)
    print("STE A/B on the v29 DEFERRALS block. Nothing served was changed.")
    print("=" * 72)

    # ---- the gate, first ------------------------------------------------
    agree = scored = 0
    for key, per_variant in rows.items():
        label = gold.get(key)
        if label is None or label == "ambiguous" or "A_v29" not in per_variant:
            continue
        reps = per_variant["A_v29"]
        verdicts = [x["judge_told"] for x in reps if x["deferred_n"]]
        m = majority(verdicts)
        if m is None:
            continue
        scored += 1
        if ("told" if m else "not_told") == label:
            agree += 1
    print("\nPRE-REGISTERED GATE: variant A per-turn agreement with gold")
    print("  %d of %d turns agree (floor was %d of 41)" % (agree, scored, GATE_FLOOR))
    passed = agree >= GATE_FLOOR
    print("  GATE %s" % ("PASSED" if passed else "FAILED"))
    if not passed:
        print("\n  Variant A is v29. A harness that cannot reproduce v29 cannot")
        print("  measure a change to it, so NO A-versus-B number is published.")
        return

    # ---- the numbers ----------------------------------------------------
    print("\nPER VARIANT")
    summary = {}
    for v in variants:
        told = n = zero_def = 0
        words, qs, over, deferred_total, partials = [], [], 0, 0, 0
        for key, per_variant in rows.items():
            reps = per_variant.get(v, [])
            if not reps:
                continue
            for x in reps:
                words.append(x["words"])
                qs.append(x["questions"])
                over += 1 if x["over_cap"] else 0
                deferred_total += x["deferred_n"]
                partials += x["partial_n"]
            live = [x for x in reps if x["deferred_n"]]
            if not live:
                zero_def += 1
                continue
            m = majority([x["judge_told"] for x in live])
            if m is None:
                continue
            n += 1
            told += 1 if m else 0
        summary[v] = {"told": told, "n": n, "zero": zero_def}
        rate = 100.0 * told / n if n else 0
        print("  %-6s judge told %d/%d (%.1f%%)   turns that deferred NOTHING: %d"
              % (v, told, n, rate, zero_def))
        print("         deferred fields total %d (with a partial value: %d)"
              % (deferred_total, partials))
        print("         reply words mean %.1f   over the spoken cap: %d of %d"
              % (sum(words) / len(words) if words else 0, over, len(words)))
        print("         replies ending on exactly one question: %d of %d"
              % (sum(1 for q in qs if q == 1), len(qs)))
        if parse_fail.get(v):
            print("         UNPARSEABLE responses: %d" % parse_fail[v])

    # ---- second scorer and the disagreement set -------------------------
    print("\nSECOND SCORER (mechanical, no model)")
    for v in variants:
        told = n = 0
        for key, per_variant in rows.items():
            live = [x for x in per_variant.get(v, []) if x["deferred_n"]]
            if not live:
                continue
            m = majority([x["mech_told"] for x in live])
            if m is None:
                continue
            n += 1
            told += 1 if m else 0
        print("  %-6s mechanical told %d/%d (%.1f%%)"
              % (v, told, n, 100.0 * told / n if n else 0))

    print("\nDISAGREEMENT SET (judge vs mechanical, per turn per variant)")
    disagreements = 0
    for key, per_variant in sorted(rows.items()):
        for v in variants:
            live = [x for x in per_variant.get(v, []) if x["deferred_n"]]
            if not live:
                continue
            j, m = majority([x["judge_told"] for x in live]), majority([x["mech_told"] for x in live])
            if j is not None and m is not None and j != m:
                disagreements += 1
                print("  %s#%s %s  judge=%s mechanical=%s fields=%s"
                      % (key[0], key[1], v, j, m, live[0]["fields"]))
    if not disagreements:
        print("  none: the two instruments agree on every turn in both variants")

    if oov_fields:
        print("\nFIELDS OUTSIDE THE MECHANICAL SCORER'S VOCABULARY")
        print("  (these read as not_told by fallthrough, which is safe but NOT a")
        print("   measurement; excluded from any claim about the mechanical rate)")
        for (v, f), c in oov_fields.most_common(12):
            print("    %-6s %-40s x%d" % (v, f, c))

    print("\nFIELD DISTRIBUTION, so equal counts are not mistaken for equal difficulty")
    for v in variants:
        dist = Counter()
        for key, per_variant in rows.items():
            for x in per_variant.get(v, []):
                for f in x["fields"]:
                    dist[(f or "?").split(".")[-1]] += 1
        print("  %-6s %s" % (v, dict(dist.most_common(8))))


if __name__ == "__main__":
    main()
