# Site to MCP — Autonomous Web-to-MCP Server Generator

> 🚧 **Status: In Progress** (Architecture & Design Phase)

Site to MCP is an autonomous utility that converts unstructured websites, API reference portals, and documentation hubs into production-ready Model Context Protocol (MCP) servers.

---

## The Problem
LLM developers and agent builders must manually write custom tools and MCP wrappers for every third-party web service and SaaS portal. When UI layouts or endpoints drift, MCP servers silently fail.

## Planned Architecture

```
[Target Web Surface / API Docs]
          │
          ▼
[TinyFish Agent & Crawler] (Deep link discovery & form extraction)
          │
          ▼
[OpenAPI / Tool Spec Synthesis] (Groq Llama 3)
          │
          ▼
[MCP Server Code Generation] (FastMCP Python Server)
          │
          ▼
[Self-Testing against Live Endpoints via TinyFish]
```

## TinyFish Role in Site to MCP
- **Fetch & Agent**: Autonomous discovery of endpoint schemas, query parameters, and payload structures.
- **Quota Allocation**: Reserved $\le 2$ active monitors and targeted agent crawls.

## Current Progress
- `[x]` System specification and interface design (`docs/specs/03_SiteToMCP_SPEC.md`).
- `[ ]` Autonomous crawler and tool schema synthesizer.
- `[ ]` FastMCP server code generator.
- `[ ]` Automated integration test harness.
