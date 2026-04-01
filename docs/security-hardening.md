# Security Hardening Guide

> **Last updated:** April 1, 2026

Post-public-repo security measures for the GhostPour + ShoulderSurf + Context Quilt stack.

## Current Security Model

| Protection | Status | How It Works |
|-----------|--------|-------------|
| **Server-side tier enforcement** | Active | JWT contains only `user_id` — tier is always read from DB. No way to "upgrade yourself" by modifying requests. |
| **Apple Sign In bundle check** | Active | `/auth/apple` verifies identity tokens against Apple JWKS and checks bundle ID. A different app's tokens are rejected. |
| **API keys never leave server** | Active | Anthropic key stays in GhostPour's env vars. Clients never see upstream credentials. |
| **Server-side cost tracking** | Active | Every request deducts from allocation. Replaying requests still costs the attacker their own quota. |
| **JWT secret server-side** | Active | Attackers can't mint tokens. |
| **HTTPS enforced** | Active | NPM redirects HTTP → HTTPS via Let's Encrypt. |
| **SSL certificate pinning** | Active | SS iOS pins Let's Encrypt intermediate + ISRG Root X1 on all GhostPour calls. Blocks MITM proxy tools. |
| **CQ JWT auth** | Active | GhostPour authenticates to CQ with JWT bearer tokens (UUID app `930824d3`). Legacy `X-App-ID` string fallback is disabled. |
| **CQ enforce_auth** | Active | CQ requires JWT for all requests from GhostPour's registered app. X-App-ID-only access is rejected. |

## Hardening Measures (all deployed)

### 1. SSL Certificate Pinning (ShoulderSurf iOS) — DEPLOYED

SS iOS app (build 1.0.6+) pins the Let's Encrypt intermediate CA and ISRG Root X1 public keys on all GhostPour API calls. This prevents MITM proxy tools (Charles, Proxyman, mitmproxy) from intercepting traffic, even on jailbroken devices.

**Pinned services (SS side):**
- CloudZapAuthManager (auth/token refresh)
- CloudZapProvider (LLM queries)
- QuiltService (Context Quilt API)
- RemoteConfigManager (config sync)
- SubscriptionManager (receipt verification)
- TierCatalog (tier catalog fetch)

**NOT pinned (intentionally):**
- BYOK direct API calls (OpenAI, Anthropic, Google)
- LiteLLM pricing fetch (GitHub)
- Apple frameworks (StoreKit, CloudKit)

**SPKI Hashes (SHA-256, base64):**

| Certificate | Subject | Hash |
|------------|---------|------|
| Leaf (rotates every 90 days) | `CN=cz.shouldersurf.com` | `yAn+9RntePRrBk83oKSUhzd+brP6oYTCWqFYbIgnpGs=` |
| **Intermediate (pinned)** | `C=US, O=Let's Encrypt, CN=E8` | `iFvwVyJSxnQdyaUvUERIf+8qk7gRze3612JMwoO3zdU=` |
| **Root (pinned, backup)** | `C=US, O=ISRG, CN=ISRG Root X1` | `C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=` |

**Risks:**
- If Let's Encrypt retires BOTH E8 intermediate AND ISRG Root X1, pinning breaks. This would be a major internet event with years of notice.
- Debug builds should bypass pinning for development (disabled in release).

### 2. CQ JWT Auth + enforce_auth — DEPLOYED

GhostPour registered as a proper CQ app (UUID: `930824d3-2ccb-4869-b3f0-0ed2693f183f`) and authenticates with JWT bearer tokens. The legacy `X-App-ID: cloudzap` string ID is now blocked by CQ.

**How it works:**
1. GhostPour obtains a JWT from `POST /v1/auth/token` using its `app_id` (UUID) and `client_secret`
2. Tokens are cached and auto-refreshed 30 seconds before expiry
3. All CQ calls (recall, capture, proxy endpoints, graph) use `Authorization: Bearer {token}`
4. If token fetch fails, falls back to `X-App-ID` header (which CQ now rejects for enforced apps)

**Config:**
- `CZ_CQ_APP_ID` — UUID app identifier (not the legacy string)
- `CZ_CQ_CLIENT_SECRET` — client secret for token exchange (env var only, never in code)

### 3. Shorter JWT Lifetimes — RECOMMENDED

**Current:** 24 hours (`CZ_JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440`)

**Recommended:** 60 minutes. Reduces the window for a stolen token. SS already has refresh token rotation, so the UX impact is minimal.

**Change:** Update `CZ_JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60` in `.env.prod` on the GCP VM.

## What We're NOT Worried About

- **Someone reading the open-source code:** The security model is server-authoritative. Knowing how it works doesn't help bypass it.
- **API structure visible in traffic:** Every endpoint requires auth. Tier and allocation are server-side.
- **Someone building a competing product:** That's the point of open source.
