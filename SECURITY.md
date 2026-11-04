# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

---

## Secret Handling & Hygiene

- No API keys, credentials, tokens, or private endpoints may be committed to this repository.
- Commit history is monitored with automated secret scanners (`gitleaks`, custom regex scanners).
- All requests modifying monitor configurations, adding pages, or reading administrative telemetry require an `X-Admin-Token` header.
- Webhook endpoints validate incoming signatures via query parameters (`?secret=...`).
- Public endpoints (`/health`, `/pages`, `/changes`, `/logs`, `/export.json`) are strictly read-only and redact sensitive configuration and wallet balances.

---

## Reporting a Vulnerability

If you discover a security vulnerability within this repository, please do **not** open a public issue.

Instead, please send an email to **pritptl2412@gmail.com** with:
1. A description of the vulnerability.
2. Steps to reproduce the issue or proof-of-concept.
3. Impact assessment.

We take all disclosures seriously and will respond within 48 hours.
