#!/usr/bin/env python3
"""Builds app/resources/n400_field_catalog.json from the client's form definition
(N400Helper/Sources/Resources/MockFixtures/form_definition_n400_tx.json), the
same table the client's `InterviewEngine.choiceFields(of:)` reads, so GP's
copy of the options is the one on the wire: a choice field's own options, and
yes/no for every yes_no_explanation field.

Run:  python3 scripts/build_n400_field_catalog.py [path to form_definition]
Then paste `render_appendix()` into interviewer-turn's systemPrompt; the test
test_n400_field_catalog.py fails until the two agree.
"""
import json
import os
import sys

DEFAULT = os.path.expanduser(
    "~/N400 App/N400Helper/Sources/Resources/MockFixtures/form_definition_n400_tx.json")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "resources", "n400_field_catalog.json")

src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
d = json.load(open(src))
fields = []
for f in d["field_catalog"]:
    row = {"field_id": f["field_id"], "part": f["part"]}
    if f.get("type") == "choice" and f.get("options"):
        row["options"] = list(f["options"])
    elif f.get("type") == "yes_no_explanation":
        row["options"] = ["yes", "no"]
    fields.append(row)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump({"_source": "form_definition_n400_tx.json revision %s" % d.get("revision"),
           "fields": fields}, open(OUT, "w"), ensure_ascii=False, indent=1)
print(OUT, len(fields), "fields,", sum(1 for f in fields if "options" in f), "with options")
