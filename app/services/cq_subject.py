"""Which CQ subject a GP user's memory belongs to, per app.

CQ decided (doc 18) that each app gets its own subject space: no shared
person objects, no app_id column on any memory table, and a human using
both apps has two subjects. Isolation rides `subject_key`, which every
patch read already filters on.

The problem that makes this urgent is ours, not theirs. We send the bare
GP user id as the subject, with no app scoping anywhere. And SS and TR
share ONE users row, because Apple's `apple_sub` is issued per developer
team rather than per app. Two production users are active in both apps
today. So the isolation CQ decided is one real TR capture away from being
violated by our identity minting. It holds right now only because TR has
never written real memory.

Two rules, and the second one is why this is a translation rather than a
schema change:

**ShoulderSurf keeps the bare user id.** It has real data and a live
install base. Moving its subject would orphan every existing patch, and
there is no reason to: it was the only writer, so the unnamespaced space
is already exclusively its own. New apps carry the namespace instead.

**Clients never learn about this.** The namespace is applied at the
outbound CQ boundary only. The path a client sends still carries the plain
GP user id, so the ownership guard still compares `user.id` to the path
value and does not change, and no client ships anything. An earlier read of
this said the guard would have to change; that was true only of a design
where the namespace rode in the request path, which this is not.

Form confirmed with CQ: the namespace lives INSIDE the id, never in the
`user:` prefix they build in about fourteen SQL sites. Everything after
`user:` is opaque to them and may contain colons.
"""

from __future__ import annotations

# The app whose subject stays unnamespaced, forever. Not a default so much
# as a historical fact: it wrote every subject that exists.
_UNNAMESPACED_APP = "shouldersurf"


def subject_for(app_id: str | None, user_id: str) -> str:
    """The CQ subject for this user, as seen by this app.

    An unknown or missing app_id maps to the unnamespaced space. That is
    deliberate: unattributed traffic has always written there, so treating
    it as a new namespace would strand memory that already exists. A
    request we cannot attribute is far more likely to be an old SS build
    than a new app.
    """
    app = (app_id or "").strip().lower()
    if not app or app == _UNNAMESPACED_APP or app == "unknown":
        return user_id
    return f"{app}:{user_id}"
