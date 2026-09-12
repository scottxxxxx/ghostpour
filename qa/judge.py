"""Per-field LLM judge for "was she told this field is not settled?",
validated against the auditor's hand-labelled set before any number.

THREE HAZARDS, all of them designed around rather than hoped away:

1. Do NOT give the judge the prompt rule text. A judge handed the rule
   evaluates the lane against the same words the lane was given, which is
   reading the instruction back to itself. The judge below sees the REPLY,
   the field, and the auditor's question. It never sees v29, never sees the
   word "deferred" as a rule, and is not told what good looks like.

2. Do NOT let an aggregate hide the hard cases. The 3 ATTACHMENT turns and
   the flagged clause-boundary turns are scored and reported SEPARATELY.
   42 turns at 80% could be 80% achieved by getting the easy ones right and
   every discriminating case wrong.

3. THE JUDGE AND THE LANE ARE THE SAME MODEL FAMILY. The lane wrote "Noted,
   zip code to verify, and since June 2020" believing it had communicated.
   A judge from the same family may read it the same way and share the exact
   blind spot the instrument exists to find. This one is in the weights, not
   the context, so a clean prompt does not touch it.

   Design response: run TWO models. The lane's own model (sonnet-5) and a
   different one (opus-5). If they agree on the attachment turns, that is
   evidence about the family. If the lane's own model calls those clean
   while the other does not, the blind spot is real and measured.

Scored PER FIELD, per the auditor's instruction, because the whole defect is
that one field gets hedged and another does not.
"""
import json
import os
import sys
import urllib.request

LABELLED = "/tmp/labelled-deferral-turns.json"
OUT = "/tmp/judge_results.json"

# The auditor's question, verbatim from their file. Nothing about our rule,
# nothing about what a good answer looks like, no examples.
# The auditor's question, VERBATIM from their file as revised 2026-09-07
# after the conf-v23 t38 disagreement. The revision matters: the test is not
# whether she understands the field's internal state, it is whether she knows
# to COME BACK to it. Hedging only the precision of a value still counts.
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
    'sentence quoting the words that decided it>"}'
)


def ask(model: str, key: str, reply_text: str, field_id: str, reason: str,
        partial: str | None) -> dict:
    user = (
        "WHAT THE ASSISTANT SAID:\n%s\n\n"
        "THE PIECE OF INFORMATION THAT DID NOT GET RECORDED:\n"
        "  internal name: %s\n"
        "  why it did not get recorded: %s\n"
        "  partial value she gave, if any: %s\n\n"
        "%s"
        % (reply_text, field_id, reason or "(not stated)",
           partial if partial else "(none)", QUESTION)
    )
    body = json.dumps({
        "model": model,
        "max_tokens": 300,
        # NO temperature. The Claude 5 models reject it outright:
        # 400 invalid_request_error, "`temperature` is deprecated for this
        # model". Which means these verdicts are NOT pinned deterministic,
        # so the run below repeats the hard cases to measure stability
        # rather than assuming it.
        "system": SYSTEM,
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
        obj = json.loads(text[i:j + 1])
    except Exception:
        return {"knows": None, "why": "UNPARSEABLE: " + text[:160]}
    return obj


def reply_for(turn: dict) -> str:
    """The locale actually served, plus the English gloss when present, since
    the labels were read that way."""
    r = turn["reply"]
    if isinstance(r, str):
        return r
    loc = turn.get("locale") or "en"
    parts = []
    if loc in r:
        parts.append(r[loc])
    for k, v in r.items():
        if k != loc:
            parts.append("[%s] %s" % (k, v))
    return "\n".join(parts)


def main():
    sys.path.insert(0, "/app")
    from app.config import get_settings
    key = get_settings().anthropic_api_key
    assert key, "no anthropic key in settings"

    data = json.load(open(LABELLED))
    turns = data["turns"]

    models = sys.argv[1].split(",")
    only_hard = len(sys.argv) > 2 and sys.argv[2] == "hard"

    reps = int(os.environ.get("JUDGE_REPS", "1"))
    out = []
    for t in turns:
        if t.get("label") == "ambiguous":
            continue  # auditor excluded it; a coin flip scored as accuracy
        if only_hard and not t.get("hard_case"):
            continue
        text = reply_for(t)
        for d in t["deferred"]:
            row = {"run": t["run"], "turn": t["turn"], "locale": t["locale"],
                   "field_id": d["field_id"], "turn_label": t["label"],
                   "hard_case": t.get("hard_case"),
                   "label_note": t.get("label_note"), "verdicts": {}}
            for m in models:
                runs = []
                for _ in range(reps):
                    try:
                        runs.append(ask(m, key, text, d["field_id"],
                                        d.get("reason"), d.get("partial_value")))
                    except Exception as e:
                        detail = ""
                        try:
                            detail = e.read().decode()[:200]
                        except Exception:
                            detail = str(e)[:200]
                        runs.append({"knows": None, "why": "ERROR: " + detail})
                row["verdicts"][m] = runs[0]
                if reps > 1:
                    row.setdefault("stability", {})[m] = [r.get("knows") for r in runs]
            out.append(row)
            print("  %-20s t%-4s %-34s %s" % (
                t["run"][:20], t["turn"], d["field_id"],
                " ".join("%s=%s" % (m.split("-")[1], row["verdicts"][m].get("knows"))
                         for m in models)), flush=True)

    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print("\nwrote", len(out), "field verdicts to", OUT)


main()
