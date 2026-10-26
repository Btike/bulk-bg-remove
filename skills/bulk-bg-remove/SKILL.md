---
name: bulk-bg-remove
description: >
  Remove image backgrounds locally and in bulk: one photo, a folder (searched
  recursively) or many folders, with BiRefNet and lighter models picked to fit
  the machine. No uploads, no API keys, no per-image cost. Writes lossless
  transparent PNG/WebP to a new "<name>-no-bg" folder next to the input and
  never touches the originals. Use when the user asks to remove, cut out or
  erase a background, make a transparent PNG, isolate a product, person, animal
  or object, prepare product or catalog photos, or replace remove.bg for a
  batch. Reads JPG, PNG, HEIC, WebP, AVIF, TIFF and BMP.
license: MIT
compatibility: >
  Needs uv (installs the engine and a matching Python 3.11-3.13 by itself).
  macOS, Linux and Windows; runs on CPU, or CUDA with onnxruntime-gpu. Product
  photos work from ~3.5 GB of free RAM (best results from ~12 GB); hair, fur,
  people and busy scenes need ~14.5 GB for the best model. Each model
  downloads once (40 MB to 1 GB).
allowed-tools: Bash(bulk-bg-remove:*) Bash(uv tool install bulk-bg-remove*) Bash(uv tool upgrade bulk-bg-remove*)
metadata:
  version: "0.1.0"
---

# bulk-bg-remove

Cut out the subject of every image the user points at, on this machine. The
engine is the `bulk-bg-remove` command: it scans, picks a model that fits the
free RAM, plans the run, processes the images and verifies every output. You
drive it with `--json` (one JSON object per line) and report back.

Run every command on its own: no `;`, `&&` or pipes. Harnesses approve shell
commands one at a time, and this skill pre-approves only `bulk-bg-remove …`
and `uv tool install|upgrade bulk-bg-remove`, so a chained command stops for
approval (or fails in a session nobody is watching).

## 1. Make sure the engine is installed

```bash
bulk-bg-remove --version
```

- Not found: run `uv tool install bulk-bg-remove` straight away (no need to
  check uv first: if uv is missing, that command says so).
