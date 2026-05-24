#!/usr/bin/env python3
"""Captions cleanup eval harness.

Sweeps multiple OpenRouter-hosted models on the same OCR cleanup prompt and
scores outputs against a ground-truth transcript. Models, prices, and the
prompt are intentionally inlined so the harness is self-contained and easy
to tune without touching the rest of the codebase.

Usage:
    python tests/evals/captions_cleanup/run_eval.py \\
        --raw  ~/Downloads/raw_ocr.txt \\
        --truth ~/Downloads/ground_truth.txt

Reads CZ_OPENROUTER_API_KEY from env or .env in the working directory.
Outputs land in /tmp/captions_eval/ (per-model outputs + results.json).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import httpx


OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OUT_DIR = Path("/tmp/captions_eval")

# Approximate prices per 1M tokens (input, output) in USD. Sourced from
# openrouter.ai/models on 2026-05-23. Recheck before quoting publicly.
MODELS = [
    # MoE — original sweep
    {"id": "mistralai/mixtral-8x22b-instruct", "in": 0.90, "out": 0.90, "tag": "moe"},
    {"id": "meta-llama/llama-4-scout",         "in": 0.08, "out": 0.30, "tag": "moe"},
    {"id": "meta-llama/llama-4-maverick",      "in": 0.18, "out": 0.60, "tag": "moe"},
    {"id": "qwen/qwen3-235b-a22b",             "in": 0.14, "out": 0.60, "tag": "moe"},
    {"id": "qwen/qwen3-30b-a3b",               "in": 0.08, "out": 0.30, "tag": "moe"},
    {"id": "moonshotai/kimi-k2",               "in": 0.55, "out": 2.20, "tag": "moe"},
    {"id": "deepseek/deepseek-chat",           "in": 0.27, "out": 1.10, "tag": "moe"},
    {"id": "z-ai/glm-4.6",                     "in": 0.50, "out": 1.50, "tag": "moe"},
    # Dense / mini — original sweep
    {"id": "anthropic/claude-haiku-4.5",       "in": 1.00, "out": 5.00, "tag": "dense"},
    {"id": "google/gemini-2.5-flash",          "in": 0.30, "out": 2.50, "tag": "dense"},
    {"id": "google/gemini-2.5-flash-lite",     "in": 0.10, "out": 0.40, "tag": "dense"},
    {"id": "openai/gpt-4.1-mini",              "in": 0.40, "out": 1.60, "tag": "dense"},
    {"id": "openai/gpt-4o-mini",               "in": 0.15, "out": 0.60, "tag": "dense"},
    # Tier 1 — structured-rewriting specialists
    {"id": "mistralai/mistral-small-3.2-24b-instruct", "in": 0.05, "out": 0.15, "tag": "rewrite"},
    {"id": "cohere/command-a",                 "in": 2.50, "out": 10.00, "tag": "rewrite"},
    {"id": "ai21/jamba-1.6-large",             "in": 2.00, "out": 8.00, "tag": "rewrite"},
    {"id": "qwen/qwen3-coder",                 "in": 0.20, "out": 0.80, "tag": "rewrite"},
    # Tier 2 — small/cheap value plays
    {"id": "microsoft/phi-4",                  "in": 0.07, "out": 0.14, "tag": "value"},
    {"id": "mistralai/ministral-8b",           "in": 0.10, "out": 0.10, "tag": "value"},
    {"id": "anthropic/claude-3.5-haiku",       "in": 0.80, "out": 4.00, "tag": "value"},
    {"id": "meta-llama/llama-3.3-70b-instruct","in": 0.13, "out": 0.40, "tag": "value"},
    # Tier 3 — wildcards
    {"id": "nvidia/llama-3.3-nemotron-super-49b-v1.5", "in": 0.13, "out": 0.40, "tag": "wildcard"},
    {"id": "nvidia/nemotron-nano-9b-v2",       "in": 0.04, "out": 0.16, "tag": "wildcard"},
    {"id": "nvidia/nemotron-3-super-120b-a12b","in": 0.30, "out": 0.90, "tag": "wildcard"},
    {"id": "nvidia/nemotron-3-nano-30b-a3b",   "in": 0.08, "out": 0.30, "tag": "wildcard"},
    {"id": "nousresearch/hermes-3-llama-3.1-70b","in": 0.40, "out": 0.40, "tag": "wildcard"},
    {"id": "x-ai/grok-4.3",                    "in": 0.20, "out": 0.80, "tag": "wildcard"},
    {"id": "x-ai/grok-4.20",                   "in": 0.30, "out": 1.50, "tag": "wildcard"},
    {"id": "openai/gpt-5-mini",                "in": 0.25, "out": 2.00, "tag": "wildcard"},
    {"id": "ai21/jamba-large-1.7",             "in": 2.00, "out": 8.00, "tag": "wildcard"},
    {"id": "mistralai/ministral-8b-2512",      "in": 0.10, "out": 0.10, "tag": "wildcard"},
    {"id": "mistralai/ministral-14b-2512",     "in": 0.14, "out": 0.20, "tag": "wildcard"},
    {"id": "microsoft/phi-4-mini-instruct",    "in": 0.07, "out": 0.14, "tag": "wildcard"},
    # Quality ceiling
    {"id": "anthropic/claude-sonnet-4.6",      "in": 3.00, "out": 15.00, "tag": "ceiling"},
]


SYSTEM_PROMPT = """You are cleaning up a raw OCR'd transcript of a live meeting. The text was extracted from on-screen captions captured by a phone camera pointed at a screen.

