# Per App Config Segregation

Status: Draft proposal
Author: (you)
Date: 2026-06-14

## Summary

Today our remote config system flattens two unrelated dimensions, which app a
config belongs to and which language it is in, into a single filename. App
identity lives in the filename prefix (`tr-` for Tech Rehearsal, no prefix for
ShoulderSurf) and language lives in the suffix (`.es`, `.ja`). This proposal
moves app identity up to a real namespace, a per app directory keyed by bundle
id, and keeps language as the suffix. The two axes become orthogonal, so adding
an app or a language never collides or confuses.

## Why now

This bit us twice in one week. The `tr-` prefix was read as Turkish by an
engineer and then again by the ShoulderSurf team, which nearly led to Tech
Rehearsal's provider config being renamed into a ShoulderSurf Japanese file.
The flat scheme also gets visibly worse the moment a second app wants a second
language: `tr-llm-providers.es.json` reads as "Turkish providers, Spanish," and
nobody can tell at a glance whether `tr` is an app, a language, or something
else. Tech Rehearsal is a real upcoming second iOS app, so we want a clean multi
app story before its config surface grows.

## Goals

- App identity is a first class boundary, not a filename prefix.
- Language stays an orthogonal suffix (`.es`, `.ja`, and so on).
- Adding an app or a language is a pure addition, no renames, no collisions.
- Migration is backward compatible. No shipped ShoulderSurf build breaks.

## Non goals (for v1)

- Translating the English placeholder content that some locale files carry.
  That is separate content work.
- Changing the request shape beyond app identity. The app already sends
  `X-App-Bundle-Id` for the app version endpoint, so the identity signal exists.
- The explicit `.en` decision (make English a real suffix instead of the
  implicit base). Related and worth doing, but separable. Called out below.

## How it works today (grounded)

- The endpoint is `GET /v1/config/{name}` with an `Accept-Language` header
  (`app/routers/config.py`). It builds `{name}.{locale}`, falls back to the base
  `{name}` if no localized file exists. `Accept-Language` is parsed down to a
  bare two letter code, and `en` resolves to the base file (no `.en`).
- App identity is not used by the config endpoint at all. The only thing that
  separates apps today is that the client asks for a different name:
  ShoulderSurf asks for `llm-providers`, Tech Rehearsal asks for
  `tr-llm-providers`.
- Files live in `config/remote/*.json`. At startup they seed into a persistent
  directory (`data/remote-config/`), seed only if missing, so dashboard edits
  win and survive restarts. `load_remote_configs` globs `*.json` and uses the
  filename stem as the slug.
- The overlay machinery (hydrate new keys at boot, drift detection,
  `sync-from-bundle`) all operate per file by that slug.
- Precedent worth reusing: `app-versions.yml` is already keyed by bundle id
  (`com.shouldersurf.ShoulderSurf`) and resolved from the `X-App-Bundle-Id`
  header. Unknown bundle ids return 404 there.

## Proposed design

### Layout: a directory per app

```
config/remote/
  shouldersurf/
    llm-providers.json          # base (en today; or .en if we adopt explicit en)
    llm-providers.es.json
    llm-providers.ja.json
    model-capabilities.json
    tiers.json
    ...
  techrehearsal/
    llm-providers.json
    model-capabilities.json
    ...
  _shared/                       # optional, for configs identical across apps
    ...
```

App is the directory. Language is the `.{code}` suffix inside it. The `tr-`
prefix disappears entirely. Tech Rehearsal's files just live under
`techrehearsal/` with ordinary names.

### App resolution

- The endpoint reads `X-App-Bundle-Id` (already sent by the app for app
  versions).
- A small registry maps bundle id to an app namespace (directory):
  - `com.shouldersurf.ShoulderSurf` to `shouldersurf`
  - `com.techrehearsal.*` to `techrehearsal`
