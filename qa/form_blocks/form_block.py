#!/usr/bin/env python3
"""THE FORM BLOCK GENERATOR (the GP prompt split, 2026-10-29): one form
definition in, one markdown section out, the FORM BLOCK GhostPour assembles
beside the generic interviewer lane. GP pins block == generator(definition
at a pinned hash), so the output is deterministic: stable ordering, no
timestamps, and a header comment carrying the definition's git blob hash
and sha256.

Run:  python3 Scripts/form_block.py DEFINITION_JSON [--out PATH]
      python3 Scripts/form_block.py --write-docs
The second form writes both shipped blocks into
Docs/contracts/form-blocks/ (i765.md, i131.md) and prints their word counts
and hashes.

THE STARTING CUT (GP's amendment 2, Docs/contracts/gp-ask-i131-generic-lane-
DRAFT.md section 6): no ask text at all (GP's trim, 2026-10-29: the
per-turn AGENDA already carries each node's English question); labels; option ids and option_labels;
required; digit rules (digits_only, accepted_digit_counts); max_length;
policy triggers (node, when, reason code); check fields with their
check_value; the fields that offer unsure, listed explicitly; and the
definition's `lane_notes`, each placed under its heading after the generated
lines and emitted verbatim. pdf_empty_text and the es and pt text are left
out. Headings follow LANE_NOTE_HEADINGS below; a heading with no
generated line and no note is left out.

Bullets are "*", never a hyphen, so the block holds no dash used as
punctuation (Scott's house rule; the model copies what it sees).
"""
import hashlib
import json
import os
import sys

# Self-contained so GP can vendor this file with the definitions alone
# (cloudzap-d8, 2026-10-29): form_machinery imports these from here.
LANE_NOTE_HEADINGS = (
    "form_identity", "part_map", "never_asks", "reason_first", "category_contract",
    "stop_answers", "conditional_fields", "interpreter_part", "gates", "name_nodes",
    "value_specifics", "identifiers", "risk_triggers", "opening_documents", "pace_bundle",
)
# Dash characters that may never appear in served text: every dash but the
# plain hyphen, and a hyphen standing alone between spaces.
DASH_CHARACTERS = ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212")


def has_dash_punctuation(text):
    """True when `text` holds a dash used as punctuation: any dash
    character but the hyphen, or a hyphen with a space on both sides."""
    return any(d in text for d in DASH_CHARACTERS) or " - " in text


DEFINITIONS_DIR = "I765Helper/Sources/Resources/MockFixtures"
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(APP)
FIXTURES = os.path.join(APP, "Sources", "Resources", "MockFixtures")
DOCS_OUT = os.path.join(REPO, "Docs", "contracts", "form-blocks")
SHIPPED = (("form_definition_i765_tx.json", "i765.md"), ("form_definition_i131.json", "i131.md"))

UNSURE = "unsure"
YES_NO_TYPE = "yes_no_explanation"


def git_blob_hash(data):
    """What `git hash-object` prints for these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def quoted(text):
    return f'"{text}"'


def required_words(required):
    if required is True:
        return "required"
    if required is False:
        return "optional"
    if isinstance(required, str) and required.startswith("if:"):
        return f"required if {required[3:]}"
    if isinstance(required, str) and required.startswith("missing:"):
        return f"required while {required[len('missing:'):]} has no value"
    return f"required {required}"


def field_line(field):
    """One catalog field in the starting cut's terms."""
    bits = [f"{field['field_id']}: {quoted(field['label']['en'])}"]
    if field.get("item"):
        bits[0] += f" (Item {field['item']})"
    kind = "yes or no box" if field["type"] == YES_NO_TYPE else field["type"]
    bits.append(kind)
    bits.append(required_words(field["required"]))
    if field.get("digits_only"):
        bits.append("digits only")
    if field.get("accepted_digit_counts"):
        bits.append("accepted digit counts " + " or ".join(str(n) for n in field["accepted_digit_counts"]))
    if field.get("max_length"):
        bits.append(f"max length {field['max_length']}")
    if field.get("check_value"):
        bits.append(f"check field, a checked box files {quoted(field['check_value'])}")
    line = ", ".join(bits) + "."
    options = field.get("options")
    if options and list(options) == ["yes", "no"]:
        # GP's trim (2026-10-29): a plain yes/no field says so in one word.
        line += " Options: yes/no."
    elif options:
        labels = field.get("option_labels") or {}
        rendered = []
        for option in options:
            label = (labels.get(option) or {}).get("en")
            # A label that only repeats its id ("male", "Male") is dropped;
            # one that says more (a category code, an admission class) is
            # what the model matches her words against, so it stays.
            if label and not label_repeats_id(label, option):
                rendered.append(f"{option} ({quoted(label)})")
            else:
                rendered.append(option)
        line += " Options: " + "; ".join(rendered) + "."
    return line


