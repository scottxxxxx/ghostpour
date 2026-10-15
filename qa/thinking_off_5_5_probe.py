"""Which thinking-off shape each 5.x model accepts (2026-10-14).

GP's adapter matched Opus 5 / Sonnet 5 by substring, so a thinking-free lane
would send {type: disabled} to Opus 5.5 and Sonnet 5.5 as well. The Claude
API reference says that is a 400 on both, with `between_tools` (effort high
or below) the off switch on Sonnet 5.5 and lower effort the only lever on
Opus 5.5. This proves each case on the wire before the adapter encodes it.
Expected: the *_400 cases fail, the *_ok cases answer.

    .venv/bin/python qa/thinking_off_5_5_probe.py --sync [--dry]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa import probe_runner as runner  # noqa: E402

runner.PRICES.setdefault("claude-sonnet-5-5", (2.0, 10.0))
runner.PRICES.setdefault("claude-haiku-5-5", (0.1, 0.5))

CASES = [
    ("opus55_disabled_high_400", "claude-opus-5-5", {"type": "disabled"}, "high"),
    ("opus55_omitted_low_ok", "claude-opus-5-5", None, "low"),
    ("sonnet55_disabled_high_400", "claude-sonnet-5-5", {"type": "disabled"}, "high"),
    ("sonnet55_between_tools_high_ok", "claude-sonnet-5-5", {"type": "between_tools"}, "high"),
    ("sonnet55_between_tools_xhigh_400", "claude-sonnet-5-5", {"type": "between_tools"}, "xhigh"),
    ("haiku55_disabled_high_ok", "claude-haiku-5-5", {"type": "disabled"}, "high"),
    ("haiku55_disabled_xhigh_400", "claude-haiku-5-5", {"type": "disabled"}, "xhigh"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    runner.add_args(ap)
    args = ap.parse_args()
    from app.config import get_settings
    key = get_settings().anthropic_api_key  # local .env: the Testing workspace key
    requests = []
    for cid, model, thinking, effort in CASES:
        p = {"model": model, "max_tokens": 16, "output_config": {"effort": effort},
             "messages": [{"role": "user", "content": "Reply with the single word: ok"}]}
        if thinking is not None:
            p["thinking"] = thinking
        requests.append((cid, p))
    results = runner.run(requests, args, key, Path(__file__).name)
    if results is None:
        return
    for cid, *_ in CASES:
        r = results.get(cid, {})
        got = "400" if "error" in r and "400" in r["error"] else (
            "ok" if "message" in r else r.get("error", "missing"))
        want = cid.rsplit("_", 1)[1]
        print(f"{'PASS' if got == want else 'FAIL'}  {cid}: got {got}"
              + (f"  stop={r['message'].get('stop_reason')}" if "message" in r else ""))


if __name__ == "__main__":
    main()
