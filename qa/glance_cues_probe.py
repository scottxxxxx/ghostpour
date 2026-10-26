"""Does Sonnet 5 hold a glance cue block reliably? (SS Dev ask, 2026-10-25)

Scott wants "glance" answers: 4 or 5 cue cards (label | headline | keywords)
plus the full answer in the same call. SS proposed a delimited block ahead of
the answer. This sends a draft glance instruction appended to the Help Me
Respond mode prompt, the way the client would append it, over synthetic
interview and meeting questions (no user data), and checks the format
mechanically: block present and first, 4 or 5 lines, 3 pipe fields each, no
bullets, no dashes, headline short, full answer after the block.

Request shape mirrors SS Copilot today: served systemPromptTemplate with the
default rules, thinking disabled (#1137).

  .venv/bin/python qa/glance_cues_probe.py --sync --stream
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from qa import probe_runner  # noqa: E402

GLANCE = (
    "GLANCE CARDS: the user reads your answer in quick glances while they keep "
    "talking, so before the full answer write a cue block in exactly this form:\n"
    "<cues>\n"
    "LABEL | headline | keyword, keyword\n"
    "</cues>\n"
    "Each line has exactly three parts separated by the pipe character. LABEL "
    "is one or two words in capitals naming the idea. The headline is a "
    "sentence of at most 10 words in the user's own voice, words they can say "
    "out loud as written, never advice about what to say. Keywords are two to "
    "four short specifics: names, numbers, examples. Order the lines the way "
    "the user would say them, one idea per line.\n"
    "Only facts the user has given, things said in the conversation, and "
    "general knowledge go in the block. The block never contains a square "
    "bracket: when a card would need a fact you do not have, leave that card "
    "out. Two or three solid cards beat five with gaps. Aim for 4 or 5 when "
    "the facts support them. Missing facts are handled in the full answer, "
    "not in the cards.\n"
    "No bullets, numbers, bold or quotation marks inside the block. Write the "
    "block in the language of your answer. After </cues>, write the full "
    "answer as you normally would, and keep every fact in the block also in "
    "the answer."
)

SUMMARY = (
    "Meeting summary so far: a hiring panel interview for a Staff AI Engineer "
    "role. The user has said they led an agent platform at a previous company, "
    "built about a dozen specialized agents for enterprise customers, and owned "
    "the routing and integration layer."
)

CASES = [
    ("int_scale", "Interviewer: Tell me about the largest agent system you've built. How many agents, and what did you own?"),
    ("int_fail", "Interviewer: What's a time an AI feature you shipped went wrong in production, and what did you do?"),
    ("int_why", "Interviewer: Why are you interested in this role specifically?"),
    ("mtg_pushback", "Priya: I'm not convinced we should move the launch. What's the actual risk if we ship on the fifteenth?"),
    ("mtg_status", "Dan: Can you give everyone a quick update on where the integration stands?"),
    ("know_rag", "Interviewer: How would you explain the difference between RAG and fine tuning to a customer?"),
    ("es_int", "Entrevistador: ¿Cuál fue el mayor reto técnico de tu último proyecto y cómo lo resolviste?"),
]
MEETING_SUMMARY = (
    "Meeting summary so far: weekly launch review for the billing integration. "
    "The user leads integration. Stated so far: the Stripe webhook retry fix "
    "merged Tuesday; load testing found p95 latency of 900 ms against a 500 ms "
    "target; Dan owns the status page update; launch is planned for the fifteenth."
)
REPS = 2
PP = str(ROOT / "config/remote/protected-prompts.json")


def build_system(mode_prompt: str, summary: str = SUMMARY) -> str:
    pp = json.loads(Path(PP).read_text())
    s = pp["systemPromptTemplate"]
    for slot, val in (("{{user_instructions}}", pp["defaultUserInstructions"]),
                      ("{{prompt_system_instructions}}", mode_prompt + "\n\n" + pp.get("glanceInstruction", GLANCE)),
                      ("{{context_quilt}}", ""), ("{{summary}}", summary), ("{{project}}", "")):
        s = s.replace(slot, val)
    return s


def check(text: str) -> dict:
    m = re.search(r"<cues>\s*\n(.*?)\n\s*</cues>", text, re.S)
    out = {"block": bool(m), "first": text.lstrip().startswith("<cues>")}
    if not m:
        return out
    lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
    out["n_lines"] = len(lines)
    out["three_fields"] = all(len(ln.split("|")) == 3 for ln in lines)
    out["no_bullets"] = not any(re.match(r"^([-*•·]|\d+[.)])", ln) for ln in lines)
    out["headline_max_words"] = max((len(ln.split("|")[1].split()) for ln in lines if ln.count("|") == 2), default=0)
    out["no_dashes"] = not re.search(r"[–—]| - ", m.group(1))
    out["brackets_in_cues"] = "[" in m.group(1)
    out["answer_after"] = len(text[m.end():].strip())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    probe_runner.add_args(ap)
    ap.add_argument("--pp", default=PP, help="protected-prompts file to build from")
    args = ap.parse_args()
    globals()["PP"] = args.pp
    pp = json.loads(Path(PP).read_text())
    mode = pp["defaultPromptModes"][1]["systemPrompt"]
    requests = []
    for cid, q in CASES:
        for r in range(REPS):
            requests.append((f"{cid}-{r}", {
                "model": "claude-sonnet-5", "max_tokens": 4096,
                "thinking": {"type": "disabled"},
                "system": build_system(mode, MEETING_SUMMARY if cid.startswith("mtg") else SUMMARY),
                "messages": [{"role": "user", "content": "Recent transcript (~2 minutes):\n" + q}],
            }))
    key = get_settings().anthropic_api_key
    results = probe_runner.run(requests, args, key, Path(__file__).name)
    if not results:
        return
    rows = []
    for cid, _ in requests:
        r = results.get(cid, {})
        msg = r.get("message")
        text = probe_runner.text_of(msg) if msg else ""
        row = {"id": cid, "ttft": r.get("ttft"), "secs": r.get("secs"), "error": r.get("error"),
               **check(text), "text": text}
        rows.append(row)
        print({k: v for k, v in row.items() if k != "text"})
    out = ROOT / "qa/runs/glance-cues-probe-2026-10-25-v5-served.json"
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print("saved", out)


if __name__ == "__main__":
    main()
