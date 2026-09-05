"""A visible backstop for one defect of the N-400 interviewer lane.

Across the Spanish runs on v13 and v14 (2026-09-05) the model kept naming
in `asking` the node whose answer had just arrived, while the reply asked
the next question. The phone's cursor follows `asking`, so the standing
question would lag the spoken one by a turn. Two prompt wordings and one
field-order change did not close it; v15 defines it mechanically and
bilingually, and this guard sits behind that definition.

The rule, agreed with the auditor: drop `asking` ONLY when every field id
of the named node (read off the agenda line the client sent) has a fact
in this same response. The two legitimate cases where a node gets a fact
AND stays in `asking` are untouched: a partial answer (some fields
minted, the reply asks for the rest) keeps a node whose ids are not all
filled, and a batch drill-in names a node the graph routes on. With
`asking` gone the client's fallback picks the standing node while it is
unsatisfied, or the first agenda line once it is, which is the right
cursor either way.

Every drop is VISIBLE, which was the auditor's one condition: the
response carries `asking_dropped` with the node and the reason, and the
route logs it with the turn id, so guard hits can be counted per run and
we know whether the definition works or the guard is carrying it. The
goal is that this fires zero times.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("ghostpour.n400_interviewer_guard")

CALL_TYPE = "n400_interviewer_turn"
DROP_REASON = "every field of this node was filled by a fact in this response"


def agenda_field_ids(agenda: str | None) -> dict[str, set[str]]:
    """node_id -> set of field ids, from the client's agenda lines.

    Line shape: `node_id | Part N: title | field_ids comma-joined | question
    [| options: ...]`. A line that does not parse is skipped rather than
    guessed, so a malformed agenda disables the guard for that node instead
    of producing a wrong drop.
    """
    out: dict[str, set[str]] = {}
    for raw in (agenda or "").splitlines():
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 4 or not parts[0]:
            continue
        ids = {f.strip() for f in parts[2].split(",") if f.strip()}
        if ids:
            out[parts[0]] = ids
    return out


def drop_stale_asking(text: str, agenda: str | None) -> tuple[str, dict | None]:
    """Return (possibly rewritten text, drop info or None).

    Anything that is not the lane's JSON object, or has no object-shaped
    `asking`, passes through byte-for-byte: the guard must never turn a
    parse problem of its own into a changed response.
    """
    try:
        turn = json.loads(text)
    except (TypeError, ValueError):
        return text, None
    if not isinstance(turn, dict):
        return text, None
    asking = turn.get("asking")
    if not isinstance(asking, dict) or not asking.get("node_id"):
        return text, None
    node_id = str(asking["node_id"])
    node_ids = agenda_field_ids(agenda).get(node_id)
    if not node_ids:
        return text, None
    facts = turn.get("facts") or []
    filled = {f.get("field_id") for f in facts if isinstance(f, dict)}
    if not node_ids <= filled:
        return text, None
    info = {"node_id": node_id, "field_ids": sorted(node_ids), "reason": DROP_REASON}
    turn["asking"] = None
    turn["asking_dropped"] = info
    return json.dumps(turn, ensure_ascii=False), info


def guard_response_text(text: str, agenda: str | None, turn_id: str | None) -> str:
    new_text, info = drop_stale_asking(text, agenda)
    if info is not None:
        logger.warning(
            "n400_asking_dropped turn_id=%s node_id=%s field_ids=%s",
            turn_id, info["node_id"], ",".join(info["field_ids"]),
        )
    return new_text