- Resolution order for a request:
  1. `{app}/{name}.{locale}`
  2. `{app}/{name}` (app specific base)
  3. optional `_shared/{name}.{locale}`
  4. optional `_shared/{name}`
- Unknown or missing bundle id: during migration, default to `shouldersurf`
  with a warning so nothing breaks while clients are updated. After migration,
  return 404, matching how the app version endpoint already treats an unknown
  bundle.

### Language

Unchanged. Language is the `.{code}` suffix. English is the base file today.

Related, separable decision: make English explicit as `.en` so every file
declares its language and a new app starts life multilingual instead of with an
ambiguous base. That needs a one line change to negotiation (map
`Accept-Language: en` to the `.en` file rather than the base). Recommended, but
it can land independently of this proposal.

## Migration plan (phased, backward compatible)

### Phase 0: disambiguate the `tr-` files

The `tr-` prefix is overloaded and must be audited file by file before anything
moves. At least one `tr-` file is not an app namespace: `tr-jd-analysis` is
referenced by `app/services/prompt_assembly.py` as the `tr_parse_jd` call type,
i.e. a transcript or prompt config, not Tech Rehearsal's. Classify every `tr-`
file as Tech Rehearsal app config versus other meaning. Do not move an ambiguous
file on assumption.

### Phase 1: endpoint understands both schemes

Teach the config endpoint to resolve the app from the bundle id into a
directory, but fall back to the current flat names when a per app file does not
exist yet. Update `load_remote_configs` to walk subdirectories, with the slug
becoming app plus name. Make the overlay, drift, and `sync-from-bundle`
machinery app aware. Nothing breaks because the flat names still resolve.

### Phase 2: move the files

- ShoulderSurf flat files move under `shouldersurf/`, names unchanged.
- Confirmed Tech Rehearsal files move under `techrehearsal/`, dropping the `tr-`
  prefix.
- Update tests (the `PROVIDER_FILES` and `CAPABILITY_FILES` lists, the
  protected prompts test) to the per app paths, and add Tech Rehearsal coverage
  as its own set rather than as a fake ShoulderSurf locale.
- Migrate the prod persistent config directory into per app subdirectories,
  preserving dashboard edits. This is a careful one time script: verify each
  file exists in the destination before removing the source, consistent with our
  migration safety practice, and never a single combined delete and move.

### Phase 3: make app identity required

Once we confirm the iOS client sends `X-App-Bundle-Id` on config requests (it
already does for app versions, so this is likely a confirmation, not an app
change), drop the flat name fallback and return 404 for an unknown bundle.

## Work items

- `config/routers/config.py`: app resolution, subdirectory walk, resolution
  order. Moderate.
- Overlay, hydrate, drift, `sync-from-bundle`: app aware slugs. Moderate.
- Config admin dashboard: add the app dimension to the editor. Depends on the
  current UI.
- Tests: per app file lists, plus Tech Rehearsal parity coverage. Small to
  moderate.
- Prod persistent directory migration script. Small but careful.
- Docs: a short config conventions page describing app as directory, language as
  suffix, and the bundle id registry.
- ShoulderSurf: confirm `X-App-Bundle-Id` is sent on `/v1/config` requests, not
  only on app version requests.

## Risks and mitigations

- Overloaded `tr-` prefix. Mis migrating `tr-jd-analysis` would break transcript
  parsing. Mitigation: the Phase 0 audit, and a rule not to move any ambiguous
  file.
- Dashboard edits in the prod persistent directory. A careless migration could
  wipe operator edits. Mitigation: verify in destination before deleting source,
  one file at a time, and snapshot the directory first.
- Client coordination. Phase 3 depends on the app sending the bundle id on
  config requests. Mitigation: keep the flat fallback until SS confirms, and the
  default to `shouldersurf` during migration means no break in the meantime.

## Open decisions

- Adopt explicit `.en`? Recommended, separable.
- A `_shared/` layer for configs identical across apps, or a full copy per app?
  Recommendation: start with a full copy per app for clarity, add `_shared/`
  only if duplication becomes painful.
