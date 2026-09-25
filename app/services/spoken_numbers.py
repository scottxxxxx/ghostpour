"""Spoken number words written as digits, in English, Spanish and Portuguese.

A Python port of the N-400 client's `SpokenDigits` as specified in the
auditor's Task 9 (N400 App qa/N400-AGENT-TASKS-2026-09-24.md), and graded on
that task's vectors C1 to C13 verbatim. GhostPour does not rewrite what the
applicant said with it: `n400_pii_leak` uses it to see an identifier that
reached us unmasked, so the two readers must agree on what counts as a
dictated number, and any disagreement is a leak one side cannot see.

The rules, from the task:
  * units are one digit, teens two ("diez" to "diecinueve", the fused
    "veinte" to "veintinueve", "ten" to "nineteen", "dez" to "dezenove");
  * a tens word followed by a unit is ONE group, with the joiner ("y",
    "e") optional: "cuarenta y cuatro" and "cuarenta cuatro" are both 44.
    (GP's older `spanish_numerals.read_groups` reads the elided form as
    40 4; the client's 44 is the agreed reading here.)
  * a tens word followed by a teen or another tens word stands alone as two
    digits: "noventa dieciocho" is 90 18;
  * the joiner joins ONLY tens and a unit; anywhere else it ends the run;
  * hundreds and thousands are not vocabulary and end a run;
  * a run is rewritten only when it PRODUCES at least three digits, so
    "cuarenta y cuatro años" (two) is left alone.
Separators inside a run are whitespace, commas and hyphens. The client's
"oh" and "double"/"triple" rules are NOT ported: no vector exercises them.
"""

from __future__ import annotations

import re
import unicodedata

_UNITS = {}
for _words in (["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
               ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"],
               ["zero", "um", "dois", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove"]):
    for _d, _w in enumerate(_words):
        _UNITS[_w] = str(_d)

_TEENS = {
    **{w: str(10 + i) for i, w in enumerate(
        ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"])},
    **{w: str(10 + i) for i, w in enumerate(
        ["diez", "once", "doce", "trece", "catorce", "quince", "dieciseis",
         "diecisiete", "dieciocho", "diecinueve"])},
    **{w: str(20 + i) for i, w in enumerate(
        ["veinte", "veintiuno", "veintidos", "veintitres", "veinticuatro", "veinticinco",
         "veintiseis", "veintisiete", "veintiocho", "veintinueve"])},
    **{w: str(10 + i) for i, w in enumerate(
        ["dez", "onze", "doze", "treze", "catorze", "quinze", "dezesseis", "dezessete",
         "dezoito", "dezenove"])},
    "quatorze": "14",
}

_TENS = {
    **{w: 20 + 10 * i for i, w in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"])},
    **{w: 30 + 10 * i for i, w in enumerate(
        ["treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"])},
    **{w: 20 + 10 * i for i, w in enumerate(
        ["vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"])},
}

# THE MONTH GUARD (Task 11, 2026-09-24). A day next to a year is one run, so
# without this "March eight nineteen seventy four" became "March 81974". A run
# right after a month name, or after a month and ONE ordinal or connector
# word, is left in words. It ENDS at the first punctuation inside the run, so
# a number said right after a date is still read: "March eight nineteen
# seventy four, six two seven four four" keeps the date and gives 62744 (D14,
# which was GP's own false ten_digits in the leak counter). "May" guards only
# when capitalised, since "may" is a verb.
_MONTHS = {
    "january", "february", "march", "april", "june", "july", "august", "september",
    "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
    "setiembre", "octubre", "noviembre", "diciembre",
    "janeiro", "fevereiro", "marco", "maio", "junho", "julho", "setembro", "outubro",
    "novembro", "dezembro",
}
_ORDINALS = {
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
    "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
    "seventeenth", "eighteenth", "nineteenth", "twentieth", "thirtieth",
    "primero", "primeiro",
}
_CONNECTORS = {"the", "of", "de", "del", "do", "da"}

_JOINERS = {"y", "e"}
_SEPARATORS = {",", "-"}
MIN_DIGITS = 3

_TOKEN = re.compile(r"[^\W\d_]+|\d+|[^\w\s]")


def _fold(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word.lower())
                   if unicodedata.category(c) != "Mn")


def _is_number_word(w: str) -> bool:
    return w in _UNITS or w in _TEENS or w in _TENS


def _is_month(text: str, tok: tuple) -> bool:
    if tok[2] == "may":
        return text[tok[0]] == "M"
    return tok[2] in _MONTHS


def _after_a_month(text: str, toks: list, i: int) -> bool:
    """Is the run starting at toks[i] right after a month, or a month and one
    ordinal or connector word? Punctuation in between does not count."""
    words = [t for t in toks[:i] if t[2][0].isalnum()]
    if not words:
        return False
    if _is_month(text, words[-1]):
        return True
    return (words[-1][2] in _ORDINALS or words[-1][2] in _CONNECTORS) \
        and len(words) > 1 and _is_month(text, words[-2])


def written(text: str) -> str:
    """`text` with every dictated run of number words written as digits.
    Anything under the threshold is returned byte for byte."""
    toks = [(m.start(), m.end(), _fold(m.group(0))) for m in _TOKEN.finditer(text or "")]
    out, cursor, i = [], 0, 0
    while i < len(toks):
        if not _is_number_word(toks[i][2]):
            i += 1
            continue
        guarded = _after_a_month(text, toks, i)
        start, digits, end = toks[i][0], [], toks[i][1]
        j = i
        while j < len(toks):
            w = toks[j][2]
            if w in _TENS:
                # tens, optionally a hyphen or a joiner, then a unit: one
                # group. A comma between them is a pause between groups
                # ("noventa, cuatro" is 90 4), so only the hyphen is skipped.
                k = j + 1
                if k < len(toks) and toks[k][2] == "-":
                    k += 1
                if k < len(toks) and toks[k][2] in _JOINERS:
                    k += 1
                if k < len(toks) and toks[k][2] in _UNITS:
                    digits.append(str(_TENS[w] + int(_UNITS[toks[k][2]])))
                    end, j = toks[k][1], k + 1
                else:
                    digits.append(str(_TENS[w]))
                    end, j = toks[j][1], j + 1
            elif w in _TEENS:
                digits.append(_TEENS[w])
                end, j = toks[j][1], j + 1
            elif w in _UNITS:
                digits.append(_UNITS[w])
                end, j = toks[j][1], j + 1
            else:
                break
            # A separator continues the run only when a number word follows it,
            # and never a guarded one: the guard ends at the first punctuation.
            if guarded:
                continue
            k = j
            while k < len(toks) and toks[k][2] in _SEPARATORS:
                k += 1
            if k < len(toks) and _is_number_word(toks[k][2]):
                j = k
                continue
            break
        produced = "".join(digits)
        if len(produced) >= MIN_DIGITS and not guarded:
            out.append(text[cursor:start])
            out.append(produced)
            cursor = end
        i = max(j, i + 1)
    out.append(text[cursor:])
    return "".join(out)
