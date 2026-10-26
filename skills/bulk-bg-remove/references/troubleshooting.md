# Troubleshooting

## Install

- `bulk-bg-remove: command not found` after `uv tool install`: uv's tool folder
  is not on PATH. Run `uv tool update-shell`, then open a new shell, or call it
  through `uvx bulk-bg-remove …` for this session.
- uv picks a Python itself (3.11–3.13). If a corporate proxy blocks the download,
  install Python 3.12 and run `uv tool install --python 3.12 bulk-bg-remove`.

## Model download

- The first run of a model downloads its weights to `~/.u2net/` (set `U2NET_HOME`
  to move it). It needs network access once; after that everything is offline.
- A download cut halfway leaves a broken file: delete `~/.u2net/<model>.onnx`
  and run again.

## RAM

- The plan refuses to start when free RAM is below what the model needs. Free
  RAM is what the OS reports right now: closing browsers, IDEs and Docker
  usually frees several GB.
- During a run, if free RAM drops below the reserve, new images wait (`paused`)
  and continue when memory frees up (`resumed`). Nothing is lost.
- Very large photos (40+ MP) use more memory per image; the plan warns about it.

## Images

- HEIC/HEIF and AVIF are read with pillow-heif and Pillow. `doctor` shows whether
  they work on this machine (`checks.heic`, `checks.avif`).
- Files are recognised by content, not extension: a PNG named `.jpg` works, and a
  web page saved as `.jpg` is skipped as "not an image".
- GIF, PDF and RAW camera files are not supported and are listed as skipped.
- Photos are rotated by their EXIF orientation first, so the output is upright.
- A photo with no clear subject (a landscape, a shelf full of products) can come
  out mostly empty. The `job_done.note` field says when matting was skipped
  because no foreground was found.

## Edges

- Hair or fur keeps a tint of the old background: re-run those images with
  `--clean-edges --force`.
- Need speed over fine detail: `--fast-edges` skips edge matting.
