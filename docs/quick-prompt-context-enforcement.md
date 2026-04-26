# Quick-Prompt Context Enforcement

> **Last updated:** April 26, 2026
> **Owner:** Shoulder Surf iOS, with optional GP-side backstop
> **Status:** SS client landed April 26, 2026 — GP changes below are required for the kill switch + recommended for default prompts shipped via remote config.

## Problem

A user could trigger a quick-prompt button (e.g. "Catch Me Up", "Action Items") with no transcript, no images, and no follow-up — turning the app into a free ChatGPT passthrough at our token expense. The freeform/ad-hoc composer is a legitimate context-free path; quick-prompt buttons are not.

## SS-side fix (already shipped)

Two new fields on the iOS side, both with backward-compatible defaults:

1. **`PromptMode.requiresContext: Bool`** — per-prompt flag, defaults `true`. When `true`, the SS client blocks the query at send-time if the resolved payload has no transcript, no images, and no follow-up. Users can flip this off in the prompt editor for prompts that genuinely don't need meeting context.

2. **`ProtectedPromptsConfig.requireMeetingContext: Bool?`** — global kill switch served via `/v1/config/protected-prompts`. When `true`, every quick-prompt is blocked regardless of its individual `requiresContext` flag. Freeform composer is always exempt.

The strictest layer wins: `effectiveRequires = (server.requireMeetingContext == true) || mode.requiresContext`.

## What GP needs to do

### 1. Extend `protected-prompts.json` schema

Add **two optional fields**. Both are backward-compatible — clients on older builds ignore unknown keys.

#### a) Top-level `requireMeetingContext` (kill switch)

```json
{
  "version": 2,
  "requireMeetingContext": false,
  "defaultGlobalSystemInstructions": "...",
  "summaryPrompts": { ... },
  "defaultPromptModes": [ ... ]
}
```

| Field | Type | Default | Purpose |
|---|---|---|---|
| `requireMeetingContext` | `bool?` | omit / `false` | When `true`, all quick prompts blocked client-side unless payload has transcript/image/follow-up. Flip to `true` only in an emergency (active abuse seen in usage logs). |

#### b) Per-prompt `requiresContext` field

Each entry in `defaultPromptModes` may now declare:

```json
{
  "name": "Catch Me Up",
  "icon": "sparkles",
  "colorHex": "#007AFF",
  "systemPrompt": "...",
  "requiresContext": true
}
```

| Field | Type | Default | Purpose |
|---|---|---|---|
| `requiresContext` | `bool?` | omit → treated as `true` | Whether this prompt needs meeting context to be useful. Set `false` only for prompts that work standalone (none of the current defaults qualify). |

**Action item for GP:** when shipping or updating any `defaultPromptModes` entry, set `requiresContext` explicitly. All current defaults (Catch Me Up, Help Me Respond, Action Items, etc.) should have `requiresContext: true`. Future prompts that are intentionally context-free (rare) should set `false`.

### 2. Bump `version` when these fields change

Standard remote-config flow — clients only re-fetch when `X-Config-Version` differs.

### 3. (Optional, recommended) Server-side backstop on `/v1/chat`

The client-side gate has one weakness: clients that haven't fetched the latest config won't honor the kill switch until their next sync. For a true emergency, GP should refuse the request server-side.

Suggested contract additions on chat requests originating from quick prompts:

| Header / field | Source | Use |
|---|---|---|
| `X-SS-Prompt-Source: quick_prompt \| freeform \| follow_up \| auto_summary \| post_session` | SS client | Lets GP differentiate quick-prompts (gateable) from freeform (always allowed). |
| `X-SS-Has-Context: true \| false` | SS client | SS attests whether the outgoing payload contained transcript/image/follow-up. |

Then GP can:
- If `requireMeetingContext` is set in tenant/global config **and** `X-SS-Prompt-Source: quick_prompt` **and** `X-SS-Has-Context: false` → return `403` with a structured error code SS can surface.

This is **not required for the initial rollout** — the per-prompt flag and config-level kill switch handle the common case. Server backstop is the "we've seen telemetry showing real abuse" upgrade.

## Rollout order

1. **Now:** SS client ships with both fields decoded and the per-prompt gate active. Bundled `ProtectedPrompts.json` fallback keeps current behavior (no `requireMeetingContext`, all defaults inherit `requiresContext: true`).
2. **Next GP config push:** add explicit `"requiresContext": true` to every entry in `defaultPromptModes`, bump `version`. No behavior change, but makes the contract explicit and lets the dashboard edit it.
3. **Future (optional):** add the server-side backstop when usage patterns justify it.

## Quick reference: gate logic (SS client)

```
isFreeformQuery     = (buttonKey == "freeform")
serverKillSwitch    = (protectedPrompts.requireMeetingContext == true)
mustHaveContext     = serverKillSwitch || mode.requiresContext
payloadHasContext   = transcript.nonEmpty || images.nonEmpty || followUp.nonEmpty

if mustHaveContext && !payloadHasContext && !isFreeformQuery:
    block with user-facing error
```

Auto-summary, post-session analysis, and report generation are gated separately (see `SessionManager.generateRollingSummary` and `triggerPostSessionAnalysis` — word-count + duration + silence guards). They are not affected by these flags.
