---
id: priv-what-stays-on-device
title: What stays on your phone
category: Privacy
keywords: [privacy, on device, local, audio, recordings, transcription, voice id, calendar, api keys, contacts, photos, offline]
screen: settings/privacy
---

Most of what Shoulder Surf does happens on the phone, and a lot of it never goes anywhere else.

**Transcription.** Speech is turned into text by Apple's on-device speech engine. No audio is sent anywhere to be transcribed, and it works with no signal.

**Speaker separation and Voice ID.** Telling voices apart, and recognizing enrolled voices, runs on the phone with on-device models. Your enrollment audio and the voice fingerprints stay on the device.

**Audio recordings.** Saved on the phone with the meeting. The app never uploads audio to Shoulder Surf's servers on its own. The two ways audio leaves the phone are both yours to choose: your own iCloud (iCloud Sync, on by default, and iCloud Drive Backup, off by default), which Apple encrypts and Shoulder Surf can't read; and a share you create with "Include the audio" switched on.

**Photos.** Stored with the meeting on the phone. A photo goes to an AI only when you attach it to a question, and only to the model you picked.

**Calendar.** Read on the device for meeting prep. Event details are never uploaded.

**Contacts.** Only if you allow it (People's "Add photos from Contacts"). The app reads Contacts on the phone to match people from your meetings, and keeps just the matched person's photo, phone, email and company. Those stay on your devices and in your private iCloud when sync is on; none of it goes to Shoulder Surf's servers. Without access, linking a person uses Apple's picker and only the card you choose is kept.

**API keys.** In the iPhone Keychain, never synced, never sent to Shoulder Surf.

**On-device AI.** With Apple On-Device selected, questions, summaries and Meeting Chat run on the phone; nothing about the meeting leaves the device for those.

**Usage statistics and crash reports** are the exception to "stays on the phone" and are described in "What goes to the server." They never include transcripts, recordings or names.

Nobody at Shoulder Surf can see your recordings. There's no path in the app that sends audio to us except a share link you make.