- No `uv` either: stop and ask the user to install uv
  (https://docs.astral.sh/uv/getting-started/installation/), then retry. Do not
  run a remote install script without the user's OK. `pipx install bulk-bg-remove`
  also works.
- Older than this skill's `metadata.version`: `uv tool upgrade bulk-bg-remove`.
- Options and model names: `bulk-bg-remove --help`.

## 2. Look at the images, then plan

```bash
bulk-bg-remove "<path>" --dry-run --sample-sheet --json
```

Add `--profile <name>` right away if the user's words already say what the
images are. The dry-run loads no model and writes nothing next to the photos
(the sheet goes to a temporary folder; no need to mention or delete it). Read
the events:

- `scan.inventory`: images found, formats, and `skipped` (not images, hidden
  image files, earlier `-no-bg` output folders) with a reason each.
- `sample_sheet.path`: a contact sheet of up to 12 of the images. **Open it and
  look.** Pick the profile that fits most of them:

  | Profile | The images are mostly |
  |---|---|
  | `uniform` (default) | products and objects with clean outlines: bottles, boxes, shoes, tools, furniture |
  | `complex` | hair, fur, plants, wires, thin or see-through parts |
  | `people` | portraits or groups of people |
  | `scene` | cluttered scenes with no clean background |
  | `drawing` | cartoons, anime, flat illustration |

  The user's own words win over the sheet ("these are product shots"). With no
  clear majority, choose by the hardest part: any hair or fur → `complex`, even
  for portraits. If a folder really mixes kinds (products and portraits), offer
  to run its sub-folders separately with their own profiles.
- `plan`: `model` (what `auto` picked), `model_choice` (its score against the
  best model for that profile, and the RAM the best one needs), `eta_seconds`,
  `blockers`. If you picked a different profile than the first dry-run used,
  dry-run again with `--profile <name>` (no `--sample-sheet` needed).

## 3. Run

If `plan.blockers` is empty, start right away, no need to ask:

```bash
bulk-bg-remove "<path>" --profile <profile> --review-sheets --json --yes
```

- `--yes` only skips the engine's own "Start?" prompt.
- stdout is pure JSON. stderr may show library warnings; judge the run by the
  exit code and the `finished` event. `worker_loading`/`worker_ready` can repeat
  mid-run when RAM is low (a worker restarting to free memory): not a crash.
- If `plan.eta_seconds` is over ~120, run it in the background if your harness
  allows, and report progress now and then from `job_done` events
  (`done`/`total`, `source`).
- Output: `shoe.jpg` → `shoe-no-bg/shoe.png`; `products/` → `products-no-bg/`
  with sub-folders mirrored, next to the input. `-o DIR` overrides it. If two
  inputs share a name (`product.jpg` + `product.png`), both get their extension
  added (`product_jpg.png`, `product_png.png`); `job_started.destination` has
  the final name.
- Images already done are skipped on a re-run; `--force` redoes them.

Exit codes: `0` all good · `1` some images failed, or the model could not be
prepared (see the `error` event) · `2` blocked by the plan · `130` interrupted.

## When the plan is blocked

Never work around a blocker on your own. Tell the user, in plain words, and
offer the choices:

- **The best model does not fit, a weaker one does** (the blocker mentions
  `--allow-fallback`): for this kind of image the light model is clearly worse
  (e.g. hair or plants: about 5/10 instead of 7.6/10). Offer: close some apps
  and retry, or go ahead with the lighter model by adding `--allow-fallback`.
  If the user asked for the best quality, say that the lighter model goes
  against it.
- **Not enough free RAM for any model**: say how much is free and how much the
  lightest model needs. If `scan.hardware.ram_total_gb` itself is below that,
  closing apps cannot fix it: say this machine cannot run it.
- **Not enough disk**: say how much is needed and where.
- **No supported images**: say what was skipped and why.

The plan is the authority on RAM; `doctor` is only a quick check. Do not lower
`--min-free-ram` to force a start: the machine would swap.

When the model is lighter but within half a point of the best (for example
product photos on a laptop with little free RAM), `auto` uses it on its own;
mention it in one line of your report.

**"Best quality"** needs no extra flag: keep `--model auto`, never add
`--fast-edges`, and check `model_choice.gap == 0` (the best model for that
profile is running). If the gap is above 0, say so and offer to free RAM.

## 4. Review the cut-outs

The engine checks files, not looks. `--review-sheets` (step 3) adds a
`review_sheets` event just before `finished`: sheet PNGs in a temporary folder,
each showing up to 12 cut-outs numbered `#n`, as original | on black | on white.

1. **Get a fresh pair of eyes.** If you can start a subagent (Claude Code: the
   `bg-reviewer` agent this plugin ships, or any subagent; Codex: a sub-agent),
   give it the sheet paths, the `review_sheets` event (it has each image's
   `zoom` close-up) and the full text of
   `references/reviewer.md`. It did not run the batch, so it sees what is
   there, not what you expect. With more than 4 sheets, split them across
   several reviewers in parallel: looking costs no engine RAM. If you cannot
   start one, review the sheets yourself with `references/reviewer.md`.
2. **Fix once, on the side.** For each `fix`, take the `source` of its `n`
   from the `review_sheets` event and run again into a retry folder next to
   the output, named so later scans skip it (`products/` → output
   `products-no-bg/`, retries `products-retry-no-bg/`), one run per set of
   flags, never two at once:

   ```bash
   bulk-bg-remove "<path>" -o "<name>-retry-no-bg" --profile <profile> --only <source> [--only <source> ...] <fix flags> --review-sheets --json --yes
   ```

   `--only` takes the absolute `source`, a path relative to `<path>`, or a bare
   file name if it is unique, and keeps the usual output names (the same
   `label` as on the first sheets). A fix whose plan is blocked (e.g. `--profile
   complex` needs more RAM) goes through "When the plan is blocked".
3. **Keep the better one.** Give the reviewer the new sheets plus the earlier
   ones and ask it to compare (see "Comparing a fix" in `reviewer.md`). Copy a
   new cut-out over the first one (same `label` under the output folder) only
   when the reviewer says `keep: new`; a
   second pass can make a good cut-out worse. One round only: anything still
   wrong, and every `ask`, goes in your report with the sheet path so the user
   can see it. Mention the retry folder: the user can delete it.

Review by default; skip it only if the user wants speed over checking or says
not to. The sheets cover the images this run processed: on a re-run where
everything was already done there is no `review_sheets` event.

## 5. Report back

From the final `finished` event:

- **Done**: `processed` images in `output`. Mention any renamed output.
- **Failed** (`failed`): one line each, in plain words. `image file is
  truncated` or `cannot identify image` → the file is damaged; re-export or
  re-download it. Do not retry the same file in a loop.
- **Not processed** (`not_processed` and `scan.inventory.skipped`): group by
  reason ("3 files are not images: notes.txt, fake.pdf, page.jpg"); collapse
  long lists. Always mention hidden image files and earlier `-no-bg` folders if
  the user said "all the photos".
- **Warnings** (`warnings`, and `job_done.note`): `empty cut-out` (no subject
  found) and `nothing removed` deserve a line each; `edge matting skipped` means
  that image probably came out empty or ghostly; `edge matting at N MP to fit
  free RAM` means its edges are a bit softer than usual.

Do not claim success for images listed in `failed`. `processed` counts only
outputs the engine verified (a readable RGBA file at the source's size; an
empty or unchanged cut-out is listed in `warnings`), so no extra check is
needed for the report. A good report is 3-5 lines:

> Done: 37 of 38 photos cut out, in `~/Pictures/products-no-bg/` (transparent
> PNG, same size, originals untouched). Model: birefnet-general-lite.
> 1 failed: `IMG_0042.jpg` is damaged, re-export it.
> 2 files skipped: not images (notes.txt, list.pdf).
> Review: 35 fine; 2 had a green fringe on the hair and were redone with
> `--clean-edges`, now fine.

**Store and catalog photos.** Say what the output is: transparent, the same
size as the original, not on white and not square-cropped, and contact
shadows under the product are removed. Offer to help with white backgrounds,
square crops or a soft shadow if their shop needs them; the engine does not
do these.

## Options worth knowing

| Flag | Use it when |
|---|---|
| `--profile NAME` | Always, from step 2. |
| `--review-sheets` | Always, unless the user wants speed over checking (step 4). |
| `--only FILE` | Redoing chosen images after a review (step 4), with `-o` to a temporary folder. Implies `--force` for them. |
| `--model NAME` | The user names a model, or `references/models.md` says so for a special case. Default `auto`. |
| `--format webp` | The user wants smaller files (still lossless). Default PNG. |
| `--clean-edges` | A coloured halo from the old background shows on hair or fur (green screen, strong colours). Recolours semi-transparent edge pixels only. |
| `--fast-edges` | Speed matters more than hair and fur detail. Never when the user asked for the best quality. |
| `-o DIR` | The user wants the output somewhere specific. |

The subject's pixels are copied bit for bit (except with `--clean-edges`, and
only on edge pixels), and the output keeps the input's exact size. No model
keeps clear glass see-through; warn the user if the photos are glassware on a
coloured background.

## Checking the machine

`bulk-bg-remove doctor --json` prints one object: `ready`, `checks` (HEIC,
AVIF, WebP support, free RAM), `auto` (the model `auto` would pick right now
for each profile) and `models` (each model's RAM need and whether it fits).
It exits 2 when not ready; `checks` says why. A missing HEIC or AVIF check only
matters if the folder has those files.

## For people, not agents

If the user would rather drag and drop photos themselves, tell them to run
`bulk-bg-remove` with no arguments: it opens a local page in the browser where
they can drop files or folders or paste images. Output lands in
`~/Downloads/<name>-no-bg/`.

## References

- `references/models.md`: every model's scores by kind of image, speed and RAM.
- `references/reviewer.md`: how to review the cut-outs, the defects and their fixes.
- `references/json-events.md`: every `--json` event and its fields.
- `references/troubleshooting.md`: install, model download, RAM and format problems.
