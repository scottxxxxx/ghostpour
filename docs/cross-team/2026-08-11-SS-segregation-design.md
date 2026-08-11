# SS: the People/Memory segregation design, for GP review before ship

Date: 2026-08-11. From ShoulderSurf, answering the boundary doc's section 3
ask ("we would rather see the design before it ships than review it after").
Companion to the ratified decision (contextquilt-ops docs/cross-team/
2026-08-11-DECISION-people-full-value.md) and CQ's ack of our answers.

## The rule

**Memory is about what the user knows; People is about who they know.** Any
object whose identity is a person renders on the People surface and nowhere
else. Any fact that references a person stays in Memory and renders the
person as a link into their People representation. A category leaves Memory
only when it has a surface to move to, which is why orgs stay memory-side
today and follow the same pattern if an org surface ever exists.

## What the user sees after the move

- The Memory tab has no PEOPLE section and no project-filtered people list.
  Person patches keep arriving in the payload (they are the graph anchors);
  they simply do not render as memories anywhere, including a meeting's
  memories list.
- Any person tap that still occurs (for example pushing through a
  connection row on another patch) lands on the People detail, resolved
  patch_id-first, then name/alias. The Memory-side person detail (the
  CONNECTIONS list) is retired; everything it showed renders better on the
  People card, from the same store.
- Delete Memory does not exist on person rows anywhere. Person lifecycle
  verbs are rename, merge, keep-separate, and confirm, all on the People
  surface, plus the not-a-person verb CQ is building for placeholder
  cleanup. There is deliberately no person delete.
- Owner references on Memory items become links into People once CQ serves
  `owner_entity_id` (their build item 2). We decode the field already; the
  chips light up when it arrives. We deliberately did NOT build a
  client-side name-matching join in the interim, because duplicating CQ's
  entity resolution on device is the split-brain this whole design exists
  to prevent.
- Free-tier empty states become first-run states: copy says what the system
  does, not what the plan withholds. Memory's paid gate copy is unchanged
  (Memory remains the paid surface).

## What deliberately does not change

- Ledger counts, decay bands, shelve/vouch/complete/uncomplete, merge,
  rename, confirm: all live on People already and are untouched.
- CQ keeps serving person patches; nothing about the wire changes for the
  move itself. The client change is filter-and-links only.
- No second person store client-side, ever. One store, one rendering.

## Status

Client half is built and committed on SS main (`804c627`), sim-verified,
behind our normal device pass. Nothing ships to TestFlight before GP has
had this design in hand; flag anything here and it changes before that
build cuts. Mocks available on ask if prose is not enough.
