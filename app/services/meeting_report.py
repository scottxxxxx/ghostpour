"""Meeting report generation: data gathering, LLM analysis, HTML rendering.

Collects meeting data from usage_log and meeting_transcripts, sends it to
the LLM for structured analysis, and renders the result into a shareable
HTML report.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.models.chat import ChatRequest, ChatResponse

logger = logging.getLogger("ghostpour.meeting_report")

# Color mappings for template rendering
_MOOD_COLORS = {
    "confident": "#639922",
    "tense": "#EF9F27",
    "concern": "#E24B4A",
    "neutral": "#888780",
}

_STOPLIGHT_COLORS = {"red": "#E24B4A", "orange": "#EF9F27", "yellow": "#FFD54F", "green": "#4CAF50"}
_STOPLIGHT_BG = {"red": "#FFF3E0", "orange": "#FFF3E0", "yellow": "#FFFDE7", "green": "#EAF3DE"}
_STOPLIGHT_TEXT = {"red": "#A32D2D", "orange": "#854F0B", "yellow": "#7D6608", "green": "#3B6D11"}
_PRIORITY_BG = {"critical": "#E24B4A", "standard": "#1a1a1a"}

_TEMPLATE_PATH = Path(__file__).parent.parent / "static" / "report_template.html"

# System prompt for the LLM analysis call
REPORT_SYSTEM_PROMPT = """You are a meeting analyst that produces structured reports. You receive a meeting transcript, a summary, an attendee list, and optionally any AI queries that were run during the meeting. You output a single JSON object following the exact schema provided.

Rules:
- Do not use hyphens to join words unless they form a standard compound word
- Be direct and concise in all text fields
- Attribute action items to specific people by name when the transcript supports it
- Use the confirmed attendee list for names, not the transcript (transcription often mangles names)
- The sentiment_score is 0 to 100 where 50 is neutral, above 50 is positive, below 50 is negative
- The stoplight color is red (blocked/critical), orange (high urgency, needs prompt attention), yellow (medium, some open items but not blocking), or green (low urgency, on track)
- The sentiment_arc should have 8 to 14 data points representing the emotional trajectory across the meeting, each tagged as "confident", "tense", "concern", or "neutral"
- The sentiment_emoji_label must be exactly one of: enthusiastic, collaborative, positive, informational, focused, cautious, frustrated, tense, concerned, disappointed. The emoji should be a single emoji that represents the chosen label.
- For suggested_tags: return 1-4 tags from the provided TAG TAXONOMY list only. Each tag needs a reason explaining why it applies.
- For queries_during_meeting: include them exactly as provided in the input, do not modify query text or response text
- Never fabricate information not present in the transcript
- If a field has no relevant data, use an empty array or null as appropriate
- When referring to the app owner, use second-person voice ("You" / "Your"). Never use parenthetical identifiers like "(you)" in output text."""

REPORT_USER_TEMPLATE = """Analyze this meeting and produce a structured JSON report.

OUTPUT FORMAT:
Respond with ONLY valid JSON, no markdown backticks, no preamble, no explanation. Just the raw JSON object.

