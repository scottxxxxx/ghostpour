# Deployment

> **Last updated:** April 1, 2026

## Infrastructure

- **GCP VM**: GCP Compute Engine (e2-medium, ~$25/mo)
- **Container**: `ghostpour` on `proxy-tier` Docker network
- **Routing**: Nginx Proxy Manager routes `api.example.com` → `ghostpour:8000`
- **CI/CD**: Push to `main` → GitHub Actions builds image → pushes to GHCR → SSH deploys
- **Data**: SQLite DB persisted in `ghostpour-data` Docker volume at `/app/data/`
- **Server config**: `/opt/ghostpour/.env.prod` + `/opt/ghostpour/docker-compose.prod.yml`

## Manual deploy

```bash
ssh into GCP VM
docker login ghcr.io
docker compose pull && up -d --force-recreate
```

## Admin Dashboard

Web UI at `/admin` with tabs:
- **Overview**: Today's stats, period summary, user counts by tier, allocation alerts (users >80%), trial funnel, cache savings, daily trend chart
- **Models**: Usage by provider/model (requests, tokens, cost, latency)
- **Users**: All users with tier badges, lifetime stats, inline set-tier dropdown, drill-down detail
- **Tiers**: Tier config cards with simulate button, per-feature state toggles (enabled/teaser/disabled)
- **Latency**: Response time percentiles (p50/p75/p90/p95/p99)
- **Providers**: API key management, credit balance checks
- **Errors**: Error summary by status/provider, recent error log table

Admin key: stored in `CZ_ADMIN_KEY` env var, persisted in browser localStorage.
