# SS handoff — honest progress indicators

Two server-side PRs landed and are live on prod as of 2026-06-18 (#267 + #269, GIT_SHA 54e7a91). They give the client what it needs to show honest progress on long calls instead of a dead spinner. This is iOS-side work now; nothing more is pending on GP.

The driving principle: a progress indicator should reflect what we actually believe is happening to the data, never a countdown to a finish time we can't predict. We don't know how long a given request will take, so the client shouldn't pretend to. Prefer a real signal from GP when there is one; when there isn't, show progress shaped to what we expect, calibrated to what we've actually measured.

## What shipped

1. **`GET /v1/timing-hints`** — per call type latency and output-size percentiles, computed from real traffic, so the client can drive an expectation-shaped curve grounded in measured data rather than a guess.
2. **SSE progress heartbeats on `/v1/chat`** — when a call streams, GP now emits liveness events during the silent gap before the first token, so the bar can move on real server activity.

The two compose: prefer the live signal, fall back to the curve.

## Signal 1: `GET /v1/timing-hints`

Same auth as your other `/v1` calls (bearer token). Send your `X-App-ID` header so the numbers are scoped to your app; without it you get an unscoped blend across apps.

Response:

```json
{
  "window_days": 30,
  "hints": {
    "query":   {"p50_ms": 4200,  "p90_ms": 11800, "p50_output_tokens": 320,  "samples": 1503},
    "summary": {"p50_ms": 9100,  "p90_ms": 21000, "p50_output_tokens": 700,  "samples": 412},
    "analysis":{"p50_ms": 15600, "p90_ms": 38000, "p50_output_tokens": 1400, "samples": 388}
  }
}
```

Notes:

- Keyed by `call_type`. Look up the entry for the call you're about to make.
- `p50_ms` / `p90_ms` are the median and 90th percentile of wall clock response time we've actually seen. `p50_output_tokens` is the typical output size, useful when you have a live token signal to scale against (see below).
- **Aggregated per call type, never per model.** We do not expose which model served a call or how fast it is. The numbers describe the work, not the engine. Don't try to infer a model from them.
- Values are cached server side and slow changing, so fetch once on app foreground (or per session) and reuse. They self update as our real latency drifts, which is the point: you never hardcode a duration.
- A call type with too few samples is omitted from `hints`. If the entry you want isn't there, fall back to a sensible built in default for that call and move on.

## Signal 2: SSE on `/v1/chat` (when you stream)

Set `stream: true` on the request. Streaming engages only when the call is interactive: `stream` is true, `call_type` is not `summary` or `analysis`, and it isn't ProjectChat. Those background call types always come back as one JSON body, by design.

The event stream (`Content-Type: text/event-stream`, each line `data: {...}`) carries these `type`s:

```
{"type":"progress","phase":"waiting","elapsed_ms":10000}     // liveness, before first token
{"type":"progress","phase":"generating","elapsed_ms":22000}  // liveness, mid output (if a gap opens)
{"type":"text","text":"partial answer ..."}                  // content delta, concatenate in order
{"type":"done","input_tokens":...,"output_tokens":...,"cost":...,"usage":...,"ai_tier":"..."}
{"type":"error","code":"...","text":"...","http_status":...} // terminal error (see below)
```

Behavior:

- **`progress`** fires every 10 seconds of silence. `phase` is `waiting` before the first token, then `generating`. It carries `elapsed_ms` and deliberately carries **no completion fraction**: GP can't truthfully predict its own finish, so it doesn't ship a fake percentage. Use it as a liveness and phase signal, and combine it with the timing hint to shape the bar.
- **`text`** is a content delta. Concatenate every `text` in arrival order. For a structured (JSON) result the deltas are JSON fragments, so accumulate and parse at the end, not per event.
- **`done`** is the terminal success event. The accumulated text is complete when this arrives. Snap the indicator to 100% here.
- **`error`** is the terminal failure event. `code` is a typed string (for example `upstream_429`, `internal_error`, `stream_timeout`). Most carry `text`; the timeout case carries `message` instead of `text`, so read both.
- There is a 180 second wall clock cap on a stream. If a call runs past it you get `{"type":"error","code":"stream_timeout","message":"..."}` and the connection closes.

**Compatibility note worth checking first:** `progress` is a new event type. Make sure your SSE parser ignores `type`s it doesn't recognize rather than throwing. Fast chats almost never emit a heartbeat (tokens arrive inside the 10 second window), so you may only hit this on a genuinely slow call, which is exactly when you least want the stream to break.

## How to render it

Same shape for both paths: an eased curve that approaches the end and holds, plus stage copy that describes the work. Never a numeric countdown.

1. On request start, look up `hints[call_type]`. Keep `p50_ms` and `p90_ms` handy.
2. Drive the curve off elapsed time against those anchors: ease toward roughly 90% by `p50_ms`, creep toward roughly 97% by `p90_ms`, then hold. Do not let it reach 100% on an estimate.
3. **If the call streams:** keep the curve animating on the `progress` heartbeats (and flip your stage label on `phase`). Once `text` starts flowing you have a real signal, so you can advance more confidently. Snap to 100% on `done`.
4. **If it's a single response (no streaming):** run the pure time based curve off the hint. Snap to 100% when the response lands. If it runs past `p90_ms`, keep holding near the top rather than stalling, and let the client timeout bound the worst case.
5. Stage labels should track the real pipeline, something like "Reading", "Working", "Finalizing", mapped loosely off `phase` and elapsed. They describe what's happening to the data, not a clock.

## What not to do

- No countdown to a predicted finish time. We can't predict it, so don't display one.
- No fabricated percentage that completes before the response actually arrives.
- Don't surface model names, model speed, or anything that implies which engine ran. GP intentionally doesn't send it.
- Don't hardcode durations in the app. Read `/v1/timing-hints` so the estimate stays honest as our real latency moves.

## Client timeouts

Raise the request timeout on the long calls to at least 120 seconds. The single response path has no 90 second ceiling and can legitimately run well over a minute on the heavier call types. The streaming path is capped at 180 seconds. Build for the slow case, not the median.

## Where this maps for SS

- **Meeting summary and report (`summary`, `analysis`):** long, and they always come back as one JSON body (streaming is off for these by design). Use the timing hint curve only here. These are the big ones where the dead spinner hurt most.
- **Interactive query / chat (`query`):** streams. Use the heartbeats and token flow as the live signal, with the timing hint shaping the wait before the first token.
