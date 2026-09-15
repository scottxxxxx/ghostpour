"""Judge the generated turns from ste_run.py, per field, per variant.

    python /tmp/ste_judge.py /tmp/ste_full.json /tmp/ste_judged.json [model]

Reuses the auditor's validated instrument verbatim: same SYSTEM text, same
QUESTION, same per-field framing, no rule text shown to the judge. That judge
scored 68/68 against their hand labels on 2026-09-07, so changing a word of it
would mean re-validating it before any number here meant anything.

⚠ THE TRAP THIS IS DESIGNED AROUND. The metric is "of the fields this reply
deferred, how many did it tell her about". A variant that simply STOPS
DEFERRING scores a perfect rate on zero fields. That is a check that passes by
construction, the same family as a `cached=True` that could never report the
bad state. So deferral COUNTS are reported beside the rate, always, and a turn
that deferred nothing is excluded from the rate and counted separately. Read
the pair or read neither.

⚠ Gold labels belong to the v29-era OBSERVED reply, not to anything generated
here. They are used two ways and neither is "score the generated reply against
gold": as a BASELINE rate to compare both variants against, and as a VALIDITY
CHECK, because variant A is v29 and should roughly reproduce the historical
behaviour. If A diverges wildly from gold, the harness is not reproducing the
lane and no A-versus-B number from it is worth reading.
"""
import json
import sys
import urllib.request

QUESTION = ("Reading only this reply, would the applicant know she has to "
            "COME BACK to this specific field before the form is done?")

SYSTEM = (
    "You are reading one turn of a spoken interview in which an assistant "
    "helps someone fill in a government form. You will be shown what the "
    "assistant said, and one specific piece of information that did NOT get "
    "recorded on the form from that turn.\n\n"
    "Answer one question about the person listening, not about the "
    "assistant's intent: from the assistant's words alone, would she know "
    "she has to COME BACK to this piece of information before the form is "
    "finished?\n\n"
    "She cannot see the form, the record, or any screen. She has only what "
    "was said out loud.\n\n"
    "The question is about whether she knows to RETURN to it, not about "
    "whether she understands its internal state. A reply that hedges only "
    "the PRECISION of a value still tells her to return, and counts as yes: "
    "if the assistant says the dates are noted but the exact days need "
    "confirming, she knows she has to come back with a document. A reply "
    "that states the value with nothing marking it as outstanding gives her "
    "no reason ever to return, and counts as no.\n\n"
    "Be careful when the reply mentions more than one piece of information. "
    "Judge only the one you are asked about. Words that mark a DIFFERENT "
    "piece as outstanding do not tell her anything about this one.\n\n"
    'Reply with JSON only: {"knows": true|false, "why": "<one short '
    'sentence quoting the words that decided it>"}')


def ask(model, key, reply_text, field_id, reason, partial):
    user = ("WHAT THE ASSISTANT SAID:\n%s\n\n"
            "THE PIECE OF INFORMATION THAT DID NOT GET RECORDED:\n"
            "  internal name: %s\n"
            "  why it did not get recorded: %s\n"
            "  partial value she gave, if any: %s\n\n%s"
            % (reply_text, field_id, reason or "(not stated)",
               partial if partial else "(none)", QUESTION))
    body = json.dumps({
        "model": model, "max_tokens": 300, "system": SYSTEM,
        # ⚠ NO `thinking` FIELD, DELIBERATELY. On Opus 5 omitting it runs
        # ADAPTIVE, and that is the configuration the judge was validated
        # under on 2026-09-07 when it scored 68/68 against the hand labels.
        # Sending {"type": "disabled"} would be a DIFFERENT instrument than
        # the validated one, and it also hits a documented Opus 5 pitfall:
        # with thinking disabled the model can leak thinking tags into the
        # visible text, which for a judge parsed as JSON turns a harness
        # fault into what looks like disagreement. Changing this line means
        # re-validating against the labelled set before any number is read.
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    text = "".join(b.get("text", "") for b in resp.get("content", [])
                   if b.get("type") == "text")
    i, j = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[i:j + 1])
    except Exception:
        return {"knows": None, "why": "UNPARSEABLE: " + text[:160]}


def reply_text_of(obj, locale):
    """The locale served plus any gloss, the way the labels were read."""
    r = obj.get("reply")
    if isinstance(r, str):
        return r
    if not isinstance(r, dict):
        return ""
    parts = []
    if locale in r:
        parts.append(r[locale])
    for k, v in r.items():
        if k != locale and isinstance(v, str):
            parts.append("[%s] %s" % (k, v))
    return "\n".join(parts)


def main():
    src, out_path = sys.argv[1], sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else "claude-opus-5"

    sys.path.insert(0, "/app")
    from app.config import get_settings
    key = get_settings().anthropic_api_key
    assert key, "no anthropic key in settings"

    data = json.load(open(src))
    attachment = {("conf-es-v27.json", 31), ("conf-v21.json", 31),
                  ("conf-v22.json", 32)}

    judged = []
    for r in data["results"]:
        rec = {k: r[k] for k in ("run", "turn", "locale", "variant", "rep",
                                 "gold_label")}
        rec["is_attachment"] = (r["run"], r["turn"]) in attachment
        try:
            obj = json.loads(r.get("envelope") or r["raw"])
        except Exception:
            rec["parse_error"] = True
            rec["deferred_n"] = None
            judged.append(rec)
            print("  PARSE FAIL", rec["run"], rec["turn"], rec["variant"], flush=True)
            continue

        deferred = obj.get("deferred") or []
        deferred = [d for d in deferred if isinstance(d, dict)]
        rec["deferred_n"] = len(deferred)
        rec["deferred_fields"] = [d.get("field_id") for d in deferred]
        reply = reply_text_of(obj, r["locale"])
        rec["fields"] = []
        for d in deferred:
            v = ask(model, key, reply, d.get("field_id"), d.get("reason"),
                    d.get("partial_value"))
            rec["fields"].append({"field_id": d.get("field_id"),
                                  "knows": v.get("knows"), "why": v.get("why")})
        knows = [f["knows"] for f in rec["fields"]]
        # AND across fields, matching how the hand labels were assigned.
        rec["turn_told"] = (all(knows) if knows else None)
        judged.append(rec)
        print("  %s#%s %s rep%s deferred=%d told=%s"
              % (rec["run"], rec["turn"], rec["variant"], rec["rep"],
                 rec["deferred_n"], rec["turn_told"]), flush=True)

    json.dump({"judge_model": model, "judged": judged}, open(out_path, "w"),
              ensure_ascii=False, indent=1)
    print("written", out_path)


if __name__ == "__main__":
    main()