Critical constraint: every word that was actually said is already present somewhere in the raw OCR. Your job is to RESTRUCTURE, not RECOVER. Do not invent dialogue, do not add details that aren't in the raw input, do not paraphrase into content the speakers did not say. If a sentence trails off mid-thought in the raw, it should trail off mid-thought in your output.

The OCR introduces predictable noise that you must fix:

1. Scroll duplicates. As the captions panel scrolled, the same phrases were OCR'd multiple times and concatenated into the same utterance. Dedupe aggressively, keep the most coherent version, drop the partial repeats.

2. Speaker name variants. The same person appears under multiple OCR'd name spellings (for example "Suresh Muchakurti" / "Suresh Muchakurtir" / "suresn muchandis"). Pick the most frequent legible spelling and use it consistently. Do not invent corrections to names — consistency matters more than perfect spelling.

3. Speaker-label leaks. A spoken phrase like "Did Vijay we had a call..." may have been misread as a new speaker label "[Did Vijay]". When the label is grammatically a sentence fragment continuing the prior thought, fold it back into the correct speaker's turn. Example: if "[Did Vijay]" appears followed by "we had a call on the calendar, right?" and the prior turn was Sukumar asking about a meeting, attribute the whole sentence to Sukumar (he's asking Vijay a question).

4. Garbage tokens. Remove meaningless OCR symbols like "‹®", "cO", "tO", "«e", "<0", "(e", stray Cyrillic or Arabic characters that don't belong, and isolated punctuation.

5. Repetitive single-word interjections (like "Yeah." repeated across many turns) should be collapsed to one acknowledgment per natural conversational break.

Output format: one paragraph per speaker turn, in conversational order:

SpeakerName: utterance text.

