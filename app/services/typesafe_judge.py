"""TypeSafe (Jev) typed judgments, and the shadow that grades them.

Jev answers typed questions about a `state`: `noul` (a yes/no probability),
`choice` (one of our options, the full distribution and a confidence) and
`score`. It writes no text, so it can only replace a model call whose whole
output is a decision. Measured from here on 2026-09-17: 0.25 to 0.57s a
call, median 0.29s, against roughly 900ms for the Haiku judges it may
replace. Input $0.042 per million tokens, output free.

TWO RULES THIS MODULE KEEPS

1. NARROWEST STATE. TypeSafe is a processor for whatever we send, so a
   question gets the words it needs to judge and nothing else: never a
   transcript, a case or an attachment.

2. A FINE DISTINCTION IS A CHOICE WITH A FLOOR, NEVER A NOUL. The scorer
   spike put a defect at 0.70 and a sentence we had RULED acceptable at
   0.62 on the same yes/no question: eight points apart and both stable, so
   no threshold separates them. As a `choice` the same pair came back a near
   tie with LOW CONFIDENCE, which is the honest answer. So every decision
   here is a choice, and a label under `CONFIDENCE_FLOOR` is not used: it
   falls to the safe direction, exactly as a parse failure does on the
   Haiku path.

SHADOW. `shadow_offer_reply` runs the Jev judgment beside the Haiku one and
logs whether the verdicts agree. It never changes what the turn does and
the turn never waits on it: the comparison is logged from the task's own
completion, after the response has moved on. The log line carries verdicts,
confidences and timings and NO user text.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger("ghostpour.typesafe_judge")

API_URL = "https://api.typesafe.ai/v1/systemone"
# Pinned, not `jev-latest`: an alias moves with their releases and would
# change answers under a comparison that is supposed to hold still.
MODEL = "jev-1.13.0"
TIMEOUT_SECONDS = 5.0
CONFIDENCE_FLOOR = 0.5

_FORMATS = ("xlsx", "docx", "pptx", "pdf")


async def ask(api_key: str, state, questions: dict,
              timeout: float = TIMEOUT_SECONDS) -> dict:
    """One request. Returns the parsed body; raises on any failure so the
    caller decides which direction is safe."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": MODEL, "state": state, "questions": questions},
        )
    resp.raise_for_status()
    return resp.json()


def offer_reply_questions(lane_choice: bool) -> dict:
    """The offer-reply judgment as typed questions, one per output field of
    `interpret_offer_reply`. They run in parallel and cannot see each
    other, so each states its own premise."""
    questions = {
        "confirm": {
            "type": "choice",
            "instructions": (
                "The assistant offered to build a file, described in `offer`, "
                "and the user answered with `user_reply`. Does the reply "
                "accept the offer? Judge only the user's own words."),
            "criteria": {
                "accept": (
                    "The reply agrees to have the file built. Casual agreement "
                    "in any language counts (yes, go ahead, sure, do it), and "
                    "so does agreement that changes the format or asks for a "
                    "tweak (actually make it a spreadsheet). Picking one of "
                    "the versions on offer is also acceptance, even with no "
                    "yes in it (detailed please, the simple one, the status "
                    "workbook, just build what I described)."),
                "decline": (
                    "The reply turns the file down, or asks for the content "
                    "inline instead (just show me here, a table in chat is "
                    "fine, just answer in chat). Naming a kind of file while "
                    "declining it is still a decline."),
                "other": (
                    "The reply is an unrelated question, is ambiguous, or "
                    "does not address the offer."),
            },
        },
        "format": {
            "type": "choice",
            "instructions": (
                "Does `user_reply` ask for a file format different from the "
                "one named in `offer`? Pick the format the user's own words "
                "ask for, or keep when they name none."),
            "criteria": {
                "keep": "The reply names no file format, or names the offered one.",
                "xlsx": "The reply asks for a spreadsheet or Excel file.",
                "docx": "The reply asks for a Word document.",
                "pptx": "The reply asks for a presentation or slides.",
                "pdf": "The reply asks for a PDF.",
            },
        },
        "style": {
            "type": "choice",
            "instructions": (
                "Some offers present a simple version and a detailed version. "
                "Do the user's own words in `user_reply` choose one of them?"),
            "criteria": {
                "none": "The reply does not choose between simple and detailed.",
                "simple": "The reply chooses the simple version (the simple one).",
                "detailed": "The reply chooses the detailed version (detailed please).",
            },
        },
    }
    if lane_choice:
        questions["version"] = {
            "type": "choice",
            "instructions": (
                "The offer presented two versions: the project status "
                "workbook, or a custom workbook. Do the user's own words in "
                "`user_reply` choose one of them?"),
            "criteria": {
                "none": "The reply does not choose between the two versions.",
                "workbook": (
                    "The reply chooses the project status workbook: words like "
                    "status workbook, workbook, status, your format, recipe, "
                    "the structured one, the gantt one."),
                "custom": (
                    "The reply chooses the custom workbook: words like custom, "
                    "ad hoc, my version, just build what I described."),
            },
        }
    return questions


