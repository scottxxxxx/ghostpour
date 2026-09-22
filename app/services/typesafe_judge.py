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

MODES (`CZ_TYPESAFE_MODE`)

shadow   `shadow_offer_reply` runs the Jev judgment beside the Haiku one and
         records whether the verdicts agree. It never changes what the turn
         does and the turn never waits on it.

primary  `try_offer_reply` lets Jev decide. It hands the decision back to
         Haiku (returns None) in three cases, and they are NOT the same
         thing:
           error / timeout   Jev did not answer. This is a FAILURE.
           low_confidence    Jev answered and was not sure. This is Jev
                             WORKING: a near tie is its honest answer to a
                             hard reply, so it is never counted as a failure.
           breaker_open      Jev was not asked at all.

THE BREAKER. More than `BREAKER_THRESHOLD` failures in a row and Jev is
skipped for `BREAKER_COOLDOWN_SECONDS`, so a TypeSafe outage costs three slow
turns and not every turn. After the cooldown the next call is a probe: a
success closes the breaker, a failure starts another cooldown. One success
anywhere resets the run. It lives in process memory, which is coherent
because prod runs ONE uvicorn worker (Dockerfile), and a restart closes it,
which is the right default for a dependency that may have recovered.

THE RECORD. Every attempt writes one `typesafe_calls` row, including the
attempts the breaker skipped, because a dashboard that only counts the calls
that were made cannot show an outage. Rows and log lines carry verdicts,
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
# On the critical path the wait is the user's. A normal call is 0.3s and the
# slowest of 76 measured was 0.77s, so 2s is a Jev that is not coming back.
PRIMARY_TIMEOUT_SECONDS = 2.0
CONFIDENCE_FLOOR = 0.5
BREAKER_THRESHOLD = 2          # opens when failures in a row EXCEED this
BREAKER_COOLDOWN_SECONDS = 300.0
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000   # output tokens are free

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
                            offered_format: str, lane_choice: bool,
                            timeout: float = TIMEOUT_SECONDS) -> dict:
    """The Jev verdict plus what gets recorded about it. Raises on failure."""
    start = time.monotonic()
    body = await ask(api_key, {"offer": offer_line, "user_reply": reply},
                     offer_reply_questions(lane_choice), timeout=timeout)
    verdict, confidence = read_offer_reply(body, offered_format)
    return {"verdict": verdict, "confidence": confidence,
            "ms": int((time.monotonic() - start) * 1000),
            "input_tokens": (body.get("usage") or {}).get("input_tokens")}


# A task nobody holds a reference to can be collected mid-flight.
_LIVE: set[asyncio.Task] = set()


def _hold(coro) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _LIVE.add(task)
    task.add_done_callback(_LIVE.discard)
    return task


# --- the breaker -------------------------------------------------------------

class Breaker:
    def __init__(self, threshold: int = BREAKER_THRESHOLD,
                 cooldown: float = BREAKER_COOLDOWN_SECONDS, clock=time.monotonic):
        self.threshold, self.cooldown, self._clock = threshold, cooldown, clock
        self.consecutive_failures = 0
        self.opened_at: float | None = None
        self.opened_count = 0
        self.last_error: str | None = None

    def allow(self) -> bool:
        """True when Jev may be asked: closed, or open with the cooldown
        served (that call is the probe)."""
        if self.opened_at is None:
            return True
        return self._clock() - self.opened_at >= self.cooldown

    def success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def failure(self, error_type: str) -> None:
        self.consecutive_failures += 1
        self.last_error = error_type
        if self.consecutive_failures > self.threshold:
            if self.opened_at is None:
                self.opened_count += 1
                logger.warning(
                    "typesafe_breaker_opened failures_in_a_row=%d last_error=%s "
                    "cooldown_s=%d", self.consecutive_failures, error_type,
                    int(self.cooldown))
            # A failed probe lands here too and restarts the cooldown.
            self.opened_at = self._clock()

    def state(self) -> dict:
        open_ = self.opened_at is not None
        wait = max(0.0, self.cooldown - (self._clock() - self.opened_at)) if open_ else 0.0
        return {"open": open_, "consecutive_failures": self.consecutive_failures,
                "threshold": self.threshold, "retry_in_seconds": int(wait),
                "opened_count_since_boot": self.opened_count,
                "last_error": self.last_error}


breaker = Breaker()


# --- the record ----------------------------------------------------------------

