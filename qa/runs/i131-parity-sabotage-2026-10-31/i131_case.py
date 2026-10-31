"""Build I-131 form_interviewer_turn prompts for one standing node, as the app would send them."""
import json, sys
sys.path.insert(0, "/Users/scottguida/cloudzap-parity")
from app.services.prompt_assembly import assemble_prompt
from app.services.locale_injection import language_directive

ROOT = "/Users/scottguida/cloudzap-parity"
OPEN = json.load(open("/Users/scottguida/I765 App/Docs/contracts/wire-proof-i131-2026-10-31/opening-body.json"))
DEF = json.load(open(f"{ROOT}/qa/form_blocks/form_definition_i131.json"))
NODES = {n["node_id"]: n for n in DEF["question_graph"]}
FIELDS = {f["field_id"]: f for f in DEF["field_catalog"]}
TITLES = {}
for line in OPEN["metadata"]["agenda"].split("\n"):
    parts = [p.strip() for p in line.split("|")]
    TITLES[parts[0].split("_")[1]] = parts[1]

def options(fid):
    f = FIELDS[fid]
    opts = f.get("options") or []
    return [o["id"] if isinstance(o, dict) else o for o in opts]

def agenda_line(nid, loc):
    n = NODES[nid]
    fids = n["field_ids"]
    line = f"{nid} | {TITLES[nid.split('_')[1]]} | {','.join(fids)} | {n['ask'].get(loc) or n['ask']['en']}"
    if len(fids) == 1 and options(fids[0]):
        line += " | options: " + ", ".join(options(fids[0]))
    return line

P1 = {"p1.screen_rtd_or_reentry": "no", "p1.ap_pending_i485": "yes", "p1.refugee_status": "no",
      "p1.inside_us": "yes", "p1.filing_with_i485": "same_package"}

def build(cfgs, nid, loc, applicant, known=None, then=("q_p4_j_visa", "q_p2_full_name")):
    md = dict(OPEN["metadata"])
    kf = {"p2.mailing_address.state": "TX", **P1, **(known or {})}
    md.update({
        "locale": loc, "turn_id": "t_014", "opening_questions": "[no opening questions]",
        "agenda": "\n".join(agenda_line(x, loc) for x in (nid, *then)),
        "known_facts": "\n".join(f"{k}: {v}" for k, v in kf.items()),
        "conversation": f"INTERVIEWER: {NODES[nid]['ask'].get(loc)}\nAPPLICANT: {applicant}",
    })
    md["applicant_context"] = md["applicant_context"].replace("language: English", {"en": "language: English", "es": "language: Spanish", "pt": "language: Portuguese"}[loc])
    r = assemble_prompt("form_interviewer_turn", applicant, cfgs, variables=md)
    return r["system_prompt"] + language_directive(loc), r["user_content"]
