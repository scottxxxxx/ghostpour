# Security Hardening Guide

> **Created:** April 1, 2026

Post-public-repo security measures for the GhostPour + ShoulderSurf + Context Quilt stack.

## Current Security Model (already solid)

| Protection | How It Works |
|-----------|-------------|
| **Server-side tier enforcement** | JWT contains only `user_id` — tier is always read from DB. No way to "upgrade yourself" by modifying requests. |
| **Apple Sign In bundle check** | `/auth/apple` verifies identity tokens against Apple JWKS and checks `com.shouldersurf.ShoulderSurf` bundle ID. A different app's tokens are rejected. |
| **API keys never leave server** | Anthropic key stays in GhostPour's env vars. Clients never see upstream credentials. |
| **Server-side cost tracking** | Every request deducts from allocation. Replaying requests still costs the attacker their own quota. |
| **JWT secret server-side** | Attackers can't mint tokens. |
| **HTTPS enforced** | NPM redirects HTTP → HTTPS via Let's Encrypt. |

## Recommended Hardening

### 1. SSL Certificate Pinning (ShoulderSurf iOS)

**What:** Pin the Let's Encrypt intermediate CA public key in SS's `URLSession` configuration. This prevents MITM proxy tools (Charles, Proxyman, mitmproxy) from intercepting GhostPour API traffic, even on jailbroken devices.

**Why not pin the leaf cert:** Let's Encrypt certs auto-renew every 90 days. Pinning the leaf cert would break the app on renewal. Pinning the intermediate CA survives renewals.

**SPKI Hashes (SHA-256, base64):**

| Certificate | Subject | Hash |
|------------|---------|------|
| Leaf (rotates every 90 days) | `CN=cz.shouldersurf.com` | `yAn+9RntePRrBk83oKSUhzd+brP6oYTCWqFYbIgnpGs=` |
| **Intermediate (pin this)** | `C=US, O=Let's Encrypt, CN=E8` | `iFvwVyJSxnQdyaUvUERIf+8qk7gRze3612JMwoO3zdU=` |
| **Root (backup pin)** | `C=US, O=ISRG, CN=ISRG Root X1` | `C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=` |

**Implementation (SS team):**

Pin both the intermediate AND root for redundancy. If Let's Encrypt rotates their intermediate CA, the root pin keeps the app working.

```swift
// In your URLSession delegate or networking layer:
class PinningDelegate: NSObject, URLSessionDelegate {
    // Let's Encrypt E8 intermediate + ISRG Root X1
    private let pinnedHashes: Set<String> = [
        "iFvwVyJSxnQdyaUvUERIf+8qk7gRze3612JMwoO3zdU=",  // E8 intermediate
        "C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=",  // ISRG Root X1
    ]

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let serverTrust = challenge.protectionSpace.serverTrust else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }

        // Check each certificate in the chain
        let certCount = SecTrustGetCertificateCount(serverTrust)
        for i in 0..<certCount {
            guard let cert = SecTrustCopyCertificateChain(serverTrust)?[i] as! SecCertificate?,
                  let publicKey = SecCertificateCopyKey(cert) else { continue }

            var error: Unmanaged<CFError>?
            guard let publicKeyData = SecKeyCopyExternalRepresentation(publicKey, &error) as Data? else { continue }

            let hash = Data(SHA256.hash(data: publicKeyData)).base64EncodedString()
            if pinnedHashes.contains(hash) {
                completionHandler(.useCredential, URLCredential(trust: serverTrust))
                return
            }
        }

        // No pin matched — reject the connection
        completionHandler(.cancelAuthenticationChallenge, nil)
    }
}
```

**Applies to:** All GhostPour API calls (`cz.shouldersurf.com` and eventually `api.ghostpour.com`). Does NOT apply to Apple's servers, App Store, or other third-party URLs.

**GhostPour/Bifrost changes required:** None. This is purely an iOS-side check.

**Risks:**
- If Let's Encrypt retires BOTH the E8 intermediate AND ISRG Root X1, pinning breaks. This would be a major internet event with years of notice.
- During development, MITM debugging tools won't work with pinning enabled. Add a debug-only bypass (disabled in release builds).

### 2. Strict Auth on Context Quilt

**What:** Enable `enforce_auth: true` on the CQ app registration. This forces JWT validation on every CQ request, so knowing the `X-App-ID` string alone isn't enough.

**Why:** The `X-App-ID: cloudzap` header is a simple string that's visible in the public repo. With strict auth, CQ also validates the JWT, so an attacker can't call CQ directly even if they know the app ID.

**Action (CQ team):**
```bash
PATCH /v1/auth/apps/{app_uuid}
Body: {"enforce_auth": true}
```

**GhostPour impact:** None. GhostPour already sends JWTs to CQ via the `Authorization` header in proxy requests. This change just tells CQ to stop accepting the `X-App-ID` fallback.

### 3. Shorter JWT Lifetimes (optional)

**Current:** 24 hours (`CZ_JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440`)

**Recommended:** 60 minutes. Reduces the window for a stolen token. SS already has refresh token rotation, so the UX impact is minimal — the app silently refreshes.

**Change:** Update `CZ_JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60` in `.env.prod` on the GCP VM.

## What We're NOT Worried About

- **Someone reading the open-source code:** The security model is server-authoritative. Knowing how it works doesn't help bypass it.
- **API structure visible in traffic:** Every endpoint requires auth. Tier and allocation are server-side.
- **Someone building a competing product:** That's the point of open source.
