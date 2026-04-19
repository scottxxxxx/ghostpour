# Remote Config (iOS App)

> **Last updated:** April 1, 2026

GhostPour serves JSON config files to the the client iOS app via `GET /v1/config/{name}`. This allows updating prompts, model lists, and capabilities without App Store releases.

## How it works

1. Baseline JSON files live in `config/remote/{slug}.json`, each with a top-level `"version"` integer
2. On startup, `seed_remote_configs()` copies any missing files from `config/remote/` into the persistent directory at `data/remote-config/`. Existing files (e.g., dashboard edits) are not overwritten.
3. All configs are loaded from `data/remote-config/` into `app.state.remote_configs`
4. iOS app calls `GET /v1/config/{slug}` on every launch
5. If client sends `X-Config-Version: N` and server version matches, returns `200` with `{"changed": false, "version": N}`
6. Otherwise returns `200` with the full JSON payload and `X-Config-Version` response header
7. Unknown slugs return `404`

> **Note:** We use 200 with `{"changed": false}` instead of HTTP 304 because Nginx Proxy Manager mangles bare 304 responses (no cached body to serve) into 404s for downstream clients.

## Available configs

| Slug | File | Purpose |
|------|------|---------|
| `idle-tips` | `config/remote/idle-tips.json` | Orb idle tip messages |
| `protected-prompts` | `config/remote/protected-prompts.json` | System prompts, summary prompts, default prompt modes |
| `llm-providers` | `config/remote/llm-providers.json` | Provider endpoints and model lists |
| `model-capabilities` | `config/remote/model-capabilities.json` | Per-model context slots, token limits, CQ readiness |

## Config persistence

Configs live in two places:

| Location | Purpose |
|----------|---------|
| `config/remote/` | Baked-in baseline (checked into git, shipped with Docker image) |
| `data/remote-config/` | Persistent runtime copy (inside the mounted `ghostpour-data` volume) |

On startup, any files in `config/remote/` that don't exist in `data/remote-config/` are copied over. This means:
- **New configs added via git** appear on the next deploy automatically
- **Dashboard edits** are preserved across restarts (they live in the volume)
- **Dashboard edits take precedence** over baked-in versions for the same file

## To update a config

**Via admin dashboard:** Edit in the Configs tab and click Save. Changes take effect immediately and persist across restarts.

**Via code:** Edit the JSON in `config/remote/`, bump the `version` integer, and redeploy. Note: if the file already exists in `data/remote-config/` (e.g., from a previous dashboard edit), the baked-in version won't overwrite it. Delete the runtime copy via the server or clear the volume to force a re-seed.

## Localization

The config endpoint supports localized variants via the `Accept-Language` header.

**How it works:**
1. Client sends `Accept-Language: es` (or `es-MX`, `es-MX,en;q=0.5`, etc.)
2. Server extracts the primary language code (e.g., `es`)
3. Looks for `{slug}.{lang}.json` first (e.g., `protected-prompts.es.json`)
4. Falls back to `{slug}.json` (English default) if no localized version exists
5. Response includes `X-Config-Locale` and `X-Config-Resolved` headers indicating which locale and config file was served

**Example:** `GET /v1/config/protected-prompts` with `Accept-Language: es` returns `protected-prompts.es.json` if it exists, otherwise `protected-prompts.json`.

English (`en`) is the default — no `.en.json` suffix needed.

**Debugging:** Every config request logs the `Accept-Language` header, parsed locale, resolved config name, and available configs at INFO level.

**Current localized configs:**

| Base Config | Locales Available |
|------------|------------------|
| `idle-tips` | `es` (Spanish) |
| `protected-prompts` | `es` (Spanish) |
| `llm-providers` | `es` (Spanish) |
| `model-capabilities` | `es` (Spanish) |

## Multi-app convention

GhostPour serves multiple iOS apps from a single instance. Each app uses a **prefix** on its config slugs to avoid collisions. The prefix is the app's short name followed by a hyphen.

### Slug naming

| App | Prefix | Example slug | URL |
|-----|--------|-------------|-----|
| Shoulder Surf | *(none — legacy, unprefixed)* | `idle-tips` | `GET /v1/config/idle-tips` |
| Tech Rehearsal | `tr-` | `tr-idle-tips` | `GET /v1/config/tr-idle-tips` |

### Required header

Every request from a client app must include:
```
X-App-ID: shouldersurf    (or)
X-App-ID: techrehearsal
```
This header is used for usage segmentation, model routing, and per-app analytics in the admin dashboard.

### Per-app config registry

**Shoulder Surf** (unprefixed slugs):

| Slug | Purpose |
|------|---------|
| `idle-tips` | Rotating tip bubbles shown under the idle orb |
| `protected-prompts` | Server-locked system prompts, default prompt modes, summary/analysis templates |
| `llm-providers` | Provider endpoints, model lists, display names for Settings |
| `model-capabilities` | Per-model context-slot allowances (transcript tokens, images, doc tokens) |

**Tech Rehearsal** (`tr-` prefixed slugs):

| Slug | Purpose |
|------|---------|
| `tr-idle-tips` | Rotating tip bubbles |
| `tr-protected-prompts` | System prompts and prompt modes for interview scenarios |
| `tr-llm-providers` | Provider/model list for TR |
| `tr-model-capabilities` | Per-model context-slot allowances |
| `tr-jd-analysis` | JD analysis prompt: system prompt + user template with `{{job_description}}` placeholder |

### Localization

Config files support localization via a 2-letter language code suffix. The base file (no suffix) is **English**.

```
idle-tips.json        ← English (default)
idle-tips.es.json     ← Spanish
idle-tips.fr.json     ← French
```

**How it works:**
1. Client sends `Accept-Language: es` header (iOS sends this automatically based on device locale)
2. GP looks for `{slug}.es` → if found, serves it
3. If not found → falls back to `{slug}` (English)
4. Response includes `X-Config-Locale` and `X-Config-Resolved` headers

**No `.en.json` suffix exists.** English is the base file without any suffix.

**Current localized configs:**

| Base Config | Locales |
|------------|---------|
| `idle-tips` | `es` |
| `protected-prompts` | `es` |
| `llm-providers` | `es` |
| `model-capabilities` | `es` |
| `tr-*` configs | English only (no localized variants yet) |

To add a localized variant for any config, create `{slug}.{lang}.json` with its own `"version"` field. Or use the admin dashboard's `+ Lang` button.

### Version tracking

Each config file has a top-level `"version"` integer. The client sends `X-Config-Version: N` on each request. If the server version matches, GP returns `{"changed": false}` (cheap 200). If newer, GP returns the full payload.

Localized variants have their own independent version numbers — bumping `idle-tips.json` to v3 does not affect `idle-tips.es.json` which may still be at v1.

## To add a new config

Drop a `.json` file with a `"version"` field into `config/remote/` and redeploy. The slug is the filename without `.json`. On startup, it will be seeded into `data/remote-config/`.

Alternatively, use the admin dashboard's `+ Lang` button to create configs without a deploy.