def _label(answers: dict, qid: str) -> tuple[str | None, float]:
    """(label, confidence), and the label is None under the floor."""
    a = answers.get(qid) or {}
    conf = float(a.get("confidence") or 0.0)
    choice = a.get("choice")
    return (choice if conf >= CONFIDENCE_FLOOR else None), conf


def read_offer_reply(body: dict, offered_format: str) -> tuple[dict, dict]:
    """(verdict in `interpret_offer_reply`'s shape, confidence per field).

    Every field falls to the safe direction under the floor: no confirm, the
    offered format, no style, no version. Those are the same values the
    Haiku path returns when it cannot parse a reply.
    """
    answers = body.get("answers") or {}
    confirm, c_conf = _label(answers, "confirm")
    fmt, f_conf = _label(answers, "format")
    style, s_conf = _label(answers, "style")
    version, v_conf = _label(answers, "version")
    confirmed = confirm == "accept"
    verdict = {
        "confirm": confirmed,
        # Haiku's contract: the revised format only rides a confirm.
        "format": fmt if (confirmed and fmt in _FORMATS) else offered_format,
        "style": style if style in ("simple", "detailed") else None,
        "version": version if version in ("workbook", "custom") else None,
    }
    return verdict, {"confirm": round(c_conf, 3), "format": round(f_conf, 3),
                     "style": round(s_conf, 3), "version": round(v_conf, 3)}


async def judge_offer_reply(api_key: str, offer_line: str, reply: str,
                            offered_format: str, lane_choice: bool) -> dict:
    """The Jev verdict plus what the shadow logs about it. Raises on failure."""
    start = time.monotonic()
    body = await ask(api_key, {"offer": offer_line, "user_reply": reply},
                     offer_reply_questions(lane_choice))
    verdict, confidence = read_offer_reply(body, offered_format)
    return {"verdict": verdict, "confidence": confidence,
            "ms": int((time.monotonic() - start) * 1000),
            "input_tokens": (body.get("usage") or {}).get("input_tokens")}


# A task nobody holds a reference to can be collected mid-flight.
_LIVE: set[asyncio.Task] = set()

_FIELDS = ("confirm", "format", "style", "version")


def shadow_offer_reply(api_key: str, offer_line: str, reply: str,
                       offered_format: str, lane_choice: bool):
    """Start the Jev judgment now, and return `finish(haiku_verdict,
    haiku_ms, haiku_ok)`. Calling finish never waits: it arranges for the
    comparison to be logged when Jev answers, which may be after the turn
    has responded. Returns None when there is no key, so a caller can
    treat "not configured" and "off" the same way.
    """
    if not api_key:
        return None
    task = asyncio.ensure_future(
        judge_offer_reply(api_key, offer_line, reply, offered_format, lane_choice))
    _LIVE.add(task)
    task.add_done_callback(_LIVE.discard)

    def finish(haiku: dict, haiku_ms: int, haiku_ok: bool) -> None:
        def _log(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            err = t.exception()
            if err is not None:
                logger.warning("offer_reply_shadow jev_failed=%s haiku_ms=%d",
                               type(err).__name__, haiku_ms)
                return
            out = t.result()
            jev = out["verdict"]
            diff = [f for f in _FIELDS if jev.get(f) != haiku.get(f)]
            # haiku_ok=False means Haiku FAILED OPEN, so its verdict is the
            # fail-open default rather than a judgment. A disagreement on
            # such a row says nothing about Jev and must be countable apart.
            logger.info(
                "offer_reply_shadow agree=%s diff=%s haiku_ok=%s haiku=%s jev=%s "
                "jev_conf=%s haiku_ms=%d jev_ms=%d jev_input_tokens=%s",
                not diff, ",".join(diff) or "-", haiku_ok,
                _compact(haiku), _compact(jev), out["confidence"],
                haiku_ms, out["ms"], out["input_tokens"])
        task.add_done_callback(_log)

    return finish


def _compact(v: dict) -> str:
    return "/".join(str(v.get(f)) for f in _FIELDS)
