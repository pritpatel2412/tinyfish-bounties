# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0-timemachine] - 2026-11-04

### Added
- **Time Machine MVP**:
  - Full point-in-time web-surface reconstruction with `/as-of?url=...&at=...` API.
  - Multi-tiered noise-filtering engine: block hashing, heading path resolution, date/timestamp stripping, dynamic ad/banner exclusion.
  - LLM change classification with Groq (`llama-3.3-70b-versatile` / `llama-3.1-8b-instant`) with deterministic fallback.
  - Full screenshot capture pipeline using TinyFish Agent and Browser CDP with cloud archival to Supabase Storage bucket `shots`.
  - Ingestion pipeline via Supabase Postgres / Edge Function and background event processor worker with cold-start wake.
  - Public judge-facing `/export.json` endpoint with 5-minute cache.
  - Public submission endpoint `POST /suggest` for page candidate recommendations.
  - Admin endpoints protected by `X-Admin-Token` constant-time verification.
  - Public `/health` endpoint returning clean status, uptime, processor heartbeat age, and version.
  - Admin-only `/admin/health` endpoint returning wallet balance, daily spend, monitor quotas, and ledger metrics.
- **TinyCore Foundation**:
  - Resilient `TF` client supporting TinyFish Fetch, Monitor, Agent, and Browser endpoints with rate-limiting, retries, and quota tracking.
  - Transparent call logging in `data/tf_calls.jsonl` and `CostLedger`.
  - Dual storage backend abstraction (`SqliteStore` for local development, `SupabaseStore` for cloud deployment).
  - Deterministic block normalizer and semantic block differ.
- **Fleet & Infrastructure**:
  - Active monitoring fleet of 28 vetted high-value documentation and release pages.
  - Production deployment configuration (`render.yaml`, Dockerfile, dynamic `$PORT` binding, Render region `singapore`).
  - Automated watchdog worker monitoring wallet balance, heartbeat lag, and cost ceilings.
  - Controlled pipeline verification test suite and real-world change isolation.
- **Developer Experience**:
  - Gitleaks secret scanner configuration (`.gitleaks.toml`).
  - 100% offline passing test suite (55 unit and integration tests).

### In Progress
- **TinyVerify**: Deterministic cross-source verification engine (`apps/tinyverify/`).
- **Site to MCP**: Zero-code autonomous MCP server generator (`apps/site2mcp/`).
