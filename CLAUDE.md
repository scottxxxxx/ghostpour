# GhostPour

## How the three teams talk to each other

Identical wording lives in CQ's, SS's and GP's CLAUDE.md, because shared
literal text is itself drift resistance. Every rule below was paid for in
the week of 2026-08-11, and each carries its receipt. Add one only after
it has cost something.

1. **Send the mechanism, not the summary.** A merge relocated identity
   because CQ's API could not express a name choice while SS's client
   expressed it by moving the surviving row. Both bugs produced the
   identical observable, so from either side the other was invisible and
   more private rigour would not have found it. What found it was one
   team describing a fix in enough detail that the other could check
   their half against it. When both parties hold a COHERENT account,
   nobody is confused, so nobody asks.

2. **Prove the test can fail.** GP verified their passthrough tests by
   making the proxy behave like a middlebox (dropping 4xx bodies,
   re-sorting arrays, flattening nested ones) and confirming each test
   went red. SS sabotaged a survivor rule to confirm three tests caught
   the exact bug shape. Both found real defects. A test that cannot fail
   on the bug it was written for is decoration.

   And prove the SABOTAGE worked, not merely that something went red. It
   failed repeatedly in one evening across both teams; five of the ways:
   a mutation that never reached the file; one that reached it but sat
   on a branch the test could not take; a module break that turned
   everything red and read as caught; the same break leaving a
   source-reading test green and reading as uncaught; and a grep
   matching a warning URL containing "error". So confirm the mutation
   reached the file, then that the test you EXPECTED failed, and only
   that one. A diff proves the edit and coverage proves the line ran;
   NEITHER proves the branch you changed was taken, because a
   short-circuit inside one expression compiles to a real jump with
   every instruction on one source line, invisible to any tool reasoning
   in lines.

   A sixth way, worse than the five because every check above passed:
   GP inverted a predicate, expected six tests red, saw two, and the
   honest reading of that is "the filter is more robust than I thought".
   It was a STALE BYTECODE CACHE after several rapid mutations, so the
   run had used the pre-mutation module while the file on disk showed
   the mutation. The mutation reached the file, the branch was
   reachable, the test was correct, and the result was still fiction.
   Nothing in the diff, the file or the test could have revealed it,
   because the thing that was stale was none of those. So when a
   sabotage says a test is more robust than you expected, treat the
   surprise as the finding and re-run it in ISOLATION with the cache
   cleared, rather than as evidence you built better than you knew. CQ
   hit the same shape from the other end the same evening: a mutation
   that landed in the file, changed nothing semantically, and went
   green, which is indistinguishable from a coverage gap unless you
   check what the edit actually did.

3. **A response-side test cannot see a request-side hole.** `to_name`
   was sent by SS and silently dropped by an unmodelled field in GP's
   schema. SS saw a correct send; CQ saw a complete request that simply
   lacked a name, so neither endpoint held evidence that anything was
   wrong. It lived only on the middle hop, and only a request-side test
   there could find it. (How long it sat is unmeasured. An early draft
   said "about a week", which nobody had counted; GP caught it, which is
   rule 6 working before this text had even shipped.)

4. **Check the echo, not the status.** A 200 says the request was
   processed, never that it did what the caller meant. A merge reported
   success while the chosen name never travelled, and an endpoint logged
   `200 OK` for sixty seconds while every device saw a 504. Where a
   write has an outcome worth confirming, serve it back and have the
   caller compare.

5. **Name which side each claim was proved on.** Additive at the writer,
   at the gateway and at the reader are three different claims, and each
   side can prove its own half while the failure lives on another. This
   is doc 19.9 stated as a habit rather than a ruling.

6. **Say what you have NOT done, and correct your own numbers out
   loud.** "I have not started it tonight and I am not going to pretend
   otherwise" is worth more than a hedge. A cost estimate that was 4x
   wrong, a scope of 41 meetings that was really 150, and a "13 of 167
   close cleanly" that was really an abstention rate were all corrected
   by the team that produced them, which is the only way any of them
   could have been.

7. **Verify the property you assert, do not just name it.** SS wrote
   "the BIGGER relationship survives, always" in a comment above code
   that chose the survivor by how many words were in the NAME, and
   shipped it; the comment was true of the intent and false of the code.
   CQ wrote `getattr(body, ...)` for a parameter actually named `req`,
   which passed a syntax check and would have been a NameError on the
   first real call. Two teams, one day, one shape: a name that sounded
   right and was never opened. A comment cannot be wrong out loud.

8. **Wherever two systems each apply a CORRECT filter, the intersection
   is invisible to both.** Scott asked why Suresh, his highest-data
   person at 140 meetings, showed FEWER insight lenses than a colleague
   at 104. CQ's `one_card_per_lens` collapse kept one card per lens,
   correctly. SS dropped any claim with fewer than three receipts,
   correctly, because three receipts is what separates a pattern from a
   coincidence. The collapse happened to keep the cards with TWO rows,
   so the client dropped them and the page starved. CQ saw three cards
   shipped, SS saw one rendered, and the deciding number lived inside a
   card neither team was inspecting. No error, no log, no failing test,
   both halves behaving exactly as written. This predicts WHERE to look
   rather than describing the damage afterwards: when a user reports
   "less than I expected" and both sides look correct, stop hunting for
   a fault and go find the INTERSECTION of two filters. Then make each
   side's drop audible, because the other team cannot build your half.
