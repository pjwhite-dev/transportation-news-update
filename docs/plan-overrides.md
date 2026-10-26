# Owner overrides to docs/rebuild-plan.md

The plan is saved verbatim; where this file disagrees, this file wins.

## 2026-10-26 — Fully local, no paid APIs
Owner decision: the system runs entirely on local models. If local generation fails, the run FAILS and alerts
the owner; it never falls back to a paid API (OpenAI, Anthropic or any other).

Dropped from the plan as a result:
- Claude Opus 5.5 as selector/writer and Claude Haiku 5.5 as the Hermes operator (Sections 5, 6, 7.5). Local models
  (qwen3.8:27b, gemma4:31b, qwen3.6:27b fallback) do all writing; the Phase 2 benchmark compares local models only.
- Cloud budget logic ($50 cap / $45 stop, Section 3) and cost-per-run ledger fields beyond local token counts.
- The 9:00 a.m. automatic cloud fallback build and the "provider-neutral cloud fallback" workflow (Phases 4, 5).
- The "Emergency OpenAI Briefing Build" workflow, removed from .github/workflows.
- Any OpenAI critic benchmark (7.5). The critic stays local.
- anthropic_api_key is no longer a stored secret.

Kept: free services only (GitHub Pages/Actions, Healthchecks.io, Telegram). The GitHub token is optional and free.
Recovery order when the local model fails (7.4) is unchanged: retry, smaller batch, fallback local model, then fail and alert.
