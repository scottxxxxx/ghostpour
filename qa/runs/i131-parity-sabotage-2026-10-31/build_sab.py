import json, sys, os, random
sys.path.insert(0, os.path.dirname(__file__))
from i131_case import build, ROOT
P = os.path.dirname(os.path.abspath(__file__))
lane = json.load(open(f"{ROOT}/config/remote/i765/interviewer-lane.json"))
blk = json.load(open(f"{ROOT}/config/remote/i765/form-block-i131.json"))
sp = lane["systemPrompt"]
s = sp.index("\nThis rule applies ONLY to a field whose options segment includes unsure.")
e = sp.index("\n", s + 1)
sab = dict(lane, systemPrompt=sp[:s] + sp[e:])
assert "This rule applies ONLY" not in sab["systemPrompt"] and len(sab["systemPrompt"]) < len(sp)
PRIOR = {f: "no" for f in ("p4.proceedings_ever","p4.screen_overstay","p4.screen_no_inspection","p4.screen_status_gap","p4.screen_prior_removal","p4.screen_false_identity")}
ARMS = {"v2": lane, "sab": sab}
CASES = [
    ("crim-en", "en", "I don't think so"),
    ("crim-es", "es", "creo que no"),
    ("crim-pt", "pt", "acho que não"),
    ("crim-es-clar", "es", "no sé, ¿cuenta una multa?"),
]
key = []
random.seed(131)
bundles = [(arm, rep) for arm in ARMS for rep in range(3)]
random.shuffle(bundles)
for i, (arm, rep) in enumerate(bundles, 1):
    d = f"{P}/sab/b{i}"; os.makedirs(d, exist_ok=True)
    cfgs = {"i765/interviewer-lane": ARMS[arm], "i765/form-block-i131": blk}
    for cid, loc, line in CASES:
        system, user = build(cfgs, "q_p4_criminal", loc, line, known=PRIOR)
        open(f"{d}/{cid}.system.txt", "w").write(system)
        open(f"{d}/{cid}.user.txt", "w").write(user)
    key.append({"bundle": f"b{i}", "arm": arm, "rep": rep})
json.dump(key, open(f"{P}/sab/key.json", "w"), indent=1)
print(key)
print(open(f"{P}/sab/b1/crim-es.user.txt").read()[-1500:])