JSON SCHEMA:
{{
  "header": {{
    "category": "string — short label like 'Technical Working Session' or 'Sprint Planning' or 'Status Update'",
    "title": "string — descriptive title summarizing the meeting's main focus, 10 to 15 words max",
    "summary": "string — 2 to 3 sentence overview of what happened and what the outcome was",
    "attendees": ["string — use the CONFIRMED ATTENDEES list below, not names from transcript"]
  }},
  "stoplight": {{
    "color": "red | orange | yellow | green",
    "label": "string — short status phrase, 3 to 6 words",
    "detail": "string — 1 to 2 sentences explaining why you chose this color"
  }},
  "sentiment": {{
    "score": "number 0-100",
    "label": "string — 2 to 5 word characterization",
    "detail": "string — 1 to 2 sentences describing the overall emotional tone",
    "emoji_label": "string — exactly one of: enthusiastic, collaborative, positive, informational, focused, cautious, frustrated, tense, concerned, disappointed",
    "emoji": "string — single emoji that represents the emoji_label",
    "arc": [
      {{
        "value": "number 20-48 representing bar height in pixels",
        "mood": "confident | tense | concern | neutral"
      }}
    ],
    "arc_narrative": "string — 2 to 3 sentences describing how the sentiment shifted during the meeting and why"
  }},
  "suggested_tags": [
    {{
      "tag": "string — tag name from the TAG TAXONOMY list",
      "reason": "string — why this tag applies to this meeting"
    }}
  ],
  "actions": [
    {{
      "owner": "string — person's name",
      "priority": "critical | standard",
      "task": "string — the action item",
      "deadline": "string | null"
    }}
  ],
  "technical_issues": [
    {{
      "severity": "gap | bug | risk",
      "title": "string — short issue title",
      "detail": "string — 2 to 4 sentences describing the issue",
      "position": "string | null — if someone stated a clear position, describe it with their name"
    }}
  ],
  "developments": [
    {{
      "title": "string — short title for a positive development",
      "detail": "string — 1 to 3 sentences"
    }}
  ],
  "decisions": [
    {{
      "title": "string — short title for the decision",
      "detail": "string — 2 to 3 sentences describing what was agreed and why"
    }}
  ],
  "open_questions": [
    {{
      "question": "string — the open question",
      "owner": "string — who is responsible"
    }}
  ],
  "queries_during_meeting": [
    {{
      "timestamp": "string",
      "mode": "string",
      "question": "string",
      "response_summary": "string"
    }}
  ]
}}

---

CONFIRMED ATTENDEES:
{attendees}

TAG TAXONOMY (use only tags from this list):
{tag_taxonomy}

MEETING TRANSCRIPT:
{transcript}

MEETING SUMMARY:
{summary}

