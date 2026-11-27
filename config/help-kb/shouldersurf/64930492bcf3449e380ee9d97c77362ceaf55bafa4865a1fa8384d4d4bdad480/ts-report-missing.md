---
id: ts-report-missing
title: The report didn't generate
category: Troubleshooting
keywords: [report missing, no report, generate your meeting report, report unavailable, report generation failed, allowance, sample report, too short, signed out, anonymous, retry]
screen: review/meeting
---

The meeting ended and there's no report. Match your case.

**"Generate your meeting report" banner on the meeting page.** The meeting was too short for an automatic report (under about five minutes, or too few words), or the request didn't go through. Tap the banner, or Regenerate Report in the ⋯ menu. Anything over about half a minute can have a report on request.

**The post-meeting screen said "Your report is still being generated."** The server takes a while on long meetings. "Tag Now, View Later" lets you move on; the report lands on the meeting page when it's done. If a server deploy interrupted it, the app retries once on its own after about half a minute.

**"Report unavailable."** The meeting was recorded while signed out (or on a plan bought without signing in). The transcript never reached the server, so there's nothing to write a report from. Sign in with Apple before future meetings.

**A SAMPLE report with a "Generate Full Report" or "Upgrade" button.** You're on Free and the month's allowance is used. It regenerates itself once you have allowance again, or upgrade.

**"Report generation failed" with "You've used your report allowance."** Same cause, on a manual request.

**"Generate full report" on the card is missing.** It only shows when the meeting reached the server. A meeting recorded signed out never will.

**The report came back in the wrong language.** Reports are written in your phone's language. Switch the meeting's language (globe chip) and the report is regenerated in that language.

**I'm on Apple Cloud or my own key.** Reports are still Shoulder Surf AI's and spend your account's allowance. Once Free's allowance is gone, automatic reports stop for those models, but the banner still works and tells you if allowance is the problem. Post-meeting analysis (sentiment, tags) runs on your model instead when you own the Extras pack.

When a report fails, the app runs the lighter post-meeting analysis instead, so you still get a title, sentiment and tags where the model allows.
