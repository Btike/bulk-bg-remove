# AGENTS.md — bulk-bg-remove

Remove image backgrounds in bulk, 100% locally, without touching a single subject
pixel. One engine, two entry points: a CLI/agent mode and a local drag-and-drop UI.

> Status: v0.1 under construction. Sections marked _(planned)_ do not exist yet.

## Stack
- **Python 3.11–3.13**, managed by **uv** (`.python-version` pins 3.12 for development)
- **rembg** (ONNX Runtime) with **BiRefNet** (MIT) — default model `birefnet-general-lite`
- **Pillow + numpy** — imaging; **pillow-heif** for HEIC/HEIF; **pymatting + scipy** — edge matting
- **FastAPI + Uvicorn** — local UI server _(planned)_
- **rich** — terminal logs · **psutil** — capacity probes
- **pytest · ruff · mypy (strict)** — quality gates

## Commands
- Install / sync: `uv sync`
- Tests: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Typecheck: `uv run mypy`
- Run: `uv run bulk-bg-remove PATH [--yes] [--json] [--clean-edges|--fast-edges]`
- Check a machine: `uv run bulk-bg-remove doctor [--json]`

## Structure
- `src/bulk_bg_remove/core/` — pure image logic: `io.py`, `matting.py`, `compositing.py`, `pipeline.py`.
  No CLI/UI/runner imports.
- `src/bulk_bg_remove/engine/model.py` — rembg session wrapper (load once per worker, reuse).
- `src/bulk_bg_remove/domain.py` — shared domain types.
- `src/bulk_bg_remove/bulk/` — the four phases: scan → plan → run → verify, plus `session.py`
  (shared by CLI and UI) and `events.py` (the JSON event contract)
- `src/bulk_bg_remove/ui/` — local server + single-file page _(planned)_
- `tests/` — unit tests for pure logic, integration tests with a fake model.

## Quality contract (do not break)
- NEVER resample or re-encode the subject's RGB. Only the alpha mask is resized
  (Lanczos); compositing copies RGB verbatim — see `core/compositing.py`. The one
  opt-in exception is `--clean-edges`, which recolours semi-transparent edge pixels only.
- Output keeps the exact input dimensions. PNG is always lossless; WebP uses
  `lossless=True, exact=True`.
- Originals are never modified, moved or deleted. Output always goes to a new
  `<name>-no-bg/` folder.

## Conventions
- Strict typing (mypy strict, no `Any` escape hatches), frozen dataclasses, named exports.
- English only: code, comments, docs, UI copy, terminal output.
- Minimal dependencies — justify every new one.
- New logic ships with tests: unit for pure logic, integration for runner/CLI/UI.
- Conventional Commits on feature branches; never commit to `main` directly.

## Gotchas
- rembg downloads model weights on first use to `~/.u2net/` (needs network once, ~224 MB).
- RAM is the real limit. Measured peak per worker (CPU, tuned session):
  `birefnet-general-lite` ~9.5 GB · `birefnet-general` ~11.8 GB · `isnet-general-use`
  ~2.6 GB. It is almost constant per model because BiRefNet always infers at 1024×1024.
  The planner refuses to start below peak + image buffers + 1.5 GB reserve (~11 GB for lite).
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
- Local bench (not in git): `scripts/bench.py INPUT RESULTS [--model M ...]` writes
  before/after sheets, time per image and peak RAM.
