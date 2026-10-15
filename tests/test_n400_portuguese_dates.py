"""Portuguese dates and years said in words (2026-10-14).

The date-shift free check: "Nasci em doze de abril de mil novecentos e oitenta
e três" lost the whole date of birth. The day guard knew no Portuguese day
word, so the day read as invented, and the partial it deferred to came out
empty because only a year in digits was read.
"""

import json

from app.services.n400_interviewer_guard import (
    _date_said, _day_was_spoken, _years_in_words, defer_dates_with_unspoken_day,
)

PT_DOB = "Nasci em doze de abril de mil novecentos e oitenta e três."


def _turn(field_id, value, said):
    return json.dumps({"facts": [{"field_id": field_id, "value": value, "value_type": "date",
                                  "provenance": {"utterance": said}}],
                       "deferred": []})


def test_portuguese_date_of_birth_in_words_is_kept():
    out, moved = defer_dates_with_unspoken_day(_turn("p1.date_of_birth", "1983-04-12", PT_DOB), PT_DOB)
    assert moved == []
    assert json.loads(out)["facts"][0]["value"] == "1983-04-12"


def test_invented_day_keeps_the_month_and_a_year_said_in_words():
    # "outubro" on purpose: "abril" is also Spanish and cannot see the Portuguese list.
    said = "Foi em outubro de mil novecentos e oitenta e três."
    _, moved = defer_dates_with_unspoken_day(_turn("p1.date_of_birth", "1983-10-12", said), said)
    assert [m["partial_value"] for m in moved] == ["1983-10"]


def test_english_year_in_words_keeps_the_year_partial():
    said = "Sometime in nineteen seventy-seven, I think."
    _, moved = defer_dates_with_unspoken_day(_turn("p1.date_of_birth", "1977-06-01", said), said)
    assert [m["partial_value"] for m in moved] == ["1977"]


def test_every_year_form_in_the_contract_reads():
    cases = {
        "nineteen eighty-five": "1985", "twenty nineteen": "2019", "twenty ten": "2010",
        "nineteen oh five": "1905", "nineteen hundred": "1900", "two thousand and one": "2001",
        "two thousand twenty-two": "2022", "two thousand six": "2006",
        "mil novecientos ochenta y cinco": "1985", "dos mil veintidós": "2022",
        "mil novecentos e oitenta e cinco": "1985", "dois mil e vinte e dois": "2022",
    }
    for words, year in cases.items():
        assert _years_in_words(words) == {year}, words


def test_a_day_in_words_is_not_a_year():
    assert _years_in_words("on the twenty second") == set()
    assert _years_in_words("twenty two") == set()


def test_carried_date_in_portuguese_words_passes_the_floor():
    assert _date_said("1983-04-12", PT_DOB.lower())
    assert _date_said("2014-03", "nos casamos em marco de dois mil e catorze")


def test_carried_day_is_not_read_out_of_the_year():
    # "seven" belongs to the year, so the 7th was never said.
    assert not _date_said("1977-03-07", "march third, nineteen seventy seven")


def test_portuguese_day_words_and_um_alone():
    assert _day_was_spoken(12, "doze de abril")
    assert _day_was_spoken(23, "vinte e três de maio")
    assert not _day_was_spoken(1, "um, I think it was April")
