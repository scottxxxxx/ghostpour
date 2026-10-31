import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from i131_case import build, ROOT
P = os.path.dirname(os.path.abspath(__file__))
lane = json.load(open(f"{ROOT}/config/remote/i765/interviewer-lane.json"))
blk = json.load(open(f"{ROOT}/config/remote/i765/form-block-i131.json"))
sp = lane["systemPrompt"]
s = sp.index("\nThis rule applies ONLY to a field whose options segment includes unsure.")
sp = sp[:s] + sp[sp.index("\n", s + 1):]
CUTS = [
    " A field whose options include unsure is never deferred: doubt there is unsure.",
    "; on a field that offers unsure, a hedge is not low confidence, it is unsure",
    "; a field that offers unsure never gets a deferred entry",
    " (an unsure filed on a field that offers it is an answer)",
]
for c in CUTS:
    assert sp.count(c) == 1, c
    sp = sp.replace(c, "")
assert "hedge is not low confidence" not in sp
sab2 = dict(lane, systemPrompt=sp)
PRIOR = {f: "no" for f in ("p4.proceedings_ever","p4.screen_overstay","p4.screen_no_inspection","p4.screen_status_gap","p4.screen_prior_removal","p4.screen_false_identity")}
CASES = [("crim-en", "en", "I don't think so"), ("crim-es", "es", "creo que no"), ("crim-pt", "pt", "acho que não")]
for i in range(1, 4):
    d = f"{P}/sab2/c{i}"; os.makedirs(d, exist_ok=True)
    cfgs = {"i765/interviewer-lane": sab2, "i765/form-block-i131": blk}
    for cid, loc, line in CASES:
        system, user = build(cfgs, "q_p4_criminal", loc, line, known=PRIOR)
        open(f"{d}/{cid}.system.txt", "w").write(system); open(f"{d}/{cid}.user.txt", "w").write(user)
print("built", len(lane["systemPrompt"]) - len(sp), "chars cut")
