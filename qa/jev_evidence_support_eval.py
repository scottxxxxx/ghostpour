"""Can Jev tell a mint her words establish from one they do not?

    .venv/bin/python qa/jev_evidence_support_eval.py [--out qa/runs/jev-evidence-support-<date>.json]

Every case is SYNTHETIC and hand labelled before Jev saw it. The label says
whether `facts_unsupported` SHOULD carry the fact (`mark` True) or not. The
first case is the shape of production turn t_008 (Haiku replay, 2026-09-19),
which is the reason this check exists. Jev is graded against the labels.

Two kinds of error, and they do not cost the same. A MISSED mark is today's
behaviour (the guess files silently). A FALSE mark puts a correctly minted
field on the review card, which costs the applicant a look. Both are counted
apart. Costs a fraction of a cent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services import n400_evidence_support as es  # noqa: E402
from app.services import typesafe_judge as tj  # noqa: E402

BASIS_Q = ("First, how long have you had your green card, and did you get it through a "
           "U.S. citizen spouse or on your own?")
BASIS_Q_ES = ("Primero, ¿cuánto tiempo ha tenido su tarjeta verde, y la obtuvo por medio de un "
              "cónyuge ciudadano estadounidense o por su cuenta?")
BASIS = ["general_provision", "military_hostilities", "military_one_year", "other",
         "spouse_employed_abroad", "spouse_usc", "vawa"]
MARITAL_Q = "What is your current marital status?"
MARITAL = ["single", "married", "divorced", "widowed", "separated", "annulled"]
YN = ["yes", "no"]
NAMES_Q = "Have you used any other names since birth, like a maiden name?"
TRIPS_Q = "In the last five years, have you taken any trip outside the United States that lasted 24 hours or longer?"
TAXES_Q = "Since becoming a permanent resident, have you ever failed to file a required tax return?"
GENDER_Q = "What is your gender?"
GENDER = ["male", "female", "another"]

# (id, question, she said, cited, value, options, mark)
CASES = [
    # --- should be MARKED: the words do not establish the option ---------------
    ("t008_shape", BASIS_Q, "Well, I've had it for 5 years and a lawyer helped me get it.",
     "I've had it for 5 years", "general_provision", BASIS, True),
    ("half_answer_years_only", BASIS_Q, "About seven years now.", "About seven years", "general_provision", BASIS, True),
    ("half_answer_three_years", BASIS_Q, "Three years.", "Three years", "spouse_usc", BASIS, True),
    ("mentions_husband_not_path", BASIS_Q, "Six years. My husband and I moved here together from Peru.",
     "My husband and I moved here together", "spouse_usc", BASIS, True),
    ("es_half_answer", BASIS_Q_ES, "Pues, la tengo desde hace cinco años y un abogado me ayudó.",
     "la tengo desde hace cinco años", "general_provision", BASIS, True),
    ("marital_from_aside", MARITAL_Q, "Well, my kids live with me.", "my kids live with me", "married", MARITAL, True),
    ("marital_was_married", MARITAL_Q, "I was married once.", "I was married once", "divorced", MARITAL, True),
    ("names_nickname_unclear", NAMES_Q, "People call me Beto.", "People call me Beto", "no", YN, True),
    ("trips_unsure", TRIPS_Q, "I'd have to check my passport.", "I'd have to check my passport", "no", YN, True),
    ("taxes_topic_only", TAXES_Q, "My cousin does my taxes every year.", "My cousin does my taxes", "no", YN, True),
    ("contradiction", MARITAL_Q, "I'm divorced, since 2019.", "I'm divorced", "married", MARITAL, True),
    ("contradiction_yn", TRIPS_Q, "Yeah, I went to Mexico for two weeks last summer.", "I went to Mexico for two weeks", "no", YN, True),
    ("unrelated_answer", MARITAL_Q, "Can you repeat the question?", "Can you repeat the question", "single", MARITAL, True),
    ("es_unsure", TRIPS_Q, "No estoy segura, tendría que revisar.", "No estoy segura", "no", YN, True),
    ("gender_from_name", GENDER_Q, "My name is Maria.", "My name is Maria", "female", GENDER, True),
    # --- should NOT be marked: the words establish it without saying the token ---
    ("own_path_clear", BASIS_Q, "Six years, and I got it on my own through my job, not through marriage.",
     "I got it on my own through my job, not through marriage", "general_provision", BASIS, False),
    ("spouse_path_clear", BASIS_Q, "Three years, through my wife, she's a U.S. citizen.",
     "through my wife, she's a U.S. citizen", "spouse_usc", BASIS, False),
    ("es_own_path_clear", BASIS_Q_ES, "Seis años, y la conseguí por mi cuenta, por mi trabajo.",
     "la conseguí por mi cuenta, por mi trabajo", "general_provision", BASIS, False),
    ("es_spouse_clear", BASIS_Q_ES, "Tres años, por mi esposo que es ciudadano.",
     "por mi esposo que es ciudadano", "spouse_usc", BASIS, False),
    ("marital_never", MARITAL_Q, "I've never been married.", "I've never been married", "single", MARITAL, False),
    ("marital_husband_passed", MARITAL_Q, "My husband passed away two years ago and I haven't remarried.",
     "My husband passed away two years ago and I haven't remarried", "widowed", MARITAL, False),
    ("marital_wife", MARITAL_Q, "I live with my wife, we've been together twelve years.",
     "I live with my wife", "married", MARITAL, False),
    ("names_never", NAMES_Q, "Never, it's always been this one.", "Never, it's always been this one", "no", YN, False),
    ("names_maiden", NAMES_Q, "I used Ramirez before I got married.", "I used Ramirez before I got married", "yes", YN, False),
    ("trips_none", TRIPS_Q, "I haven't left the country since I got here.", "I haven't left the country since I got here", "no", YN, False),
    ("trips_went", TRIPS_Q, "I went to Mexico for two weeks last summer.", "I went to Mexico for two weeks", "yes", YN, False),
    ("taxes_always", TAXES_Q, "I file every single year, never missed one.", "I file every single year, never missed one", "no", YN, False),
    ("es_never", NAMES_Q, "Nunca, siempre he usado este nombre.", "Nunca, siempre he usado este nombre", "no", YN, False),
    ("es_trips_none", TRIPS_Q, "No he salido del país desde que llegué.", "No he salido del país", "no", YN, False),
    ("casual_negative", TRIPS_Q, "Nope, can't afford to travel.", "Nope", "no", YN, False),
    ("casual_affirmative", NAMES_Q, "Uh huh, my maiden name.", "Uh huh, my maiden name", "yes", YN, False),
    ("gender_stated", GENDER_Q, "I'm a woman.", "I'm a woman", "female", GENDER, False),
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--variant", default="prod", help="a question set in jev_support_variants.py")
    args = ap.parse_args()
    questions = es.support_questions
    if args.variant != "prod":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import jev_support_variants as variants
        questions = variants.VARIANTS[args.variant]
    print(f"variant {args.variant}")
    key = get_settings().typesafe_api_key
    if not key:
        print("needs CZ_TYPESAFE_API_KEY in .env")
        return 2
    rows = []
    for cid, q, said, cited, value, options, mark in CASES:
        checked = [{"field_id": cid, "question": q, "applicant_said": said, "cited_words": cited,
                    "recorded_answer": value, "options": sorted(options)}]
        body, row = await tj.guarded_ask(key, {"facts": checked}, questions(1),
                                         judgment="eval", mode="eval", timeout=10)
        a = (body or {}).get("answers", {}).get("f0", {})
        marked = bool(es.read_support(body, checked)) if body else False
        rows.append({"id": cid, "said": said, "value": value, "want_mark": mark, "marked": marked,
                     "choice": a.get("choice"), "confidence": round(float(a.get("confidence") or 0), 3),
                     "ms": row.get("jev_ms"), "input_tokens": row.get("input_tokens")})
    should = [r for r in rows if r["want_mark"]]
    shouldnt = [r for r in rows if not r["want_mark"]]
    missed = [r for r in should if not r["marked"]]
    false = [r for r in shouldnt if r["marked"]]
    print(f"{len(rows)} cases, hand labels are the authority, floor {tj.CONFIDENCE_FLOOR}\n")
    print(f"unsupported mints caught : {len(should) - len(missed)}/{len(should)}")
    for r in missed:
        print(f"    MISSED     {r['id']}: {r['choice']} @ {r['confidence']}")
    print(f"good mints left alone    : {len(shouldnt) - len(false)}/{len(shouldnt)}")
    for r in false:
        print(f"    FALSE MARK {r['id']}: {r['choice']} @ {r['confidence']}")
    ms = [r["ms"] for r in rows if r["ms"] is not None]
    print(f"\nmedian {int(statistics.median(ms))}ms  max {max(ms)}  "
          f"input tokens total {sum(r['input_tokens'] or 0 for r in rows)}")
    t = next(r for r in rows if r["id"] == "t008_shape")
    print(f"t_008 shape: {t['choice']} @ {t['confidence']} -> {'MARKED' if t['marked'] else 'not marked'}")
    if args.out:
        Path(args.out).write_text(json.dumps({"model": tj.MODEL, "floor": tj.CONFIDENCE_FLOOR,
                                              "variant": args.variant, "rows": rows}, ensure_ascii=False, indent=1))
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
