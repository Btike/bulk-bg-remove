## What and why

<!-- What this changes and the problem it solves. Link the issue: Fixes #123 -->

## How it was checked

<!-- Commands run, images tried, before/after for anything visible. -->

## Checklist

- [ ] `scripts/ci.sh` is green (add `--linux` for changes to the runner, RAM or platform code)
- [ ] New logic has tests: unit for pure logic, integration for runner, CLI or UI
- [ ] The quality contract in `AGENTS.md` still holds: subject pixels, exact size, lossless output and originals untouched, unless the change is an opt-in flag
- [ ] `--json` event changes are in `skills/bulk-bg-remove/references/json-events.md`
- [ ] Behaviour an agent relies on is updated in `skills/bulk-bg-remove/SKILL.md`
- [ ] Model scores or RAM figures changed only with a re-run of `scripts/bench.py` (see `BENCHMARK.md`)
