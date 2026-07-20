# Launch-day config changes (SS App Store release)

Staged 2026-07-20. Two config changes to make when ShoulderSurf goes
live on the App Store. Values in `<angle brackets>` are filled in at
launch from the approved build. Nothing here is applied yet: the current
config is deliberately in its safe pre-launch state.

---

## 1. App Store version block (REQUIRED at launch)

**File:** `config/app-versions.yml`
**Path:** `com.shouldersurf.ShoulderSurf` → `platforms` → `ios` →
`latest_by_channel` → `appstore`

**Current (safe no-op, do not ship as-is past launch):**
```yaml
        appstore:
          version: "0.0"
          build: "0"
          upgrade_url: ""
```

**Change to:**
```yaml
        appstore:
          version: "<approved App Store marketing version>"   # e.g. "1.14"
          build: "<approved App Store build number>"           # e.g. "749"
          upgrade_url: "https://apps.apple.com/app/id<APP_STORE_ID>"
```

**Where the values come from:**
- `version` / `build`: the exact `CFBundleShortVersionString` and
  `CFBundleVersion` of the build Apple approved (App Store Connect ->
  your app -> the live version).
- `APP_STORE_ID`: App Store Connect -> App Information -> the numeric
  Apple ID for the app.

**Leave alone:**
- The top-level `latest` fallback block stays on the TestFlight values.
  It only serves header-less clients, which are old TestFlight builds.
- The `testflight` block keeps tracking beta builds (they run ahead of
  the App Store); bump it on each TestFlight release as before.

**How it takes effect:** `app-versions.yml` is load-at-startup, so this
is a normal PR -> merge -> Build & Deploy. Not a live overlay change.

**Verify after deploy** (App Store users get the new toast, TestFlight
unaffected):
```
GET /v1/app/version
  X-App-Bundle-Id: com.shouldersurf.ShoulderSurf
  X-App-Distribution: production
-> latest.version == the App Store version, upgrade_url == the App Store link

  X-App-Distribution: sandbox
-> latest.version == the TestFlight version (unchanged)
```

---

## 2. Reactive image-quality nudge (OPTIONAL, flip whenever)

Not strictly launch-day. Turns on the "a sharper photo would help me get
the rest" note when a reproduction is built from a blurry image.

**Files:** `config/remote/client-config.json` (+ `.es`, `.ja`)
**Change:** `image_quality_note.enabled` `false` -> `true`
(leave `blur_threshold: 200`; tune later against real traffic). Bump the
`version` on each locale file.

**Current:** `{"enabled": false, "blur_threshold": 200}`, version 14.

**How it takes effect:** this is a served-config VALUE change (not a new
key), so it needs the sync step after deploy:
1. PR -> merge -> Build & Deploy.
2. `POST /webhooks/admin/config/client-config/sync-from-bundle` with the
   `image_quality_note` key so the persistent overlay picks up the new
   value (new keys auto-hydrate at boot; value changes need the sync).

**Verify:** served client-config shows `image_quality_note.enabled: true`;
a blurry photo-to-file reply carries the capture tips.

---

## Order / notes
- Item 1 gates a clean launch (App Store users must not get a TestFlight
  toast). Item 2 is independent and can wait.
- Both are additive to what SS already ships; no client build is required
  for either (SS already sends `X-App-Distribution`; the nudge is
  server-side).
