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
OFF_AGENDA_REASON = "this node is not on the agenda"


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
    by_node = agenda_field_ids(agenda)
    if by_node and node_id not in by_node:
        # conf-v19 English 60: asking named a node the agenda did not list
        # (Selective Service, answered by a known fact). Nothing was minted
        # for it, so the all-fields rule below could not see it. The client
        # falls back to its own cursor on a node off the agenda; making the
        # drop visible is what lets the audit count it.
        info = {"node_id": node_id, "field_ids": [], "reason": OFF_AGENDA_REASON}
        turn["asking"] = None
        turn["asking_dropped"] = info
        return json.dumps(turn, ensure_ascii=False), info
    node_ids = by_node.get(node_id)
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


# --- the evidence floor, server side --------------------------------------
#
# conf-v18 turn 47: the applicant said "yes" to a section summary and the
# lane minted the one empty id on the standing line (p6.child1.supported =
# yes) with a PRIOR turn's words as provenance. The phone's floor drops any
# fact whose cited words are not in the current utterance, but by then the
# wrong value had ridden into the spoken read-back. Applying the same floor
# here, before the reply leaves, keeps the transcript and the record in
# step, and every drop is visible so the audit can count them.

EVIDENCE_DROP_REASON = "provenance.utterance is not in what the applicant just said"


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split()).strip("\"'“”‘’ ")


def drop_facts_without_current_evidence(text: str, user_content: str | None) -> tuple[str, list[dict]]:
    """Return (possibly rewritten text, list of dropped fact summaries).

    A fact stays when its `provenance.utterance` is a substring of the
    current utterance after whitespace and case folding. A fact with no
    utterance at all is dropped too: the contract requires one. Anything
    that is not the lane's object passes through byte for byte.
    """
    try:
        turn = json.loads(text)
    except (TypeError, ValueError):
        return text, []
    if not isinstance(turn, dict) or not isinstance(turn.get("facts"), list):
        return text, []
    said = _norm(user_content or "")
    kept, dropped = [], []
    for f in turn["facts"]:
        utt = ((f.get("provenance") or {}).get("utterance") if isinstance(f, dict) else None) or ""
        if utt and _norm(utt) in said:
            kept.append(f)
        else:
            dropped.append({"field_id": f.get("field_id") if isinstance(f, dict) else None,
                            "utterance": utt, "reason": EVIDENCE_DROP_REASON})
    if not dropped:
        return text, []
    turn["facts"] = kept
    turn["facts_dropped"] = dropped
    return json.dumps(turn, ensure_ascii=False), dropped


def guard_response_text(text: str, agenda: str | None, turn_id: str | None,
                        user_content: str | None = None) -> str:
    new_text, info = drop_stale_asking(text, agenda)
    if info is not None:
        logger.warning(
            "n400_asking_dropped turn_id=%s node_id=%s field_ids=%s",
            turn_id, info["node_id"], ",".join(info["field_ids"]),
        )
    if user_content is not None:
        new_text, dropped = drop_facts_without_current_evidence(new_text, user_content)
        for d in dropped:
            logger.warning("n400_fact_dropped_no_evidence turn_id=%s field_id=%s", turn_id, d["field_id"])
    return new_text
