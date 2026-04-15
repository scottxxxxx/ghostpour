# Meeting Card Data Guide

> **Last updated:** April 14, 2026
>
> Maps the data GP returns from post-session endpoints to UI elements on the SS meeting card and report.

---

## Data Sources

Each meeting gets data from up to two GP calls:

| Source | Endpoint | When | Model | Cost |
|--------|----------|------|-------|------|
| **Analysis** | `POST /v1/chat` (call_type: analysis, prompt_mode: PostSessionAnalysis) | Every meeting ≥ 5 min | Tier model (Haiku or Sonnet) | ~$0.002 (Haiku) |
| **Report** | `POST /v1/meetings/{id}/report` | On demand or at session end | Sonnet (quality: best) or Haiku (quality: fast) | ~$0.01-$0.05 |

If a report is generated, SS can skip the analysis call — the report contains all analysis fields plus more.

---

## PostSessionAnalysis Fields

Returned in the `/v1/chat` response `text` as JSON. These are defined by the SS system prompt in `protected-prompts.json`, not by GP.

| Field | Type | Example | UI Element |
|-------|------|---------|------------|
| `sentimentScore` | float 0-100 | `64` | Internal / card background tint |
| `sentimentLabel` | string | `"Constructive but frustrated"` | Available for detail view |
| `sentimentEmoji` | string (1 emoji) | `"😤"` | Meeting card — emoji badge |
| `sentimentReason` | string | `"Coordination gaps caused friction early" ` | Emoji tooltip / detail view |
| `meetingUrgency` | string | `"high"` | Meeting card — orb color |
| `urgencyReason` | string | `"Pipeline blocked on access"` | Available for detail view |
| `title` | string | `"Staffing & Work Allocation"` | Meeting card — title |
| `suggestedTags` | string[] | `["Follow-up", "Action Items"]` | Meeting card — tag pills |
| `tagReasons` | dict | `{"Follow-up": "needs scheduling"}` | Tap-to-reveal on tag pill |
| `personalityMessage` | string | `"Scott, that was a dense one..."` | End-of-session personality prompt |

### Urgency → Color Mapping

| meetingUrgency | Orb Color | Hex |
|---------------|-----------|-----|
| `critical` | Red | `#E24B4A` |
| `high` | Orange | `#EF9F27` |
| `medium` | Yellow | `#FFD54F` |
| `low` | Green | `#4CAF50` |

---

## Report Fields (report_json)

Returned from `POST /v1/meetings/{id}/report` in the `report_json` object. Superset of analysis data.

### Sentiment

| Field | Type | Example | UI Element |
|-------|------|---------|------------|
| `sentiment.score` | int 0-100 | `64` | Card background tint intensity |
| `sentiment.label` | string | `"Constructive but frustrated"` | Detail view |
| `sentiment.detail` | string | `"Coordination gaps created friction..."` | Report body |
| `sentiment.emoji_label` | string (fixed 10) | `"frustrated"` | Card background color driver |
| `sentiment.emoji` | string (1 emoji) | `"😤"` | Meeting card — emoji badge |
| `sentiment.arc` | array | `[{"value": 30, "mood": "tense"}, ...]` | Report — sentiment bar chart |
| `sentiment.arc_narrative` | string | `"Started neutral, hit friction..."` | Report — arc description |

### Emoji Label Taxonomy (fixed, 10 values)

| Label | Suggested Card Tint | Valence |
|-------|-------------------|---------|
| `enthusiastic` | Green | Positive |
| `collaborative` | Green | Positive |
| `positive` | Green | Positive |
| `informational` | Blue/Neutral | Neutral |
| `focused` | Blue/Neutral | Neutral |
| `cautious` | Yellow | Cautious |
| `frustrated` | Orange | Negative |
| `tense` | Orange | Negative |
| `concerned` | Red/Salmon | Negative |
| `disappointed` | Red/Salmon | Negative |

### Stoplight (maps to urgency)

| Field | Type | Example | UI Element |
|-------|------|---------|------------|
| `stoplight.color` | `"red"` / `"orange"` / `"yellow"` / `"green"` | `"orange"` | Orb color (1:1 with meetingUrgency) |
| `stoplight.label` | string | `"Blocked on access"` | Subtitle or tooltip |
| `stoplight.detail` | string | `"VPC access pending..."` | Report body |

**Stoplight → Urgency Mapping (SS-side):**

| stoplight.color | Maps to | Orb Color |
|----------------|---------|-----------|
| `red` | critical | `#E24B4A` |
| `orange` | high | `#EF9F27` |
| `yellow` | medium | `#FFD54F` |
| `green` | low | `#4CAF50` |

### Tags

| Field | Type | Example |
|-------|------|---------|
| `suggested_tags[].tag` | string | `"Action Items"` |
| `suggested_tags[].reason` | string | `"Four items assigned with owners"` |

**Built-in Tag Taxonomy:**
Review, Follow-up, Schedule Meeting, Research, Share, Important, Action Items, Decision Made

SS can extend with custom tags via `tag_taxonomy` in the report request.

### Other Report Sections

| Field | Description | Always present |
|-------|-------------|---------------|
| `header.category` | Short label (e.g., "Sprint Planning") | Yes |
| `header.title` | Meeting title (10-15 words) | Yes |
| `header.summary` | 2-3 sentence overview | Yes |
| `header.attendees` | Array of names | Yes |
| `actions[]` | Action items with owner, priority, deadline | Yes (may be empty) |
| `decisions[]` | Decisions with title and detail | If any |
| `open_questions[]` | Open questions with owner | If any |
| `technical_issues[]` | Issues with severity, detail, position | If any |
| `developments[]` | Positive developments | If any |
| `queries_during_meeting[]` | User's in-session queries and first paragraph of response | If any |

---

## Report Request Fields

```json
POST /v1/meetings/{meeting_id}/report
{
  "duration_seconds": 879,
  "project": "Project Name",
  "attendees": ["Scott", "Vishnu"],
  "tag_taxonomy": ["Review", "Follow-up", "Custom Tag"],
  "meeting_start_iso": "2026-04-14T13:01:00-05:00",
  "timezone_abbr": "CDT",
  "quality": "best"
}
```

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `duration_seconds` | Yes | — | Meeting length |
| `project` | No | null | Project name |
| `attendees` | No | "(not specified)" | LLM uses this list, not transcript names |
| `tag_taxonomy` | No | 8 built-in tags | Custom tags the user has created |
| `meeting_start_iso` | No | Current UTC | ISO 8601 with timezone offset |
| `timezone_abbr` | No | UTC±N fallback | e.g., "CDT", "EST", "IST" |
| `quality` | No | "best" (Sonnet) | "fast" = Haiku, "best" = Sonnet |

---

## Report Retrieval

```
GET /v1/meetings/{meeting_id}/report  — cached copy, free, no LLM
POST /v1/meetings/{meeting_id}/report — generate new, LLM call, charges allocation
POST /v1/reports/render               — re-render from edited JSON, no LLM, free
```

Cached reports retained 30 days. SS should persist locally once received.
