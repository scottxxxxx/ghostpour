---
id: gs-permissions
title: Permissions, and why each one is asked
category: Getting started
keywords: [permissions, microphone, camera, speech recognition, calendar, reminders, photos, notifications, local network, contacts, denied, settings]
screen: settings/privacy
---

Shoulder Surf asks for a few iOS permissions. Only one is required. You can see the status of each in Settings > Privacy & About > Permissions.

**Microphone (required).** It's a recording app. Without this it can't hear anything. If you denied it, iOS Settings > Shoulder Surf > Microphone turns it back on.

**Speech recognition (needed to transcribe).** iOS asks this so the phone's own speech engine can turn audio into text. Transcription runs on the device; this permission doesn't send audio anywhere.

**Camera (optional).** For capturing slides and screens during a meeting, and for Read the screen mode. Deny it and you still get audio and transcript.

**Notifications (optional).** The silence watchdog ("No Speech Detected") and the "Recording in Background" notices need it.

**Calendar (optional, off until you turn it on).** Settings > Meetings > Preparation > "Use my calendar." The app reads your calendar on the device to recognize your next meeting and prep you for it. Event details never leave the device. It asks with its own explanation first, so you can say "not now" without burning the one system prompt iOS gives.

**Reminders (optional).** Only when you save action items to the Reminders app.

**Photos, add only (optional).** Only when you choose Save to Photos on a captured image. The app never reads your photo library through this; picking a photo to attach uses Apple's picker, which needs no permission.

**Local Network (optional).** Only if you use the Mac or Windows companion. The app needs it to find your computer on the Wi-Fi.

**Contacts (optional, asked only when you choose).** People shows an "Add photos from Contacts" card; tap Allow and iOS asks once. The app reads Contacts on the phone to match people from your meetings to their cards and copy photos (and, when you tap Get from Contacts on a person, their phone, email and company). Say no, or tap "Not now," and you can still link one person at a time with Edit > Choose from Contacts, which uses Apple's picker and needs no permission.

If something is "Denied," the Permissions list in Settings > Privacy & About shows it, and the fix is always iOS Settings > Shoulder Surf.
