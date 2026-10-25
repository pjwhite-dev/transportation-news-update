# legacy/hermes_path

Snapshot (2026-10-24) of the Hermes "advtransport" edition path, which lived outside this
repository in a separate checkout (Documents\Codex\projects\advanced-transportation-update,
branch hermes/vercel-neon-migration-plan, HEAD ee841e1) and was driven by Hermes cron jobs.
Secrets (.env.local) were NOT copied. Reference only; not imported by production code.

- hermes_source_monitor.py — "ATU source monitor" (every 5 min)
- hermes_daily_build.py — bounded build (deterministic selection + local-model prose)
- publish_validated_site.py — "ATU validated website publication"
- atu_repair_monitor.py / atu_self_heal.py — guarded repair agent hooks
- build_readiness.py, final_edition_qc.py, final_summary_qc.py, source_fact_check.py, summary_quality.py — QC
- working-tree-vs-HEAD.patch — uncommitted edits to tracked files in that checkout (excluding data/)
