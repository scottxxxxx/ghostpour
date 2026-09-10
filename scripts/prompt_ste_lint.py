#!/usr/bin/env python3
"""Prompt lint: the checkable half of ASD-STE100, over the served prompt config.

Run from the repo root:

    python3 scripts/prompt_ste_lint.py                 # human report
    python3 scripts/prompt_ste_lint.py --json out.json # machine readable
    python3 scripts/prompt_ste_lint.py --baseline out.json   # did an edit make it worse?

WHY THIS FILE EXISTS. ASD-STE100 (Simplified Technical English) is a
controlled language for aerospace maintenance procedures, written so a
non-native reader and a machine translator both land on ONE reading. Its
900 word dictionary is copyrighted and is NOT reproduced here; only the
writing rules are implemented, in our own words, from the public rule
summary. What this tool measures is therefore a SUBSET, and a clean score
is not a claim of STE compliance.

The rules below are aimed at a defect this project has already paid for
twice. A prompt rule in the N-400 lane banned the mechanism it was written
to protect, and neither a green suite nor a passing sabotage could see it;
only the auditor reading the rule against a named real turn found it. And
when the per-field judge went unstable across reps on that same lane, the
instability tracked SPECIFICATION AMBIGUITY rather than sampling noise. A
rule that carries five clauses in one sentence cannot be tested clause by
clause, and a prohibition with no positive form leaves the model to pick a
scope. Both are mechanically visible, which is all this tool does.

WHAT IT CANNOT DO. It cannot tell you a rule is WRONG. A 12 word sentence
that states a false rule scores perfectly. This is a readability and
testability instrument, not a correctness one. Treat a flag as "go read
this line", never as "this line is broken".

WHAT COUNTS AS TEXT. Only model facing strings: values under prompt shaped
keys (systemPrompt, userPromptTemplate, anything matching /prompt|instruction
|template|guidance|rubric|criteria/). `_comment` blocks are documentation for
us, never sent to a model, and are skipped unless you pass --include-comments.
Embedded JSON shape blocks inside a prompt (the "return ONLY this shape"
passages) are stripped before analysis, because their length is a wire
contract and not prose. Pass --keep-json-blocks to score them anyway.

THE RULES, and the STE clause each one approximates:

  STE_LONG_INSTRUCTION   an instruction over 20 words          (STE 5.1)
  STE_LONG_DESCRIPTIVE   a descriptive sentence over 25 words  (STE 5.2)
  STE_MULTI_INSTRUCTION  more than one instruction per sentence
  STE_PASSIVE            passive voice, English only, regex approximation
  STE_OPEN_PRONOUN       a sentence opening on an unbound it/this/that/those

Two ratios are reported per prompt rather than flagged per sentence,
because neither has a defensible per sentence threshold:

  prohibition ratio   "never/do not/avoid" against positive instructions.
                      STE prefers a positive instruction: a prohibition
                      does not say what to DO, so the reader picks a scope.
  emphasis density    ALL CAPS tokens per 1000 words. When everything is
                      emphasised, nothing is.

EXIT CODES. 0 in report mode, always: every prompt in this repo has flags
and a tool that always exits 1 is a tool nobody runs. With --baseline it
exits 1 only when a rule count or a ratio got WORSE than the baseline file,
which is the check worth wiring to an edit.
"""

import argparse
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = [
    REPO / "config" / "remote" / "protected-prompts.json",
    REPO / "config" / "remote" / "prompt-envelope.json",
    REPO / "config" / "remote" / "n400",
    REPO / "config" / "remote" / "techrehearsal",
]

PROMPT_KEY = re.compile(r"(prompt|instruction|template|guidance|rubric|criteria)", re.I)

# An imperative opens a sentence that tells the model to do something. This
# list is deliberately explicit rather than a part of speech tagger: a tagger
# would be a second thing to trust, and every verb below was read off the
# corpus this tool lints.
IMPERATIVE_VERBS = {
    "never", "always", "use", "write", "be", "give", "keep", "refer", "note",
    "summarize", "summarise", "output", "return", "prioritize", "prioritise",
    "group", "match", "ask", "answer", "do", "avoid", "include", "exclude",
    "stop", "say", "prefer", "treat", "read", "check", "start", "end", "list",
    "report", "make", "take", "call", "set", "leave", "put", "name", "state",
    "explain", "describe", "decide", "choose", "pick", "add", "remove", "send",
    "assume", "ensure", "confirm", "repeat", "continue", "follow", "apply",
}

