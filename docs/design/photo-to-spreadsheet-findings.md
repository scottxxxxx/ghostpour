# Photo → Spreadsheet: capability status and findings (2026-07-19)

What happens when a user attaches a **photo of a spreadsheet** and asks for
an Excel rebuild. Grounded in a two-photo experiment run 2026-07-19 with
ground-truth comparison against the original workbook.

## 1. Does the live lane do this today? Yes, with caveats.

The pipeline requires no changes to accept the ask:

- **Image transport**: photos ride `ChatRequest.images` (≤5). The Anthropic
  adapter appends image blocks to the message content unconditionally —
  including on **generation-mode** turns — so an armed turn that carries a
  photo gives the sandbox model vision + code execution in one turn.
- **Intent gate**: `looks_like_file_ask` + the classifier judge the ask
  text ("turn this photo into an excel file" passes on file vocabulary);
  the photo itself is invisible to the gate, which is fine — the ask names
  the format.
- **Execution**: ad-hoc sandbox lane, ~2-minute class, $0.15–0.35, normal
  offer/progress/rescue UX.

### Path-dependent hazards (the caveats)

| Path | Image reaches generation turn? |
|---|---|
| Manual "Generate a File" toggle on the photo-carrying send | **Yes** — same-turn arming, image rides that send |
| Pill tap (resend of original ask) | Probably — resend composition is client-side; unverified |
| Typed "yes" at a conversational offer | **At risk** — the offer store keeps `ask_content` (text only); SS's echo re-attach provably covers `documents` and text-injection blocks, **images unverified** (third attachment species) |
| Template lane (e.g. photo of a Gantt matching `match_template`) | **At risk** — the extraction turn is rebuilt from stored `ask_content` text; a photo-sourced plan lives in the image, so a template-intercepted photo ask may extract blind |

An image-blind generation turn does not fail — it **confidently invents**
a plausible spreadsheet. That failure mode is worse than an error and is
the main reason to verify the unverified cells before promoting this use.

## 2. Fidelity: what the experiment showed

Two photos, both rebuilt successfully; ground truth obtained for the second
(the original .xlsx).

**Case A — photo of GP's own `gantt_smartsheet` output**: essentially
lossless. Recognizing the template let the plan data be extracted and
re-rendered through the deterministic renderer — same rows, derived
predecessor codes, statuses, milestones, at-risk flags, real formulas/CF.
Photos of our own template output round-trip.

**Case B — photo of a third-party tracker** (9-sheet workbook, one sheet
visible): within the photographed window, transcription was near-perfect —
every id, name, ticket number, status, and both pivot tables exact; even
the one low-confidence flagged cell was read correctly. But two whole
classes of information were lost **invisibly**:

1. **Render ≠ value.** A photo captures the display. The "Original ETA"
   column *displayed* "October 2025" on 15 rows; the cells *contain* 15
   distinct dates format-collapsed by `mmmm yyyy`. Currency displayed
   rounded ($875,833 vs 875833.29). Formulas, comments (markers visible,
   contents not), and number formats are all in this class — never
   recoverable from a photo.
2. **Coverage.** The photo showed 19 of 29 data rows (scroll position) of
   1 of 9 sheets (no tab bar in frame). The rebuild is a slice, silently.

## 3. Confidence warnings are mostly computable, not vibes

The experiment's key product finding: the sheet often **tells on itself**,
so a rebuild can ship an honest confidence block derived mechanically:

- **Coverage arithmetic**: visible pivot/total rows vs count of visible
  data rows (29 vs 19 was provable *from the photo alone*); first visible
  row number > header row ⇒ rows scrolled off; no tab bar in frame ⇒
  unknown sibling sheets. All computable by the model during extraction.
- **Render-vs-value signatures**: N consecutive identical strings in a
  date-named column; suspiciously round currency — flag the column as
  "transcribed as displayed; underlying values not recoverable". This
  warning is *always true* for photo sources and costs nothing.
- **Per-cell read confidence**: blur/truncation flags. Calibration
  evidence: the one cell flagged low-confidence in the experiment was
  read correctly — flag rate seems about right, not noise.

## 4. Recommended follow-ups (not built)

1. **Verify the two at-risk cells** in §1 on device (typed-yes with photo;
   template-matched photo ask) before treating photo→xlsx as a promoted
   use case. One wire check each.
2. **Confidence block in the closing summary** for photo-sourced
   generations: coverage numbers, render-vs-value caveat per column type,
   flagged cells. Server-side copy + extraction-prompt additions; no wire
   change. Offer copy states capability positively ("I'll rebuild
   everything visible in the photo") per the no-limitation-framing rule,
   with specifics in the result.
3. **Template-lane image support** (pass originating images to the
   extraction turn) — also unlocks "photo of a Smartsheet Gantt" → the
   deterministic renderer, which Case A showed is the lossless path.

## 5. Relation to 2b

Independent of the 2b transform lane: 2b ingests attached *office files*
raw; photos are image inputs and already work through 2a's vision. The
lanes converge later (2c iterate: photo rebuild → Reference → refine).
