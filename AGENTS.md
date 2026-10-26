# Repository Operating Rules

This repository produces the daily **Advanced Transportation News Update**
(https://news.peterjwhite.org, GitHub Pages). It is being rebuilt. The full plan
is in `docs/rebuild-plan.md`; read it, and `docs/plan-overrides.md` (which wins), before changing anything. Keep the visible
site title, section set and footer unchanged unless the owner says otherwise.

The previous OpenAI / GitHub Actions design described in older versions of this
file is obsolete. Its useful editorial rules are in `docs/editorial-rules-legacy.md`.

## Operating rules (plan Section 3)

- Work autonomously. Do not ask for approval of routine work; decide and log the
  decision in `docs/decisions.md` (one line each, with the reason).
- Run everything under the owner's own Windows account. No separate user.
- Every change goes on a branch. Merge to main automatically (fast-forward or
  squash) once the full test suite and validation gates pass. Push without force.
- Never: force-push or rewrite main's history; delete or rewrite past editions in
  `data/archive` or `data/raw_archive`; weaken, skip or disable a validation gate
  to make a run pass; print, log or commit secrets; read or write outside the
  repository, `C:\newsroom`, and the config folders of Ollama, Hermes, Task
  Scheduler and NSSM; send data to any endpoint not named in the plan; modify the
  owner's separate email newsletter.
- Ask the owner only for: credentials, actions needing administrator rights or
  BIOS access, the Phase 2 blind judging, genuine blockers. Batch requests into
  one message per phase with exact steps.
- Secrets: never ask for one in chat. `scripts/set_secret.py` stores values with
  `keyring` (Windows Credential Manager); code reads secrets only from keyring.
- Keep changes small and tested. Anything with one right answer (fetching,
  dedup, tracker status, validation, git, deployment) is deterministic code,
  never model judgment.
- A failure that recurs twice gets a new deterministic recovery rule, not a
  cleverer prompt.
- Fully local: no paid API (OpenAI, Anthropic or any other) is ever called. If local generation
  fails, the run fails and alerts the owner. See docs/plan-overrides.md (it overrides the plan).
- Fallback/template text is a hard failure; it must never be saved as an edition.

## Session discipline

- Append a dated entry to `docs/progress.md` at the end of every work session
  (what changed, tests run, what is next).
- Until the move to `C:\newsroom`, stay inside this repository folder.
