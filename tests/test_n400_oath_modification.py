"""The one harm in this lane that no later correction reaches.

Only the BEARING ARMS and NONCOMBATANT SERVICES clauses of the Oath permit
a modification, on a religious or conscientious objection. WORK OF NATIONAL
IMPORTANCE UNDER CIVILIAN DIRECTION PERMITS NONE. USCIS instructions: "You
may not request a modification to the portion of the Oath requiring you to
perform work of national importance under civilian direction." 12 USCIS-PM
J.3(A)(1): "There is no exemption from the clause."

Every other defect here is recoverable: a wrong value is corrected, a
starved field re-asked, a premature checkpoint refused and retried. An
applicant told a route exists either attests to something she does not
accept or chases a remedy that does not exist and files anyway, and both
are decisions she makes once.

THE RULE IS THE AUDITOR'S. Their first version was whole-line
co-occurrence and it flagged USCIS's OWN denial, which would have shipped
a guard that refuses the right answer. The polarity test is what
discriminates and it is theirs; these six cases are theirs too.
"""
import json

import pytest

from app.services.n400_interviewer_guard import (
    mark_impossible_modification, offers_impossible_oath_modification,
)


def _reply(text, locale="en"):
    return json.dumps({"intent": "answer", "facts": [], "reply": {locale: text}})


# (label, sentence, must_flag) — the auditor's six, verbatim
AUDITOR_SIX = [
    ("uscis_own_denial",
     "You may not request a modification to the portion of the Oath requiring you "
     "to perform work of national importance under civilian direction.", False),
    ("plain_denial",
     "There is no modification available for work of national importance.", False),
    ("a_real_offer",
     "You can ask for a modification of the work of national importance clause.", True),
    ("mixed_and_dangerous",
     "You cannot modify the first two, but you can modify work of national importance.", True),
    ("the_question_itself",
     "If the law requires it, are you willing to perform work of national importance "
     "under civilian direction?", False),
    ("known_over_flag",
     "You can modify the first two, but not the work of national importance one.", True),
]


@pytest.mark.parametrize("label,sentence,must_flag", AUDITOR_SIX,
                         ids=[c[0] for c in AUDITOR_SIX])
def test_the_auditors_six_sentences(label, sentence, must_flag):
    got = offers_impossible_oath_modification(_reply(sentence))
    assert (got is not None) == must_flag, sentence


def test_the_negation_must_GOVERN_not_merely_appear():
    """The whole discriminator. Whole-line search gets this exactly
    backwards: it passes the dangerous sentence because a 'not' appears
    somewhere, and flags USCIS's own wording."""
    dangerous = ("You cannot modify the first two, but you can modify work of "
                 "national importance.")
    assert offers_impossible_oath_modification(_reply(dangerous)) is not None, (
        "a 'not' elsewhere in the sentence must not license the offer")

    safe = ("You may not request a modification to work of national importance.")
    assert offers_impossible_oath_modification(_reply(safe)) is None


def test_the_over_flag_is_deliberate_and_cheap():
    """Left in on purpose. The response is a retry, so a false flag costs
    one rewrite; a miss is the harm nothing reaches. Tuning toward
    precision would be optimising the cheap direction."""
    got = offers_impossible_oath_modification(
        _reply("You can modify the first two, but not the work of national importance one."))
    assert got is not None


def test_other_oath_clauses_are_untouched():
    """Bearing arms and noncombatant services DO permit a modification, so
    saying so must never flag. A guard that suppressed the true statement
    would push her away from a remedy she is entitled to."""
    for s in ("You can ask USCIS for a modification of the bearing arms question.",
              "A modification is available for noncombatant services if you have a "
              "religious objection.",
              "If the law requires it, are you willing to bear arms (carry weapons) "
              "on behalf of the United States?"):
        assert offers_impossible_oath_modification(_reply(s)) is None, s


@pytest.mark.parametrize("locale,sentence,must_flag", [
    ("es", "Puede pedir una modificación del trabajo de importancia nacional.", True),
    ("es", "No hay modificación para el trabajo de importancia nacional.", False),
    ("es", "¿Está dispuesta a realizar trabajo de importancia nacional?", False),
    ("pt", "Você pode pedir uma modificação do trabalho de importância nacional.", True),
    ("pt", "Não há modificação para o trabalho de importância nacional.", False),
])
def test_spanish_and_portuguese(locale, sentence, must_flag):
    """⚠ GP's addition, NOT the auditor's tested rule. The lane runs live in
    Spanish, so an English-only detector would be blind in the language it
    is least tested in, which is how the day guard nearly shipped broken.
    These cases are constructed, not drawn from graded transcripts."""
    got = offers_impossible_oath_modification(_reply(sentence, locale))
    assert (got is not None) == must_flag, sentence


def test_a_bare_string_reply_is_still_scanned():
    assert offers_impossible_oath_modification(json.dumps(
        {"reply": "You can get an exemption from work of national importance."})) is not None


def test_nothing_to_scan_is_not_a_flag():
    for t in ("not json", "[1,2]", json.dumps({"reply": None}),
              json.dumps({"reply": {"en": "Good morning, shall we begin?"}})):
        assert offers_impossible_oath_modification(t) is None


def test_the_marker_carries_the_sentence_and_the_outcome():
    info = offers_impossible_oath_modification(
        _reply("You can ask for a modification of the work of national importance clause."))
    out = mark_impossible_modification(_reply("rewritten"), info, retried=True, resolved=False)
    m = json.loads(out)["impossible_oath_modification"]
    assert m["retried"] is True and m["resolved"] is False
    assert "national importance" in m["sentence"]
    assert m["locale"] == "en" and m["offer"]
