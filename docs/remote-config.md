# Remote Config (iOS App)

> **Last updated:** March 29, 2026

GhostPour serves JSON config files to the the client iOS app via `GET /v1/config/{name}`. This allows updating prompts, model lists, and capabilities without App Store releases.

## How it works

1. JSON files live in `config/remote/{slug}.json`, each with a top-level `"version"` integer
2. All configs are loaded at startup into `app.state.remote_configs`
3. iOS app calls `GET /v1/config/{slug}` on every launch
4. If client sends `X-Config-Version: N` and server version matches, returns `200` with `{"changed": false, "version": N}`
5. Otherwise returns `200` with the full JSON payload and `X-Config-Version` response header
6. Unknown slugs return `404`

> **Note:** We use 200 with `{"changed": false}` instead of HTTP 304 because Nginx Proxy Manager mangles bare 304 responses (no cached body to serve) into 404s for downstream clients.

## Available configs

| Slug | File | Purpose |
|------|------|---------|
| `idle-tips` | `config/remote/idle-tips.json` | Orb idle tip messages |
| `protected-prompts` | `config/remote/protected-prompts.json` | System prompts, summary prompts, default prompt modes |
| `llm-providers` | `config/remote/llm-providers.json` | Provider endpoints and model lists |
| `model-capabilities` | `config/remote/model-capabilities.json` | Per-model context slots, token limits, CQ readiness |

## To update a config

Edit the JSON in `config/remote/`, bump the `version` integer, and redeploy. The iOS app picks up changes on next launch.

## Localization

The config endpoint supports localized variants via the `Accept-Language` header.

**How it works:**
1. Client sends `Accept-Language: es` (or `es-MX`, `es-MX,en;q=0.5`, etc.)
2. Server extracts the primary language code (e.g., `es`)
3. Looks for `{slug}.{lang}.json` first (e.g., `protected-prompts.es.json`)
4. Falls back to `{slug}.json` (English default) if no localized version exists
5. Response includes `X-Config-Locale` header indicating which locale was served

**Example:** `GET /v1/config/protected-prompts` with `Accept-Language: es` returns `protected-prompts.es.json` if it exists, otherwise `protected-prompts.json`.

English (`en`) is the default — no `.en.json` suffix needed.

**Current localized configs:**

| Base Config | Locales Available |
|------------|------------------|
| `protected-prompts` | `es` (Spanish) |

## Multi-app convention

For deployments serving multiple client apps, prefix config slugs with the app name to avoid collisions:
- `myapp-prompts` instead of `prompts`
- `myapp-providers` instead of `providers`

This is a naming convention, not enforced by code. Single-app deployments can use unprefixed slugs.

## To add a new config

Drop a `.json` file with a `"version"` field into `config/remote/` and restart. The slug is the filename without `.json`.

## To add a localized variant

Create `{slug}.{lang}.json` in `config/remote/` (e.g., `protected-prompts.fr.json`). It must have its own `"version"` field. The version is tracked independently from the base config.
