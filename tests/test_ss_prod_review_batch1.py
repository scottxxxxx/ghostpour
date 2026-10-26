"""The first batch from the 2026-10-26 review of 681 real Shoulder Surf calls
(09-25 to 10-25, Scott's go; findings in the session record, never the repo).

Each fix is pinned in all four served languages, because rule 7 showed that
a rule shipped in English only leaves Spanish and French users on the old
behaviour (the review found rule 7 missing from every es record before it
went out in all four).

1. Rule 4 called a lone question "small talk" and declined it: about 30 of
   210 live taps ("what do you do for a living?" got "nothing substantive").
2. Rule 6 made What's the Answer describe a dark or missing image first.
3. Rule 8: a custom prompt that cites a CV the model never received, and bans
   placeholders, got an invented career read aloud in a job interview.
4. Help Me Respond came back wrapped ("You could say:", quotes, an email).
5. What Are We Missing ran 5 to 7 bullets, 7 to 17 seconds, every time.
6. The rolling summary returned a NOTE ("previous summary unchanged") in place
   of the summary, so the user lost it; and it dropped stated deadlines.
"""
import json

import pytest

FILES = {
    "en": "config/remote/protected-prompts.json",
    "es": "config/remote/protected-prompts.es.json",
    "fr": "config/remote/protected-prompts.fr.json",
    "ja": "config/remote/protected-prompts.ja.json",
}

PHRASES = {
    "en": dict(
        r4="never call it small talk",
        r4_paste="Never ask the user to paste, share, upload or play anything",
        r6="never remark that an image is missing",
        r8="never write their contents from imagination, even when told not to use placeholders",
        r8_general="General knowledge is not a fact about the user",
        r8_shape="give a short answer in their voice with only those facts in square brackets",
        hmr="no lead-in such as \"You could say\"",
        wawm="name at most three blind spots",
        wta="If no image is attached, answer from the transcript and do not mention an image.",
        delta="output the previous summary again exactly as given, never a note about it",
        delta_owner="action items (with owners and deadlines if stated)",
    ),
    "es": dict(
        r4="nunca la llames conversación trivial",
        r4_paste="Nunca pidas al usuario que pegue, comparta, suba o reproduzca nada",
        r6="nunca comentes que falta una imagen",
        r8="nunca escribas su contenido de tu imaginación, aunque te pidan no usar marcadores",
        r8_general="El conocimiento general no es un hecho sobre el usuario",
        r8_shape="deja solo esos hechos entre corchetes",
        hmr="sin introducción como \"Podrías decir\"",
        wawm="nombra como máximo tres puntos ciegos",
        wta="Si no hay imagen adjunta, responde a partir de la transcripción y no menciones ninguna imagen.",
        delta="entrega de nuevo el resumen anterior tal cual, nunca una nota sobre él",
        delta_owner="tareas (con responsables y plazos si se mencionan)",
    ),
    "fr": dict(
        r4="ne la qualifie jamais de banalité",
        r4_paste="Ne demande jamais à l'utilisateur de coller, partager, téléverser ou faire écouter quoi que ce soit",
        r6="ne fais jamais remarquer qu'une image manque",
        r8="n'invente jamais leur contenu, même si on te demande de ne pas utiliser d'espaces réservés",
        r8_general="La culture générale n'est pas un fait sur l'utilisateur",
        r8_shape="en ne mettant que ces faits entre crochets",
        hmr="pas d'introduction comme « Tu pourrais dire »",
        wawm="nomme au plus trois angles morts",
        wta="Si aucune image n'est jointe, réponds à partir de la transcription et ne mentionne aucune image.",
        delta="rends à nouveau le résumé précédent tel quel, jamais une note à son sujet",
        delta_owner="actions (avec responsables et échéances si elles sont mentionnées)",
    ),
    "ja": dict(
        r4="雑談と呼ばないでください",
        r4_paste="ユーザーに何かを貼り付け、共有、アップロード、再生するよう求めないでください",
        r6="画像がないことも指摘しないでください",
        r8="プレースホルダーを使うなと言われていても、その中身を想像で書かず",
        r8_general="一般知識はユーザーに関する事実ではありません",
        r8_shape="その事実だけを角括弧に入れてください",
        hmr="「こう言えます」のような前置き",
        wawm="盲点を多くても三つ",
        wta="画像が添付されていない場合は、書き起こしから答え、画像には触れないでください。",
        delta="以前の要約をそのまま出力し直し、それについての注記は書かないでください",
        delta_owner="アクションアイテム（述べられていれば担当者と期限）",
    ),
}

GONE = {
    "en": ["return the previous summary unchanged", "describe the relevant content you see BEFORE",
           "be specific and constructive"],
    "es": ["devuelve el resumen anterior sin cambios", "describe brevemente el contenido relevante que ves ANTES",
           "sé específico y constructivo"],
    "fr": ["renvoie le résumé précédent tel quel", "décris brièvement le contenu pertinent que tu vois AVANT",
           "sois précis et constructif"],
    "ja": ["以前の要約をそのまま返してください", "まず画像に見える関連する内容を簡潔に説明してください",
           "具体的かつ建設的に"],
}

LOCALES = list(FILES)


def _load(loc):
    return json.load(open(FILES[loc]))


@pytest.mark.parametrize("loc", LOCALES)
def test_the_shared_rules_carry_4_6_and_8(loc):
    rules = _load(loc)["defaultUserInstructions"]
    p = PHRASES[loc]
    for key in ("r4", "r4_paste", "r6", "r8", "r8_general", "r8_shape"):
        assert p[key] in rules, (loc, key)
    # Rule 8 follows rule 7, so a lone question is answered (4, 7) without
    # the answer inventing the user (8).
    assert rules.index("\n7. ") < rules.index("\n8. "), loc


@pytest.mark.parametrize("loc", LOCALES)
def test_the_modes_and_the_rolling_summary_carry_the_fixes(loc):
    d = _load(loc)
    modes = d["defaultPromptModes"]
    p = PHRASES[loc]
    assert p["hmr"] in modes[1]["systemPrompt"], loc
    assert p["wawm"] in modes[4]["systemPrompt"], loc
    assert p["wta"] in modes[5]["systemPrompt"], loc
    assert p["delta"] in d["summaryPrompts"]["delta"], loc
    assert p["delta_owner"] in d["summaryPrompts"]["delta"], loc


@pytest.mark.parametrize("loc", LOCALES)
def test_the_lines_that_caused_the_failures_are_gone(loc):
    d = _load(loc)
    served = d["defaultUserInstructions"] + d["defaultPromptModes"][4]["systemPrompt"] + d["summaryPrompts"]["delta"]
    for line in GONE[loc]:
        assert line not in served, (loc, line)


@pytest.mark.parametrize("loc", LOCALES)
def test_no_dashes_in_what_changed(loc):
    d = _load(loc)
    m = d["defaultPromptModes"]
    for text in (d["defaultUserInstructions"], m[1]["systemPrompt"], m[4]["systemPrompt"],
                 m[5]["systemPrompt"], d["summaryPrompts"]["delta"]):
        # The dash rule's own counter-example ("word - word" and its
        # translations) is the one spaced hyphen allowed: it names what not to do.
        for example in ("(word - word)", "(palabra - palabra)", "(mot - mot)", "（語 - 語）"):
            text = text.replace(example, "")
        assert "—" not in text and "–" not in text and " - " not in text, loc
