---
id: mem-what-it-stores
title: What Memory stores, and where
category: Memory
keywords: [memory, patches, patch types, decision, commitment, blocker, trait, preference, server, stored, transcript capture, on-device, own key, which models]
screen: review/memory
---

**The patches.** Each patch is one fact with a type: Trait, Preference, Role, Person, Project, Decision, Commitment, Blocker, Takeaway, Goal, Constraint, Organization, Event, Deliverable, Moment, Insight, Artifact, Observation. Commitments and blockers carry an owner and, when one was said, a deadline. Patches can be connected to each other (a commitment "belongs to" a project, a decision "motivated by" a blocker). Each one points back at the meeting it came from.

**Where it lives.** On Shoulder Surf's servers, tied to your account. The phone keeps a cache so the view opens fast, but the memory itself isn't on the device and doesn't need to sync between your devices; sign in on the iPad and it's there.

**How it's made.** At the end of a meeting, when you're signed in with Apple, the transcript is sent to the server with your name marked on your lines (from Voice ID). The server extracts patches from it. Questions you asked during the meeting and the auto-summaries feed it too. If the upload fails (no signal at the end of a meeting), the app keeps a note and sends it the next time it's open.

**Which AI model you use doesn't matter for capture.** On-device, Apple Cloud, your own key: as long as you're signed in, the transcript is still captured for Memory and People. What does depend on the model is **recall**, memory being used in answers: that only happens on Shoulder Surf AI, because the memory is injected on the server. On any other model the Context Quilt chip in chats is unavailable.

**What never goes in.** Meetings recorded while signed out. Meetings on an anonymous purchase-only account (subscribe without signing in and nothing is captured until you sign in with Apple). Shared meetings someone sent you, unless you choose "Add to memory." Audio never goes to Memory; only text.

**Plans.** Free accounts get People pages built from their meetings and a preview of Memory; Plus and Pro get the full quilt and recall in answers. Plus and Pro recall from every meeting; Plus's Project Chat covers the last 30 days, and Pro's reaches every meeting you've ever recorded.

**Deleting.** Delete a patch and it's gone from the server. Deleting a meeting from Review does not delete the patches that came from it; see "Correcting Memory." Deleting your account deletes all of it.
