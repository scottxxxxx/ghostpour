# Prompt Dossiers

One file per rehearsal call type. Each dossier is the memory of how that
prompt got its current shape: what it's for, what broke, what fixed it,
what the evals said, and the tuning rules that must not be re-learned
the hard way.

## Contract

- The `served_version` in each dossier's front matter is the overlay
  version the dossier was last reconciled against. `ops/prompt_watchdog.py`
  compares it to the live overlay and flags any dossier that has fallen
  behind. When you change a served prompt, update its dossier in the
  same PR.
- Shaping history is append-only, dated, with PR numbers. Never rewrite
  old entries; corrections get a new entry.
- Tuning rules capture eval-backed conclusions (for example: anchors are
  Sonnet-tuned, moving the grader model requires a band-accuracy rerun).
  A rule leaves this section only when an eval overturns it.

## Where things live

- Served prompts: prod overlay `/app/data/remote-config/techrehearsal/`,
  seeded from `config/remote/techrehearsal/` in this repo. Value changes
  do NOT auto-hydrate; additions do. Sync with
  `POST /webhooks/admin/config/{slug}/sync-from-bundle`.
- Model dials: `config/remote/model-routing.json`, editable live in the
  dashboard Models tab (source of truth, hot-reloads).
- Eval harness: `~/tr_eval/` (fixtures.json, run_eval2.py, uses the prod
  assembly path). Grader eval history is summarized in each dossier.
- Watchdog: `ops/prompt_watchdog.py`, run weekly by the scheduled check
  and on demand.

## Call types covered

| Dossier | call_type | Lane |
|---|---|---|
| intake.md | tr_intake | Conversational prep intake, all scenarios |
| counterpart-turn.md | tr_counterpart_turn | Live in-character counterpart |
| brief-analysis.md | tr_brief_analysis | Structured prep brief |
| response-analysis.md | tr_response_analysis | Per-response grading |
| rewrite.md | tr_rewrite | Say-it-better line rewrite |
| debrief.md | tr_debrief | End-of-session debrief |
| mock-interview.md | tr_mock_interview | Scripted mock interview |
| compare-reality.md | tr_compare_reality | Real-vs-rehearsal comparison |

Non-rehearsal TR calls (parse, research, resume) are out of scope here;
they are shaped by their own feature work.
