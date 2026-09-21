"""Jev against Haiku on the offer-reply judgment, both graded against hand labels.

    .venv/bin/python qa/jev_offer_reply_eval.py [--out qa/runs/jev-offer-reply-<date>.json]

Every case is SYNTHETIC and hand labelled before either model saw it. The
labels are the authority: each model is graded against them, never against
the other, because two models agreeing is not a fact about either.

Sends nothing but the offer line and the reply. Costs about a cent, nearly
all of it Haiku. Keys come from `.env` through Settings and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services import typesafe_judge as tj  # noqa: E402
from app.services.document_generation import (  # noqa: E402
    _CLASSIFIER_MODEL, _INTERPRETER_SYSTEM,
)

DOCX = "OFFER: a docx file for onboarding new people"
XLSX = "OFFER: a xlsx file tracking the launch risks"
LANE = ("OFFER: a xlsx file for the website relaunch (the offer presented two "
        "versions: the project status workbook, or a custom workbook)")

# (id, offer line, offered format, lane_choice, reply, expected)
# expected keys: confirm, format, style, version. Only keys PRESENT are graded,
# so a case grades the fields it was written to test.
CASES = [
    ("en_yes", DOCX, "docx", False, "yes", {"confirm": True, "format": "docx"}),
    ("en_go_ahead", DOCX, "docx", False, "sure, go ahead", {"confirm": True, "format": "docx"}),
    ("en_do_it", XLSX, "xlsx", False, "do it", {"confirm": True, "format": "xlsx"}),
    ("en_please", DOCX, "docx", False, "yes please, that would be great", {"confirm": True}),
    ("en_revise_xlsx", DOCX, "docx", False, "actually make it a spreadsheet", {"confirm": True, "format": "xlsx"}),
    ("en_revise_pdf", DOCX, "docx", False, "yes but as a PDF", {"confirm": True, "format": "pdf"}),
    ("en_revise_slides", DOCX, "docx", False, "go for it, slides would be better though", {"confirm": True, "format": "pptx"}),
    ("en_no", DOCX, "docx", False, "no", {"confirm": False}),
    ("en_no_thanks", DOCX, "docx", False, "no thanks", {"confirm": False}),
    # Live 2026-08-17: a decline that names file words, read as a fresh ask.
    ("en_live_decline_names_files", DOCX, "docx", False,
     "just answer here and chat, I don't need a word document or a workbook", {"confirm": False}),
    ("en_inline", XLSX, "xlsx", False, "a table in chat is fine", {"confirm": False}),
    ("en_show_here", XLSX, "xlsx", False, "just show me here", {"confirm": False}),
    ("en_unrelated", DOCX, "docx", False, "no, what time is the standup?", {"confirm": False}),
    ("en_unrelated_2", DOCX, "docx", False, "who owns the vendor contract?", {"confirm": False}),
    ("en_ambiguous", DOCX, "docx", False, "hmm, maybe later", {"confirm": False}),
    ("en_question_back", DOCX, "docx", False, "how long would that take?", {"confirm": False}),
    ("en_not_a_format_switch", DOCX, "docx", False, "no, not a spreadsheet either", {"confirm": False, "format": "docx"}),
    ("en_style_detailed", XLSX, "xlsx", False, "detailed please", {"confirm": True, "style": "detailed"}),
    ("en_style_simple", XLSX, "xlsx", False, "yes, the simple one", {"confirm": True, "style": "simple"}),
    ("en_style_none", XLSX, "xlsx", False, "yes", {"confirm": True, "style": None}),
    ("en_lane_workbook", LANE, "xlsx", True, "the status workbook", {"confirm": True, "version": "workbook"}),
    ("en_lane_gantt", LANE, "xlsx", True, "yes, the gantt one", {"confirm": True, "version": "workbook"}),
    ("en_lane_custom", LANE, "xlsx", True, "just build what I described", {"confirm": True, "version": "custom"}),
    ("en_lane_bare_yes", LANE, "xlsx", True, "yes", {"confirm": True, "version": None}),
    # Added AFTER run 1 (Jev 32/34: both misses were an acceptance made by
    # PICKING a version with no yes in it). Written before the rerun, so they
    # are held out from the criterion edit that run prompted.
    ("h_pick_detailed", XLSX, "xlsx", False, "the detailed version", {"confirm": True, "style": "detailed"}),
    ("h_pick_simple", XLSX, "xlsx", False, "keep it simple", {"confirm": True, "style": "simple"}),
    ("h_pick_custom", LANE, "xlsx", True, "custom", {"confirm": True, "version": "custom"}),
    ("h_pick_your_format", LANE, "xlsx", True, "use your format", {"confirm": True, "version": "workbook"}),
    ("h_neg_neither", LANE, "xlsx", True, "neither, forget it", {"confirm": False}),
    ("h_neg_which", LANE, "xlsx", True, "what's the difference between them?", {"confirm": False}),
    ("h_neg_detail_question", XLSX, "xlsx", False, "can you give me more detail on the second risk?", {"confirm": False}),
    ("h_es_pick", XLSX, "xlsx", False, "la versión detallada, por favor", {"confirm": True, "style": "detailed"}),
    ("es_si", DOCX, "docx", False, "sí, adelante", {"confirm": True, "format": "docx"}),
    ("es_claro", DOCX, "docx", False, "claro, hazlo", {"confirm": True}),
    ("es_revise", DOCX, "docx", False, "sí, pero mejor en una hoja de cálculo", {"confirm": True, "format": "xlsx"}),
    ("es_no", DOCX, "docx", False, "no gracias, solo respóndeme aquí en el chat", {"confirm": False}),
    ("es_unrelated", DOCX, "docx", False, "¿a qué hora es la reunión de mañana?", {"confirm": False}),
    ("ja_yes", DOCX, "docx", False, "はい、お願いします", {"confirm": True, "format": "docx"}),
    ("ja_no", DOCX, "docx", False, "いいえ、チャットで答えてください", {"confirm": False}),
    ("ja_revise", DOCX, "docx", False, "はい、でもスプレッドシートでお願いします", {"confirm": True, "format": "xlsx"}),
    ("fr_yes", DOCX, "docx", False, "oui, vas-y", {"confirm": True}),
    ("fr_no", DOCX, "docx", False, "non merci, réponds simplement ici", {"confirm": False}),
]


async def haiku(api_key: str, offer_line: str, reply: str, offered: str) -> tuple[dict, int]:
    import httpx
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            json={"model": _CLASSIFIER_MODEL, "max_tokens": 150,
                  "system": _INTERPRETER_SYSTEM,
                  "messages": [{"role": "user",
                                "content": f"{offer_line}\nUSER REPLY: {reply}"}]})
    r.raise_for_status()
    ms = int((time.monotonic() - start) * 1000)
    txt = r.json()["content"][0]["text"]
    p = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    # The same normalisation interpret_offer_reply applies.
    fmt = p.get("format") if p.get("format") in ("xlsx", "docx", "pptx", "pdf") else None
    return ({"confirm": p.get("confirm") is True, "format": fmt or offered,
             "style": p.get("style") if p.get("style") in ("simple", "detailed") else None,
             "version": p.get("version") if p.get("version") in ("workbook", "custom") else None}, ms)


def wrong_fields(verdict: dict, expected: dict) -> list[str]:
    return [k for k, v in expected.items() if verdict.get(k) != v]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()
    s = get_settings()
    if not s.typesafe_api_key or not s.anthropic_api_key:
        print("needs CZ_TYPESAFE_API_KEY and CZ_ANTHROPIC_API_KEY in .env")
        return 2
    rows = []
    for cid, offer_line, offered, lane, reply, expected in CASES:
        j = await tj.judge_offer_reply(s.typesafe_api_key, offer_line, reply, offered, lane)
        h, h_ms = await haiku(s.anthropic_api_key, offer_line, reply, offered)
        rows.append({"id": cid, "reply": reply, "expected": expected,
                     "jev": j["verdict"], "jev_conf": j["confidence"], "jev_ms": j["ms"],
                     "jev_input_tokens": j["input_tokens"],
                     "jev_wrong": wrong_fields(j["verdict"], expected),
                     "haiku": h, "haiku_ms": h_ms,
                     "haiku_wrong": wrong_fields(h, expected)})
    n = len(rows)
    print(f"{n} cases, hand labels are the authority\n")
    for who in ("jev", "haiku"):
        bad = [r for r in rows if r[f"{who}_wrong"]]
        ms = [r[f"{who}_ms"] for r in rows]
        print(f"{who:6} right on {n - len(bad)}/{n}   median {int(statistics.median(ms))}ms  "
              f"min {min(ms)}  max {max(ms)}")
        for r in bad:
            extra = f"  conf={r['jev_conf']}" if who == "jev" else ""
            print(f"    WRONG {r['id']}: {r[f'{who}_wrong']} got {r[who]}{extra}")
    print(f"\njev input tokens total: {sum(r['jev_input_tokens'] or 0 for r in rows)}")
    if args.out:
        Path(args.out).write_text(json.dumps(
            {"model_jev": tj.MODEL, "model_haiku": _CLASSIFIER_MODEL,
             "floor": tj.CONFIDENCE_FLOOR, "rows": rows}, ensure_ascii=False, indent=1))
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
