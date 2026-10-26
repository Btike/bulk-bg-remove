<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Btike/bulk-bg-remove/main/assets/logo-dark.svg">
    <img src="https://raw.githubusercontent.com/Btike/bulk-bg-remove/main/assets/logo-light.svg" width="200" alt="bulk-bg-remove logo: a pixel-art monitor showing a green vase with a pink tulip on a transparency checkerboard">
  </picture>
</p>

<h1 align="center">bulk-bg-remove</h1>

<p align="center"><em>Every photo in the folder. No background. Nothing leaves your machine.</em></p>

<p align="center">
  <img alt="works with" src="https://img.shields.io/badge/works%20with-Claude%20Code%20·%20Codex%20·%20any%20agent-0d1117?style=flat-square">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-0d1117?style=flat-square">
  <img alt="status" src="https://img.shields.io/badge/status-pre--release-0d1117?style=flat-square">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Btike/bulk-bg-remove/main/assets/showcase.webp" width="880" alt="Four before-and-after pairs: three friends by train tracks, a black camera on black, a bottle of olive oil and white flowers on white, each cut out onto a transparency checkerboard">
</p>

<p align="center">
  <strong>11 models benchmarked on 80 hard images, judged blind at zoom.</strong><br>
  The default scored <strong>8.6/10 on real product catalogue photos</strong>, the best of the 11, at <strong>3.2 s per photo</strong> on a laptop CPU.<br>
  <sub>Apple M5, CPU only, one worker. Method, every score and the RAM numbers: <a href="https://github.com/Btike/bulk-bg-remove/blob/main/BENCHMARK.md">BENCHMARK.md</a>.</sub>
</p>

> **Pre-release.** Not on PyPI yet; the install lines below go live with v0.1.

## What it is

An agent skill plus the engine behind it. Ask your coding agent to *"remove the
background from every photo in this folder"* and it:

1. installs the engine (one `uv tool install`, ~500 MB with Python),
2. checks your machine (free RAM, formats) and plans the run,
3. looks at a sample, picks the best model that fits your free RAM (BiRefNet when there is
   room), and cuts out every image in a queue or in parallel depending on RAM and CPU,
4. has a second agent, with fresh eyes, review every cut-out on black and on white,
   redo the bad ones with a better setting and keep whichever version is better,
5. tells you what worked, what failed and why.

Output goes to a new `<name>-no-bg/` folder next to the input. Originals are never
touched, and the subject's pixels are copied bit for bit: only the alpha channel is new.

Reads JPG, PNG, HEIC, WebP, AVIF, TIFF and BMP (by content, not extension), follows
EXIF rotation, and handles CMYK, 16-bit and 45 MP photos.

## Install

Any agent ([skills CLI](https://github.com/vercel-labs/skills), needs Node.js 22.20 or newer):

```bash
npx skills add Btike/bulk-bg-remove
```

<details>
<summary>Claude Code</summary>

```
/plugin marketplace add Btike/bulk-bg-remove
```
```
/plugin install bulk-bg-remove@bulk-bg-remove
```
</details>

<details>
<summary>Codex</summary>

```bash
codex plugin marketplace add Btike/bulk-bg-remove
codex plugin add bulk-bg-remove@bulk-bg-remove
```
</details>

<details>
<summary>Just the command, no agent</summary>

```bash
uv tool install bulk-bg-remove
bulk-bg-remove ~/Pictures/products          # → ~/Pictures/products-no-bg/
bulk-bg-remove                              # opens a drag-and-drop page in your browser
```
</details>

## Which model it picks

You do not choose: the agent looks at a few of your photos, tells the engine
what kind they are, and the engine takes the best model for that kind that
fits in your free RAM. Scores are from a [blind-judged benchmark](https://github.com/Btike/bulk-bg-remove/blob/main/BENCHMARK.md) (0–10):

| Your photos | Best model | With little RAM (~4 GB free) |
|---|---|---|
| Products, objects | `birefnet-general-lite` 8.6 | `silueta` 8.1, used on its own |
| Hair, fur, plants, wires | `birefnet-general` 7.6 | `silueta` 5.2, only if you say yes |
| People | `birefnet-general` 7.8 | `silueta` 5.8, only if you say yes |
| Busy scenes | `birefnet-general` 6.3 | `silueta` 4.0, only if you say yes |
| Drawings, anime | `isnet-anime` 9.0 | needs ~5 GB free |

## Options

| Flag | What it does |
|---|---|
| `--profile uniform\|complex\|people\|scene\|drawing` | what the photos are; the model follows |
| `--model NAME` | force a model instead of `auto` |
| `--format webp` | smaller lossless files (PNG by default) |
| `--clean-edges` | remove the old background's colour halo from hair and fur edges |
| `--fast-edges` | skip edge matting: faster, coarser hair |
| `--review-sheets` | sheets and close-ups for reviewing the cut-outs |
| `--only FILE` | redo just some images, keeping their output names |
| `--dry-run --json` | scan and plan only; one JSON event per line for agents |
| `doctor` | check the machine, and what `auto` would pick right now |

## Requirements

- Tested on macOS (Apple silicon). Linux and Windows use the same pure-Python
  stack and are next on the test list. CPU by default; CUDA with `onnxruntime-gpu`.
- **3.5 GB of free RAM** is enough for product photos: the engine picks the best
  model that fits (about 12 GB gets the best ones). For hair, fur and plants a
  light model is clearly worse, so the agent asks before using one.
- The model downloads once (~220 MB). After that everything runs offline.

## Credits

Built on [rembg](https://github.com/danielgatis/rembg) and
[BiRefNet](https://github.com/ZhengPeng7/BiRefNet). Showcase photos (CC0, via
Wikimedia Commons): Priscilla Du Preez, Dennis Klein, margenauer, Hisu lee.

## License

MIT — see [LICENSE](https://github.com/Btike/bulk-bg-remove/blob/main/LICENSE).
