# Raw captures of the interviewer lane's sentence stream

Made 2026-09-20 for the N-400 client by `tests/capture_n400_stream_fixtures.py`.
Re-make them with:

    .venv/bin/python -m pytest tests/capture_n400_stream_fixtures.py -q -p no:cacheprovider

Each file is exactly what left the server, byte for byte, with a
`<name>.meta.json` beside it holding the HTTP status, the content type and a
note. The PROVIDER is a mock (the route tests' one), so nothing here cost
anything. Everything else is the production path: the framing function, the
release controller, the guard tail and the envelope.

What a mock cannot show, so do not read it off these files: real provider
chunk boundaries and timing, and the shape of a real upstream failure.

| file | what it is |
|---|---|
| 01-ordinary.sse | two sentence events, then the envelope with the third sentence held |
| 02-buffered-reply-before-keys.sse | reply came before the three keys, nothing streams |
| 03-buffered-oath.sse | an oath node on the agenda, nothing streams |
| 04-error-after-release.sse | two sentences, then one error event, no envelope, HTTP 200 |
| 05-progress-then-ordinary.sse | progress frames first (tick shortened to capture them, so elapsed reads 0) |
| 06-spanish-non-ascii.sse | UTF-8 sent raw, never \u escaped |
| 07a / 07b | a streamed turn, then the SAME top level turn_id again: plain JSON replay |
| 08a / 08b | a stream that died unbilled, then the same id again: a fresh stream |
| 09a / 09b | a 429 before any stream, then the same id again: turn_in_progress |
