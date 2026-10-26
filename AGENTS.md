# AGENTS.md — bulk-bg-remove

Remove image backgrounds in bulk, 100% locally, without touching a single subject
pixel. One engine, two entry points: a CLI/agent mode and a local drag-and-drop UI.

> Status: v0.1 under construction. Sections marked _(planned)_ do not exist yet.

## Stack
- **Python 3.11–3.13**, managed by **uv** (`.python-version` pins 3.12 for development)
- **rembg** (ONNX Runtime) with **BiRefNet** (MIT) — default model `birefnet-general-lite`
- **Pillow + numpy** — imaging; **pillow-heif** for HEIC/HEIF _(planned)_
- **FastAPI + Uvicorn** — local UI server _(planned)_
- **rich** — terminal logs _(planned)_ · **psutil** — capacity probes _(planned)_
- **pytest · ruff · mypy (strict)** — quality gates

## Commands
- Install / sync: `uv sync`
- Tests: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Typecheck: `uv run mypy`
- Run: `uv run bulk-bg-remove [PATH] [--yes] [--json]` _(planned)_

## Structure
- `src/bulk_bg_remove/core/` — pure image logic: `io.py`, `compositing.py`, `pipeline.py`.
  No CLI/UI/runner imports.
- `src/bulk_bg_remove/engine/model.py` — rembg session wrapper (load once per worker, reuse).
- `src/bulk_bg_remove/domain.py` — shared domain types.
- `src/bulk_bg_remove/bulk/` — the four phases: probe → plan → run → verify _(planned)_
- `src/bulk_bg_remove/ui/` — local server + single-file page _(planned)_
- `tests/` — unit tests for pure logic, integration tests with a fake model.

## Quality contract (do not break)
- NEVER resample or re-encode the subject's RGB. Only the alpha mask is resized
  (Lanczos); compositing copies RGB verbatim — see `core/compositing.py`.
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
- Each worker process holds the full model in RAM (~1.6 GB for the lite model):
  RAM, not CPU, is usually what caps parallelism.
- `rembg` sizes ONNX threads from `OMP_NUM_THREADS` — set it per worker before
  creating the session, or N workers will oversubscribe the CPU.
- CoreML is listed by onnxruntime on macOS but BiRefNet does not compile to it; treat
  it as CPU.
