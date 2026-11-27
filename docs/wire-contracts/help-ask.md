# Help Ask: wire contract

`POST /v1/help/ask`. On phones without Apple Intelligence, GP writes the
answer to a question typed into Shoulder Surf's Help, using ONLY the Help
articles the app sends with it. GP defines the wire, Shoulder Surf emits it.

Last updated: 2026-11-27. Status: live for Shoulder Surf 1.20 (2702) in the articles shape. The `kb_hash` shape (GP holds the articles) is built on GP; no client sends it yet.

## Two request shapes

1. **`kb_hash` (2026-11-27, the one to use).** GP holds its own copy of the
   app's Help articles and finds the right ones itself. The app sends the
   question and the hash of the Help set bundled in the build, nothing else.
2. **`articles` (and `catalog`).** The app picks up to three articles by
   keyword and sends their text. Kept for builds that predate `kb_hash`, and
   as the re-send when GP does not have the build's Help set.

### The `kb_hash` shape

```json
{
  "question": "how do i see who owes something after a meeting",
  "kb_hash": "64930492bcf3449e380ee9d97c77362ceaf55bafa4865a1fa8384d4d4bdad480",
  "app_locale": "en",
  "device_id": "5B0C1D8E-3F2A-4E6B-9C7D-1A2B3C4D5E6F",
  "app_version": "1.21",
  "app_build": "2710",
  "support_email": "help@example.com"
}
```

`kb_hash` is 64 lowercase hex characters (anything else is a 400 naming
`kb_hash`). It is computed by the app at run time from its bundled
`HelpKnowledgeBase` folder, so it names exactly what is installed:

- the files are every `index.json` and every `*.md` under the folder, English
  at the root and one folder per other language. `whats-new.json` is not
  included;
- for each file, the line `<path relative to the folder, forward slashes>`,
  a tab, the lowercase hex SHA-256 of the file's bytes, a newline;
- the lines sorted by path in byte order, concatenated, SHA-256, lowercase hex.

One hash covers every language, since they ship together. `app_locale`
chooses the language GP reads: an exact folder (`pt-BR`), then the bare
language (`es` for `es-MX`, `pt-BR` for `pt`), then English.

What GP does with it:

| GP has that Help set | `articles` sent | Result |
|---|---|---|
| yes | either | answered from GP's copy. `articles`, `catalog` and `ui_terms` are ignored. The response has `"source": "kb"` and echoes `kb_hash` |
| no | yes | answered from the sent `articles`, exactly as the other shape |
| no | no | **409** `kb_unknown`, `details.kb_hash` echoes the value. Returned before any rate-limit slot or model call is spent. Re-send once in the `articles` shape |

Every id in `used_article_ids` and `related_article_ids` comes from the Help
set the build named, so every link opens. GP never answers a build from a
different Help set, and never from "the newest".

GP makes two model calls for this shape: one reads the titles and summaries
of every article and chooses up to five to read, the second writes the
answer from those articles in full. Expect about twice the latency of the
`articles` shape, so give it a longer timeout (10 seconds is safe).
`help_ask_events` keeps one row per question: `offered_article_ids` is what
GP chose, and tokens and cost are the two calls added.

**Importing a Help set.** GP keeps every imported set, in
`config/help-kb/<app_id>/<kb_hash>/`. Before a build that changes Help
reaches users, the app team sends GP the commit and the hash, and GP runs
`scripts/import_help_kb.py --repo <app repo> --commit <c> --expect <hash>`,
which refuses unless it computes the same hash, and merges the result. On first
use after each start GP re-hashes a folder and refuses to serve one
that no longer matches its name.

## Request (the `articles` shape)

No `Authorization` is needed, so it works signed out. If one is sent it is
not read at all: an expired token cannot fail the call and a valid one does
not attach the call to an account. Header `X-App-ID: shouldersurf`.

```json
{
  "question": "How do I delete my account?",
  "articles": [
    {"id": "acct-delete", "title": "Delete your account", "body": "Open Settings, tap Account..."},
    {"id": "acct-plans", "title": "Plans", "body": "..."}
  ],
  "app_locale": "pt-BR",
  "device_id": "5B0C1D8E-3F2A-4E6B-9C7D-1A2B3C4D5E6F",
  "app_version": "1.21",
  "app_build": "912",
  "support_email": "help@example.com",
  "ui_terms": {"Settings": "Ajustes"},
  "catalog": [
    {"id": "gs-mac-companion", "title": "Mac Companion", "summary": "A menu bar app that sends both sides of a computer call to the phone."}
  ]
}
```

