"""A capture gap with her words is offered back, never asked cold (v62).

Build 188, Scott's device: "I am a carpenter at Alamo Builders now" said at
the birth-country ask became a capture gap, and Part 7 opened with "where do
you work or study right now?" as if she had said nothing. v62 tells the lane
to offer her words back and, on her yes, mint each field with her yes as the
utterance, the same as any offer. That instruction is only safe if GP's own
evidence floor keeps such a fact, so this pins it.
"""

import json
from pathlib import Path

from app.services.n400_interviewer_guard import drop_facts_without_current_evidence

_PROMPT = json.loads((Path(__file__).parent.parent / "config" / "remote" / "n400"
                      / "interviewer-turn.json").read_text(encoding="utf-8"))["systemPrompt"]


def _turn(*facts):
    return json.dumps({"facts": [
        {"field_id": fid, "value": v, "value_type": "string",
         "provenance": {"source": "user_stated", "confidence": 0.95, "utterance": utt}}
        for fid, v, utt in facts]})


def test_a_yes_to_the_offered_employer_keeps_both_fields():
    text = _turn(("p7.employer1.employer_name", "Alamo Builders", "yes"),
                 ("p7.employer1.occupation", "carpenter", "yes"))
    out, dropped = drop_facts_without_current_evidence(text, "yes, still there")
    assert dropped == []
    assert [f["value"] for f in json.loads(out)["facts"]] == ["Alamo Builders", "carpenter"]


def test_a_yes_cited_fact_is_still_dropped_when_she_did_not_say_yes():
    """The floor is not waived: the cited yes must be in THIS line."""
    text = _turn(("p7.employer1.employer_name", "Alamo Builders", "yes"))
    _, dropped = drop_facts_without_current_evidence(text, "no, I left there in May")
    assert [d["field_id"] for d in dropped] == ["p7.employer1.employer_name"]


def test_the_prompt_offers_the_partial_back_at_a_section_boundary():
    assert "A CAPTURE GAP WITH HER WORDS IS OFFERED BACK, NEVER ASKED COLD" in _PROMPT
    i = _PROMPT.index("A CAPTURE GAP WITH HER WORDS IS OFFERED BACK")
    rule = _PROMPT[i:i + 900]
    assert "SECTION BOUNDARY" in rule
    assert "with her yes as its provenance.utterance" in rule
