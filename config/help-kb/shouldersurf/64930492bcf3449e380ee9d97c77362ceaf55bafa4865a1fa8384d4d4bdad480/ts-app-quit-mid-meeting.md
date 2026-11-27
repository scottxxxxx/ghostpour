---
id: ts-app-quit-mid-meeting
title: The app quit during a meeting
category: Troubleshooting
keywords: [crash, quit, killed, meeting interrupted, resume, autosave, lost recording, recovered, session at risk, force quit]
screen: session
---

iOS killed the app, or it crashed, with a meeting running. Here's what you'll find.

**A "Meeting interrupted" card** on the Copilot tab with a Resume button. The app autosaves the running meeting every 30 seconds (and immediately when it goes to the background), so the transcript, answers, summary and audio up to the last save are there. Resume picks the meeting up where it left off; End saves what was captured.

**If you don't see the card,** check Review: a meeting that was ending when the app died is saved under its original name once the app relaunches.

**What may be missing:** up to 30 seconds of transcript before the kill, and the last photo if it hadn't been written yet. The audio file is written continuously, so the recording usually runs right up to the moment it stopped.

**"Session at Risk" notification.** iOS was about to cut the app's background time. Return to the app to keep recording; if it was too late, the autosave above is what you have.

**Why it happens.** Very long meetings with the camera on and a hot phone are the usual culprit, and iOS is strict about memory. Turning the camera off when you don't need slides helps a lot. The app also turns the camera off on its own when the phone gets hot.

**Help us fix it.** If the app crashed, it sends us Apple's crash report on its own the next time you open it, with nothing from your meetings. To tell us what you were doing at the time, write to support@shouldersurf.com.
