"""N-400 policy engine: State x Form x Capability -> ALLOW/RESTRICT/ESCALATE/BLOCK.

The compliance boundary for N-400 Helper, and the one gate in this codebase
that is deliberately NOT a feature tier. Tiers answer "has this user paid
for it". This answers "is the product permitted to do it at all, for anyone,
in this jurisdiction", which is a different question with a different
failure direction: a tier that misreads costs money, a policy row that
misreads gives immigration advice we are not licensed to give.

So every unknown here resolves to BLOCK. No matching rule, an unreadable
document, a capability nobody wrote a row for, an outcome string with a typo
in it: all BLOCK, all logged with a reason_code that says which one it was.
There is no path through this module that returns ALLOW by default.

Rules live in served config (`config/remote/n400/policy-matrix.json`), so a
row moves without a deploy. Read `config/remote/OWNERSHIP.md` first: adding
a key propagates on boot, CHANGING A VALUE DOES NOT, so a matrix edit merged
in this repo is not a matrix edit in production until it is pushed through
`PUT /webhooks/admin/config/{slug}`.

⚠ The content of the matrix is pending formal legal review (Scott owns it,
2026-09-01). `basis` on each rule records whether the row rests on a citable
public source or is an engineering default held at its safest setting. This
module enforces whatever the document says; it does not know law.
"""

from __future__ import annotations

import logging

from app.routers.config import load_apps

logger = logging.getLogger("ghostpour.n400_policy")

_APP_ID = "n400"
SLUG_NAME = "policy-matrix"

ALLOW = "ALLOW"
RESTRICT = "RESTRICT"
ESCALATE = "ESCALATE"
BLOCK = "BLOCK"
VALID_OUTCOMES = frozenset({ALLOW, RESTRICT, ESCALATE, BLOCK})

# Every refusal path in this module lands on one of these. They are logged
# and served, so a support conversation can name which one fired instead of
# describing what the screen looked like.
REASON_DOCUMENT_MISSING = "policy_matrix_unavailable"
REASON_DOCUMENT_MALFORMED = "policy_matrix_malformed"
REASON_UNKNOWN_CAPABILITY = "capability_not_in_matrix"
REASON_NO_MATCHING_ROW = "no_row_for_jurisdiction"
REASON_INVALID_OUTCOME = "outcome_not_recognized"


def policy_slug() -> str:
    """The config slug this engine reads, e.g. `n400/policy-matrix`.

    Built from N-400's REGISTERED directory rather than from a request
    header. `resolve_app_dir` fails open to ShoulderSurf by design, which is
    right for serving config and wrong for reading a compliance matrix: a
    caller with a mistyped X-App-ID must not be handed some other app's
    policy. If n400 is somehow not in apps.yml we use the literal `n400`,
    which resolves to nothing, which fails closed.
    """
    entry = (load_apps().get("apps") or {}).get(_APP_ID) or {}
    return f"{entry.get('dir') or _APP_ID}/{SLUG_NAME}"


def policy_document(remote_configs: dict | None) -> dict | None:
    """The served matrix document, or None when it is absent or unusable.

    NO FLAT FALLBACK, deliberately. `candidate_slugs` would try the bare
    `policy-matrix` name after the per-app one, which is correct for copy
    an app can reasonably inherit and wrong for this: if a flat
    `policy-matrix.json` ever appears for some other app, N-400 must 404
    rather than quietly enforce someone else's jurisdiction rules.
    """
    doc = (remote_configs or {}).get(policy_slug())
    if not isinstance(doc, dict):
        return None
    if not isinstance(doc.get("capabilities"), list):
        logger.error("n400_policy_document_malformed: capabilities is not a list")
        return None
    return doc


def _rule_for(doc: dict, capability: str) -> dict | None:
    want = (capability or "").strip()
    for rule in doc.get("capabilities") or []:
        if isinstance(rule, dict) and rule.get("capability") == want:
            return rule
    return None


def _row_for(rule: dict, state: str, form: str) -> dict | None:
    """The winning row: exact state beats `*`, and form must match either way.

    Two passes rather than one sorted scan, because "exact beats wildcard"
    has to hold no matter what order the rows were authored in. A single
    first-match loop would make the answer depend on file ordering, which is
    exactly the kind of thing that reads fine in review and changes meaning
    when someone tidies the JSON.
    """
    want_state = (state or "").strip().upper()
    want_form = (form or "").strip()
    rows = [r for r in (rule.get("matrix") or []) if isinstance(r, dict)]

    def _matches_form(row: dict) -> bool:
        row_form = (row.get("form") or "").strip()
        return row_form == want_form or row_form == "*"

    for row in rows:
        if _matches_form(row) and (row.get("state") or "").strip().upper() == want_state:
            return row
    for row in rows:
        if _matches_form(row) and (row.get("state") or "").strip() == "*":
            return row
    return None


