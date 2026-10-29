# Test baseline (2026-10-24)

`python -m pytest tests -q` had **48 failures at commit 1403cb8 before any code change**; one was fixed
by replacing a test of the removed template fallback, leaving **47 known failures** that predate the rebuild.
They are stale expectations in the legacy editorial layer (old section names such as "Military UAS" /
"Counter-UAS / Airspace Security" vs current ones, Administration Win gates, email/site renderers).
They are not hidden: the exact list is in docs/test-baseline-failures.txt.

Merge rule until Phase 2 replaces that layer: no failure outside this set, and every new test passes.
CI runs the new, always-green suites explicitly (gates, tracker, providers, resolver, alerts).
Fixing or deleting these tests is scheduled with the Phase 2 editorial rebuild.

## Closed (2026-10-29)

The legacy failures are gone: the full suite (`python -m unittest discover -s tests -p "test_*.py"`) passes with
0 failures. Each test was updated to the current section names, deleted because its feature was retired, or kept
as the regression for a real bug; the list is in docs/decisions.md (2026-10-29, "legacy test cleanup").
`docs/test-baseline-failures.txt` was removed. Merge rule from here on: the full suite passes.
