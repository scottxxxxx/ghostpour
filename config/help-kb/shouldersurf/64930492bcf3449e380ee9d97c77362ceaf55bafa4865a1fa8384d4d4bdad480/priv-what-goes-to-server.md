---
id: priv-what-goes-to-server
title: What goes to the server, and when
category: Privacy
keywords: [privacy, server, upload, transcript, signed in, shoulder surf ai, anthropic, openai, third party, usage statistics, analytics, share, where your data goes, privacy policy]
screen: settings/privacy
---

Settings > Privacy & About > "Where your data goes" is the in-app version of this. Here's the plain list.

**When you ask a question.** The question, the slice of transcript you chose, the rolling summary, project info, and any photos or references you attached go to the AI model you selected. On Shoulder Surf AI that's our server, which forwards to the AI provider under contract and keeps a record for your account. On Apple Cloud it goes to Apple's Private Cloud Compute. On your own key it goes straight from the phone to that provider under your account. On Apple On-Device it stays on the phone.

**When a meeting ends, if you're signed in with Apple.** The transcript (text only, never audio) goes to Shoulder Surf's server. It's what the report, translation, Memory and People are built from. This happens whichever AI model you've picked for questions. It does not happen when you're signed out, or on a plan bought without signing in.

**Meeting Chat and Project Chat.** Same as questions: the context you included goes to the model you picked. Project Chat on Shoulder Surf AI keeps the conversation on the server.

**Reports and translation.** Written on the server from the transcript it already has.

**Sharing.** A share link uploads what you included (transcript always; report, chat, files, photos and audio by your switches) and hosts it until the link expires or you revoke it. Export Artifacts uploads nothing; it makes files you send yourself.

**Usage statistics** (always on): when the app opens, when meetings start and stop and how long they run, which AI model you picked, how far you got through setup, app and iOS version, device model and language, and which Apple Ads ad you came from if any. Plus an install identifier and your account ID when signed in. Never transcripts, recordings, meeting content, names or email.

**Crash reports.** If the app crashes or freezes, it sends Apple's crash report so we can fix it: what the app was doing in code, plus app version, iOS version and device model. No identifier, no logs and nothing from your meetings.

**Email.** Only if you ticked "Keep me in the loop by email" at setup or in Settings > Privacy & About > Communications.

The full privacy policy is at shouldersurf.com/privacy, linked from the same Settings page. Deleting your account removes everything the server holds; see "Signing out and deleting your account."