def _copy_for(doc: dict, outcome: str) -> dict:
    """Served message + CTA for an outcome, or {} when there is none.

    ALLOW has no copy by design: there is nothing to tell the applicant when
    the thing simply happens.
    """
    block = (doc.get("outcome_copy") or {}).get(outcome)
    return block if isinstance(block, dict) else {}


def _denial(capability: str, state: str, form: str, reason_code: str,
            doc: dict | None = None) -> dict:
    decision = {
        "schema_version": 1,
        "capability": capability,
        "state": (state or "").strip().upper(),
        "form": (form or "").strip(),
        "outcome": BLOCK,
        "reason_code": reason_code,
        "halt_branch_nodes": [],
    }
    if doc is not None:
        decision.update(_copy_for(doc, BLOCK))
    # Logs the outcome it is ACTUALLY returning rather than the word BLOCK,
    # so this line cannot go on reporting a refusal that stopped happening.
    logger.warning(
        "n400_policy_fail_closed capability=%s state=%s form=%s reason=%s outcome=%s",
        capability, state, form, reason_code, decision["outcome"],
    )
    return decision


def evaluate(remote_configs: dict | None, capability: str,
             state: str, form: str = "N-400") -> dict:
    """One PolicyDecision for one capability. Never raises, never returns None.

    Callers attach this to a turn as `policy_decision`. One decision per
    capability per turn: this function answers about a single capability, so
    a turn touching two of them gets two calls and two log lines.
    """
    doc = policy_document(remote_configs)
    if doc is None:
        return _denial(capability, state, form, REASON_DOCUMENT_MISSING)

    rule = _rule_for(doc, capability)
    if rule is None:
        return _denial(capability, state, form, REASON_UNKNOWN_CAPABILITY, doc)

    row = _row_for(rule, state, form)
    if row is None:
        return _denial(capability, state, form, REASON_NO_MATCHING_ROW, doc)

    outcome = (row.get("outcome") or "").strip().upper()
    if outcome not in VALID_OUTCOMES:
        logger.error(
            "n400_policy_bad_outcome capability=%s row_outcome=%r",
            capability, row.get("outcome"),
        )
        return _denial(capability, state, form, REASON_INVALID_OUTCOME, doc)

    decision = {
        "schema_version": 1,
        "capability": capability,
        "state": (state or "").strip().upper(),
        "form": (form or "").strip(),
        "outcome": outcome,
        "reason_code": row.get("reason_code") or rule.get("reason_code"),
        # A branch is only ever halted by a stop. ALLOW and RESTRICT both mean
        # the interview keeps going, so they never carry halt nodes even if a
        # rule declares some.
        "halt_branch_nodes": (
            list(rule.get("halt_branch_nodes") or [])
            if outcome in (ESCALATE, BLOCK) else []
        ),
    }
    if outcome != ALLOW:
        decision.update(_copy_for(doc, outcome))

    logger.info(
        "n400_policy_decision capability=%s state=%s form=%s outcome=%s reason=%s",
        capability, decision["state"], decision["form"], outcome,
        decision["reason_code"],
    )
    return decision


def effective_matrix(remote_configs: dict | None, state: str,
                     form: str = "N-400") -> dict:
    """Every capability's outcome for one jurisdiction, for the state picker.

    Built by running `evaluate` per capability rather than by reading rows
    directly, so what this endpoint advertises and what a turn actually gets
    cannot drift apart. A capability the document does not name is not listed
    here at all; asking for it still BLOCKs.
    """
    doc = policy_document(remote_configs)
    capabilities = []
    if doc is not None:
        for rule in doc.get("capabilities") or []:
            if not isinstance(rule, dict) or not rule.get("capability"):
                continue
            name = rule["capability"]
            decision = evaluate(remote_configs, name, state, form)
            capabilities.append({
                "capability": name,
                "outcome": decision["outcome"],
                "reason_code": decision.get("reason_code"),
                "basis": rule.get("basis"),
            })
    review = (doc or {}).get("legal_review") or {}
    return {
        "schema_version": 1,
        "state": (state or "").strip().upper(),
        "form": (form or "").strip(),
        "matrix_version": (doc or {}).get("version"),
        "legal_review_status": review.get("status") or "UNKNOWN",
        "capabilities": capabilities,
    }


def unreviewed_permissive_rules(doc: dict | None) -> list[str]:
    """Capabilities marked NEEDS_LEGAL that the document lets through.

    The invariant a test pins: once someone stamps `legal_review.status` as
    REVIEWED, no row still labelled an engineering default may be sitting at
    ALLOW or RESTRICT. Stamping the header is the cheap half of a legal
    review and the half most likely to happen alone.
    """
    out = []
    for rule in (doc or {}).get("capabilities") or []:
        if not isinstance(rule, dict) or rule.get("basis") != "NEEDS_LEGAL":
            continue
        outcomes = {
            (r.get("outcome") or "").strip().upper()
            for r in rule.get("matrix") or [] if isinstance(r, dict)
        }
        if outcomes & {ALLOW, RESTRICT}:
            out.append(rule.get("capability") or "<unnamed>")
    return out