Output ONLY the cleaned transcript. No preamble, no explanation, no commentary, no markdown fences."""


OCR_ARTIFACT_RE = re.compile(
    r"‹®|\bcO\b|\btO\b|«e|<0|\(e\b|»|°|[Ѐ-ӿ]+|[؀-ۿ]+"
)

SPEAKER_LINE_RE = re.compile(r"^\s*\[?([A-Z][A-Za-z .'-]{1,40}?)\]?\s*[:\-]\s*", re.MULTILINE)


def levenshtein_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def lcs_length(a: list, b: list) -> int:
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        ai = a[i - 1]
        for j in range(1, n + 1):
            if ai == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[n]


def rouge_l(hyp: str, ref: str) -> float:
    h = re.findall(r"\w+", hyp.lower())
    r = re.findall(r"\w+", ref.lower())
    if not h or not r:
        return 0.0
    lcs = lcs_length(h, r)
    p = lcs / len(h) if h else 0.0
    rr = lcs / len(r) if r else 0.0
    if p + rr == 0:
        return 0.0
    return 2 * p * rr / (p + rr)


def extract_speakers(text: str) -> set:
    raw = SPEAKER_LINE_RE.findall(text)
    norm = set()
    for s in raw:
        s = s.strip().lower()
        if not s:
            continue
        first = s.split()[0]
        norm.add(first)
    return norm


def speaker_f1(hyp: str, ref: str) -> float:
    h = extract_speakers(hyp)
    r = extract_speakers(ref)
    if not h and not r:
        return 1.0
    if not h or not r:
        return 0.0
    tp = len(h & r)
    if tp == 0:
        return 0.0
    p = tp / len(h)
    rr = tp / len(r)
    return 2 * p * rr / (p + rr)


def artifact_count(text: str) -> int:
    return len(OCR_ARTIFACT_RE.findall(text))


def length_ratio(hyp: str, ref: str) -> float:
    return len(hyp) / len(ref) if ref else 0.0


async def call_or(client: httpx.AsyncClient, key: str, model: str, raw: str) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Raw OCR transcript:\n\n{raw}"},
        ],
        "temperature": 0.2,
        "max_tokens": 8000,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://cloudzap.local/eval",
        "X-Title": "captions-cleanup-eval",
    }
    t0 = time.time()
    try:
        r = await client.post(OR_URL, headers=headers, json=body, timeout=180.0)
        dt = time.time() - t0
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}", "latency_s": dt}
        data = r.json()
        choice = data.get("choices", [{}])[0]
        content = (choice.get("message") or {}).get("content", "") or ""
        usage = data.get("usage", {}) or {}
        return {
            "ok": True,
            "content": content,
            "latency_s": dt,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "served_by": data.get("provider", "?"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "latency_s": time.time() - t0}


def load_env_key() -> str:
    key = os.environ.get("CZ_OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("CZ_OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("CZ_OPENROUTER_API_KEY not in env or .env")


def composite(r: dict) -> float:
    """Weighted score: quality dominates, artifacts + length sanity penalize."""
    q = 0.35 * r["rouge_l"] + 0.25 * r["lev"] + 0.20 * r["spk_f1"]
    artifact_penalty = min(r["artifacts"] / 20.0, 1.0) * 0.10
    len_penalty = min(abs(r["len_ratio"] - 1.0), 1.0) * 0.10
    return q - artifact_penalty - len_penalty


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--raw", required=True, help="Path to raw OCR transcript")
    p.add_argument("--truth", required=True, help="Path to ground-truth transcript")
    p.add_argument("--models", default="", help="Comma-separated model ids to filter (default: all)")
    args = p.parse_args()

    raw = Path(os.path.expanduser(args.raw)).read_text()
    truth = Path(os.path.expanduser(args.truth)).read_text()
    key = load_env_key()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "raw_input.txt").write_text(raw)
    (OUT_DIR / "ground_truth.txt").write_text(truth)

    selected = MODELS
    if args.models:
        wanted = {m.strip() for m in args.models.split(",") if m.strip()}
        selected = [m for m in MODELS if m["id"] in wanted]

    print(f"Raw input: {len(raw)} chars")
    print(f"Ground truth: {len(truth)} chars")
    print(f"Models: {len(selected)}\n")

    async with httpx.AsyncClient() as client:
        responses = await asyncio.gather(
            *[call_or(client, key, m["id"], raw) for m in selected]
        )

    results = []
    for m, resp in zip(selected, responses):
        row = {"model": m["id"], "tag": m["tag"], **resp}
        if resp.get("ok"):
            out = resp["content"]
            row["lev"] = levenshtein_ratio(out, truth)
            row["rouge_l"] = rouge_l(out, truth)
            row["spk_f1"] = speaker_f1(out, truth)
            row["artifacts"] = artifact_count(out)
            row["len_ratio"] = length_ratio(out, truth)
            row["cost_usd"] = (
                resp["prompt_tokens"] * m["in"] / 1_000_000
                + resp["completion_tokens"] * m["out"] / 1_000_000
            )
            row["score"] = composite(row)
            safe = m["id"].replace("/", "__")
            (OUT_DIR / f"out_{safe}.txt").write_text(out)
        results.append(row)

    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2, default=str))

    ok = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    ok.sort(key=lambda r: -r["score"])

    bar = "=" * 110
    print(f"\n{bar}")
    print(
        f"{'MODEL':<42} {'SCORE':>6} {'LEV':>5} {'ROUGE':>6} "
        f"{'SPK':>5} {'ART':>4} {'LRAT':>5} {'LAT_S':>6} {'COST_$':>8}"
    )
    print(bar)
    for r in ok:
        print(
            f"{r['model']:<42} {r['score']:>6.3f} {r['lev']:>5.3f} {r['rouge_l']:>6.3f} "
            f"{r['spk_f1']:>5.3f} {r['artifacts']:>4d} {r['len_ratio']:>5.2f} "
            f"{r['latency_s']:>6.1f} {r['cost_usd']:>8.5f}"
        )

    if failed:
        print(f"\nFailed ({len(failed)}):")
        for r in failed:
            print(f"  {r['model']}: {r.get('error', '?')[:160]}")

    print(f"\nOutputs in {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
