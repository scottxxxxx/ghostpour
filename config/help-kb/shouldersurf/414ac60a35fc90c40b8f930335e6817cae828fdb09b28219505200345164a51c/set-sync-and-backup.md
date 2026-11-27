---
id: set-sync-and-backup
title: iCloud Sync and iCloud Drive Backup
category: Settings
keywords: [icloud, sync, backup, icloud drive, files app, second device, ipad, private database, what syncs, settings sync, verify and back up]
screen: settings/backup-sync
---

Two different things, both in Settings > Backup & Sync, both using your own iCloud account.

**iCloud Sync.** Keeps the app's data the same on your iPhone and iPad. On by default when the phone has an iCloud account (if you ever turn it off, it stays off). What syncs: meetings with their transcripts, summaries, questions, chats, reports, tags, analysis, language and share info; the audio; photos; projects; voice profiles (so you don't re-enroll on the iPad); contact links for people; References. Most settings sync too (voice, speed, summary settings, custom tags, Quick Prompts, transcription language, watchdog interval, and so on). Not synced: API keys and translations (a second device rebuilds those on demand). Everything goes to your private iCloud database, which Apple encrypts and Shoulder Surf can't read. Sync needs no Shoulder Surf account. "Sync Status" and "Last Synced" show on the same page; pull down in Review to nudge it.

**iCloud Drive Backup.** Off by default. Writes each meeting as plain files you can open anywhere, in a "Shoulder Surf" folder in iCloud Drive, organized by project: audio.m4a, transcript.txt, summary.txt, report.html, chat.html, queries.html, captions.srt, and a photos folder. It's an archive for keeping forever, even without the app. The app never restores from it. "Verify & Back Up Now" fills gaps; "Full Verify" rewrites everything (slow on a big library). Each device backs up on its own to the same folder.

**Which to use.** Sync for a second device and for a lost phone (see "What a lost phone recovers"). Drive Backup if you want files you can hand to someone or keep outside the app.

**Audio and your privacy.** With Sync on, your recordings are in your private iCloud. With Drive Backup on, they're also files in your iCloud Drive. Neither goes to Shoulder Surf's servers; only a share link you create does that.
