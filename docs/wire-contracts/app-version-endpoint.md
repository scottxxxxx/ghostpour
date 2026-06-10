# `/v1/app/version` — wire contract

Per-app version metadata endpoint. Backs the "you're on an old version"
soft banner iOS renders on launch. Multi-tenant by bundle id from day
one so the gateway can serve any app on top of GhostPour without
changing the wire shape.

Last updated: 2026-06-10.

## Concepts

- **Bundle id on the request** — the app identifies itself with its
  `CFBundleIdentifier` (e.g. `com.shouldersurf.ShoulderSurf`) sent on
  every call. Required. No fallback, no inference. Two apps with two
  bundle ids get two completely independent answers from the same
  endpoint.
- **`latest` block** — canonical nested form. Couples the version with
  the URL that lands users on it, because both change as a release
  unit. Operators only edit this on the server side.
- **`latest_version` + `upgrade_url` flat aliases** — synthesized in
  the response next to the nested `latest` block. Belt and suspenders
  for older iOS builds that decoded the flat shape PR #210 originally
  shipped. Same values as the nested form; operators don't manage
  these directly.
- **`min_supported_version`** — hard floor. iOS may show a non-dismissible
  prompt below this. Currently stays at `1.0` (soft banner only). Bump
  only when an older build has a real correctness or security issue.
- **Cache lifetime** — `Cache-Control: public, max-age=300` on the
  response so an iOS install can read the value at most every 5
  minutes, smoothing out the load and giving operators a predictable
  window between flipping the YAML and devices picking up the change.

## Request

```
GET /v1/app/version
X-App-Bundle-Id: <bundle id>          [required]
```

No `Authorization` header. The call fires pre-login on app launch.
Trust comes from the request being explicit about which app is asking,
not from a JWT — an attacker poking the endpoint just gets back the
same public version data anyone with TestFlight can already see.

### Headers

| Header | Required | Notes |
|---|---|---|
| `X-App-Bundle-Id` | yes | App's `CFBundleIdentifier`, exact case |

## Responses

### 200 — known bundle

```json
{
  "bundle_id": "com.shouldersurf.ShoulderSurf",
  "platforms": {
    "ios": {
      "latest": {
        "version": "1.13",
        "build": "450",
        "upgrade_url": "https://testflight.apple.com/join/ubRWVcXF"
      },
      "latest_version": "1.13",
      "latest_build": "450",
      "upgrade_url": "https://testflight.apple.com/join/ubRWVcXF",
      "min_supported_version": "1.0"
    }
  }
}
```

Headers: `Cache-Control: public, max-age=300`.

Field reference:

| Field | Type | Notes |
|---|---|---|
| `bundle_id` | string | Echo of the request header |
| `platforms.ios.latest.version` | string | Canonical latest released marketing version |
| `platforms.ios.latest.build` | string | CFBundleVersion (numeric string), optional |
| `platforms.ios.latest.upgrade_url` | string | Tap target for the upgrade banner |
| `platforms.ios.latest_version` | string | Flat alias mirroring `latest.version` |
| `platforms.ios.latest_build` | string | Flat alias mirroring `latest.build`, optional |
| `platforms.ios.upgrade_url` | string | Flat alias mirroring `latest.upgrade_url` |
| `platforms.ios.min_supported_version` | string | Hard floor for the gate |

### Build number semantics

`latest_build` is opt-in. Clients only consult it when their marketing
version equals `latest_version`:

- **TestFlight installs** whose `CFBundleVersion` is below `latest_build`
  show the same soft update banner as a version mismatch. Useful for
  pushing testers from build 450 to build 451 within the 1.13 cycle.
- **App Store installs** ignore the field entirely (App Store doesn't
  expose build numbers as a tap target).
- **Field absent** = backward compatible no-op. Every iOS build in the
  field before 451 ignores it anyway; first build that reads it is 451+.

The field is always a numeric string ("447", "1042") — never numeric.

Clients MAY read from either the nested or the flat fields. Both shapes
will always be served in sync. New clients should prefer the nested
form (the URL belongs to the version it points to, semantically), but
legacy decoders reading the flat form will continue to work without
intervention.

### 400 — missing or empty bundle id header

```json
{
  "detail": {
    "code": "missing_bundle_id",
    "message": "X-App-Bundle-Id header is required."
  }
}
```

Whitespace-only header counts as missing.

### 404 — unknown bundle id

```json
{
  "detail": {
    "code": "unknown_bundle_id",
    "message": "No version metadata for bundle id 'com.example.unknown'."
  }
}
```

Returned both when the bundle id has no registry entry and when the
entry exists but has no `platforms` block (treated as misconfigured).
404 is intentional: silent 200 with empty data would be a worse
failure mode because clients would log "no update available" with no
hint that the gateway doesn't actually know about them.

## Server-side configuration

Registry lives at `config/app-versions.yml`, keyed by bundle id.
Operators only edit the nested form; the flat aliases are emitted
by the response transformer.

```yaml
com.shouldersurf.ShoulderSurf:
  platforms:
    ios:
      latest:
        version: "1.13"
        upgrade_url: "https://testflight.apple.com/join/ubRWVcXF"
      min_supported_version: "1.0"
```

Adding a new app is one top-level key, no code change. New platforms
(Android, macOS, etc.) drop in as new keys under `platforms` with the
same nested + flat alias shape.

## Release sequencing

Operator MUST follow this order when bumping `latest.version`:

1. The new build is shipped to the public group.
2. The new build is confirmed installable by the public group.
3. THEN bump `latest.version` in the YAML and deploy.

Bumping ahead of installability nags devices toward a build they
cannot yet get, which produces a frustrating "tapped the banner, got
an error" UX. Always lag.

## Implementation

- Endpoint: `app/routers/app_version.py`
- Service: `app/services/app_version.py` (registry loader + response transformer)
- Registry: `config/app-versions.yml`
- Settings: `Settings.app_versions_path` (default `config/app-versions.yml`)
- Tests: `tests/test_app_version.py`

## History

- 2026-06-05: PR #210 — endpoint shipped with flat fields under `ios`.
- 2026-06-06: PR #213 — restructured into nested `latest` block. The
  first-shipped 1.13 iOS build (build 377) decoded the flat form only
  and silently treated the nested response as "no update available."
- 2026-06-06: PR #220 — additive flat aliases added back alongside the
  nested form as belt and suspenders. Wire shape now stable for both
  legacy and new decoders.
- 2026-06-06: this doc (PR #225), written to prevent another silent decode drift.
- 2026-06-07: PR #227 — `latest_build` added as an optional flat alias of
  `latest.build` (documented above).
- 2026-06-09: PR #233 — build bumped 447 → 450 (447 was an internal-only build,
  superseded before release). Examples here track the live value.

## Out of scope

- Authentication: this endpoint is intentionally unauthenticated.
- Localized banner copy: iOS owns the rendered string (Spanish, English,
  etc). The server only ships the version + URL.
- Hard gate UX: `min_supported_version` exists in the contract today
  but is not actually used in iOS until a hard-floor incident.
