# Decisions

One line per judgment call: date — decision — reason.

- 2026-10-24 — Worked on the checked-out `main` branch for the Section 1 setup commit instead of a feature branch — the owner answered the branch prompt with "stay on current branch".
- 2026-10-24 — Read the Hermes checkout at Documents\Codex\projects\advanced-transportation-update and copied its scripts (not .env.local) into legacy/hermes_path/ — the brief (Phase 0 items 1-2) requires locating and preserving the Hermes edition script; read-only access, scoped to that task.
- 2026-10-24 — Paused (not deleted) the Hermes jobs: builds, deliveries, publication, repair agent, self-heal — "exactly one publisher"; pausing is reversible, deletion is not.
- 2026-10-24 — Left the Hermes "ATU source monitor" cron running — it only discovers/monitors, does not publish, and may feed discovery later.
- 2026-10-24 — Deactivated the n8n workflow via `n8n update:workflow` and stopped the n8n node process — it had failed every 5 minutes since Oct 12 and was a second publisher candidate.
- 2026-10-24 — Did not pull into the OneDrive checkout: it holds a locally modified data/raw_archive/2026-10-06.json that differs from origin and the rules forbid rewriting raw_archive. Commits go through a separate git worktree on origin/main instead.
- 2026-10-24 — Added Edit denies for data/archive/** and data/raw_archive/** in .claude/settings.json (plus git reset --hard, rebase, commit --amend denies) to enforce the no-rewrite rule; the new-edition writer is Python code, not the Edit tool.
