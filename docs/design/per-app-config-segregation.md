# Per App Config Segregation

Status: Draft proposal, revised v2 (post Phase 0 audit)
Author: (you)
Date: 2026-06-14

## Summary

We serve remote config to several iOS apps, and app identity is handled two
different ways today. Server consumed config (`model-routing.json`) keys apps
internally and resolves them from the `X-App-ID` header. Client fetched config
(`llm-providers`, `model-capabilities`, `idle-tips`, `protected-prompts`,
`jd-analysis`) instead carries app identity as a filename prefix (`tr-` for Tech
Rehearsal), and the config endpoint ignores `X-App-ID` entirely. This proposal
converges both onto the identity signal we already have, `X-App-ID`, and
recommends a single physical layout per consumption mode: per app directories
for client delivered config, and internal app keys (the existing pattern) for
server side routing tables. Language stays an orthogonal `.{code}` suffix.

## Why now

The split bit us three times in one week. The `tr-` prefix was read as Turkish
by an engineer and twice by the ShoulderSurf team, nearly leading to Tech
Rehearsal's provider config being renamed into a ShoulderSurf Japanese file and
its interview prep prompt being dismissed as a stale "transcript" config. The
scheme also gets visibly worse as apps add languages (`tr-llm-providers.es.json`
reads as "Turkish providers, Spanish"). Tech Rehearsal is a live upcoming app and
the README names a third, Interview Buddy, so this has to scale past two and stop
being ambiguous.

## Goals

- App identity is resolved one way everywhere, from `X-App-ID`.
- Client delivered config is isolated per app: its own version, its own file, its
  own edit and sync blast radius.
- Language stays an orthogonal suffix.
- Adding an app or a language is a pure addition. No renames, no collisions.
- Migration is backward compatible. No shipped build breaks.

## Non goals (for v1)

- Translating English placeholder content that some locale files carry. Separate
  content work.
- Forcing one physical layout on server side routing tables that are legitimately
  better as a single consolidated file.
- The explicit `.en` decision (make English a real suffix). Related, separable,
  recommended, called out below.

## How it works today (grounded by the Phase 0 audit)

- `GET /v1/config/{name}` plus an `Accept-Language` header (`app/routers/config.py`)
  builds `{name}.{locale}`, falling back to the base `{name}`. Locale is the bare
  two letter code; `en` resolves to the base file. The endpoint does not read
  `X-App-ID`.
- App identity already exists as `X-App-ID`. `app/middleware/request_logging.py`
  reads it into `request.state.app_id`; `app/routers/chat.py` routes model
  selection through `model-routing.json` `apps.<app_id>`. Values seen:
  `shouldersurf`, `techrehearsal`. (`X-App-Bundle-Id` is a separate header used
  only by `/v1/app/version`.)
- SS confirmed (2026-06-15 iOS review): the client sends `X-App-ID: shouldersurf` on
  the chat stream today but NOT on `/v1/config`, `/v1/tiers`, or `/v1/events/ping`,
  which carry only `Accept-Language`, `X-Config-Version`, and auth. Adding it to config
  is a one-line change in their request builder. SS offered to add it to all three
  endpoints together (it is cheap) and leans universal, so we take that: `X-App-ID`
  becomes the universal app-identity signal. This is a confirmed, agreed client change,
  not an open unknown.
- `model-routing.json` keys apps internally (`apps.shouldersurf`,
  `apps.techrehearsal`) in one file. This is server consumed.
- The client fetched configs key apps by filename prefix (`tr-`) in separate
  files, and are differentiated only by the client asking for a different name.
- Files live in `config/remote/*.json`, seed into a persistent directory
  (`data/remote-config/`, seed only if missing so dashboard edits win), and
  `load_remote_configs` globs `*.json` with the filename stem as slug. The
  overlay machinery (hydrate, drift, `sync-from-bundle`) runs per file by slug.

### Phase 0 audit findings

All five `tr-` files were created in one commit, #36 (2026-04-18), "Add multi-app
support: Tech Rehearsal configs + multi-bundle-ID auth." Every one is Tech
Rehearsal. None is a locale, none is a transcript.

| File | Verdict | Content state | Consumed |
|------|---------|---------------|----------|
| `tr-jd-analysis.json` | Tech Rehearsal, TR specific | Interview prep JD parser. No ShoulderSurf equivalent. | Server side (`prompt_assembly.py`, call type `tr_parse_jd`). |
| `tr-llm-providers.json` | Tech Rehearsal | Mirrors ShoulderSurf's lineup exactly today. | Client fetched. |
| `tr-model-capabilities.json` | Tech Rehearsal | Mirrors ShoulderSurf. | Client fetched. |
| `tr-idle-tips.json` | Tech Rehearsal namespace | Stale ShoulderSurf placeholder (meetings, AirPods), not interview copy. | Client fetched. |
| `tr-protected-prompts.json` | Tech Rehearsal | Partially customized, differs from SS. | Server side. |

