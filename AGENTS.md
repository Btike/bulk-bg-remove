# AGENTS.md — bulk-bg-remove

Remove image backgrounds in bulk, 100% locally, without touching a single subject
pixel. One engine, two entry points: a CLI/agent mode and a local drag-and-drop UI.

> Status: v0.1 under construction. Sections marked _(planned)_ do not exist yet.

## Stack
- **Python 3.11–3.13**, managed by **uv** (`.python-version` pins 3.12 for development)
- **rembg** (ONNX Runtime) with **BiRefNet** (MIT) — default model `birefnet-general-lite`
- **Pillow + numpy** — imaging; **pillow-heif** for HEIC/HEIF; **pymatting + scipy** — edge matting
- **FastAPI + Uvicorn** — local UI server
- **rich** — terminal logs · **psutil** — capacity probes
- **pytest · ruff · mypy (strict)** — quality gates

## Commands
- Install / sync: `uv sync`
- Tests: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Typecheck: `uv run mypy`
- Run: `uv run bulk-bg-remove PATH [--profile KIND] [--yes] [--json] [--clean-edges|--fast-edges]`
  (`--model` defaults to `auto`; `--sample-sheet` with `--dry-run` writes a contact sheet)
- Check a machine: `uv run bulk-bg-remove doctor [--json]`
- **CI runs locally**: `scripts/ci.sh` (lock, ruff, mypy, tests on Python 3.11–3.13, wheel
  build, plugin manifests); `--quick` for lint, types and tests only; `--linux` adds the suite
  on Linux x86_64 in Docker with the real-model smoke test (~10 min, emulated: run it before a
  merge or release). Hosted CI (`.github/workflows/ci.yml`, Linux/macOS/Windows) is skipped
  while the repo is private. Run the full script before every commit.
- Linux arm64 VMs on Apple M4/M5 (Docker, UTM, Lima) advertise SVE the chip cannot run:
  numba and ONNX Runtime die with SIGILL there. Test Linux as x86_64; the runner reports a
  SIGILL crash in plain words (`crash_reason` in `bulk/runner.py`).

## Structure
- `src/bulk_bg_remove/core/` — pure image logic: `io.py`, `matting.py`, `compositing.py`,
  `finish.py` (opt-in shop finishing), `certify.py` (chroma-key certification that lets clean
  cut-outs skip the visual review), `pipeline.py`.
  No CLI/UI/runner imports.
- `src/bulk_bg_remove/engine/model.py` — rembg session wrapper (load once per worker, reuse).
- `src/bulk_bg_remove/domain.py` — shared domain types.
- `src/bulk_bg_remove/bulk/` — the four phases: scan → plan → run → verify, plus `session.py`
  (shared by CLI and UI), `events.py` (the JSON event contract), `models.py` (`--model auto`:
  per-profile scores from the blind-judged bench) and `sample.py` (contact sheet for agents)
- `src/bulk_bg_remove/ui/` — local server + single-file page
- `skills/bulk-bg-remove/` — the agent skill (`SKILL.md` + `references/`); plugin manifests in
  `.claude-plugin/`, `.codex-plugin/` and `.agents/plugins/`. The `--json` event contract is
  documented in `references/json-events.md`: update it with any event change.
- `tests/` — unit tests for pure logic, integration tests with a fake model, and one
  real-model smoke test (`test_real_model.py`, `silueta`, 43 MB download) that runs only with
  `BULK_BG_REMOVE_REAL_MODEL=1` — set by `scripts/ci.sh --linux` and in hosted CI on
  Linux, macOS and Windows.

## Quality contract (do not break)
- NEVER resample or re-encode the subject's RGB. Only the alpha mask is resized
  (Lanczos); compositing copies RGB verbatim — see `core/compositing.py`. The one
  opt-in exception is `--clean-edges`, which recolours semi-transparent edge pixels only.
- Output keeps the exact input dimensions. PNG is always lossless; WebP uses
  `lossless=True, exact=True`.
- The other opt-in exception is finishing (`core/finish.py`: `--background`, `--square`,
  `--pad`, `--size`, `--shop`): it blends edge pixels onto a colour, crops, resamples or
  writes JPEG only when asked. Review sheets keep judging the transparent cut-out (a
  preview saved before finishing), never the finished image.