- Where the bundle id to app map lives: extend `app-versions.yml`, or a single
  dedicated `apps.yml` registry that both app versions and config resolution
  read. Recommendation: one shared registry, so there is a single source of
  truth for "what apps exist."

## Phase 0 audit findings (2026-06-14)

All five `tr-` files were created in a single commit, #36 (2026-04-18), "Add
multi-app support: Tech Rehearsal configs + multi-bundle-ID auth." Every one is
Tech Rehearsal. None is a locale and none is a transcript or unrelated config.
The earlier "transcript" reading of `tr-jd-analysis` was wrong.

| File | Verdict | Content state | Consumed |
|------|---------|---------------|----------|
| `tr-jd-analysis.json` | Tech Rehearsal | TR specific. System prompt is an interview preparation analyst that parses a pasted job description. No ShoulderSurf equivalent exists. | Server side, via `prompt_assembly.py` call type `tr_parse_jd`. |
| `tr-llm-providers.json` | Tech Rehearsal | Currently mirrors ShoulderSurf exactly (same 10 providers, same model set). TR's own copy of the BYOK lineup. | Client fetched. |
| `tr-model-capabilities.json` | Tech Rehearsal | Mirrors ShoulderSurf. | Client fetched. |
| `tr-idle-tips.json` | Tech Rehearsal namespace | Content is a stale ShoulderSurf placeholder (tips about meetings, AirPods, speakers), not interview rehearsal. Needs real TR copy. | Client fetched. |
| `tr-protected-prompts.json` | Tech Rehearsal | Partially customized for TR, differs from the ShoulderSurf file. | Server side prompt assembly. |

Conclusion: all `tr-` files are safe to migrate under `techrehearsal/`. Two
content follow ups, both separate from the migration: `tr-idle-tips` needs real
Tech Rehearsal copy, and `tr-llm-providers` / `tr-model-capabilities` should be
allowed to diverge from ShoulderSurf rather than be kept in forced lockstep (the
old parity test treated them as a ShoulderSurf locale, which is what caused the
Qwen mis-edit).

## Correction the audit forced: app identity is already `X-App-ID`, not bundle id, and there are already two patterns

The audit found that multi app support already exists in two inconsistent shapes,
which changes the "App resolution" section above:

1. `model-routing.json` keys apps INTERNALLY: it has an `apps.shouldersurf` and
   `apps.techrehearsal` block. The middleware (`request_logging.py`) reads the
   `X-App-ID` header into `request.state.app_id`, and `chat.py` routes on it
   (`routing.get(app_id)`). So the server consumed config is already app
   segregated, by internal key, keyed off `X-App-ID` (values `shouldersurf`,
   `techrehearsal`).
2. The client fetched configs (`llm-providers`, `model-capabilities`,
   `idle-tips`, `protected-prompts`, and `jd-analysis`) segregate by filename
   prefix (`tr-`) instead, and the `/v1/config/{name}` endpoint does not read
   `X-App-ID` at all.

So the real task is to converge these two patterns, and to do it on the header
that already exists, `X-App-ID`, rather than introducing `X-App-Bundle-Id`. Note
also that the README and CHANGELOG name three apps, Shoulder Surf, Tech
Rehearsal, and Interview Buddy, so this must scale past two.

Revised resolution recommendation: the config endpoint reads `X-App-ID`
(the same value `chat.py` already uses), maps it to a directory, and resolves
`{app}/{name}.{locale}`. This reuses the existing identity signal end to end
instead of adding a parallel one. Whether per app directories or internal app
keys is the better physical layout is now an open decision, because
`model-routing.json` already demonstrates the internal key approach working.

## Recommendation

Adopt per app directories keyed by bundle id, keep language as the `.{code}`
suffix, and migrate in the phased, backward compatible way above, starting with
the Phase 0 disambiguation audit. Treat explicit `.en` and the `_shared/` layer
as separable follow ups.
