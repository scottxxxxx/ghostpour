import json
from collections.abc import AsyncIterator

from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse

from .base import ProviderAdapter
from .reasoning import (
    anthropic_output_config,
    anthropic_thinking_block,
)


class AnthropicAdapter(ProviderAdapter):
    """Anthropic Messages API — custom format, not OpenAI-compatible."""

    # Generation turns run a server-side tool loop that can pause and need
    # continuation; bound the replays so a pathological turn can't spin.
    _MAX_GENERATION_CONTINUATIONS = 4
    _GENERATION_TIMEOUT_S = 400.0  # per leg; see send_request

    async def send_request(self, request: ChatRequest) -> ChatResponse:
        body, headers = self._build_body(request)
        # Generation turns run a server-side sandbox loop with real runtime
        # variance (first live runs: 124s and 180s+) — the shared client's
        # 180s default sits INSIDE that envelope, so armed turns get their
        # own ceiling. Applies per continuation leg, not to the whole turn.
        timeout = self._GENERATION_TIMEOUT_S if request.generation else None
        status, data, raw_req, raw_resp = await self._post(
            self.base_url, body, headers, timeout=timeout
        )

        # pause_turn continuation (generation turns only): the server-side
        # sandbox loop hit its iteration limit mid-work. Re-send with the
        # assistant content appended and the same container so it resumes.
        # No extra user message — the API detects the trailing server_tool_use.
        if request.generation:
            replays = 0
            while (
                status == 200
                and data.get("stop_reason") == "pause_turn"
                and replays < self._MAX_GENERATION_CONTINUATIONS
            ):
                replays += 1
                cont = dict(body)
                container_id = (data.get("container") or {}).get("id")
                if container_id:
                    cont["container"] = container_id
                cont["messages"] = body["messages"] + [
                    {"role": "assistant", "content": data["content"]}
                ]
                status, data, raw_req, raw_resp = await self._post(
                    self.base_url, cont, headers, timeout=timeout
                )

        if status != 200:
            error_msg = "Unknown error"
            if "error" in data:
                error_msg = data["error"].get("message", str(data["error"]))
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "provider_error",
                    "message": f"anthropic: {error_msg}",
                    "details": {"status_code": status},
                },
            )

        # Anthropic returns content as array of blocks
        text_parts = [
            block["text"] for block in data.get("content", []) if block.get("type") == "text"
        ]
        if request.generation and text_parts:
            # A generation turn's content interleaves working narration with
            # tool blocks — reading the skill, fixing its own script errors —
            # which is not a chat bubble (SS field report, 2026-07-11). The
            # final text block is the model's closing summary; the full
            # working transcript stays in raw_response_json for logs and the
            # future curated narration stream.
            text = text_parts[-1]
        else:
            text = "\n".join(text_parts) if text_parts else ""

        # Capture the full usage block from the provider
        # Anthropic returns: input_tokens, output_tokens,
        # cache_creation_input_tokens, cache_read_input_tokens.
        # When web_search is enabled, also: server_tool_use.web_search_requests.
        raw_usage = data.get("usage", {})
        usage = self._flatten_usage(raw_usage)

        # Count web_search invocations from the content blocks. Anthropic
        # emits one `server_tool_use` block per search with name="web_search".
        # We mirror this into a top-level usage key so the chat router's
        # gate can increment the per-user counter without re-parsing the
        # raw content array.
        search_count = sum(
            1 for block in data.get("content", [])
            if block.get("type") == "server_tool_use"
            and block.get("name") == "web_search"
        )
        if search_count:
            usage["web_search_requests"] = search_count

        # Also capture response-level metadata
        if data.get("id"):
            usage["response_id"] = data["id"]
        if data.get("model"):
            usage["model_version"] = data["model"]
        if data.get("stop_reason"):
            usage["finish_reason"] = data["stop_reason"]

        return ChatResponse(
            text=text,
            input_tokens=raw_usage.get("input_tokens"),
            output_tokens=raw_usage.get("output_tokens"),
            model=request.model,
            provider=request.provider,
            usage=usage,
            raw_request_json=raw_req,
            raw_response_json=raw_resp,
        )

    def _build_body(self, request: ChatRequest) -> tuple[dict, dict]:
        """Build Anthropic request body and headers. Shared by stream and non-stream."""
        content_parts: list[dict] = []
        if request.documents:
            # Documents passthrough (#359): app/services/documents.py has
            # already gated these to PDF on the managed Pro path; render
            # each as a native document block so vision sees layout.
            for doc in request.documents:
                block: dict = {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": doc.media_type,
                        "data": doc.data,
                    },
                }
                if doc.name:
                    block["title"] = doc.name
                content_parts.append(block)
            # Saved References resend the SAME bytes on every send of a chat
            # session (launch contract: resend, no server-side file store). A
            # cache breakpoint after the documents means repeat sends bill the
            # document tokens at cache-read rates instead of full input price.
            # Uses breakpoint 3 of 4 (system prefix + recall hold the first two).
            content_parts[-1]["cache_control"] = {"type": "ephemeral"}
        if request.images:
            for img_b64 in request.images[:5]:
                content_parts.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
                })
        content_parts.append({"type": "text", "text": request.user_content})

        system_block = _build_system_blocks(request)

        max_tokens = request.max_tokens or 4096
        thinking = anthropic_thinking_block(request.reasoning, request.model)
        # All Anthropic reasoning-pickable models are on the effort path
        # (Opus 4.7, Sonnet 4.6, Mythos). They don't constrain max_tokens
        # against budget_tokens, so no lift needed. Haiku 4.5 is hidden
        # from the picker (manual budget_tokens path isn't user-friendly).

        body = {
            "model": request.model,
            "system": system_block,
            "messages": [{"role": "user", "content": content_parts}],
            "max_tokens": max_tokens,
        }
        if thinking:
            body["thinking"] = thinking
        # GP-controlled sampling temperature (e.g. low for reproducible structured
        # output). Anthropic requires temperature=1 when extended thinking is on,
        # so only send an explicit temperature when there is no thinking block.
        elif request.temperature is not None:
            body["temperature"] = request.temperature
        # Effort-path models (Sonnet 4.6, Opus 4.7): attach output_config.effort
        # alongside `thinking: {type: "adaptive"}`. Per Anthropic's docs the
        # effort parameter is what controls thinking depth on these models;
        # legacy budget_tokens returns 400 on Opus 4.7.
        output_config = anthropic_output_config(request.reasoning, request.model)
        if output_config:
            body["output_config"] = output_config

        # Web search tool — gated upstream by the chat router. The router
        # only sets search_enabled=True after passing tier + cap checks,
        # so the adapter trusts the flag and just attaches Anthropic's
        # native web_search_20250305 tool. max_uses bounds how many
        # searches a single turn can make (otherwise one query could
        # burn through the user's monthly cap).
        if request.get_meta("search_enabled"):
            body["tools"] = [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                }
            ]

        headers = self._build_headers()

        # Document generation (phase 2a). Gated upstream; the adapter arms
        # the execution sandbox + the four document skills, raises the
        # output ceiling, and — the 2a-spike-mandated part — puts a cache
        # breakpoint on the user content: the server-side tool loop re-reads
        # the whole prompt on every internal iteration, and caching it cut
        # the measured per-generation cost from $1.04 to $0.33.
        if request.generation:
            body["container"] = {"skills": [
                {"type": "anthropic", "skill_id": s, "version": "latest"}
                for s in ("xlsx", "pptx", "docx", "pdf")
            ]}
            # Toolchain steering: the first live docx (docx.js) produced a
            # file Word rejects while every lenient reader accepts it —
            # python-docx output is Word-derived and safe. Steering fixes
            # the source; the collection-side rebuild is the backstop.
            body["system"] = list(body["system"]) + [{
                "type": "text",
                "text": ("When creating Word (.docx) files in the sandbox, "
                         "use the python-docx library — do not use docx.js "
                         "or hand-written OOXML; Word rejects their output. "
                         "For checklists, use plain paragraphs starting with "
                         "the ballot-box glyph — never checkbox glyphs inside "
                         "bulleted list items (double markers). Style the "
                         "document title as Title, sections as Heading 1-3."),
            }]
            body.setdefault("tools", []).append(
                {"type": "code_execution_20260521", "name": "code_execution"}
            )
            body["max_tokens"] = max(body.get("max_tokens") or 0, 16000)
            content = body["messages"][0]["content"]
            for part in reversed(content):
                if part.get("type") == "text":
                    part["cache_control"] = {"type": "ephemeral"}
                    break
            extra = "code-execution-2025-08-25,skills-2025-10-02"
            headers["anthropic-beta"] = (
                headers["anthropic-beta"] + "," + extra
                if headers.get("anthropic-beta") else extra
            )

        return body, headers

    async def send_request_stream(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Stream an Anthropic Messages API request, yielding event dicts.

        Yields:
          {"type": "text", "text": "chunk", "done": False}  — for each text delta
          {"type": "text", "text": "", "done": True, "response": ChatResponse}  — at end
        """
        body, headers = self._build_body(request)
        body["stream"] = True

        raw_request = self._redact_base64(self._pretty_json(body))

        # Accumulate response data
        full_text = ""
        input_tokens = 0
        output_tokens = 0
        response_id = ""
        model_version = ""
        stop_reason = ""
        cache_creation = 0
        cache_read = 0
        web_search_count = 0

        try:
            async for line in self._post_stream(self.base_url, body, headers):
                # Anthropic SSE: "event: <type>" followed by "data: <json>"
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type")

                    if event_type == "message_start":
                        msg = event.get("message", {})
                        response_id = msg.get("id", "")
                        model_version = msg.get("model", "")
                        usage = msg.get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)
                        cache_creation = usage.get("cache_creation_input_tokens", 0)
                        cache_read = usage.get("cache_read_input_tokens", 0)

                    elif event_type == "content_block_start":
                        # Anthropic emits one content_block_start per
                        # server_tool_use invocation. Mirror parse_response's
                        # counter so streaming responses populate
                        # web_search_requests; without this, the chat router's
                        # post-stream gate reads 0, no audit row is written,
                        # and the per-user cap counter never advances.
                        cb = event.get("content_block", {})
                        if (
                            cb.get("type") == "server_tool_use"
                            and cb.get("name") == "web_search"
                        ):
                            web_search_count += 1

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            chunk = delta.get("text", "")
                            full_text += chunk
                            yield {"type": "text", "text": chunk, "done": False}

                    elif event_type == "message_delta":
                        delta = event.get("delta", {})
                        stop_reason = delta.get("stop_reason", "")
                        usage = event.get("usage", {})
                        output_tokens = usage.get("output_tokens", output_tokens)

        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail={"code": "provider_error", "message": f"anthropic stream: {e}"},
            )

        # Build final ChatResponse for logging/cost calculation
        usage_dict = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "response_id": response_id,
            "model_version": model_version,
            "finish_reason": stop_reason,
        }
        if web_search_count:
            usage_dict["web_search_requests"] = web_search_count

        final_response = ChatResponse(
            text=full_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=request.model,
            provider=request.provider,
            usage=usage_dict,
            raw_request_json=raw_request,
            raw_response_json=None,  # Not available in streaming
        )

        yield {"type": "text", "text": "", "done": True, "response": final_response}


def _build_system_blocks(request: ChatRequest) -> list[dict]:
    """Build the Anthropic `system` field as one or more cache_control blocks.

    Default: single block carrying the full `system_prompt` with
    `cache_control: ephemeral` — the long-standing layout that already
    delivers cross-turn caching when the prompt is byte-stable.

    CQ recall split: when the Context Quilt feature hook stashed the
    recall text on `metadata.cq_recall_block` and that text appears
    verbatim inside `system_prompt`, the prompt is sliced into three
    blocks at the recall boundary:

        1. prefix  (cache_control)
        2. recall  (cache_control)
        3. suffix  (no cache_control — last block)

    Two breakpoints isolate the base prompt prefix from the recall block
    so the prefix keeps caching cross-turn even when recall content
    differs. Anthropic supports up to 4 cache_control breakpoints; this
    uses 2, leaving headroom for future splits.

    Falls back to the single-block layout when:
      - no recall block was stashed (free/plus tier, recall empty, no CQ)
      - the recall text isn't found in `system_prompt` (defensive — should
        not happen since the hook just inserted it, but a hook refactor
        or a downstream mutation should not break Anthropic calls)
      - the recall block is empty after slicing
    """
    recall = request.get_meta("cq_recall_block") if request.metadata else None
    if recall:
        idx = request.system_prompt.find(recall)
        if idx >= 0:
            prefix = request.system_prompt[:idx]
            suffix = request.system_prompt[idx + len(recall):]
            blocks: list[dict] = []
            if prefix:
                blocks.append({
                    "type": "text",
                    "text": prefix,
                    "cache_control": {"type": "ephemeral"},
                })
            blocks.append({
                "type": "text",
                "text": recall,
                "cache_control": {"type": "ephemeral"},
            })
            if suffix:
                blocks.append({"type": "text", "text": suffix})
            return blocks

    return [{
        "type": "text",
        "text": request.system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]
