# Model routing — call_type spec for ShoulderSurf iOS

> **Last updated:** 2026-05-07.
> **Status:** GP-side shipped; iOS migration pending.

What `call_type` (and `prompt_mode`) ShoulderSurf iOS should set on
each `POST /v1/chat` request. Lets the GhostPour admin dashboard dial
the model used on each surface (Copilot/freeform, Meeting Chat,
Project Chat) and within a surface, first-send vs follow-up,
independently.

## The matrix

| Surface | First send | Follow-up |
|---|---|---|
| Copilot / in-meeting freeform | `call_type: "query"` | `call_type: "query_follow_up"` |
| Meeting Chat | `call_type: "meeting_chat"`<br/>`prompt_mode: "PostMeetingChat"` | `call_type: "meeting_chat_follow_up"`<br/>`prompt_mode: "PostMeetingChat"` |
| Project Chat | `call_type: "project_chat"`<br/>`prompt_mode: "ProjectChat"` | `call_type: "project_chat_follow_up"`<br/>`prompt_mode: "ProjectChat"` |

Background paths (unchanged): `summary`, `analysis`, `report`.

## Why three follow-up types instead of one

Before this respec, `follow_up` was a single shared dial — flipping it
affected every surface's follow-up at once. Splitting per-surface lets
ops tune Project Chat follow-ups (which carry rich CQ recall context
and benefit from Sonnet) differently from Copilot follow-ups (which
are usually quick refinements where Haiku is fine).

## Server-side fallback (transition aid)

While iOS migrates, GP's resolver applies prompt_mode preference so
legacy clients still route correctly:

- `prompt_mode == "ProjectChat"` + any `call_type` other than
  `project_chat_follow_up` → routes to **`project_chat`** dial
  (matches PR #161 behavior).
- `prompt_mode == "PostMeetingChat"` + any `call_type` other than
  `meeting_chat_follow_up` → routes to **`meeting_chat`** dial.

Once iOS sends the canonical `call_type` per the matrix above, the
prompt_mode fallback becomes a no-op — but it's not load-bearing for
correctness. Don't delete prompt_mode from the request just because
the call_type covers it.

## What changes for iOS

1. **Drop** any code path that sets `call_type: "follow_up"` — that
   row no longer exists. (Searches of GP traffic over the past 30
   days suggest no observed sends use that string, but worth a grep
   on iOS just to confirm.)
2. **Add** the four new call_types in the matrix above.
3. **Existing `prompt_mode` values stay** — `ProjectChat`,
   `PostMeetingChat`, `AutoSummary`, `PostSessionAnalysis`. No iOS
   change needed there.

## Dashboard

Admin → Model Routing renders one row per call_type. Changes save via
PUT and hot-reload `app.state.remote_configs` — next request honors
the new value, no restart.

Defaults shipped 2026-05-07:

| call_type | Free | Plus | Pro |
|---|---|---|---|
| `summary` | Haiku 4.5 | Haiku 4.5 | Haiku 4.5 |
| `analysis` | Haiku 4.5 | Haiku 4.5 | Sonnet 4.6 |
| `report` | Haiku 4.5 | Haiku 4.5 | Sonnet 4.6 |
| `query` | Haiku 4.5 | Haiku 4.5 | Sonnet 4.6 |
| `query_follow_up` | Haiku 4.5 | Haiku 4.5 | Haiku 4.5 |
| `meeting_chat` | Haiku 4.5 | Haiku 4.5 | Sonnet 4.6 |
| `meeting_chat_follow_up` | Haiku 4.5 | Haiku 4.5 | Haiku 4.5 |
| `project_chat` | Haiku 4.5 | Haiku 4.5 | Sonnet 4.6 |
| `project_chat_follow_up` | Haiku 4.5 | Haiku 4.5 | Haiku 4.5 |

Pattern: first sends use the better model on Pro; follow-ups stay
cheap by default. Dial up via dashboard if a specific cell needs
something else.

## Why follow-ups default to Haiku

Most follow-ups are short refinements ("can you elaborate on point
3?", "make that more concise") that don't benefit much from Sonnet's
reasoning on top of an already-Sonnet-generated first answer. Keeping
follow-ups on Haiku captures most of the cost upside without an
obvious quality regression. If telemetry shows otherwise, dial up via
dashboard — no code change needed.
