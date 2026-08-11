# People, Memory, and what a capture actually feeds

Date: 2026-08-10. From GhostPour, for ContextQuilt and ShoulderSurf.

Scott's position first, then the observation that changed the free-tier
build, then the four things we have not worked through. The last section is
the one that needs both teams.

---

## 1. The position

People is being surfaced as its own part of the ShoulderSurf app, and it
needs to be genuinely useful there. **Utility comes before holding value
hostage.** A People tab that exists to advertise a paid feature by being
disappointing is worse than not shipping it.

That is settled and not up for debate. What follows is about making it
true without breaking the thing it is built on.

## 2. The observation that changed the free-tier build

People was enabled on every tier and the only closed gate was signed-out.
But People is built from captured meetings, and a free user was capped at
one capture a month, of which CQ extracts people from roughly 43 percent.
So a free user's People tab was empty or near-empty for months.

That reads as broken rather than as locked, which is the one impression a
gate must never give. A locked feature should look locked.

The fix rests on noticing what a capture actually feeds. **It feeds two
things and only one of them is paid.** Person entities are People, which is
free on every tier. Quilt patches are Memory, which is not. Skipping the
capture was starving a feature the user is entitled to in order to meter
one they are not.

So as of today a free user past their Memory quota is captured anyway. The
quota still governs the upsell copy, not whether the meeting reaches CQ.

## 3. What that surfaced, and had not been thought through

Putting People in its own tab demonstrates ContextQuilt's value, which is
the point. But it changes the shape of something that used to live inside
Memory, and four consequences follow.

**Duplication.** Person objects should not appear in both the People tab
and the Memory section. If they are surfaced in both, the same knowledge is
rendered twice in two different shapes, and a user editing one will expect
the other to change. Those objects should move out of Memory rather than be
copied into People.

**Continuity.** People must keep working the way it did when it lived
inside Memory. Moving a surface is not a reason for its behaviour to change,
and any behaviour that does change should be a decision somebody made rather
than a side effect of the move.

**Upgrade has to be seamless.** A user who pays gets everything CQ
provides. At that moment the People objects CQ has already been maintaining
for them, for free, have to integrate with the rest rather than sit beside
it as a second set. Nothing should be rebuilt, re-extracted, or duplicated
at the upgrade boundary. The upgrade should reveal history, not create it.

**The interface has to segregate them, and we have not designed that.** How
the People tab and the Memory section divide the same underlying knowledge
is an open UI question. It is not obvious and it should not be settled by
whichever team ships first.

## 4. The one that needs both teams: recall

**This is the most important open question and it is currently answered by
accident rather than by design.**

Today, recall is gated on the `context_quilt` entitlement:

| tier | entitlement | recall on a query |
|---|---|---|
| Free | `disabled` | **does not run at all** |
| Plus | `enabled` | runs |
| Pro | `enabled` | runs |

So after today's change a free user's quilt fills up with real knowledge
from every meeting they record, and **not one word of it ever reaches a
query they ask.** Their People tab will show colleagues, projects and
commitments extracted from their own meetings, while the assistant in the
same app answers as though it has never heard of any of them.

That is coherent as a pricing boundary and incoherent as a product
experience, and nobody chose it. It is what the old gate did when People was
not a separate surface, and the surface changed underneath it.

The question is not "should free users get recall", which is a pricing
decision. It is narrower and it belongs to all three of us:

- **When we recall from CQ to add context to a query, what role do person
  objects play, separately from patches?** Today recall returns one context
  block. If People is its own surface with its own entitlement, is there a
  people-shaped part of recall that follows the People entitlement rather
  than the Memory one?
- **If yes, what does it contain?** Names and relationships a free user can
  already see in their own tab are not a paid secret. The commitments,
  history and cross-meeting synthesis behind them plausibly are.
- **If no, we should say so plainly**, and accept that a free People tab is
  a browsable directory rather than something the assistant knows about.

Either answer is fine. Not choosing is what produces the version where the
product contradicts itself in front of the user.

## 5. What each side is holding

**GhostPour.** Free-tier capture is uncapped as of today, keyed to the
People entitlement so the dashboard toggle still closes the door. Copy
updated in four locales, because the old line said "Want your AI to remember
meetings?" while we had just started remembering all of them. We have not
gated the Memory read server-side: it is already gated by the entitlement,
and accumulating means an upgrade reveals real history instead of an empty
room.

**ContextQuilt.** Expect free-tier extraction volume to rise, from roughly
one capture per free user per month to every meeting they record. Small in
absolute terms today, 103 free calls against 914 pro over thirty days, so
this is a shape change rather than a load event. The alert threshold we
agreed, 500 free captures in a rolling thirty days, is the number to watch.
The question above about a people-shaped recall is the one we would most
like your read on, because you know what is cheap to serve separately and
what is not.

**ShoulderSurf.** The empty state for a brand new account with no meetings
is now a first-run state rather than a tier state, and the copy should say
what the system does rather than what the plan withholds. The duplication
and segregation questions in section 3 are mostly yours to design, and we
would rather see the design before it ships than review it after.

## 6. What we are asking for

One decision from each team, and one shared one.

1. **CQ:** is there a people-shaped part of recall that can follow the
   People entitlement rather than the Memory one, and what would it cost?
2. **SS:** how do the People tab and the Memory section divide the same
   knowledge, and what moves out of Memory?
3. **All three:** what does a free user's assistant know about the people
   in their own People tab? That answer determines the other two.
