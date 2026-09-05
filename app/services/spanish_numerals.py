"""A deterministic reading of Spanish spoken number words, as a HINT.

The N-400 interviewer lane hears identifiers (SSN, A-Number, phone, ZIP)
as Spanish number words: "seis dos siete, cuarenta y cuatro, noventa
dieciocho". The model split "noventa dieciocho" as 0 9 1 8 about half the
time across v14 and v15 even with a worked example in the prompt, and
each miss cost a nervous applicant a correction of her own number.

This module does the arithmetic the model kept getting wrong, and ONLY
that. It never rewrites what the applicant said: the client's
verbatim-evidence floor drops any fact whose cited words are not in the
current utterance, so the utterance stays byte-identical and this reading
rides beside it as its own line (`{{spoken_numerals}}`), which the prompt
tells the model to take digits from while quoting her words.

Scope is identifier speech: units, teens, tens with "y" plus a unit, the
fused twenties, and "cien"/"ciento" for a bare hundred. A compound is one
group ("cuarenta y cuatro" -> "44"); a tens word followed by a teen or
another tens word starts a NEW group ("noventa dieciocho" -> "90 18"),
which is exactly the split the model missed. Anything outside that
vocabulary is left alone, so a sentence with no number words yields no
hint at all.
"""

from __future__ import annotations

import re
import unicodedata

_UNITS = {"cero": 0, "uno": 1, "una": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4,
          "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9}
_TEENS = {"diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
          "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
          "veinte": 20, "veintiuno": 21, "veintiun": 21, "veintiuna": 21, "veintidos": 22,
          "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
          "veintisiete": 27, "veintiocho": 28, "veintinueve": 29}
_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
         "ochenta": 80, "noventa": 90}
_HUNDRED = {"cien": 100, "ciento": 100}
_ALL = set(_UNITS) | set(_TEENS) | set(_TENS) | set(_HUNDRED) | {"y"}


def _fold(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word.lower())
                   if unicodedata.category(c) != "Mn")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúüñ]+|\d+|[,.;]", text, flags=re.IGNORECASE)


def read_groups(text: str) -> list[tuple[str, str]]:
    """[(original words, digits)] for every run of number words in `text`.

    Runs are broken by punctuation and by any non-number word, and inside
    a run each group is one spoken value. Digits already spoken as digits
    ("627") pass through as their own group.
    """
    out: list[tuple[str, str]] = []
    toks = _tokens(text)
    i = 0
    while i < len(toks):
        raw = toks[i]
        w = _fold(raw)
        if raw.isdigit():
            out.append((raw, raw)); i += 1; continue
        if w in _UNITS or w in _TEENS or w in _HUNDRED:
            out.append((raw, str(_UNITS.get(w, _TEENS.get(w, _HUNDRED.get(w)))))); i += 1; continue
        if w in _TENS:
            # tens [y unit] is one compound; anything else starts a new group
            if i + 2 < len(toks) and _fold(toks[i + 1]) == "y" and _fold(toks[i + 2]) in _UNITS:
                out.append((" ".join(toks[i:i + 3]), str(_TENS[w] + _UNITS[_fold(toks[i + 2])]))); i += 3; continue
            out.append((raw, str(_TENS[w]))); i += 1; continue
        i += 1
    return out


def numeral_hint(text: str) -> str | None:
    """The line handed to the model, or None when there are no number words.

    Kept as `words = digits` pairs in spoken order so the model can see the
    grouping ("noventa dieciocho = 90 18" is two pairs, "noventa y ocho =
    98" is one) and still quote the original words.
    """
    groups = [(w, d) for w, d in read_groups(text) if not w.isdigit()]
    if not groups:
        return None
    return "; ".join(f"{w} = {d}" for w, d in groups)


INTERVIEWER_CALL_TYPE = "n400_interviewer_turn"
VARIABLE = "spoken_numerals"


def numeral_variables(call_type: str | None, locale: str | None, user_content: str | None) -> dict:
    """The extra prompt variable for one request, or {} when it does not apply.

    Gated to the interviewer lane and Spanish only: English speech of
    numbers is not the defect, and an unrelated lane must never receive a
    placeholder it does not declare.
    """
    if call_type != INTERVIEWER_CALL_TYPE or not (locale or "").lower().startswith("es"):
        return {}
    hint = numeral_hint(user_content or "")
    return {VARIABLE: hint} if hint else {}