AI QUERIES RUN DURING MEETING:
{queries_json}"""


async def gather_meeting_data(
    db: aiosqlite.Connection,
    user_id: str,
    meeting_id: str,
) -> dict:
    """Collect all data for a meeting from usage_log and meeting_transcripts.

    Returns:
        {
            "transcript": str | None,
            "summary": str | None,
            "analysis": dict | None,
            "queries": [{"timestamp", "mode", "question", "response"}],
            "project": str | None,
        }
    """
    result = {
        "transcript": None,
        "summary": None,
        "analysis": None,
        "queries": [],
        "project": None,
    }

    # Get transcript
    cursor = await db.execute(
        "SELECT transcript, project FROM meeting_transcripts WHERE meeting_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1",
        (meeting_id, user_id),
    )
    row = await cursor.fetchone()
    if row:
        result["transcript"] = row["transcript"]
        result["project"] = row["project"]

    # Get all chat calls for this meeting
    cursor = await db.execute(
        """SELECT call_type, prompt_mode, request_timestamp, metadata
           FROM usage_log
           WHERE meeting_id = ? AND user_id = ? AND status = 'success'
           ORDER BY request_timestamp ASC""",
        (meeting_id, user_id),
    )
    rows = await cursor.fetchall()

    for row in rows:
        call_type = row["call_type"]
        prompt_mode = row["prompt_mode"]
        meta = {}
        if row["metadata"]:
            try:
                meta = json.loads(row["metadata"])
            except json.JSONDecodeError:
                pass

        raw_req = meta.get("raw_request")
        raw_resp = meta.get("raw_response")

        if call_type == "summary" and prompt_mode in ("AutoSummary", "DeltaSummary", "SummaryConsolidation"):
            # Use the latest summary/consolidation
            if raw_resp:
                try:
                    resp_data = json.loads(raw_resp)
                    # Anthropic format: content[0].text
                    content = resp_data.get("content", [])
                    if content and isinstance(content, list):
                        text = content[0].get("text", "")
                    else:
                        text = resp_data.get("text", "")
                    if text:
                        result["summary"] = text
                except (json.JSONDecodeError, IndexError, KeyError):
                    pass

        elif call_type == "analysis" and prompt_mode == "PostSessionAnalysis":
            if raw_resp:
                try:
                    resp_data = json.loads(raw_resp)
                    content = resp_data.get("content", [])
                    if content and isinstance(content, list):
                        text = content[0].get("text", "")
                    else:
                        text = resp_data.get("text", "")
                    if text:
                        result["analysis"] = text
                except (json.JSONDecodeError, IndexError, KeyError):
                    pass

        elif call_type == "query":
            # Interactive query — extract for research notes
            response_text = ""

            if raw_resp:
                try:
                    resp_data = json.loads(raw_resp)
                    content = resp_data.get("content", [])
                    if content and isinstance(content, list):
                        response_text = content[0].get("text", "")
                    else:
                        response_text = resp_data.get("text", "")
                except (json.JSONDecodeError, IndexError, KeyError):
                    pass

            if response_text:
                ts = row["request_timestamp"]
                try:
                    dt = datetime.fromisoformat(ts)
                    time_str = dt.strftime("%-I:%M %p")
                except (ValueError, TypeError):
                    time_str = ts

                # Extract first meaningful paragraph of the response
                first_para = _extract_first_paragraph(response_text)

                result["queries"].append({
                    "timestamp": time_str,
                    "mode": prompt_mode or "Ask",
                    "question": prompt_mode or "Query",
                    "response": first_para,
                })

    return result


_DEFAULT_TAG_TAXONOMY = [
    "Review", "Follow-up", "Schedule Meeting", "Research",
    "Share", "Important", "Action Items", "Decision Made",
]


def build_report_prompt(
    meeting_data: dict,
    attendees: list[str] | None = None,
    tag_taxonomy: list[str] | None = None,
) -> tuple[str, str]:
    """Build the system prompt and user message for the report LLM call.

    Returns (system_prompt, user_message).
    """
    transcript = meeting_data.get("transcript") or "(No transcript available)"
    summary = meeting_data.get("summary") or "(No summary available)"
    queries = meeting_data.get("queries", [])
    attendee_list = attendees or ["(attendees not specified)"]
    tags = tag_taxonomy or _DEFAULT_TAG_TAXONOMY

    queries_json = json.dumps(queries, indent=2) if queries else "[]"

    user_message = REPORT_USER_TEMPLATE.format(
        attendees="\n".join(f"- {a}" for a in attendee_list),
        tag_taxonomy=", ".join(tags),
        transcript=transcript,
        summary=summary,
        queries_json=queries_json,
    )

    return REPORT_SYSTEM_PROMPT, user_message


def render_report_html(report_json: dict, metadata: dict) -> str:
    """Render the report JSON into HTML using the template.

    metadata should contain: meeting_date, meeting_time, meeting_duration
    """
    template = _TEMPLATE_PATH.read_text()

    header = report_json.get("header", {})
    stoplight = report_json.get("stoplight", {})
    sentiment = report_json.get("sentiment", {})
    actions = report_json.get("actions", [])
    technical_issues = report_json.get("technical_issues", [])
    developments = report_json.get("developments", [])
    decisions = report_json.get("decisions", [])
    open_questions = report_json.get("open_questions", [])
    queries = report_json.get("queries_during_meeting", [])

    # Color helpers
    sl_color = _STOPLIGHT_COLORS.get(stoplight.get("color", "green"), "#4CAF50")
    sl_bg = _STOPLIGHT_BG.get(stoplight.get("color", "green"), "#EAF3DE")
    sl_text = _STOPLIGHT_TEXT.get(stoplight.get("color", "green"), "#3B6D11")

    # Simple string replacements
    html = template
    html = html.replace("{{header.title}}", _esc(header.get("title", "")))
    html = html.replace("{{header.category}}", _esc(header.get("category", "")))
    html = html.replace("{{header.summary}}", _esc(header.get("summary", "")))
    html = html.replace("{{stoplight.label}}", _esc(stoplight.get("label", "")))
    html = html.replace("{{stoplight.detail}}", _esc(stoplight.get("detail", "")))
    html = html.replace("{{stoplight_text}}", sl_text)
    html = html.replace("{{stoplight_bg}}", sl_bg)
    html = html.replace("{{stoplight_color}}", sl_color)
    html = html.replace("{{sentiment.score}}", str(sentiment.get("score", 50)))
    html = html.replace("{{sentiment.label}}", _esc(sentiment.get("label", "")))
    html = html.replace("{{sentiment.detail}}", _esc(sentiment.get("detail", "")))
    html = html.replace("{{sentiment.arc_narrative}}", _esc(sentiment.get("arc_narrative", "")))
    html = html.replace("{{meeting_date}}", _esc(metadata.get("meeting_date", "")))
    html = html.replace("{{meeting_time}}", _esc(metadata.get("meeting_time", "")))
    html = html.replace("{{meeting_duration}}", _esc(metadata.get("meeting_duration", "")))
    # report_model_label removed — model names not exposed to end users
    html = html.replace("{{project_name}}", _esc(metadata.get("project_name", "")))

    # Remove masthead if no project name
    if not metadata.get("project_name"):
        html = _remove_conditional(html, "project_name")

    # Stoplight circles
    for color in ("red", "orange", "yellow", "green"):
        active = _STOPLIGHT_COLORS[color] if stoplight.get("color") == color else "#e0e0db"
        html = html.replace(
            f"{{{{#if stoplight.color == '{color}'}}}}{_STOPLIGHT_COLORS[color]}{{{{else}}}}#e0e0db{{{{/if}}}}",
            active,
        )

    # Attendees loop
    attendees_html = " ".join(
        f'<span style="background:#f0f0ec;padding:2px 8px;border-radius:3px;margin-right:4px;margin-bottom:4px;display:inline-block;">{_esc(a)}</span>'
        for a in header.get("attendees", [])
    )
    html = _replace_each(html, "header.attendees", attendees_html,
        r'<span style="background:#f0f0ec.*?{{this}}</span>')

    # Sentiment arc
    arc_html = "".join(
        f'<td style="vertical-align:bottom;"><table cellpadding="0" cellspacing="0" width="100%"><tr><td bgcolor="{_MOOD_COLORS.get(p.get("mood", "neutral"), "#888780")}" height="{p.get("value", 30)}" style="border-radius:2px 2px 0 0;">&nbsp;</td></tr></table></td>'
        for p in sentiment.get("arc", [])
    )
    html = _replace_each(html, "sentiment.arc", arc_html,
        r'<td style="vertical-align:bottom;">.*?</td>')

    # Actions
    actions_html = "".join(
        f'<tr style="border-bottom:1px solid #f0e0d8;">'
        f'<td style="padding:10px 12px 10px 0;width:96px;vertical-align:top;">'
        f'<span style="background:{_PRIORITY_BG.get(a.get("priority", "standard"), "#1a1a1a")};color:#ffffff;font-size:11px;font-weight:600;padding:3px 8px;border-radius:3px;white-space:nowrap;">{_esc(a.get("owner", ""))}</span>'
        f'</td>'
        f'<td style="padding:10px 0;font-size:12px;color:#333;line-height:1.6;">{_esc(a.get("task", ""))}{" <strong>" + _esc(a["deadline"]) + "</strong>" if a.get("deadline") else ""}</td>'
        f'</tr>'
        for a in actions
    )
    html = _replace_each(html, "actions", actions_html,
        r'<tr style="border-bottom:1px solid #f0e0d8;">.*?</tr>')

    # Technical issues
    issues_html = ""
    for iss in technical_issues:
        position_html = ""
        if iss.get("position"):
            position_html = (
                f'<div style="margin-top:10px;padding-top:10px;border-top:1px solid #fadddd;">'
                f'<div style="font-size:9px;text-transform:uppercase;letter-spacing:0.1em;color:#A32D2D;margin-bottom:4px;">Position stated</div>'
                f'<div style="font-size:11px;line-height:1.6;color:#666;">{_esc(iss["position"])}</div>'
                f'</div>'
            )
        issues_html += (
            f'<div style="border-left:3px solid #E24B4A;background:#fff5f5;padding:14px 16px;border-radius:0 4px 4px 0;margin-bottom:12px;">'
            f'<div style="font-size:9px;text-transform:uppercase;letter-spacing:0.1em;color:#A32D2D;margin-bottom:4px;">{_esc(iss.get("severity", "issue"))} Identified</div>'
            f'<div style="font-size:13px;font-weight:700;color:#1a1a1a;margin-bottom:6px;">{_esc(iss.get("title", ""))}</div>'
            f'<div style="font-size:12px;line-height:1.7;color:#555;">{_esc(iss.get("detail", ""))}</div>'
            f'{position_html}'
            f'</div>'
        )
    html = _replace_each(html, "technical_issues", issues_html,
        r'<div style="border-left:3px solid #E24B4A.*?</div>\s*</div>')

    # Developments
    dev_html = "".join(
        f'<div style="border-left:3px solid #4CAF50;background:#f5fbf0;padding:14px 16px;border-radius:0 4px 4px 0;margin-bottom:12px;">'
        f'<div style="font-size:9px;text-transform:uppercase;letter-spacing:0.1em;color:#3B6D11;margin-bottom:4px;">New Development</div>'
        f'<div style="font-size:13px;font-weight:700;color:#1a1a1a;margin-bottom:6px;">{_esc(d.get("title", ""))}</div>'
        f'<div style="font-size:12px;line-height:1.7;color:#555;">{_esc(d.get("detail", ""))}</div>'
        f'</div>'
        for d in developments
    )
    html = _replace_each(html, "developments", dev_html,
        r'<div style="border-left:3px solid #4CAF50.*?</div>\s*</div>')

    # Decisions
    decisions_html = "".join(
        f'<div style="border:1px solid #e8e8e4;border-radius:4px;overflow:hidden;margin-bottom:10px;">'
        f'<div style="padding:14px 16px;">'
        f'<div style="font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#3B6D11;margin-bottom:4px;">Agreed</div>'
        f'<div style="font-size:13px;font-weight:700;color:#1a1a1a;margin-bottom:6px;">{_esc(d.get("title", ""))}</div>'
        f'<div style="font-size:12px;line-height:1.7;color:#555;">{_esc(d.get("detail", ""))}</div>'
        f'</div></div>'
        for d in decisions
    )
    html = _replace_each(html, "decisions", decisions_html,
        r'<div style="border:1px solid #e8e8e4.*?</div>\s*</div>\s*</div>')

    # Open questions
    oq_html = "".join(
        f'<tr style="border-bottom:1px solid #f0f0ec;">'
        f'<td style="padding:10px 12px;font-size:12px;color:#333;line-height:1.5;">{_esc(q.get("question", ""))}</td>'
        f'<td style="padding:10px 12px;font-size:12px;color:#666;">{_esc(q.get("owner", ""))}</td>'
        f'</tr>'
        for q in open_questions
    )
    html = _replace_each(html, "open_questions", oq_html,
        r'<tr style="border-bottom:1px solid #f0f0ec;">.*?</tr>')

    # Queries (research notes)
    queries_html = ""
    for q in queries:
        queries_html += (
            f'<div style="background:#f7f7f5;border:1px solid #e8e8e4;border-radius:6px;padding:12px 14px;margin-bottom:8px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
            f'<span style="font-size:10px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:0.08em;">{_esc(q.get("mode", ""))}</span>'
            f'<span style="font-size:10px;color:#999;font-family:monospace;">{_esc(q.get("timestamp", ""))}</span>'
            f'</div>'
            f'<div style="font-size:12px;font-weight:600;color:#1a1a1a;margin-bottom:6px;">{_esc(q.get("question", ""))}</div>'
            f'<div style="font-size:11px;color:#555;line-height:1.6;">{_esc(q.get("response_summary", ""))}</div>'
            f'</div>'
        )
    html = _replace_each(html, "queries_during_meeting", queries_html,
        r'<div style="background:#f7f7f5.*?</div>\s*</div>')
    html = html.replace("{{queries_during_meeting.length}}", str(len(queries)))

    # Conditional sections: remove entire section divs if empty
    if not technical_issues and not developments:
        html = _remove_conditional(html, "technical_issues.length")
    if not decisions:
        html = _remove_conditional(html, "decisions.length")
    if not open_questions:
        html = _remove_conditional(html, "open_questions.length")
    if not queries:
        html = _remove_conditional(html, "queries_during_meeting.length")

    # Clean up remaining template comments
    html = re.sub(r'<!--\s*\{\{.*?\}\}\s*-->', '', html)

    return html


def _esc(s: str) -> str:
    """HTML-escape a string."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _extract_first_paragraph(text: str, max_len: int = 300) -> str:
    """Extract the first meaningful paragraph from an LLM response.

    Skips markdown headers (# lines) and empty lines. Returns the first
    block of substantive text, truncated to max_len.
    """
    lines = text.strip().split("\n")
    paragraph = []
    for line in lines:
        stripped = line.strip()
        # Skip markdown headers and empty lines at the start
        if not paragraph and (not stripped or stripped.startswith("#")):
            continue
        # Stop at the next empty line or header after we've started collecting
        if paragraph and (not stripped or stripped.startswith("#")):
            break
        paragraph.append(stripped)

    result = " ".join(paragraph).strip()
    if not result:
        # Fallback: just take the first non-empty line
        for line in lines:
            if line.strip():
                result = line.strip()
                break
    if len(result) > max_len:
        result = result[:max_len].rsplit(" ", 1)[0] + "..."
    return result