async def record(row: dict, app_id: str | None) -> None:
    """One `typesafe_calls` row on its OWN connection, because the shadow
    finishes after the request's connection has closed. Never raises: the
    record of a judgment must not be able to fail the turn it describes."""
    try:
        import uuid
        from datetime import datetime, timezone

        import aiosqlite

        from app import database
        tokens = row.get("input_tokens")
        async with aiosqlite.connect(database._db_path) as db:
            await db.execute(
                """INSERT INTO typesafe_calls
                   (id, created_at, app_id, judgment, mode, outcome, error_type,
                    fell_back, jev_ms, fallback_ms, input_tokens, cost_usd,
                    confidence, agreed, facts_checked, facts_marked)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, datetime.now(timezone.utc).isoformat(), app_id,
                 row["judgment"], row["mode"], row["outcome"], row.get("error_type"),
                 1 if row.get("fell_back") else 0, row.get("jev_ms"),
                 row.get("fallback_ms"), tokens,
                 round(tokens * USD_PER_INPUT_TOKEN, 8) if tokens else None,
                 row.get("confidence"),
                 None if row.get("agreed") is None else (1 if row["agreed"] else 0),
                 row.get("facts_checked"), row.get("facts_marked")))
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("typesafe_call_not_recorded %s: %s", type(e).__name__, e)


def record_later(row: dict, app_id: str | None) -> None:
    """Record without making the turn wait for the write."""
    _hold(record(row, app_id))


# --- one guarded call, for any judgment -----------------------------------------

async def guarded_ask(api_key: str, state, questions: dict, *, judgment: str,
                      mode: str, timeout: float = PRIMARY_TIMEOUT_SECONDS) -> tuple[dict | None, dict]:
    """(body, row) through the SAME breaker every judgment shares: TypeSafe
    being down is one fact, so one judgment's failures spare the others the
    wait. A None body means Jev did not answer and `row` says why."""
    row = {"judgment": judgment, "mode": mode}
    if not breaker.allow():
        row["outcome"] = "breaker_open"
        return None, row
    start = time.monotonic()
    try:
        body = await ask(api_key, state, questions, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        row["jev_ms"] = int((time.monotonic() - start) * 1000)
        row["error_type"] = type(e).__name__
        row["outcome"] = "timeout" if isinstance(e, httpx.TimeoutException) else "error"
        breaker.failure(row["error_type"])
        logger.warning("typesafe_call_failed judgment=%s outcome=%s error=%s jev_ms=%d "
                       "failures_in_a_row=%d", judgment, row["outcome"], row["error_type"],
                       row["jev_ms"], breaker.consecutive_failures)
        return None, row
    breaker.success()
    row.update(outcome="ok", jev_ms=int((time.monotonic() - start) * 1000),
               input_tokens=(body.get("usage") or {}).get("input_tokens"))
    return body, row


# --- primary ---------------------------------------------------------------------

async def try_offer_reply(api_key: str, offer_line: str, reply: str,
                          offered_format: str, lane_choice: bool) -> tuple[dict | None, dict]:
    """(verdict, row). A None verdict means Haiku decides, and `row` says
    why. The caller records the row once it knows how the fallback went."""
    row = {"judgment": "offer_reply", "mode": "primary", "fell_back": True}
    if not breaker.allow():
        row["outcome"] = "breaker_open"
        return None, row
    start = time.monotonic()
    try:
        out = await judge_offer_reply(api_key, offer_line, reply, offered_format,
                                      lane_choice, timeout=PRIMARY_TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001
        row["jev_ms"] = int((time.monotonic() - start) * 1000)
        row["error_type"] = type(e).__name__
        row["outcome"] = "timeout" if isinstance(e, httpx.TimeoutException) else "error"
        breaker.failure(row["error_type"])
        logger.warning("typesafe_offer_reply_failed outcome=%s error=%s jev_ms=%d "
                       "failures_in_a_row=%d", row["outcome"], row["error_type"],
                       row["jev_ms"], breaker.consecutive_failures)
        return None, row
    breaker.success()
    row.update(jev_ms=out["ms"], input_tokens=out["input_tokens"],
               confidence=out["confidence"]["confirm"])
    if out["confidence"]["confirm"] < CONFIDENCE_FLOOR:
        # Jev answered and was not sure whether she said yes. Haiku decides.
        row["outcome"] = "low_confidence"
        return None, row
    row["outcome"], row["fell_back"] = "ok", False
    return out["verdict"], row

_FIELDS = ("confirm", "format", "style", "version")


def shadow_offer_reply(api_key: str, offer_line: str, reply: str,
                       offered_format: str, lane_choice: bool,
                       app_id: str | None = None):
    """Start the Jev judgment now, and return `finish(haiku_verdict,
    haiku_ms, haiku_ok)`. Calling finish never waits: it arranges for the
    comparison to be logged when Jev answers, which may be after the turn
    has responded. Returns None when there is no key, so a caller can
    treat "not configured" and "off" the same way.
    """
    if not api_key:
        return None
    task = _hold(
        judge_offer_reply(api_key, offer_line, reply, offered_format, lane_choice))

    def finish(haiku: dict, haiku_ms: int, haiku_ok: bool) -> None:
        def _log(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            err = t.exception()
            if err is not None:
                logger.warning("offer_reply_shadow jev_failed=%s haiku_ms=%d",
                               type(err).__name__, haiku_ms)
                record_later({"judgment": "offer_reply", "mode": "shadow",
                              "outcome": ("timeout" if isinstance(err, httpx.TimeoutException)
                                          else "error"),
                              "error_type": type(err).__name__,
                              "fallback_ms": haiku_ms}, app_id)
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
            record_later({"judgment": "offer_reply", "mode": "shadow",
                          "outcome": ("ok" if out["confidence"]["confirm"] >= CONFIDENCE_FLOOR
                                      else "low_confidence"),
                          "jev_ms": out["ms"], "fallback_ms": haiku_ms,
                          "input_tokens": out["input_tokens"],
                          "confidence": out["confidence"]["confirm"],
                          # A Haiku that failed open is no verdict to agree with.
                          "agreed": (not diff) if haiku_ok else None}, app_id)
        task.add_done_callback(_log)

    return finish


def _compact(v: dict) -> str:
    return "/".join(str(v.get(f)) for f in _FIELDS)