- Originals are never modified, moved or deleted. Output always goes to a new
  `<name>-no-bg/` folder.

## Conventions
- Strict typing (mypy strict, no `Any` escape hatches), frozen dataclasses, named exports.
- English only: code, comments, docs, UI copy, terminal output.
- Minimal dependencies — justify every new one.
- New logic ships with tests: unit for pure logic, integration for runner/CLI/UI.
- Conventional Commits on feature branches; never commit to `main` directly.

## Gotchas
- `bulk/models.py` scores come from `scripts/bench.py` plus blind vision judging (see
  `references/models.md`). Re-run both before changing a score or adding a model; every
  ranked model needs a measured entry in `MODEL_PEAK_GB`.
- rembg downloads model weights on first use to `~/.u2net/` (needs network once, ~224 MB).
- RAM is the real limit. Measured peak per worker without matting (CPU, tuned session):
  `birefnet-general-lite` 9.4 GB · other BiRefNet variants 11.6 · `isnet-general-use` 2.4 ·
  `u2net` 1.8 · `silueta` 0.7 (all 11 in `bulk/plan.py`). Nearly constant per model: each
  model infers at a fixed size whatever the photo size.
- Edge matting adds ~0.35 GB per matted MP plus ~2.3 GB per million uncertain-band pixels
  (a 4 MP wire chair: 4.9 GB; a product shot: 1.8 GB). `core/matting.py` shrinks the matting
  resolution to fit a budget the worker sets from free RAM (0.6–3 GB), re-measuring after each
  step (the eroded band shrinks slower than the area). Scaling the matte back to full
  resolution costs ~0.03 GB/MP more (~0.065 with `--clean-edges`), outside that budget. The
  start floor counts 0.6 GB of matting (lite: ~12 GB for 12 MP photos); parallel sizing, 3 GB.
- A long-lived worker's RSS creeps to ~3x one image's peak (heap fragmentation from images
  of different sizes; `gc` and malloc pressure relief do not help). Workers restart when RSS
  ends an image more than 1 GB above the model's peak (`recycle_above_gb`). Measured on the
  edge-heavy bench: lite 15.7 → 12.3 GB, silueta 9.1 → 3.9 GB. They only restart while free RAM
  is below reserve + full matting + margin (~5.5 GB): a reload costs ~2 s per image.
- Certification (`core/certify.py`) only runs with `--review-sheets` (not with `--review-all`) and
  lets a cut-out skip review only when a chroma-key check proves it: plain background, subject
  in contrast with it, nothing unlike it removed, nothing half-transparent away from the cut,
  no background colour on the edge or kept inside, same mask on the mirrored photo (a second
  model pass on a 1024 px copy, run only when the cheap checks pass). A check that cannot
  run means "needs review", never a failed image. Its thresholds were set against
  the blind judge's scores (BENCHMARK.md, "Certification"): re-check them against
  `benchmark/scores.csv` before loosening any. Edge softness and second-model agreement were
  tried and rejected: they cannot tell a bad hard cut-out from a good one.
- Worker stdout goes to devnull: pymatting prints harmless "incomplete Cholesky" warnings there.
- On CPU a second worker adds only ~25% throughput (ONNX already uses every core);
  more workers are slower. `OMP_NUM_THREADS` sets intra-op threads per worker.
- `rembg.remove(..., only_mask=True)` silently ignores `alpha_matting`. Edge matting is
  done by `core/matting.py` instead — never pass matting flags to rembg.
- Matting runs at ≤ 4 MP; sure foreground/background come from the full-resolution
  mask. Keep the trimap erosion separable (row pass + column pass): a square
  structure is ~40× slower on large photos.
- CoreML is listed by onnxruntime on macOS but BiRefNet does not compile to it; treat
  it as CPU.
- 16-bit greyscale must be scaled, not converted: Pillow's `I;16 -> RGB` clips to white.
- Bench: results in `BENCHMARK.md` + `benchmark/` (scores, image credits; photos stay out of
  git). `scripts/bench.py INPUT RESULTS [--model M ...]` writes
  before/after sheets, time per image and peak RAM; `scripts/bench_grids.py` makes blind
  grids (models as letters) and close-ups for judging.