def _replace_each(html: str, section: str, rendered: str, pattern: str) -> str:
    """Replace an {{#each}} block with rendered content.

    Removes the {{#each}} / {{/each}} comments and the template pattern between them,
    replacing with the pre-rendered HTML.
    """
    # Find and replace the section between the each comments
    start_marker = f"<!-- {{{{#each {section}}}}} -->"
    end_marker = f"<!-- {{{{/each}}}} -->"

    start_idx = html.find(start_marker)
    if start_idx == -1:
        return html

    # Find the matching /each after this start
    end_idx = html.find(end_marker, start_idx)
    if end_idx == -1:
        return html

    # Replace everything between start and end markers (inclusive) with rendered content
    before = html[:start_idx]
    after = html[end_idx + len(end_marker):]
    return before + rendered + after


def _remove_conditional(html: str, condition: str) -> str:
    """Remove an {{#if}} conditional section (the entire enclosing div)."""
    start_marker = f"<!-- {{{{#if {condition}}}}} -->"
    end_marker = f"<!-- {{{{/if}}}} -->"

    start_idx = html.find(start_marker)
    if start_idx == -1:
        return html

    end_idx = html.find(end_marker, start_idx)
    if end_idx == -1:
        return html

    # Remove from the start marker back to the previous newline (catches the opening div)
    line_start = html.rfind("\n", 0, start_idx)
    if line_start == -1:
        line_start = 0

    # Remove to the end marker plus the closing line
    line_end = html.find("\n", end_idx + len(end_marker))
    if line_end == -1:
        line_end = len(html)

    return html[:line_start] + html[line_end:]


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
