# TinyVerify — Deterministic Cross-Source Verification Engine

> 🚧 **Status: In Progress** (Architecture & Design Phase)

TinyVerify is a deterministic cross-source verification engine designed to validate facts, business registry data, and company credentials across independent authoritative web sources.

---

## The Problem
Autonomous agents and data pipelines frequently encounter stale, conflicting, or hallucinated claims about organizations, licenses, and executives. Traditional web scraping yields noisy unstructured text that is difficult to cross-verify without manual human audit.

## Planned Architecture

```
[Target Claim / Entity]
          │
    ┌─────┴─────┐
    ▼           ▼
[Primary]   [Secondary]  ──► TinyFish Fetch / Agent
 Source      Registry
    │           │
    ▼           ▼
[Deterministic Block Normalization & Key Extraction]
          │
          ▼
[Conflict Detection & Semantic Cross-Verification] (Groq Llama 3)
          │
          ▼
[Signed Verification Verdict + Immutable Evidence Snapshot]
```

## TinyFish Role in TinyVerify
- **Fetch**: Free retrieval of primary source registries and public filing pages.
- **Agent**: Autonomous navigation through search forms, captcha-free corporate registries, and multi-step state portals ($0.016/step).
- **Quota Allocation**: Reserved $\le 10$ active monitors and targeted agent runs.

## Current Progress
- `[x]` Problem definition and specification (`docs/specs/01_TinyVerify_SPEC.md`).
- `[x]` Core verification schema design.
- `[ ]` Registry search adapter implementation.
- `[ ]` Cross-referencing reconciliation engine.
- `[ ]` Verification report API.