All are safe to migrate as Tech Rehearsal. Two content follow ups, separate from
this work: `tr-idle-tips` needs real TR copy, and the `tr-llm-providers` /
`tr-model-capabilities` forced lockstep with ShoulderSurf should be dropped so
Tech Rehearsal can diverge (that lockstep is what caused the Qwen mis edit).

## The core decision: internal app keys vs per app directories

Both patterns already exist in the tree, so this is a real choice. The deciding
factors are version independence, blast radius, and how much of the existing
serving and overlay machinery has to change.

| Dimension | Internal app keys (one file, apps nested inside) | Per app directories (one file per app) |
|-----------|--------------------------------------------------|----------------------------------------|
| Version and download skip | One `version` per file, so any app's change bumps it and every app's client re-downloads, even if its slice is identical. Breaks the `X-Config-Version` skip per app. | Each app's file has its own version. Unchanged apps skip the download. Protocol preserved. |
| Payload | Client pulls every app's data and picks its slice, or the endpoint slices server side (extra logic, served payload differs from stored file). | Client gets only its app's file. |
| Blast radius and overlay safety | Many apps share one file, so a dashboard edit or `sync-from-bundle` for one app can clobber another app's slice. We have been bitten by overlay clobber before. | One app per file. Edits and sync are isolated to that app. |
| Change vs today's machinery | Rework version, hydrate, drift, and `sync-from-bundle` to be app slice aware within a file. | Small extension: resolve `{app}/{name}.{locale}`; the per file version and overlay model is unchanged. |
| Language axis | Awkward. App internal plus language suffix means every language file carries all apps, or both go internal and you get deep nesting that breaks the existing `Accept-Language` suffix resolution. | Clean. App is the directory, language is the suffix. Fully orthogonal. |
| Scaling to 3+ apps | Files grow wide and the coupling worsens. | Linear and isolated. |

### Recommendation

Use **per app directories for client delivered config**, resolved from
`X-App-ID`, with language as the suffix. The decisive reasons are version
independence (the download skip protocol only works per app if each app owns its
version) and blast radius (one app per file means a sync or dashboard edit can
never clobber another app), and it is the smaller change to the serving and
overlay code.

Keep **internal app keys for server side routing tables**, specifically
`model-routing.json`. There the whole point is one table the server reads across
apps and tiers, it is not client version fetched with a per app download skip, and
it is small and rarely edited. Forcing it into directories would add files without
buying the isolation that matters for client deliverables. So the unifying rule is
not "one physical layout," it is "resolve the app from `X-App-ID` everywhere, keep
client deliverables isolated per app, and let server side tables stay
consolidated."

## Proposed design

### Layout

```
config/remote/
  shouldersurf/
    llm-providers.json          # base (en today; or .en if we adopt explicit en)
    llm-providers.es.json
    llm-providers.ja.json
    model-capabilities.json
    idle-tips.json
    protected-prompts.json
    ...
  techrehearsal/
    llm-providers.json
    model-capabilities.json
    idle-tips.json
    protected-prompts.json
    jd-analysis.json            # TR specific, was tr-jd-analysis
  model-routing.json            # stays a single file, internal app keys
```

App is the directory, language is the `.{code}` suffix inside it. The `tr-` prefix
disappears. `model-routing.json` stays where it is, keyed internally.

### App resolution

- The config endpoint reads `X-App-ID` (the same value `chat.py` already uses),
  maps it to a directory through a small registry, then resolves
  `{app}/{name}.{locale}`, falling back to `{app}/{name}`.
- A single `apps` registry maps the app id to its directory and is the one source
  of truth for which apps exist. `model-routing.json` and the config endpoint both
  read it.
- Missing `X-App-ID`: resolve to `shouldersurf`, permanently. ShoulderSurf is the
  default app, so a header-less request is always correctly ShoulderSurf, and this
  protects every old build forever with no coordination. This is NOT a migration
  crutch we remove later. (iOS review 2026-06-15 caught that 404-ing a missing header
  would silently freeze old ShoulderSurf builds on their bundled config.)
- Present but UNKNOWN `X-App-ID` (a typo or unregistered app): return 404, matching
  how `/v1/app/version` treats an unknown app. A present-but-unknown id is a real
  error; a missing header is not.

### Language

Unchanged. Language is the `.{code}` suffix; English is the base file today.
Related separable decision: make English explicit as `.en` so every file declares
its language and a new app starts multilingual instead of with an ambiguous base.
That is a one line negotiation change (map `Accept-Language: en` to the `.en`
file). If we do it, keep a base or default-locale fallback so a missing `.en` never
404s English users (iOS review note). Recommended, can land on its own.

## Migration plan (phased, backward compatible)

Phase 0, done: audit the `tr-` files. All five are Tech Rehearsal. Safe to move.

