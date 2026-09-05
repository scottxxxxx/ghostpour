from app.services.spanish_numerals import numeral_hint, read_groups


def test_the_ssn_that_was_misread_twice():
    """conf-es-v14 turn 21 and conf-es-v15b turn 22, the same utterance."""
    text = "seis dos siete, cuarenta y cuatro, noventa dieciocho"
    assert [d for _, d in read_groups(text)] == ["6", "2", "7", "44", "90", "18"]
    assert numeral_hint(text) == "seis = 6; dos = 2; siete = 7; cuarenta y cuatro = 44; noventa = 90; dieciocho = 18"


def test_a_tens_word_with_y_is_one_group_and_without_y_starts_a_new_one():
    assert [d for _, d in read_groups("noventa y ocho")] == ["98"]
    assert [d for _, d in read_groups("noventa ocho")] == ["90", "8"]
    assert [d for _, d in read_groups("noventa dieciocho")] == ["90", "18"]


def test_fused_twenties_and_accents_fold():
    assert [d for _, d in read_groups("veintitrés dieciséis")] == ["23", "16"]
    assert [d for _, d in read_groups("VEINTIUNO cero")] == ["21", "0"]


def test_digits_spoken_as_digits_pass_through_and_are_not_hinted():
    assert [d for _, d in read_groups("627 44 9018")] == ["627", "44", "9018"]
    assert numeral_hint("627 44 9018") is None


def test_no_number_words_means_no_hint():
    assert numeral_hint("Mi esposo es ciudadano y llevamos tres años casados") == "tres = 3"
    assert numeral_hint("No tengo segundo nombre.") is None


def test_words_are_quoted_as_spoken_so_provenance_can_still_match():
    hint = numeral_hint("mi código postal es siete siete cero cero seis")
    assert hint.startswith("siete = 7; siete = 7; cero = 0")


def test_the_variable_is_gated_to_the_interviewer_lane_and_spanish():
    from app.services.spanish_numerals import numeral_variables
    text = "cuarenta y cuatro"
    assert numeral_variables("n400_interviewer_turn", "es", text) == {"spoken_numerals": "cuarenta y cuatro = 44"}
    assert numeral_variables("n400_interviewer_turn", "es-MX", text) == {"spoken_numerals": "cuarenta y cuatro = 44"}
    assert numeral_variables("n400_interviewer_turn", "en", text) == {}
    assert numeral_variables("n400_interview_turn", "es", text) == {}
    assert numeral_variables("n400_interviewer_turn", "es", "no tengo segundo nombre") == {}


def test_the_route_merges_the_variable_into_the_assembler_call():
    src = open("app/routers/chat.py").read()
    i = src.index("_numeral_variables(")
    assert "variables={**dict(body.metadata or {}), **_numeral_variables(" in src[i - 80:i + 40]


def test_the_served_template_declares_the_placeholder_as_optional():
    import json
    cfg = json.load(open("config/remote/n400/interviewer-turn.json"))
    assert "{{spoken_numerals}}" in cfg["userPromptTemplate"]
    assert "spoken_numerals" in cfg["optionalVariables"]
    assert "spoken_numerals" not in cfg["requiredVariables"]
