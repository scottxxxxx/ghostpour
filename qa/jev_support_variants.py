"""Candidate question sets for the evidence support check, run by
`jev_labelled_option_mints_eval.py --variant NAME` beside production's
`n400_evidence_support.support_questions` ("prod").

Each variant changes ONE idea against prod so a moved count can be put down
to it. Every example inside a criterion is taken from a field that is NOT in
the auditor's labelled set (`qa/labelled-option-mints.json`), so a variant
cannot win by quoting the cases it is graded on. Structured criteria follow
TypeSafe's choice guidance (what an option covers, what belongs to a
neighbour instead, a few examples); the key names are ours, not reserved.

2026-09-24, the first iteration, 17 checked real cases at 3 reps and the 32
synthetic cases (runs in qa/runs/*-2026-09-24-*.json):
  v1 (then prod)  real 1/4 caught, 8/8 left alone; synthetic 14/15, 16/17
  hedge           real 3/4, 8/8; synthetic 13/15 (lost marital_from_aside)
  nearest         real 1/4, 7/8 (a false mark on has_job2); hair got WORSE
  both            real 3/4, 8/8; no better than hedge alone
  hedge2          real 3/4, 8/8; synthetic 14/15, 16/17  -> SHIPPED
In the tables of the earlier runs the name "prod" means v1.
"""

from __future__ import annotations


def _instructions(f: str) -> str:
    return (f"An interviewer filling in a form asked {f}.question`. The applicant "
            f"answered {f}.applicant_said`. The system recorded the answer "
            f"{f}.recorded_answer`, chosen from {f}.options`, and quoted "
            f"{f}.cited_words` as its evidence. Do the applicant's own words "
            "establish that recorded answer?")


V1_SUPPORTS = ("Her words state the recorded answer or directly imply it, so that "
                 "none of the other options could be what she meant.")
V1_INSUFFICIENT = ("Her words are about the question but do not settle which option "
                     "applies: she answered only part of a question with several parts, "
                     "or what she said fits more than one option, or the recorded answer "
                     "is a guess from something she said about a different matter.")
V1_CONTRADICTS = "Her words point to a different option than the recorded one."
UNRELATED = "Her words do not address this question at all."

HEDGE_SUPPORTS = {
    "what": ("She says which option applies to her, in her own words or an unmistakable "
             "equivalent, so that none of the other options could be what she meant."),
    "not_for": ("A detail from which the option might be worked out, when she has not said "
                "which option applies and another option is still possible: that is "
                "insufficient."),
    "examples": [
        "Asked about her father's citizenship, she says 'he was naturalized in 1990' and "
        "the recorded answer is naturalized.",
        "Asked whether she has ever used another name, she says 'no, only this one' and the "
        "recorded answer is no.",
    ],
}
HEDGE_INSUFFICIENT = {
    "what": ("Her words are about the question but do not settle which option applies. "
             "This covers: she answered only part of a question with several parts; what "
             "she said fits more than one option; the recorded option was worked out from a "
             "detail (a number of years, a place, a date) rather than from her saying which "
             "option applies; she says she does not know or is not sure; or she only repeats "
             "what someone else told her."),
    "not_for": "Words that name a different option in the list: that is contradicts.",
    "examples": [
        "Asked how she entered the country, she says 'I'm not sure, my uncle handled "
        "everything' and the recorded answer is with_inspection.",
        "Asked how her mother became a citizen, she says 'she's been here thirty years' and "
        "the recorded answer is naturalized.",
    ],
}
# hedge dropped prod's "a guess from something she said about a different
# matter" while rewriting, and the synthetic `marital_from_aside` ("my kids
# live with me" minted married) went from 5/5 marked to 0/5 on it. hedge2 is
# hedge with that clause restored, nothing else.
HEDGE2_INSUFFICIENT = {
    "what": ("Her words are about the question but do not settle which option applies. "
             "This covers: she answered only part of a question with several parts; what "
             "she said fits more than one option; the recorded option was worked out from a "
             "detail (a number of years, a place, a date) rather than from her saying which "
             "option applies; the recorded answer is a guess from something she said about a "
             "different matter; she says she does not know or is not sure; or she only "
             "repeats what someone else told her."),
    "not_for": HEDGE_INSUFFICIENT["not_for"],
    "examples": HEDGE_INSUFFICIENT["examples"],
}
NEAREST_CONTRADICTS = {
    "what": ("Her words point to a different option than the recorded one. This includes "
             "words that name, describe or translate to another option in the list more "
             "exactly than they fit the recorded one: a synonym, a word in another language, "
             "a closer shade or category."),
    "not_for": "Words that fit the recorded option and another equally well: that is insufficient.",
    "examples": [
        "Asked about eye colour, she says 'hazel' and the recorded answer is brown while "
        "hazel is an option.",
        "Asked about her father's citizenship, she says 'nació aquí' and the recorded answer "
        "is naturalized.",
    ],
}


def _variant(supports, insufficient, contradicts):
    def questions(n: int) -> dict:
        qs = {}
        for i in range(n):
            qs[f"f{i}"] = {
                "type": "choice",
                "instructions": _instructions(f"`facts[{i}]"),
                "criteria": {"supports": supports, "insufficient": insufficient,
                             "contradicts": contradicts, "unrelated": UNRELATED},
            }
        return qs
    return questions


VARIANTS = {
    # The first wording, production until 2026-09-24.
    "v1": _variant(V1_SUPPORTS, V1_INSUFFICIENT, V1_CONTRADICTS),
    "hedge": _variant(HEDGE_SUPPORTS, HEDGE_INSUFFICIENT, V1_CONTRADICTS),
    "nearest": _variant(V1_SUPPORTS, V1_INSUFFICIENT, NEAREST_CONTRADICTS),
    "both": _variant(HEDGE_SUPPORTS, HEDGE_INSUFFICIENT, NEAREST_CONTRADICTS),
    # Production since 2026-09-24 (`support_questions` is byte identical).
    "hedge2": _variant(HEDGE_SUPPORTS, HEDGE2_INSUFFICIENT, V1_CONTRADICTS),
}
