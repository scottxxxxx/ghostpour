# Native action block — wire contract

Extends the live-session `savesToReminders` flow to the chat surfaces.
When a Project Chat / Meeting Chat ask is action-items-shaped, the chat
response envelope carries an OPTIONAL additive `native_action` block and
the client renders a one-tap "Add to Reminders" chip on that turn.
Display text stays clean — items are extracted server-side from the
finished answer by a post-answer sub-call; there are no sentinel blocks
and nothing for the client to strip.

Shipped 2026-07-20. Config flag landed in `client-config` **version 13**
(all three locales): `"native_actions": {"enabled": true, "kinds":
["reminders"]}` — served so the client can gate chip code on capability
presence; enforcement of the flag is server-side (block absent when
disabled).

## Envelope

Rides BOTH transports, additively:

- non-streaming: top-level field on the JSON chat response
- streaming: field on the SSE `done` event (same vehicle as
  `search_state`) — extraction runs after the text finishes streaming,
  so it delays only the `done` event, never visible tokens

```json
"native_action": {
  "kind": "reminders",
  "items": [
    {"title": "Review HubSpot pipeline", "due": "2026-07-24", "owner": "Scott"},
    {"title": "Ping Doug about response times"}
  ]
}
```

| Field | Type | Rules |
|---|---|---|
| `kind` | string | `"reminders"` today. Future kinds (e.g. `"email_draft"` with `subject`/`body`) extend the same block; dispatch on `kind` and ignore unknown kinds. |
| `items` | array | 1–20 entries. Never empty (an empty extraction means the block is absent). |
| `items[].title` | string | Required. 1–200 chars, imperative phrasing. |
| `items[].due` | string | Optional — ABSENT when unknown (never null on the wire). `YYYY-MM-DD`, or `YYYY-MM-DDTHH:MM` when the answer stated a time. No timezone: treat as device-local. |
| `items[].owner` | string | Optional — ABSENT when unknown. Display name as spoken, ≤80 chars. |

## When the block appears

All of the following, otherwise absent:

1. `prompt_mode` is `ProjectChat` or `PostMeetingChat`.
2. Served `native_actions.enabled` is true.
3. The question portion of the send matches the action-items prefilter
   (deterministic vocabulary — "action items", "task list", "to-do",
   "next steps", "reminders", …).
4. The post-answer extraction (Haiku, temp 0, metered as
   `native_action_extract` rows) returns ≥1 valid item **stated by the
   answer** — the extractor is instructed never to invent tasks, dates,
   or owners.
5. The turn is not an armed generation turn (no coexistence with
   `generated_files`).

Every failure anywhere is fail-open: the block is simply absent, the
answer is untouched. Clients that predate the field ignore it (additive
contract, same as `generated_files` / `search_state`).

## Client obligations

- Render the chip from the STRUCTURED items only; never parse the
  display text.
- The items describe what the visible answer says — if the user edits
  nothing, saving all items must match what they read.
- Unknown `kind` values: ignore the block entirely (forward compat).
