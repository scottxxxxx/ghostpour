# GP in flight, 2026-10-17 (session close)

Nothing is running, every worktree is committed and pushed, and main is clean. Read this, then MEMORY.md's top lines.

## Live now (each read back on prod, not from memory)

/health serves f5f7f0a (#1126). The served N-400 interviewer prompt is v64, and systemPrompt, userPromptTemplate and optionalVariables are byte-equal to the bundle. TODAY is in the template.

- #1119 v62 (13b66ff): every part read back and confirmed before the next, finish a part before moving on, a stated none files its gate's no, date read-back with the day, the close replaces the review offer, field_ids and node_id discipline. The system prompt and the template keys are both synced (the template went later, on Scott's "sync the template").
- #1124 (d6cd272): llm-providers catalog v29 in en/es/fr/ja. supportsWebSearch is true on the 37 Anthropic and OpenRouter models and absent elsewhere. supportsDocuments is true on 53 models and false only on openai gpt-5.4-nano. SS read it back themselves and it matches. The deploy alone put v29 in place; the sync said "unchanged".
- #1125 v63 (0f64f70): the wrong-digit-count rule, free text filed in English (names never translated, provenance and read-back in her language), and "no middle name" on a row with no has-middle-name gate filed as "". Code: the carried free-text floor (_free_text_carried, which reads the new metadata key applicant_line_field_ids that the client sends from build 203, and falls back to her previous answer plus the agenda) and the spelled-name floor (_spelled_runs in _name_words_said).
- #1126 v64 (f5f7f0a): the field asked decides which identifier the digits are, and v63's "ten digits in phone groups are a phone number" clause is retired. Code: drop_identifiers_not_as_said (SSN, A-Number, spouse A-Number and USCIS account must equal exactly one cited digit run; phones are out; no judgment when it can't read digits) plus reask_dropped_identifier (the reply becomes a re-ask with the count heard, in en/es/pt, after this drop or the count-guard drop), and _split_date_said (a date across her current and previous line, the field tagged on EITHER line, which the auditor agreed to).

## Owed

- **SS demo account 29c3fc76-1a73-4da0-922d-9e31ad594895 is on PRO** for Social's brand-film capture (set under Scott's standing internal-teams rule; exclude_from_stats is still 1). Revert it to free with POST /webhooks/admin/set-tier when SS Dev says the capture is done, then read back the users row.
- Watch journald on Scott's next device run for n400_identifier_not_as_said, n400_fact_dropped_no_evidence and n400_stream_spoken_prefix_stale. The v64 identifier floor has never fired on a real turn.
- The carried free-text check is only exact once client build 203 sends applicant_line_field_ids. Until then it uses the fallback.
- Not probed live: v63's digit rule, v64's prompt, and the free-text rule (6 Sonnet subagent turns only, qa/runs/v63-free-text-english-subagents.json, which is untracked).
- Older items, still open: the Apple sign-in anonymous merge read-back (5085663a to fa4d903c), the Free "Web search" bullet, the untraced +2 tiers version, #1118 on hold, and the I-765 App Store link swap.

## Findings from tonight's prod reads (Scott OK'd each one)

- 74aa56aa t_033 (the NIKANDRO email): GP extracted the object from the prose on all three calls. The "unreadable" was the client's restore missing a stand-in inside an email, fixed in build 193.
- 0e731ec1 t_009 to t_013: GP's floor dropped the spelled other-name last name (fixed in v63). The lane never filed the new first name after "Seymour, but." and said it back anyway (v62's never-say-back rule).
- 766ab91c t_016: the lane filed a 10-digit SSN as NINE digits on all three calls, GP passed it (nine is a valid count), and the client's build 198 refused it. My first explanation (that the phone-shape clause hid the SSN) was wrong and is corrected in the PR, the dossier and the prompt _comment. t_015: the floor dropped p2.lpr_date given across two turns (fixed in v64).

## How things ran (gotchas that cost time)

- Prompt syncs run INSIDE the container: scp scripts/sync_n400_prompt.py to the VM, docker cp it in, then `docker exec -w /app ghostpour python /tmp/s.py --expect-sha <merge sha>`. CZ_ADMIN_KEY is in the container env. Never print it.
- The auto-mode classifier blocks prod reads and template syncs unless Scott OKs that specific action in the session.
- A new worktree needs the .env symlink, or the suite errors at collection. Never edit a worktree while its suite runs.
- usage_log has a case_id column. raw_request and raw_response are JSON strings, and raw_response is pre-guard.
- Merges and deploys need Scott's word typed in this session. A relay from SS or the auditor doesn't count, and I refused one tonight.