def label_repeats_id(label, option):
    """True when the label is the id in other casing or spacing."""
    def squash(text):
        return "".join(ch for ch in text.lower() if ch.isalnum())
    return squash(label) == squash(option)


def node_part(node, fields):
    return fields[node["field_ids"][0]]["part"]


def note_lines(notes):
    lines = []
    for note in notes:
        applies = note.get("applies_to")
        where = f" (applies to {', '.join(applies)})" if applies else ""
        lines.append(f"* [{note['id']}]{where} {note['text']}")
    return lines


def split_nodes(composite, nodes):
    """The nodes a name composite's branches lead to, in the order the
    graph reaches them, up to where the route rejoins at the composite's
    own next node."""
    rejoin = composite.get("default_next")
    queue = [b["node"] for b in composite.get("branches") or []]
    seen = []
    while queue:
        node_id = queue.pop(0)
        if node_id == rejoin or node_id == composite["node_id"] or node_id in seen or node_id not in nodes:
            continue
        seen.append(node_id)
        node = nodes[node_id]
        queue += [b["node"] for b in node.get("branches") or []]
        if node.get("default_next"):
            queue.append(node["default_next"])
    return [nodes[node_id] for node_id in seen]


def generated_sections(definition):
    """Heading -> the generated lines under it, in a fixed order."""
    fields = {f["field_id"]: f for f in definition["field_catalog"]}
    graph = definition["question_graph"]
    out = {heading: [] for heading in LANE_NOTE_HEADINGS}

    # form_identity
    out["form_identity"] = [
        f"* Form {definition['form_code']}, {definition['title']['en']}.",
        f"* Revision {definition['revision']}, jurisdiction {definition['jurisdiction']}.",
    ]

    # part_map: the parts in interview order, each with its nodes in graph
    # order; a field's details print at the first node that asks it.
    sessions = definition.get("sessions") or []
    order = [s["part"] for s in sessions]
    titles = {s["part"]: s["title"]["en"] for s in sessions}
    for node in graph:
        part = node_part(node, fields)
        if part not in order:
            order.append(part)
    lines = ["Interview order: " + ", ".join(
        f"Part {p}" + (f" ({titles[p]})" if p in titles else "") for p in order) + "."]
    described = set()
    for part in order:
        lines.append("")
        lines.append(f"#### Part {part}" + (f": {titles[part]}" if part in titles else ""))
        for node in graph:
            if node_part(node, fields) != part:
                continue
            # No ask text (GP, 2026-10-29): the per-turn AGENDA already
            # carries each node's English question, so the cached block
            # holds ids and rules only.
            lines.append(f"* {node['node_id']}" + (" (opens the part)" if node.get("opener") else ""))
            for field_id in node["field_ids"]:
                if field_id in described:
                    lines.append(f"  * {field_id} (described above).")
                else:
                    described.add(field_id)
                    lines.append(f"  * {field_line(fields[field_id])}")
    unasked = [f["field_id"] for f in definition["field_catalog"] if f["field_id"] not in described]
    if unasked:
        lines.append("")
        lines.append("No node asks these fields: " + ", ".join(unasked) + ".")
    out["part_map"] = lines

    # stop_answers: every policy trigger, then the fields that offer unsure.
    lines = []
    for node in graph:
        trigger = node.get("policy_trigger")
        if not trigger:
            continue
        lines.append(f"* {node['node_id']} ({', '.join(node['field_ids'])}): when {trigger['when']}, "
                     f"{trigger['outcome']} {trigger['capability']}, reason {trigger['reason_code']}.")
    unsure = [f["field_id"] for f in definition["field_catalog"] if UNSURE in (f.get("options") or [])]
    if unsure:
        lines.append("* These fields offer unsure: " + ", ".join(unsure) + ".")
    else:
        lines.append("* No field on this form offers unsure.")
    out["stop_answers"] = lines

    # conditional_fields: each condition once, with the fields it governs.
    conditions = {}
    for field in definition["field_catalog"]:
        required = field["required"]
        if isinstance(required, str):
            conditions.setdefault(required, []).append(field["field_id"])
    out["conditional_fields"] = [
        f"* {required_words(condition)}: {', '.join(ids)}." for condition, ids in conditions.items()]

    # interpreter_part: the reads English field and the interpreter's nodes.
    node_of = {}
    for node in graph:
        for field_id in node["field_ids"]:
            node_of.setdefault(field_id, node["node_id"])
    reads = [f for f in definition["field_catalog"] if f.get("semantic") == "language.reads_english"]
    interpreter_nodes = []
    for field in definition["field_catalog"]:
        if (field.get("semantic") or "").startswith("interpreter.") and field["field_id"] in node_of:
            if node_of[field["field_id"]] not in interpreter_nodes:
                interpreter_nodes.append(node_of[field["field_id"]])
    lines = [f"* The reads English question is {node_of[f['field_id']]} ({f['field_id']}, Part {f['part']})."
             for f in reads if f["field_id"] in node_of]
    if interpreter_nodes:
        lines.append("* The interpreter's nodes: " + ", ".join(interpreter_nodes) + ".")
    out["interpreter_part"] = lines

    # gates: a yes or no gate and the number it opens.
    out["gates"] = [
        f"* {f['field_id']} ({node_of.get(f['field_id'])}) opens {f['gate_for']} ({node_of.get(f['gate_for'])})."
        for f in definition["field_catalog"] if f.get("gate_for")]

    # name_nodes: each name composite, then the split nodes its branches
    # lead to (the review's S3, 2026-10-29), read from the graph.
    nodes = {node["node_id"]: node for node in graph}
    lines = []
    for node in graph:
        if node.get("composite") != "name":
            continue
        line = f"* {node['node_id']}: {', '.join(node['field_ids'])}"
        if node.get("split_name_locales"):
            line += f"; asked in parts in {', '.join(node['split_name_locales'])}"
        lines.append(line + ".")
        split = split_nodes(node, nodes)
        if split:
            lines.append(f"* {node['node_id']} splits into: " + "; ".join(
                f"{n['node_id']} ({', '.join(n['field_ids'])})" for n in split) + ".")
    out["name_nodes"] = lines

    # value_specifics: the check fields.
    checks = [f for f in definition["field_catalog"] if f.get("check_value")]
    out["value_specifics"] = [
        f"* Check field {f['field_id']} ({node_of.get(f['field_id'])}): a checked box files "
        f"{quoted(f['check_value'])}; a box she does not name is never filed." for f in checks]

    # identifiers: the masked fields and their digit rules.
    out["identifiers"] = [f"* {field_line(f)}" for f in definition["field_catalog"] if f.get("sensitive")]

    # risk_triggers: the warn and continue cards.
    lines = []
    for warning in definition.get("warnings") or []:
        when = warning["when"]
        test = ", ".join(f"{k} {json.dumps(v)}" for k, v in when.items() if k != "field")
        lines.append(f"* Warning {warning['warning_id']}: when {when['field']} {test}.")
    out["risk_triggers"] = lines
    return out