BE_FORM = r"(?:is|are|was|were|be|been|being)"
PASSIVE_RE = re.compile(rf"\b{BE_FORM}\s+(?:\w+ly\s+)?(\w+(?:ed|en))\b", re.I)
# Adjectives that end in -ed or -en and follow a be form without being passive.
PASSIVE_EXCEPT = {
    "based", "related", "limited", "detailed", "mixed", "given", "supposed",
    "used", "needed", "allowed", "intended", "concerned", "interested",
}
# A demonstrative bound to a noun ("This rule holds") is not the defect. The
# defect is a pronoun standing alone as the subject ("That is the rule"), where
# the reader has to guess the antecedent. So the pronoun only counts when a
# comma or a verb follows it directly. The verb list is deliberately short:
# under-flagging costs one missed line, while over-flagging gets the whole
# report ignored, and this rule fired a false positive on its first real use.
_BARE_SUBJECT_FOLLOWS = (
    r"(?:is|are|was|were|be|been|being|means|holds|makes|does|did|has|have|had|"
    r"will|would|can|could|should|must|may|might|gets|goes|comes|stays|reads|"
    r"says|tells|shows|leaves|keeps|belongs|applies|counts|matters|happens|"
    r"works|fails|remains|becomes|seems|looks|sounds|feels)"
)
OPEN_PRONOUN_RE = re.compile(
    rf"^(it|this|that|these|those|them)(?:\s*,|\s+{_BARE_SUBJECT_FOLLOWS}\b)", re.I
)
PROHIBITION_RE = re.compile(r"\b(never|do not|don't|avoid|must not|cannot|no longer)\b", re.I)
ALLCAPS_RE = re.compile(r"\b[A-Z]{3,}\b")
SEMICOLON_SPLIT = re.compile(r";\s*")
CONJOINED_RE = re.compile(
    r",?\s+and\s+(never|always|do not|don't|use|write|note|keep|give|refer|ask|say|return|output)\b",
    re.I,
)