| Field | Rule |
|---|---|
| `question` | required string, 1 to 500 characters, not blank |
| `articles` | required unless `kb_hash` is sent, 1 to 3 of `{id, title, body}`, in the client's rank order |
| `articles[].id` | slug, `^[a-z0-9][a-z0-9._-]{0,63}$` (the Help telemetry's ids), distinct within the request |
| `articles[].title` | required string, 1 to 200 characters |
| `articles[].body` | required string, 1 to 8000 characters |
| `app_locale` | required string, 1 to 35 characters. `pt-BR` or `pt_BR`. Used only as the fallback language (see below) |
| `device_id` | required UUID (any letter case) |
| `app_version` | optional string or null, 1 to 32 characters when sent |
| `app_build` | optional string or null, 1 to 16 characters when sent |
| `support_email` | optional string. Used only when it is an email address; anything else is ignored without an error |
| `ui_terms` | optional object, a name as the (English) articles write it mapped to the name this phone shows: `{"Settings": "Ajustes"}`. At most 80 pairs, each side a non-blank string of 1 to 60 characters with no line break. Send it only when the app language is not the articles' language, ideally only the names that appear in the articles of this request. A glossary that breaks a rule is ignored whole with a log line, never a 400. Where the answer names a key, the model writes the value; other names stay as the article writes them. Counts toward the 64 KB cap |
| `catalog` | optional (since 2026-11-26). Every Help article in the user's language as `{id, title, summary}`, 1 to 120 entries. `id`: the slug rule above, distinct within the catalog (an id may also be in `articles`). `title`: 1 to 200 characters. `summary`: 1 to 300 characters. A catalog that breaks a rule is a 400 that names the field (`catalog.14.summary`), not ignored. Absent or null: the request behaves as it did before |

Lengths are counted in Unicode code points (Swift: `unicodeScalars.count`,
not `count`). The whole body may be at most 64 KB (65536 bytes) as sent (32 KB before
the catalog), and that is a second limit beside the character counts: three
8000 character articles in Japanese or Chinese, where a character is three
bytes, are 72 KB on their own and do NOT fit. Trim to fit before sending.

Why the catalog exists: the client ranks articles by keyword, and a
paraphrased question can leave the right article out of its top three
(measured by SS: "can I use my computer to record a meeting" did not rank
the Mac Companion article in the top five). The catalog lets the model see
every article's summary. An article body still wins when one answers the
question. When none does and a summary states the answer, the answer says
only what the summary states, in one or two sentences with no steps, and
names that article in `used_article_ids` so the link carries the detail.

Unknown keys are accepted and ignored.

## 200

```json
{
  "answer": "1. Open Settings.\n2. Tap Account.\n3. Tap Delete Account.",
  "covered": true,
  "used_article_ids": ["acct-delete"],
  "related_article_ids": ["acct-export", "acct-plans"],
  "source": "articles",
  "output_language": null
}
```

| Field | Meaning |
|---|---|
| `answer` | plain text to show. At most about 120 words. It may hold a short numbered list (`1.` `2.`), newlines between items. No Markdown otherwise |
| `covered` | `true`: the articles answer the question. `false`: they do not, and `answer` says so in one or two sentences and points to support |
| `used_article_ids` | ids of the articles the answer was written from. Always ids this request carried: in `articles`, or, when a `catalog` was sent, in the catalog too (an answer written from a summary). Always `[]` when `covered` is `false` |
| `related_article_ids` | always present. 0 to 3 catalog ids worth reading next, in the model's order, never one that is in `used_article_ids`, never an id the request did not carry. May be non-empty when `covered` is `false` (nothing answers the question, but these are close). Always `[]` when no `catalog` was sent. Render the used ids first, then these |
| `source` | always `"articles"` |
| `output_language` | always `null`. The answer is in the language the question was typed in, which GP does not detect or direct |

Three cases to handle on the client:

- `covered: false`. When `support_email` was sent and is an address, the
  answer names it. Otherwise it says "contact support" with no address,
  link or phone number.
- `covered: true` with `used_article_ids: []`. Rare: the model said the
  articles answer the question but named no id from the request, and more
  than one article was sent, so GP does not guess. Show the answer without
  a source link. (With one article in the request, GP returns that id.)
- A reply GP could not read, or one that named an email address found
  neither in `support_email` nor in the articles. GP serves its own "not
  covered" line with `covered: false`, in the language of `app_locale`
  (en, es, fr, pt, ja; English otherwise), because it cannot tell the
  question's language itself.

The answer never contains an em dash, an en dash or a hyphen used as
punctuation: GP strips them after the model answers.

Language: the answer is in the language of the question. `app_locale`
decides only when the question is too short to tell (one word, a name, a
number). If the question asks for a language ("answer in Spanish"), that
language is used. When the articles are in another language than the
answer, button, screen, setting and plan names stay as the article writes
them.

## Errors

Every error is `{"detail": {"code", "message", "details"}}`. Shape failures
are a 400 in this envelope, never FastAPI's 422 list.

| Status | `code` | When | `details` |
|---|---|---|---|
| 400 | `invalid_request` | body is not a JSON object, a field is missing or the wrong type, a length is out of range, 0 or more than 3 articles, an id that is not a slug or repeats, a malformed `catalog`, a `device_id` that is not a UUID | `{"field": "articles.0.id", "reason": "..."}`. The value sent is never echoed |
| 413 | `body_too_large` | body over 65536 bytes | `{"check": "content_length" or "streamed_bytes", "limit_bytes": 65536, "bytes": N}` |
| 409 | `kb_unknown` | `kb_hash` names a Help set GP does not hold and no `articles` were sent. Nothing was spent and no limit was counted | `{"kb_hash": "<the value sent>"}` |
| 429 | `rate_limited` | a limit below is reached | `{"retry_after": seconds, "limit": "device_hour" or "device_day" or "ip_hour"}` |
| 503 | `help_ask_unavailable` | switched off, the day's spend cap is reached, the model call failed, or the model's reply could not be read or used (`model_error` for both; the call is still billed and recorded) | `{"reason": "disabled" or "daily_cap" or "model_error" or "no_price"}` |

```json
{"detail": {"code": "rate_limited",
            "message": "Too many Help questions; retry in 2114s",
            "details": {"retry_after": 2114, "limit": "device_hour"}}}
```

On a 503 show the articles themselves. `daily_cap` clears at 00:00 UTC.
There is no `Retry-After` header; the number is in the body.

## Limits and cost

Served, server only, in `config/remote/help-ask.json` (`/v1/config/help-ask`
answers 404):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | the switch. Ships off (503 `disabled`) and is turned on in the served doc once the first real answer has been read back. Anything that is not `true` or `false` reads as off |
| `provider`, `model` | `anthropic`, `claude-haiku-5-5` | the model that writes the answer. No code default: without them the feature is off |
| `max_output_tokens` | 800 | output cap (50 to 1000) |
| `per_device_per_hour` | 10 | per `device_id` |
| `per_device_per_day` | 30 | per `device_id`, a rolling 24 hours |
| `per_ip_per_hour` | 30 | per client IP (first `X-Forwarded-For` entry) |
| `daily_spend_cap_usd` | 5.0 | the whole feature's budget per UTC day (0 to 50) |

A request that fails validation takes no rate-limit slot, and a request one
limit refuses takes no slot in the others. The windows are in memory and
reset when GP restarts; the spend cap is in the database and does not.

The cap is checked before the model is called, in the same statement that
reserves the call's worst-case cost, so concurrent requests cannot each see
"under the cap". The day's real spend can pass the cap by at most one
call's worst case (under half a cent at the default model's prices).

Nothing here is charged to a user: no `usage_log` row, no allowance, no
per-user cost. The feature's spend is its own ledger.

## What GP stores

`help_ask_events`, one row per question that reached the model:

- the question, lowercased and whitespace-collapsed, run through the same
  guards as Help search text (`help-tour-telemetry.md`). A question that
  looks like an email address, a link or a phone number is still answered,
  and its row is kept WITHOUT the text
- `covered`, the article ids offered, the ids used, the outcome
- `app_locale`, `app_version`, `app_build`, `device_id`, the app id
- the model, token counts, cost in dollars, latency

No user id and no IP (the IP is hashed in memory for the rate limit and
never written). The answer is not stored. Rows are deleted after 180 days.
Because no row names an account, account deletion has nothing to remove
here.

The admin live log never holds the question or the answer for this path:
both are replaced with `<redacted>`, and article bodies with their length.

Read side: the "Help and learning" section of the dashboard's Telemetry
tab (`GET /webhooks/admin/telemetry/help-tour`, key `ask`).