Phase 1: the config endpoint reads `X-App-ID` and resolves `{app}/{name}.{locale}`,
but falls back to the current flat and `tr-` names when a per app file does not
exist yet. `load_remote_configs` walks subdirectories, slug becomes app plus name,
and the overlay, drift, and `sync-from-bundle` machinery becomes app aware. Nothing
breaks because the old names still resolve.

Phase 2: move files. ShoulderSurf flat files move under `shouldersurf/`, names
unchanged. The `tr-` files move under `techrehearsal/`, dropping the prefix, and
`tr-jd-analysis` becomes `techrehearsal/jd-analysis.json`. Update the call type to
slug map in `prompt_assembly.py` accordingly. Update tests to per app paths and add
Tech Rehearsal coverage as its own set rather than a fake ShoulderSurf locale. Drop
the forced lockstep between `tr-llm-providers` and ShoulderSurf. Migrate the prod
persistent directory into per app subdirectories, preserving dashboard edits, one
file at a time, verifying each file in the destination before removing the source.

Phase 3: tighten resolution once clients send the header. Keep "missing `X-App-ID`
means shouldersurf" permanently (see App resolution), so old ShoulderSurf builds need
no coordination ever. The 404 only ever applies to a present-but-unknown app id. The
app genuinely at risk in this migration is Tech Rehearsal, not ShoulderSurf: a
header-less Tech Rehearsal build resolves to shouldersurf and would get the wrong
config, so the TR build must ship `X-App-ID: techrehearsal` BEFORE its config moves
under `techrehearsal/`, and TR's header-less requests are the dangerous ones.
ShoulderSurf is safe because header-less resolving to shouldersurf is correct for it.
Sequence the TR rollout deliberately; ShoulderSurf needs none. (iOS review 2026-06-15
confirmed the ShoulderSurf one-liner and flagged this TR sequencing.)

## Work items

- `app/routers/config.py`: read `X-App-ID`, subdirectory walk, resolution order.
- Overlay, hydrate, drift, `sync-from-bundle`: app aware slugs.
- `app/services/prompt_assembly.py`: update the call type to slug mapping for the
  moved `jd-analysis`.
- A single `apps` registry (id, directory, label) read by both config resolution
  and model routing.
- Config admin dashboard: add the app dimension.
- Tests: per app file lists, Tech Rehearsal parity as its own set, drop the SS
  lockstep over `tr-`.
- Prod persistent directory migration script (careful, verify before delete).
- Docs: a config conventions page (app is directory, language is suffix,
  `X-App-ID` resolves the app).
- ShoulderSurf: CONFIRMED 2026-06-15 — `X-App-ID` is sent on chat requests but not
  on `/v1/config` today; SS agreed to add `X-App-ID: shouldersurf` to config requests
  when we move. Needs scheduling alongside Phase 1/3, not a new ask.

## Risks and mitigations

- ShoulderSurf old builds: no risk. With "missing `X-App-ID` means shouldersurf" kept
  permanent, header-less requests always resolve correctly to ShoulderSurf, so no
  coordination is needed for SS at all. Their header addition is a one-liner shipped
  whenever convenient.
- Tech Rehearsal sequencing: this is the real risk. A header-less TR build resolves to
  shouldersurf and gets the wrong config. Mitigation: TR must ship
  `X-App-ID: techrehearsal` before its config moves under `techrehearsal/`; do not move
  TR's files until that build is the effective floor.
- Dashboard edits in the prod persistent directory. Mitigation: snapshot first,
  verify in destination before deleting source, one file at a time.
- The forced lockstep removal could let Tech Rehearsal silently drift on fields
  that should match. Mitigation: keep per app schema tests so each app is internally
  valid, just not a clone of ShoulderSurf.

## Open decisions

- X-App-ID scope: RESOLVED to universal. iOS confirmed it is chat-only today and
  offered to add it to `/v1/config` (this work), `/v1/tiers`, and `/v1/events/ping`
  together since it is cheap; they lean universal and so do we.
- Missing-header behavior: RESOLVED — missing `X-App-ID` resolves to `shouldersurf`
  permanently; 404 only on a present-but-unknown id (iOS review).
- Adopt explicit `.en`? Recommended, separable. Keep a base/default-locale fallback.
- Migrate `model-routing.json` to per app files too, or keep it internal keyed?
  Recommendation: keep it internal keyed. It is the right tool for a server side
  routing table.
- Registry location: a dedicated `apps.yml`, or extend an existing file.
  Recommendation: a dedicated `apps.yml` as the single source of truth for apps.

## Recommendation

Resolve the app from `X-App-ID` everywhere. Put client delivered config in per app
directories with language as the suffix, because that preserves per app version
skip and isolates blast radius for the small price of more files. Keep
`model-routing.json` internal keyed as a server side table. Migrate in the phased,
backward compatible order above, starting from the completed Phase 0 audit. Treat
explicit `.en` and the registry location as separable follow ups.
