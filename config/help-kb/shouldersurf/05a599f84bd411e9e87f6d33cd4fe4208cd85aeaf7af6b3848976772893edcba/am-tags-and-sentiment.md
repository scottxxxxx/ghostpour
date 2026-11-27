---
id: am-tags-and-sentiment
title: Tags, sentiment and urgency
category: After a meeting
keywords: [tags, custom tags, built-in tags, sparkles, ai suggested, reason, remove tag, sentiment, emoji, urgency, orb, stoplight, auto-analyze, minimum duration]
screen: review/meeting
---

**Tags.** Eight built-in tags: Review, Follow-up, Schedule Meeting, Research, Share, Important, Action Items, Decision Made. Add your own in Settings > Meetings > Tags (or type one in the "+ Tag" popover on a meeting). Custom tags sync to your other devices. Renaming a custom tag doesn't rename it on past meetings. The "+ Tag" picker lists the built-ins; to reuse a custom tag, type it again.

**AI-suggested tags.** After a meeting, the analysis picks one to four built-in tags and gives each a one-line reason. Those tags wear a small purple sparkle. Tap any tag to see why it's there ("AI suggested" with the reason, or "Manually assigned tag") and to Remove Tag, which asks you to confirm. The report suggests tags with reasons too.

**Sentiment.** The emoji at the top of the card. Labels: informational, positive, collaborative, cautious, pressured, tense, disconnected, decisive. Tap it for the reason.

**Urgency.** The colored orb beside the emoji: green low, yellow medium, orange high, red critical. The card's tint follows it, so a red card means "act on this." When a report exists, its stoplight drives the color.

**Where this comes from.** Post-meeting analysis runs after any meeting of about two minutes or more (Settings > Meetings > Summaries > Auto-analyze meetings, with a Minimum duration picker). Meetings long enough for a report get the same things from the report. Which AI runs it:

- Shoulder Surf AI: yes.
- Your own key: yes, with the own-key Extras pack.
- Apple Cloud: yes, with the Apple Cloud Extras pack.
- Apple On-Device: no. The on-device model is too small, so those meetings get no sentiment, urgency or suggested tags.

Re-analyze Meeting in the ⋯ menu runs it again with your current model.