def strip_json_blocks(text):
    """Remove embedded JSON shape passages and fenced code from a prompt.

    A "return ONLY a JSON object with this exact shape: { ... }" passage is a
    wire contract. Its length says nothing about how readable the prose is,
    and leaving it in floods the report with flags nobody will act on.
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    out = []
    depth = 0
    buf = []
    for ch in text:
        if ch == "{":
            depth += 1
            buf.append(ch)
            continue
        if depth:
            buf.append(ch)
            if ch == "}":
                depth -= 1
                if depth == 0:
                    block = "".join(buf)
                    buf = []
                    # A short brace group is probably a template variable such
                    # as {applicant_name}; keep it so the sentence still reads.
                    if len(block) < 40 or '":' not in block:
                        out.append(block)
                    else:
                        out.append(" ")
            continue
        out.append(ch)
    out.append("".join(buf))
    return "".join(out)


def sentences(text):
    """Split into sentences, treating a line break and a bullet as a boundary.

    Newlines are boundaries BEFORE whitespace is collapsed. Several prompts in
    this repo carry a rule per line with no terminal period, and collapsing
    first would weld those lines into one 300 word sentence that no author
    ever wrote.
    """
    text = text.replace("\r\n", "\n")
    parts = []
    for line in text.split("\n"):
        line = re.sub(r"^\s*[-*•]\s*", "", line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            continue
        parts.extend(
            s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[\"'\(A-ZÀ-ſ])", line) if s.strip()
        )
    return parts


def is_instruction(sentence):
    first = re.sub(r"^[^A-Za-zÀ-ſ]+", "", sentence).split(" ")[0].lower()
    return first in IMPERATIVE_VERBS


def clause_is_instruction(clause):
    return is_instruction(clause)


def check_sentence(sentence):
    """Return the list of rule ids this sentence violates."""
    flags = []
    words = len(sentence.split())
    instruction = is_instruction(sentence)

    if instruction and words > 20:
        flags.append("STE_LONG_INSTRUCTION")
    elif not instruction and words > 25:
        flags.append("STE_LONG_DESCRIPTIVE")

    clauses = [c for c in SEMICOLON_SPLIT.split(sentence) if c.strip()]
    if len(clauses) > 1 and sum(1 for c in clauses if clause_is_instruction(c)) >= 2:
        flags.append("STE_MULTI_INSTRUCTION")
    elif instruction and CONJOINED_RE.search(sentence):
        flags.append("STE_MULTI_INSTRUCTION")

    for match in PASSIVE_RE.finditer(sentence):
        if match.group(1).lower() not in PASSIVE_EXCEPT:
            flags.append("STE_PASSIVE")
            break

    if OPEN_PRONOUN_RE.match(sentence):
        flags.append("STE_OPEN_PRONOUN")

    return flags, words


def analyse(text):
    text = re.sub(r"[ \t]+", " ", text)
    sents = sentences(text)
    per_rule = {}
    findings = []
    total_words = 0
    prohibitions = 0
    affirmations = 0

    for sentence in sents:
        flags, words = check_sentence(sentence)
        total_words += words
        if PROHIBITION_RE.search(sentence):
            prohibitions += 1
        elif is_instruction(sentence):
            affirmations += 1
        for flag in flags:
            per_rule[flag] = per_rule.get(flag, 0) + 1
        if flags:
            findings.append({"flags": flags, "words": words, "sentence": sentence})

    allcaps = len(ALLCAPS_RE.findall(text))
    return {
        "sentences": len(sents),
        "words": total_words,
        "mean_sentence_words": round(total_words / len(sents), 1) if sents else 0.0,
        "flagged_sentences": len(findings),
        "flag_rate_pct": round(100 * len(findings) / len(sents), 1) if sents else 0.0,
        "rules": per_rule,
        "prohibitions": prohibitions,
        "affirmations": affirmations,
        "prohibition_ratio": round(prohibitions / affirmations, 2) if affirmations else None,
        "emphasis_per_1000w": round(1000 * allcaps / total_words, 1) if total_words else 0.0,
        "findings": findings,
    }


def walk_strings(obj, prefix=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk_strings(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk_strings(value, f"{prefix}[{index}]")
    elif isinstance(obj, str):
        yield prefix, obj


def collect_files(roots):
    files = []
    for root in roots:
        root = pathlib.Path(root)
        if root.is_dir():
            files.extend(sorted(root.glob("*.json")))
        elif root.exists():
            files.append(root)
    return files


def lint_file(path, include_comments=False, keep_json_blocks=False, min_chars=120):
    report = {}
    data = json.loads(pathlib.Path(path).read_text())
    for key, text in walk_strings(data):
        if not include_comments and "_comment" in key:
            continue
        if not PROMPT_KEY.search(key):
            continue
        if len(text) < min_chars:
            continue
        body = text if keep_json_blocks else strip_json_blocks(text)
        report[key] = analyse(body)
    return report


def run(roots, include_comments=False, keep_json_blocks=False):
    result = {}
    for path in collect_files(roots):
        per_key = lint_file(path, include_comments, keep_json_blocks)
        if per_key:
            result[str(pathlib.Path(path).relative_to(REPO))] = per_key
    return result


def summarise(result):
    """Totals across every prompt, for the one line a human remembers."""
    totals = {"sentences": 0, "words": 0, "flagged": 0, "rules": {},
              "prohibitions": 0, "affirmations": 0}
    for per_key in result.values():
        for stats in per_key.values():
            totals["sentences"] += stats["sentences"]
            totals["words"] += stats["words"]
            totals["flagged"] += stats["flagged_sentences"]
            totals["prohibitions"] += stats["prohibitions"]
            totals["affirmations"] += stats["affirmations"]
            for rule, count in stats["rules"].items():
                totals["rules"][rule] = totals["rules"].get(rule, 0) + count
    return totals


def print_report(result, show_findings=0):
    for path, per_key in result.items():
        print(f"\n{path}")
        for key, stats in sorted(per_key.items(), key=lambda kv: -kv[1]["flag_rate_pct"]):
            ratio = stats["prohibition_ratio"]
            ratio_text = f"{ratio}:1" if ratio is not None else "n/a"
            print(
                f"  {key:44s} {stats['sentences']:4d} sent  "
                f"mean {stats['mean_sentence_words']:5.1f}w  "
                f"flagged {stats['flag_rate_pct']:5.1f}%  "
                f"prohibit {ratio_text:>7s}  caps/1kw {stats['emphasis_per_1000w']:5.1f}"
            )
            if stats["rules"]:
                inline = "  ".join(f"{r.replace('STE_','').lower()}={c}"
                                   for r, c in sorted(stats["rules"].items()))
                print(f"  {'':44s} {inline}")
            for finding in stats["findings"][:show_findings]:
                tags = ",".join(f.replace("STE_", "").lower() for f in finding["flags"])
                print(f"      [{finding['words']:3d}w {tags}] {finding['sentence'][:220]}")

    totals = summarise(result)
    rate = 100 * totals["flagged"] / totals["sentences"] if totals["sentences"] else 0
    ratio = (totals["prohibitions"] / totals["affirmations"]) if totals["affirmations"] else 0
    print(
        f"\nTOTAL  {totals['words']} words, {totals['sentences']} sentences, "
        f"{totals['flagged']} flagged ({rate:.1f}%), "
        f"prohibition to affirmation {ratio:.1f}:1"
    )
    for rule, count in sorted(totals["rules"].items(), key=lambda kv: -kv[1]):
        print(f"  {rule:24s} {count}")


def compare(result, baseline):
    """Exit 1 when any prompt got worse than the baseline. Silence is a pass."""
    worse = []
    for path, per_key in result.items():
        for key, stats in per_key.items():
            before = baseline.get(path, {}).get(key)
            if before is None:
                continue
            for rule, count in stats["rules"].items():
                was = before.get("rules", {}).get(rule, 0)
                if count > was:
                    worse.append(f"{path}:{key} {rule} {was} -> {count}")
            # The RATIO, never the raw count. Splitting "never do X, and never
            # do Y" into two sentences raises the prohibition count while
            # making the prompt strictly more testable, and a gate that fails
            # on that would punish exactly the edit it exists to encourage.
            was_ratio = before.get("prohibition_ratio")
            now_ratio = stats.get("prohibition_ratio")
            if was_ratio is not None and now_ratio is not None and now_ratio > was_ratio:
                worse.append(
                    f"{path}:{key} prohibition ratio {was_ratio}:1 -> {now_ratio}:1"
                )
    if worse:
        print("WORSE THAN BASELINE:")
        for line in worse:
            print(f"  {line}")
        return 1
    print("No rule count rose above the baseline.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("paths", nargs="*", help="JSON files or directories (default: served config)")
    parser.add_argument("--json", dest="json_out", help="write the full report to this file")
    parser.add_argument("--baseline", help="compare against a previous --json report and exit 1 if worse")
    parser.add_argument("--show", type=int, default=0, help="print this many flagged sentences per prompt")
    parser.add_argument("--include-comments", action="store_true", help="also lint _comment blocks")
    parser.add_argument("--keep-json-blocks", action="store_true", help="do not strip embedded JSON shapes")
    parser.add_argument("--quiet", action="store_true", help="totals only")
    args = parser.parse_args(argv)

    roots = args.paths or DEFAULT_ROOTS
    result = run(roots, args.include_comments, args.keep_json_blocks)

    if not result:
        print("No prompt bearing keys found. Check the paths.", file=sys.stderr)
        return 2

    if args.json_out:
        pathlib.Path(args.json_out).write_text(json.dumps(result, indent=2))

    if args.baseline:
        baseline = json.loads(pathlib.Path(args.baseline).read_text())
        return compare(result, baseline)

    if args.quiet:
        totals = summarise(result)
        print(json.dumps(totals, indent=2))
    else:
        print_report(result, args.show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
