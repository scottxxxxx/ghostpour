"""N-400 choice certainty gate (G1), POST /v1/n400/jev-gate.

Contract: N400 App qa/steven-2026-11-13/CHOICE-CERTAINTY-DESIGN.md, "G1
contract". Scott approved the build 2026-11-18. The phone asks before it
files a choice node from her words: does her reply state ONE status plainly,
and did she volunteer other answers? GP asks Jev and passes its numbers
through untouched. GP applies NO floor: the phone does, so the floor moves
without a deploy. Anything short of a clean answer (timeout at 1.5 s, error,
rate limit, missing key, breaker open, a node not turned on) is
{"code": "fallthrough"}, and the phone sends the reply to the model lane as
it does today.

Logged: node, the two choices, their probabilities and confidence, latency
and the code. NEVER the question or her reply.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import get_settings
from app.dependencies import get_current_user
from app.services import typesafe_judge as tj

logger = logging.getLogger("ghostpour.n400_jev_gate")
router = APIRouter()

TIMEOUT_SECONDS = 1.5
STATUS_IDS = ("one_status_plainly", "qualified_or_unsure", "more_than_one_status", "no_status")
VOLUNTEER_IDS = ("gives_other_answers", "status_only")

# Turned on per node after its own check. First deploy: marital only.
NODES: dict[str, dict] = {
    "q_p5_marital_status": {
        "status": {"type": "choice",
                   "instructions": "The assistant asked her current marital status and she replied. How does her reply state her marital status?",
                   "criteria": {
                       "one_status_plainly": "She states one marital status plainly and without doubt. Extra remarks that don't qualify the status are fine.",
                       "qualified_or_unsure": "She names a status but qualifies it, doubts it, says it is not official or not final, or says it is in progress.",
                       "more_than_one_status": "Her reply describes more than one status, or says one thing on paper and another in practice.",
                       "no_status": "Her reply does not state a marital status."}},
        "volunteers": {"type": "choice",
                       "instructions": "Besides her marital status, does her reply give any other answer the form asks for: how many times she has been married, her spouse's name, date of birth or citizenship, the marriage date, whether they live together, or where the spouse works?",
                       "criteria": {
                           "gives_other_answers": "Yes, her reply gives at least one of those other answers.",
                           "status_only": "No, her reply gives none of those other answers."}},
    },
}

FALLTHROUGH = {"code": "fallthrough"}


class JevGateBody(BaseModel):
    case_id: str = Field(max_length=200)
    node_id: str = Field(max_length=200)
    locale: str = Field(default="en", max_length=10)
    question: str = Field(max_length=2000)
    reply: str = Field(max_length=4000)


def _read(answers: dict, qid: str, ids: tuple[str, ...]) -> dict | None:
    a = answers.get(qid) or {}
    choice = a.get("choice")
    if choice not in ids:
        return None
    probs = a.get("probabilities") or {}
    return {"choice": choice, "confidence": float(a.get("confidence") or 0.0),
            "probabilities": {k: float(probs.get(k) or 0.0) for k in ids}}


def _log(node_id: str, code: str, out: dict | None, row: dict) -> None:
    s, v = (out or {}).get("status") or {}, (out or {}).get("volunteers") or {}
    logger.info("n400_jev_gate node=%s code=%s outcome=%s status=%s status_conf=%s status_p=%s "
                "volunteers=%s volunteers_conf=%s volunteers_p=%s latency_ms=%s",
                node_id, code, row.get("outcome"), s.get("choice"), s.get("confidence"),
                s.get("probabilities"), v.get("choice"), v.get("confidence"),
                v.get("probabilities"), row.get("jev_ms"))


@router.post("/n400/jev-gate")
async def n400_jev_gate(body: JevGateBody, user=Depends(get_current_user)):
    questions = NODES.get(body.node_id)
    key = get_settings().typesafe_api_key or ""
    if not questions or not key:
        _log(body.node_id, "fallthrough", None,
             {"outcome": "node_off" if not questions else "no_key"})
        return FALLTHROUGH
    state = "Form question: " + body.question + " Her reply: " + body.reply
    resp, row = await tj.guarded_ask(key, state, questions, judgment="n400_choice_gate",
                                     mode="primary", timeout=TIMEOUT_SECONDS)
    tj.record_later(row, "n400")
    if resp is None:
        _log(body.node_id, "fallthrough", None, row)
        return FALLTHROUGH
    answers = resp.get("answers") or {}
    status, vol = _read(answers, "status", STATUS_IDS), _read(answers, "volunteers", VOLUNTEER_IDS)
    if status is None or vol is None:
        row["outcome"] = "unreadable"
        _log(body.node_id, "fallthrough", None, row)
        return FALLTHROUGH
    out = {"code": "ok", "status": status, "volunteers": vol, "latency_ms": row.get("jev_ms")}
    _log(body.node_id, "ok", out, row)
    return out
