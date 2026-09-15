"""Generate interviewer turns under two prompt variants, for the STE A/B.

Runs INSIDE the prod container. Reads the served config off disk and swaps one
section in memory, so NO config is written and nothing serves differently.

    python /tmp/ste_run.py <n_rows> <reps> <out.json>
    python /tmp/ste_run.py 5 1 /tmp/ste_probe.json      # the cost probe

Variant A is v29 exactly as served. Variant B is v29 with the DEFERRALS block
(systemPrompt line 83) replaced by the STE rewrite. Everything else, including
GP's own assemble_prompt, the jurisdiction variant selection and the declared
variables, is the real path.

⚠ WHAT THIS DOES NOT EXERCISE: the route's guards (stale `asking`, checkpoint
refusal, envelope retry, spanish numerals). Those rewrite the OBJECT and the
judge reads the `reply`, so the omission is defensible, but it is an omission
and the report must say so rather than imply a full-route test.

Token usage is recorded per call from the API response, so the cost is
MEASURED rather than estimated from list price.
"""
import json
import sys
import time
import urllib.request

INPUTS = "/tmp/n400-deferral-ste-inputs.json"
STE_SECTION = "/tmp/ste_section.txt"
LINE_INDEX = 83  # the DEFERRALS block


def call_model(model, key, system, user, max_tokens, thinking):
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        # NO temperature: deprecated on Claude 5, returns 400. Variation
        # across reps is therefore real sampling, which is why we repeat.
        # The system block is CACHED, as the real provider path does
        # (app/services/providers/anthropic.py builds the system field as
        # cache_control blocks). Without it every call re-reads ~24k tokens
        # at full price and the run costs roughly ten times what production
        # pays for the same turn, which would also make any cost number
        # from this harness unrepresentative of the lane.
        "system": [{"type": "text", "text": system,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }
    # ⚠ Sonnet 5 and the Opus 5 family THINK BY DEFAULT, so omitting this
    # field leaves thinking ON and it eats the max_tokens budget it shares
    # with the reply. The first probe did exactly that: five of ten calls
    # spent all 2048 tokens thinking and returned no reply at all, and the
    # five that did answer were still not the served configuration. The
    # served config says thinking "disabled"; see
    # app/services/providers/reasoning.py:anthropic_accepts_disabled_thinking.
    if thinking == "disabled":
        payload["thinking"] = {"type": "disabled"}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    text = "".join(b.get("text", "") for b in resp.get("content", [])
                   if b.get("type") == "text")
    return text, resp.get("usage", {})


def main():
    n_rows = int(sys.argv[1])
    reps = int(sys.argv[2])
    out_path = sys.argv[3]

    sys.path.insert(0, "/app")
    from app.config import get_settings
    from app.routers.config import load_remote_configs
    from app.services.prompt_assembly import assemble_prompt
    from app.services.n400_envelope import is_envelope, extract_envelope
    from app.services.spanish_numerals import numeral_variables as _numeral_variables
    globals()["is_envelope"], globals()["extract_envelope"] = is_envelope, extract_envelope

    key = get_settings().anthropic_api_key
    assert key, "no anthropic key in settings"

    configs = load_remote_configs()
    slug = "n400/interviewer-turn"
    served = configs[slug]
    model = served.get("recommendedModel")
    max_tokens = served.get("maxTokens", 2048)

    lines = served["systemPrompt"].split("\n")
    original_block = lines[LINE_INDEX]
    assert original_block.startswith("When the applicant cannot give a value"), \
        "line %d is not the DEFERRALS block; refusing to swap the wrong text" % LINE_INDEX

    ste_block = open(STE_SECTION).read().strip()

    # Variant B: same document, one line replaced. Deep-copied so variant A
    # cannot be mutated by accident.
    variant_b_lines = list(lines)
    variant_b_lines[LINE_INDEX] = ste_block
    configs_b = json.loads(json.dumps(configs))
    configs_b[slug]["systemPrompt"] = "\n".join(variant_b_lines)

    rows = json.load(open(INPUTS))["rows"][:n_rows]
    results = []
    totals = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "calls": 0}

    for row in rows:
        req = row["request"]
        for variant, cfgs in (("A_v29", configs), ("B_ste", configs_b)):
            for rep in range(reps):
                # ⚠ The route does NOT pass the client's metadata alone: it
                # merges a server-computed `spoken_numerals` hint for the
                # Spanish interviewer lane (chat.py, via
                # spanish_numerals.numeral_variables). Most rows here are es,
                # and this section is largely about partial DATES, so a
                # harness without it runs a prompt production never sends.
                variables = {**req, **_numeral_variables(
                    req["call_type"], req.get("locale"), row["user_content"])}
                assembled = assemble_prompt(
                    call_type=req["call_type"],
                    user_content=row["user_content"],
                    remote_configs=cfgs,
                    jurisdiction=req.get("jurisdiction"),
                    variables=variables,
                )
                assert assembled, "assemble_prompt returned None"
                t0 = time.monotonic()
                text, usage = call_model(model, key, assembled["system_prompt"],
                                         assembled["user_content"],
                                         assembled.get("max_tokens") or max_tokens,
                                         assembled.get("thinking"))
                ms = int((time.monotonic() - t0) * 1000)
                totals["in"] += usage.get("input_tokens", 0)
                totals["out"] += usage.get("output_tokens", 0)
                totals["cache_read"] += usage.get("cache_read_input_tokens", 0)
                totals["cache_write"] += usage.get("cache_creation_input_tokens", 0)
                totals["calls"] += 1
                # The route recovers an object wrapped in prose rather than
                # failing the turn, so the harness must too or it scores as
                # failures what production serves fine. 3 of 10 in the second
                # probe were prose-wrapped and every one was recoverable.
                obj_text = text if is_envelope(text) else (extract_envelope(text) or text)
                results.append({
                    "run": row["run"], "turn": row["turn"],
                    "locale": row["locale"], "gold_label": row["gold_label"],
                    "deferred_expected": row["deferred_expected"],
                    "variant": variant, "rep": rep,
                    "raw": text, "envelope": obj_text, "usage": usage, "ms": ms,
                })
                print(f"  {row['run']}#{row['turn']} {variant} rep{rep} "
                      f"{ms}ms in={usage.get('input_tokens')} "
                      f"out={usage.get('output_tokens')} "
                      f"cache_r={usage.get('cache_read_input_tokens', 0)}",
                      flush=True)

    json.dump({"model": model, "totals": totals, "results": results},
              open(out_path, "w"), ensure_ascii=False, indent=1)
    print("\nTOTALS", json.dumps(totals))
    print("written", out_path)


if __name__ == "__main__":
    main()
