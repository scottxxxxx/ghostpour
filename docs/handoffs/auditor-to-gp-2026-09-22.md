# Compound answers: what the client changed and three items for the lane

Scott's ruling, 2026-09-22: the first question of each part invites the whole
part in one go, and the lane asks one by one for whatever she left out. The
probe behind it is qa/COMPOUND-PROBE-2026-09-22.md (24 live turns, runs
compound-probe-1 and compound-probe-2). Nothing in the prompt has to change
for the openers to work: the lane already mints the asked field, files the
rest of a current-part breath, skips their questions and asks the gaps.

## On the client (shipped in build 86)

1. Section openers: the ask text of the first node of Parts 2 through 9 now
   names what the part needs and says "give me all of it in one go and I'll
   only ask for what's missing", ending on the part's first question. Parts 1,
   10, 11, 12 and 13 are single asks and are unchanged. The openers ride in
   the agenda like every ask; conf-v20 and conf-es-v20 were rebased (agenda
   text only, envelopes identical, facts unchanged).
2. A row that exists settles the gate in front of it: has_job2 from an
   employer2 row, has_prior_address1 from a prior address, has_trips and
   has_tripN from trip rows. Derived, printed in known_facts after the window
   gates, and the harness mirror carries the same rule.
3. A U.S. address block settles province and foreign postal code as confirmed
   empty, so Austin, Texas is never asked about a province.
4. Repeated rows print on the form most recent first (trips by departure,
   jobs current first then by start, prior addresses by start), whatever
   order she gave them in. The case keeps her order for the read-back.

## For the lane, in priority order

1. A fact volunteered for the NEXT part is filed tentative and then asked
   again in full, one by one (compound-probe-1, turns 3 to 5: all 17 Part 2
   facts were right and every question came anyway). The saving is thrown
   away for that part. Wanted: confirm them at the part's checkpoint the way
   current-part facts are, or at least ask each as a one-line read-back.
2. Option ids only. compound-probe-2 turn 9 minted p5.spouse_citizen_how =
   "birth"; the catalogue id is by_birth; the client refused it and the
   question was asked again in full.
3. Never mint an empty string. compound-probe-2 turn 7 minted
   p4.current_address.in_care_of = "" from "It's in the United States, no
   province or foreign postal code."

Also seen, lower priority: after a compound Part 8 answer with two trips the
lane asked "any other trips" again although has_trip3 had been filed (turn
18); the client's row gate now settles has_trip2 from the row, but the
"any more" question after the last row is still the lane's to skip when she
said "nothing else".
