"""Release complete sentences of the interviewer reply while the object is
still being generated, without bypassing a single guard.

Scott, 2026-09-19: the app does TTS on the reply, so it can start speaking
before the turn finishes, but it must never start a sentence and stop mid
sentence. This module is the GP half of that. The client half (TTS queue,
bubble growth, the held last sentence) is the auditor's, rulings 1 to 4 of
2026-09-19.

WHY THE LANE WAS WALLED OFF, AND WHAT THIS KEEPS. chat.py forbade streaming
on this lane because the stream transport returned before _run_turn_tail,
where the envelope retry, the checkpoint refusal, the oath refusal and
guard_response_text all run. That reason still holds and this module does
not touch it: the FULL object still goes through the tail, once, at the end.
What is released early is only complete sentences of `reply`, and only
under a rule built from reading what each guard actually reads:

  * No guard rewrites the spoken text. drop_unusable_reply fires only when
    no locale carries text (we release only from a locale that does);
    normalize_reply_shape changes the wrapper, not the words; the rest
    rewrite asking, facts, deferred, section_checkpoint, interview_over.
  * The checkpoint refusal reads section_checkpoint, facts and deferred
    (checkpoint_contradicts_agenda applies the response's own settled ids
    to the agenda before testing, the conf-v25b fix). None of it reads the
    reply. So it is fully decidable once those three keys are parsed, and
    ⚠ a pre-check WITHOUT facts and deferred is strictly conservative (it
    can over-refuse, never under-refuse): safe, but it would buffer every
    legitimate read-back turn, which is where streaming matters most. Hence
    the prompt orders section_checkpoint, facts, deferred BEFORE reply.
  * The oath refusal is sentence scoped (it runs a regex per _SENTENCE
    fragment of each reply string), so running it on one released unit is
    the same computation the full check does on that unit. A sentence that
    passes is safe regardless of what follows.

THE RELEASE RULE, per complete sentence of reply[locale]:
  1. section_checkpoint, facts and deferred must all have been parsed
     before reply. If reply arrives first, the turn BUFFERS (delivered whole
     at the end, exactly today's behaviour) and the late key is logged. The
     carrier is the parser refusing to release, not the prompt remembering.
  2. checkpoint_is_refused on the partial object must be None, else buffer;
     the tail retries as today and she never hears the bad claim.
  3. offers_impossible_oath_modification on the sentence must be None, else
     release stops; the tail retries and she never hears the offer.
  4. THE LAST SENTENCE IS HELD (auditor ruling 1). The reply shape is
     read-back then question, and the question is what A7 on the client
     may discard (a gate the same turn's derivation settled). Holding it
     behind the envelope covers A7 and the asking-rewrite case in one move.
     Sentence k is released the moment sentence k+1 BEGINS.
  5. When the agenda carries an oath node, the whole turn buffers (auditor
     ruling 3): Part 9 is the one section where the wording is the product.
  6. Prose never releases anything (no reply key is parsed at object depth)
     and a malformed object stops release; the tail handles both as today.

Anything held or buffered still reaches the client, in the envelope, so the
worst case of every rule is today's behaviour rather than silence.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# The keys the checkpoint refusal reads. All three must be parsed before
# reply for the pre-check to equal the full check.
KEYS_BEFORE_REPLY = ("section_checkpoint", "facts", "deferred")

# Sentence terminators for TTS. ¿ and ¡ are openers and belong to the next
# sentence, which falls out of splitting AFTER the terminator.
_TERMINALS = ".?!…"
# Characters that close a sentence after its terminator and stay with it.
_CLOSERS = "\"”’')]»"
# No split after these. Case-insensitive, matched against the last token
# before the terminator (the token includes its own internal dots).
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "sr", "sra", "srta", "ud", "uds", "u.s",
    "a.m", "p.m", "no", "st", "vs", "e.g", "i.e", "jr", "prof", "etc",
}


# --- 1. sentence splitting on decoded text ---------------------------------

class SentenceSplitter:
    """Feed decoded reply text; get complete sentences back.

    Emits ("started",) once the first non-space character of a sentence has
    arrived, and ("sentence", text) once its boundary is confirmed. The
    boundary needs one character of lookahead (is the next thing whitespace
    or more text?), so a sentence is emitted one character after it ends.
    close() flushes whatever remains as the last sentence.
    """

    def __init__(self) -> None:
        self._buf: list[str] = []
        self._started = False
        # Set when a terminator (plus closers) has been seen and we are
        # waiting for the next character to decide whether it ends a
        # sentence.
        self._at_boundary = False

    def feed(self, text: str) -> list[tuple]:
        out: list[tuple] = []
        for ch in text:
            if self._at_boundary:
                if ch in _CLOSERS:
                    self._buf.append(ch)
                    continue
                if ch.isspace():
                    if self._is_real_boundary():
                        out.append(("sentence", "".join(self._buf).strip()))
                        self._buf = []
                        self._started = False
                    else:
                        self._buf.append(ch)
                    self._at_boundary = False
                    continue
                # Something else follows directly (1.5, U.S.A, e.g.,):
                # not a boundary, keep going.
                self._at_boundary = False
                self._buf.append(ch)
                if not self._started and not ch.isspace():
                    self._started = True
                    out.append(("started",))
                continue

            if not self._started and ch.isspace():
                continue        # leading whitespace between sentences
            if not self._started:
                self._started = True
                out.append(("started",))
            self._buf.append(ch)
            if ch in _TERMINALS:
                self._at_boundary = True
        return out

    def close(self) -> list[tuple]:
        rest = "".join(self._buf).strip()
        self._buf = []
        self._started = False
        self._at_boundary = False
        return [("sentence", rest)] if rest else []

    def _is_real_boundary(self) -> bool:
        """The buffer ends in terminator(+closers). Decide whether that is a
        sentence end or an abbreviation / initial."""
        s = "".join(self._buf).rstrip(_CLOSERS)
        # Strip the terminator run (handles "..." and "?!")
        core = s.rstrip(_TERMINALS)
        if not core:
            return True
        term = s[len(core):]
        if term and term[0] != ".":
            return True            # ? ! … always end a sentence
        # The last token before the period.
        m = re.search(r"([^\s]+)$", core)
        tok = m.group(1) if m else ""
        tok_l = tok.lower().rstrip(".")
        if tok_l in _ABBREVIATIONS:
            return False
        if len(tok_l) == 1 and tok_l.isalpha():
            return False           # an initial: "J. Smith"
        if "." in tok_l and all(p.isalpha() and len(p) == 1 for p in tok_l.split(".") if p):
            return False           # dotted acronym: U.S.A
        return True


# --- 2. incremental JSON over the model's text deltas ---------------------

@dataclass
class _Str:
    """An in-progress JSON string token."""
    chars: list[str] = field(default_factory=list)
    esc: str | None = None        # pending escape text after the backslash


class ReplyStreamParser:
    """A minimal streaming JSON reader for the interviewer object.

    It tracks only what release needs: the top-level keys in order, the raw
    JSON of the three keys the checkpoint refusal reads, and the decoded
    text of reply[locale] (or a bare-string reply), delivered as it arrives.
    Everything else is consumed with correct nesting and ignored.

    Events from feed():
      ("key_done", name, value)    a top-level key other than reply finished
      ("reply_start",)             the reply value has begun
      ("reply_text", chunk)        decoded characters of reply[locale]
      ("reply_end",)               reply[locale]'s string closed
      ("malformed", reason)        parsing gave up; nothing further releases
    Text before the first `{` is ignored (the extract_envelope case:
    deliberation prose followed by the object).
    """

    def __init__(self, locale: str) -> None:
        self.locale = (locale or "en").lower()
        self.depth = 0                 # 0 = outside the top object
        self.seen_keys: list[str] = []
        self.values: dict[str, object] = {}
        self.malformed: str | None = None
        self.reply_started = False
        self.reply_locale_done = False
        # Tokenizer state
        self._str: _Str | None = None
        self._str_role: str | None = None   # "key" | "value" | "skip"
        self._expect: str = "start"        # start|key|colon|value|comma_or_end
        self._cur_key: str | None = None   # top-level key being read
        self._capture: list[str] | None = None   # raw json of a captured top-level value
        self._capture_depth = 0
        self._in_reply = False             # inside the reply value
        self._reply_kind: str | None = None  # "string" | "object"
        self._reply_locale_key: str | None = None  # key being read inside reply object
        self._reply_streaming = False      # currently inside reply[locale] string
        self._stack: list[str] = []        # nesting for skipped/captured values: "{" or "["
        self._lit: list[str] = []          # in-progress literal/number

    # -- public --------------------------------------------------------------

    def feed(self, delta: str) -> list[tuple]:
        if self.malformed:
            return []
        out: list[tuple] = []
        for ch in delta:
            try:
                self._char(ch, out)
            except _Malformed as e:
                self.malformed = str(e)
                out.append(("malformed", str(e)))
                break
            if self.malformed:
                break
        return out

    def keys_before_reply_seen(self) -> list[str]:
        return [k for k in KEYS_BEFORE_REPLY if k in self.values]

    def partial_object(self) -> dict:
        """The captured top-level values, for the checkpoint pre-check."""
        return {k: v for k, v in self.values.items() if k in KEYS_BEFORE_REPLY}

    # -- tokenizer -------------------------------------------------------------

    def _char(self, ch: str, out: list[tuple]) -> None:
        # Inside a string token: this dominates every other state.
        if self._str is not None:
            self._string_char(ch, out)
            return

        if self.depth == 0:
            if ch == "{":
                self.depth = 1
                self._expect = "key"
            return   # prose before the object

        # Inside a captured or skipped nested value: track nesting only.
        if self._stack and not self._in_reply:
            self._nested_char(ch, out)
            return

        if self._in_reply:
            self._reply_char(ch, out)
            return

        # Top level of the object.
        if ch.isspace() and self._expect != "literal":
            return
        if self._expect == "key":
            if ch == '"':
                self._str = _Str(); self._str_role = "key"
            elif ch == "}":
                self.depth = 0; self._expect = "end"
            else:
                raise _Malformed(f"expected key, got {ch!r}")
        elif self._expect == "colon":
            if ch != ":":
                raise _Malformed(f"expected ':', got {ch!r}")
            self._expect = "value"
        elif self._expect == "value":
            self._top_value_start(ch, out)
        elif self._expect == "literal":
            # null / true / false / a number: ends at the next , or } (or
            # whitespace, which then falls through to comma_or_end).
            if ch in ",}" or ch.isspace():
                self._finish_top_value(out)
                if ch == ",":
                    self._expect = "key"
                elif ch == "}":
                    self.depth = 0; self._expect = "end"
            else:
                self._lit.append(ch)
                if self._capture is not None:
                    self._capture.append(ch)
        elif self._expect == "comma_or_end":
            if ch == ",":
                self._expect = "key"
            elif ch == "}":
                self.depth = 0; self._expect = "end"
            else:
                raise _Malformed(f"expected ',' or '}}', got {ch!r}")
        elif self._expect == "end":
            return   # trailing text after the object: ignored

    def _top_value_start(self, ch: str, out: list[tuple]) -> None:
        key = self._cur_key or ""
        if key == "reply":
            self._in_reply = True
            self.reply_started = True
            out.append(("reply_start",))
            if ch == '"':
                self._reply_kind = "string"
                self._reply_streaming = True
                self._str = _Str(); self._str_role = "reply_text"
            elif ch == "{":
                self._reply_kind = "object"
                self._expect = "rkey"
            else:
                # reply: null or something odd. Consume as a literal.
                self._reply_kind = "literal"
                self._lit = [ch]
            return
        capture = key in KEYS_BEFORE_REPLY
        if capture:
            self._capture = [ch]
        if ch == '"':
            self._str = _Str(); self._str_role = "capture" if capture else "skip"
        elif ch in "{[":
            self._stack.append(ch)
        elif ch in "}]":
            raise _Malformed(f"unexpected {ch!r} at value start")
        else:
            self._lit = [ch]      # number / true / false / null
            self._expect = "literal"

    def _finish_top_value(self, out: list[tuple]) -> None:
        key = self._cur_key or ""
        if self._capture is not None:
            raw = "".join(self._capture)
            self._capture = None
            try:
                self.values[key] = json.loads(raw)
            except ValueError as e:
                raise _Malformed(f"could not parse {key}: {e}")
            out.append(("key_done", key, self.values[key]))
        elif key and key != "reply":
            out.append(("key_done", key, None))
        if key not in self.seen_keys:
            self.seen_keys.append(key)
        self._cur_key = None
        self._lit = []
        self._expect = "comma_or_end"

    def _nested_char(self, ch: str, out: list[tuple]) -> None:
        """Inside a skipped or captured top-level object/array value."""
        if self._capture is not None:
            self._capture.append(ch)
        if ch == '"':
            self._str = _Str(); self._str_role = "capture" if self._capture is not None else "skip"
            return
        if ch in "{[":
            self._stack.append(ch)
        elif ch in "}]":
            opener = self._stack.pop() if self._stack else None
            if opener is None or (opener == "{") != (ch == "}"):
                raise _Malformed("mismatched bracket in nested value")
            if not self._stack:
                self._finish_top_value(out)

    def _reply_char(self, ch: str, out: list[tuple]) -> None:
        """Inside the reply value (object form, or a literal)."""
        if self._reply_kind == "literal":
            if ch in ",}":
                self._lit = []
                self._in_reply = False
                self._finish_top_value(out)
                if ch == "}":
                    self.depth = 0; self._expect = "end"
                else:
                    self._expect = "key"
            return
        # object form: {"en": "...", "es": "..."}
        if ch.isspace():
            return
        if self._expect == "rkey":
            if ch == '"':
                self._str = _Str(); self._str_role = "rkey"
            elif ch == "}":
                self._in_reply = False
                self._finish_top_value(out)
            else:
                raise _Malformed(f"expected reply locale key, got {ch!r}")
        elif self._expect == "rcolon":
            if ch != ":":
                raise _Malformed("expected ':' in reply object")
            self._expect = "rvalue"
        elif self._expect == "rvalue":
            if ch == '"':
                is_target = (self._reply_locale_key or "").lower() == self.locale and not self.reply_locale_done
                self._reply_streaming = is_target
                self._str = _Str(); self._str_role = "reply_text" if is_target else "skip_reply"
            elif ch in "{[":
                # A non-string locale value: skip it structurally.
                self._stack.append(ch); self._str_role = None
                self._reply_nested = True
            else:
                self._lit = [ch]; self._expect = "rlit"
        elif self._expect == "rlit":
            if ch in ",}":
                self._lit = []
                self._after_reply_member(ch, out)
        elif self._expect == "rcomma_or_end":
            self._after_reply_member(ch, out)

    def _after_reply_member(self, ch: str, out: list[tuple]) -> None:
        if ch == ",":
            self._expect = "rkey"
        elif ch == "}":
            self._in_reply = False
            self._finish_top_value(out)
        else:
            raise _Malformed(f"expected ',' or '}}' in reply object, got {ch!r}")

    def _string_char(self, ch: str, out: list[tuple]) -> None:
        s = self._str
        assert s is not None
        role = self._str_role
        # Captured raw json keeps the string verbatim, escapes included.
        if role == "capture" or (self._capture is not None and role in ("capture",)):
            self._capture.append(ch)
        if s.esc is not None:
            s.esc += ch
            done, decoded = _decode_escape(s.esc)
            if done:
                s.esc = None
                self._emit_str(decoded, out)
            return
        if ch == "\\":
            s.esc = ""
            return
        if ch == '"':
            self._end_string(out)
            return
        self._emit_str(ch, out)

    def _emit_str(self, text: str, out: list[tuple]) -> None:
        s = self._str
        assert s is not None
        if self._str_role == "reply_text":
            out.append(("reply_text", text))
        else:
            s.chars.append(text)

    def _end_string(self, out: list[tuple]) -> None:
        s = self._str
        assert s is not None
        text = "".join(s.chars)
        role = self._str_role
        self._str = None
        self._str_role = None
        if role == "key":
            self._cur_key = text
            self._expect = "colon"
        elif role == "rkey":
            self._reply_locale_key = text
            self._expect = "rcolon"
        elif role == "reply_text":
            self._reply_streaming = False
            self.reply_locale_done = True
            out.append(("reply_end",))
            if self._reply_kind == "string":
                self._in_reply = False
                self._finish_top_value(out)
            else:
                self._expect = "rcomma_or_end"
        elif role == "skip_reply":
            self._expect = "rcomma_or_end"
        elif role in ("capture", "skip"):
            if not self._stack:
                self._finish_top_value(out)
            # else: a string inside a nested value; nesting continues

    # literals/numbers at top level end at , or }
    # (handled by treating them like a nested-free value)


class _Malformed(Exception):
    pass


def _decode_escape(esc: str) -> tuple[bool, str]:
    """(done, decoded) for the text after a backslash so far."""
    if not esc:
        return False, ""
    c = esc[0]
    simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
              "n": "\n", "r": "\r", "t": "\t"}
    if c in simple:
        return True, simple[c]
    if c == "u":
        if len(esc) < 5:
            return False, ""
        try:
            cp = int(esc[1:5], 16)
        except ValueError:
            raise _Malformed(f"bad \\u escape {esc!r}")
        # Surrogate pair: wait for the low half.
        if 0xD800 <= cp <= 0xDBFF:
            if len(esc) < 11:
                return False, ""
            if esc[5:7] != "\\u":
                raise _Malformed("lone high surrogate")
            lo = int(esc[7:11], 16)
            return True, chr(0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00))
        return True, chr(cp)
    raise _Malformed(f"bad escape \\{c}")


# --- 3. the release policy ------------------------------------------------

def agenda_carries_oath(agenda: str | None) -> bool:
    """True when any agenda node is an oath node (id contains 'oath', a
    field id starts with p9., or the part is 9). Conservative on purpose:
    over-buffering costs streaming on a turn, under-buffering costs the one
    harm no later correction reaches."""
    from app.services.n400_interviewer_guard import agenda_field_ids, agenda_parts
    if not agenda:
        return False
    for node, ids in agenda_field_ids(agenda).items():
        if "oath" in node.lower() or any(i.startswith("p9.") for i in ids):
            return True
    return any(p == 9 for p in agenda_parts(agenda).values())


@dataclass
class ReleaseResult:
    released: list[str]           # sentence events to emit, in order
    held: str | None = None       # the last sentence, delivered in the envelope
    buffered_reason: str | None = None


class ReleaseController:
    """Turn model text deltas into released sentences under the rule above.

    feed(delta) -> sentences to emit now.
    close() -> the held last sentence and the buffered reason, if any.
    """

    def __init__(self, *, locale: str, agenda: str | None,
                 known_facts: str | None, turn_id: str | None = None) -> None:
        self.locale = (locale or "en").lower()
        self.agenda = agenda
        self.known_facts = known_facts
        self.turn_id = turn_id
        self.parser = ReplyStreamParser(self.locale)
        self.splitter = SentenceSplitter()
        self.buffered_reason: str | None = None
        self.released: list[str] = []
        self._pending: str | None = None    # complete, not yet released (may be last)
        self._index = 0
        if agenda_carries_oath(agenda):
            self._buffer("oath_node_on_agenda")

    # -- public --------------------------------------------------------------

    def feed(self, delta: str) -> list[dict]:
        if self.buffered_reason:
            return []
        out: list[dict] = []
        for ev in self.parser.feed(delta):
            kind = ev[0]
            if kind == "malformed":
                self._buffer(f"malformed:{ev[1]}")
                break
            if kind == "reply_start":
                missing = [k for k in KEYS_BEFORE_REPLY if k not in self.parser.values]
                if missing:
                    self._buffer("reply_before_" + ",".join(missing))
                    break
                if self._checkpoint_refused():
                    self._buffer("checkpoint_refused")
                    break
            elif kind == "reply_text":
                for sev in self.splitter.feed(ev[1]):
                    if sev[0] == "started":
                        self._release_pending(out)
                    elif sev[0] == "sentence":
                        if not self._admit(sev[1]):
                            self._buffer("oath_modification")
                            return out
                        self._pending = sev[1]
            elif kind == "reply_end":
                for sev in self.splitter.close():
                    if sev[0] == "sentence":
                        if not self._admit(sev[1]):
                            self._buffer("oath_modification")
                            return out
                        if self._pending is not None:
                            # A tail without terminator after a complete
                            # sentence: the tail is the last one.
                            self._release_pending(out)
                        self._pending = sev[1]
            if self.buffered_reason:
                break
        return out

    def close(self) -> ReleaseResult:
        held = None if self.buffered_reason else self._pending
        self._pending = None
        return ReleaseResult(released=list(self.released), held=held,
                             buffered_reason=self.buffered_reason)

    # -- internals -------------------------------------------------------------

    def _release_pending(self, out: list[dict]) -> None:
        if self._pending is None:
            return
        ev = {"index": self._index, "locale": self.locale, "text": self._pending}
        self._index += 1
        self.released.append(self._pending)
        self._pending = None
        out.append(ev)

    def _admit(self, sentence: str) -> bool:
        """The real oath check, on this one sentence."""
        from app.services.n400_interviewer_guard import offers_impossible_oath_modification
        probe = json.dumps({"reply": {self.locale: sentence}}, ensure_ascii=False)
        return offers_impossible_oath_modification(probe) is None

    def _checkpoint_refused(self) -> bool:
        from app.services.n400_interviewer_guard import checkpoint_is_refused
        partial = json.dumps(self.parser.partial_object(), ensure_ascii=False)
        return checkpoint_is_refused(partial, self.agenda, self.known_facts) is not None

    def _buffer(self, reason: str) -> None:
        if self.buffered_reason:
            return
        self.buffered_reason = reason
        # Anything pending is NOT released: it rides the envelope instead.
        self._pending = None
        logger.info("n400_stream_buffered turn_id=%s reason=%s released=%d",
                    self.turn_id, reason, len(self.released))
