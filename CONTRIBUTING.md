# Contributing to TinyFish Bounties

Thank you for your interest in contributing to the TinyFish Bounties repository! This monorepo hosts three autonomous web-intelligence services powered by TinyFish and Groq:
- **Time Machine** (Live MVP): Continuous web-surface intelligence and as-of change auditor.
- **TinyVerify** (In progress): Deterministic cross-source verification engine.
- **Site to MCP** (In progress): Zero-code autonomous MCP server generator.

---

## Architecture Principles

1. **Deterministic Logic in Python**: Parsing, hashing, differential analysis, noise filtering, and state machines are 100% deterministic code.
2. **LLMs for Classification Only**: Groq/Llama-3 models classify or extract structured entities only. Every output is strictly schema-validated via Pydantic or fallback rules.
3. **Unified Client Boundary**: All TinyFish API interactions must pass through `tinycore/tf_client.py` and be recorded in `data/tf_calls.jsonl` and `CostLedger`.
4. **Source Attribution & Timestamps**: Every piece of data stores its `source_url` and ISO 8601 UTC timestamp.
5. **Zero Secrets in Code**: Secrets live strictly in `.env` (development) or environment variables (production).

---

## Local Setup

### 1. Prerequisites
- Python 3.11 or 3.12
- Git

### 2. Environment Setup
```bash
git clone https://github.com/pritpatel2412/tinyfish-bounties.git
cd tinyfish-bounties
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

### 3. Running Tests
We maintain 100% passing tests for offline and mock suites:
```bash
# Run unit and integration tests (offline / mocked)
pytest -m "not live"

# Run with verbose output
pytest -v -m "not live"
```

Live tests against the real TinyFish API consume credits and are marked with `@pytest.mark.live`:
```bash
pytest -m "live"
```

---

## Code Quality & Conventions

- **Formatting & Linting**: We use [Ruff](https://github.com/astral-sh/ruff) for linting and code formatting.
  ```bash
  ruff check .
  ruff format --check .
  ```
- **Commit Messages**: We follow Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `ci:`, `test:`).
- **Type Hints**: Type annotations are encouraged for public interfaces and data models.
