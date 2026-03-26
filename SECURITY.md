# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in GhostPour, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email: **security@cloudzap.com**

Or use [GitHub's private vulnerability reporting](https://github.com/scottxxxxx/cloudzap/security/advisories/new).

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Response Timeline

- **Acknowledgment**: Within 48 hours
- **Assessment**: Within 1 week
- **Fix**: Depends on severity, but we aim for critical fixes within 72 hours

## Scope

The following are in scope:
- Authentication bypass
- Authorization flaws (tier enforcement bypass)
- API key exposure
- Injection vulnerabilities
- Rate limit bypass

The following are out of scope:
- Denial of service (we're a small project)
- Issues in upstream LLM provider APIs
- Social engineering

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Best Practices for Deployers

- Use a strong `CZ_JWT_SECRET` (minimum 32 characters, randomly generated)
- Keep `CZ_ADMIN_KEY` secret and rotate periodically
- Never commit `.env` or `.env.prod` files
- Keep provider API keys in environment variables, not config files
- Restrict NPM admin panel (port 81) access to trusted IPs
