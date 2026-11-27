---
id: dm-background-and-lock
title: Locking the phone or switching apps mid-meeting
category: During a meeting
keywords: [background, lock screen, switch apps, recording in background, keep recording, speaker id, camera, notification, session at risk]
screen: session
---

You can lock the phone or open another app during a meeting. Recording keeps going; the app has iOS's background audio permission for exactly this.

**What keeps running:** audio capture and the on-device transcription.

**What pauses until you come back:** the camera (so no photos, and nothing captured in Read the screen mode), speaker identification, and the rolling summary timer. When you return, the app restarts its speech engine, which can drop a word or two at the seam.

**What you'll see:** a notification, "Recording in Background. Audio is still being captured. Return to Shoulder Surf for speaker ID and real-time features." If you had paused first, it says "Meeting Paused" instead. If iOS is about to cut the app's background time, you get "Session at Risk," which means come back to the app.

**The silence watchdog** still works in the background, as a notification with Keep Recording and Stop Session buttons.

**Phone calls and other apps grabbing the mic** pause capture; the app resumes on its own when the mic is free and shows a short banner. If iOS kills the app entirely, your meeting is autosaved every 30 seconds, and the Copilot tab offers a "Meeting interrupted" card with Resume the next time you open it.

Tip: the app keeps the screen from auto-locking while you're on the recording screen. If you want to lock it, press the side button.