def form_block(definition_path):
    """The FORM BLOCK for one definition file, as text."""
    with open(definition_path, "rb") as fh:
        data = fh.read()
    definition = json.loads(data)
    # A fixed repo-relative path, never where the file happens to sit, so a
    # vendored copy run anywhere reproduces the committed block byte for byte.
    shown_path = DEFINITIONS_DIR + "/" + os.path.basename(definition_path)
    generated = generated_sections(definition)
    notes = {}
    for note in definition.get("lane_notes") or []:
        notes.setdefault(note["heading"], []).append(note)

    lines = [
        "<!-- FORM BLOCK, generated by I765Helper/Scripts/form_block.py; never edit by hand. -->",
        f"<!-- definition: {shown_path} git_blob {git_blob_hash(data)} "
        f"sha256 {hashlib.sha256(data).hexdigest()} -->",
        "",
        f"## FORM BLOCK: Form {definition['form_code']}",
    ]
    for heading in LANE_NOTE_HEADINGS:
        generated_lines = generated[heading]
        noted = note_lines(notes.get(heading, []))
        if not generated_lines and not noted:
            continue
        lines += ["", f"### {heading}", ""] + generated_lines
        if generated_lines and noted:
            lines.append("")
        lines += noted
    text = "\n".join(lines) + "\n"
    if has_dash_punctuation(text):
        raise SystemExit(f"{definition_path}: the block holds a dash used as punctuation")
    return text


def word_count(text):
    return len(text.split())


def write_docs():
    os.makedirs(DOCS_OUT, exist_ok=True)
    for definition_name, block_name in SHIPPED:
        path = os.path.join(FIXTURES, definition_name)
        text = form_block(path)
        with open(os.path.join(DOCS_OUT, block_name), "w", encoding="utf-8") as fh:
            fh.write(text)
        data = open(path, "rb").read()
        print(f"{block_name}: {word_count(text)} words; {definition_name} git_blob {git_blob_hash(data)} "
              f"sha256 {hashlib.sha256(data).hexdigest()}")


def main(argv):
    if argv == ["--write-docs"]:
        write_docs()
        return
    out = None
    args = list(argv)
    if len(args) >= 3 and args[-2] == "--out":
        out, args = args[-1], args[:-2]
    if len(args) != 1:
        raise SystemExit("usage: form_block.py DEFINITION_JSON [--out PATH] | --write-docs")
    text = form_block(args[0])
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main(sys.argv[1:])
