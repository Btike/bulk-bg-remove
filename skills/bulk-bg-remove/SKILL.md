---
name: bulk-bg-remove
description: >
  Remove image backgrounds locally and in bulk: one photo, a folder (searched
  recursively) or many folders, with BiRefNet. No uploads, no API keys, no
  per-image cost. Writes lossless transparent PNG/WebP to a new "<name>-no-bg"
  folder next to the input and never touches the originals. Use when the user
  asks to remove, cut out or erase a background, make a transparent PNG,
  isolate a product, person, animal or object, prepare product or catalog
  photos, or replace remove.bg for a batch. Reads JPG, PNG, HEIC, WebP, AVIF,
  TIFF and BMP.
license: MIT
compatibility: >
  Needs uv (installs the engine and a matching Python 3.11-3.13 by itself).
  macOS, Linux and Windows; runs on CPU, or CUDA with onnxruntime-gpu. The
  default model needs about 12 GB of free RAM and downloads once (~220 MB).
allowed-tools: Bash(bulk-bg-remove:*) Bash(uv tool install bulk-bg-remove*) Bash(uv tool upgrade bulk-bg-remove*)
metadata:
  version: "0.1.0"
---

# bulk-bg-remove

Cut out the subject of every image the user points at, on this machine. The
engine is the `bulk-bg-remove` command. It scans, plans the run against free
RAM and CPU, processes the images and verifies every output. You drive it with
`--json` and report back.

## 1. Make sure the engine is installed

```bash
bulk-bg-remove --version
```

- Not found: install it with `uv tool install bulk-bg-remove`.
- No `uv` either: stop and ask the user to install uv
  (https://docs.astral.sh/uv/getting-started/installation/), then retry. Do not
  run a remote install script without the user's OK. `pipx install bulk-bg-remove`
  also works.
- Older than this skill's `metadata.version`: `uv tool upgrade bulk-bg-remove`.

## 2. Check the machine

```bash
bulk-bg-remove doctor --json
```

It prints one JSON object. Read `ready`, `checks` and `min_free_ram_gb`.
If `checks.model_downloaded` is false, tell the user the first run downloads
the model once (~220 MB for the default) before you start.

## 3. Plan first, then run

Always dry-run first: it scans and plans without loading the model.

```bash
bulk-bg-remove "<path>" --dry-run --json
```

Read the `scan` and `plan` events (one JSON object per line):

- `scan.inventory`: images found, formats, and `skipped` files with reasons.
- `scan.output`: where the cut-outs will go.
- `plan.blockers`: if this list is not empty, the run cannot start. See
  "When the plan is blocked".
- `plan.eta_seconds`, `plan.mode` (`queue` or `parallel`), `plan.workers`.

Then run it:

```bash
bulk-bg-remove "<path>" --json --yes
```

- `<path>` is a file or a folder. Folders are searched recursively; files that
  are not supported images are skipped and listed, never an error.
- Output: `shoe.jpg` → `shoe-no-bg/shoe.png`; `products/` → `products-no-bg/`
  (sub-folders mirrored), always next to the input. `-o DIR` overrides it.
- Images already done are skipped on a re-run; `--force` redoes them.
- Large batches take a while (the plan's ETA). Run the command in the
  background if your harness allows it, and report progress from `job_done`
  events instead of waiting silently.

Exit codes: `0` all good · `1` some images failed · `2` blocked by the plan ·
`130` interrupted.

## 4. Report back

Use the final `finished` event: `processed`, `already_done`, `failed`
(path + reason), `not_processed`, `warnings`, `seconds` and `output`.
Tell the user the output folder, the counts, and every failure or warning in
one line each. Do not claim success for images listed in `failed`.

## When the plan is blocked

Never work around a blocker on your own. Show it to the user and offer the
choices:

- **Not enough free RAM** (`free RAM X GB is below the Y GB this model needs`):
  ask the user to close apps and retry, or offer a lighter model
  (`--model isnet-general-use`, ~5 GB free needed) and say plainly that hair, fur and
  thin edges come out coarser. Only switch models if the user agrees.
- **Not enough disk**: say how much is needed and where.
- **No supported images**: list what was skipped and why.

Do not lower `--min-free-ram` to force a start: the machine would swap.

## Options worth knowing

| Flag | Use it when |
|---|---|
| `--format webp` | The user wants smaller files (still lossless). Default is PNG. |
| `--clean-edges` | A coloured halo from the old background shows on hair or fur edges (green screen, strong colours). Recolours semi-transparent edge pixels only. |
| `--fast-edges` | Speed matters more than hair and fur detail (skips edge matting). |
| `--model NAME` | See `references/models.md` to pick a model for the kind of image. |
| `--workers N` | Rarely: the plan already sizes workers to RAM and CPU. |
| `-o DIR` | The user wants the output somewhere specific. |

The subject's pixels are copied bit for bit (except with `--clean-edges`, and
only on edge pixels), and the output keeps the input's exact size.

## For people, not agents

If the user would rather drag and drop photos themselves, tell them to run
`bulk-bg-remove` with no arguments: it opens a local page in the browser where
they can drop files or folders or paste images. Output lands in
`~/Downloads/<name>-no-bg/`.

## References

- `references/models.md`: which model to use for which kind of image, with
  measured speed and RAM.
- `references/json-events.md`: every `--json` event and its fields.
- `references/troubleshooting.md`: install, model download, RAM and format problems.
