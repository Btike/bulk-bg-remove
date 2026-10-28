# Benchmark

How well each background-removal model works, how fast it is and how much RAM
it needs, measured on 80 hard images and judged blind. These numbers drive
`--model auto` and the skill's model guide.

**In short**

- The default, `birefnet-general-lite`, scores **7.9/10** overall, against 8.0 for
  `birefnet-general`, at **3.2 s per product photo** against 5.6 s.
- On 35 real product catalogue photos it scores **8.6/10**, the best of the 11
  models, and never 3 or less.
- With **3.4 GB of free RAM**, `silueta` still scores 8.1 on clean product shots.
  On hair, fur and plants the light models drop to ~5 against 7.6, so the skill
  asks before using one.
- The cut-out reviewer flags **8 of 9** cut-outs the judges scored 5 or less, and
  a chroma-key check spares it **half** of real catalogue photos without passing a
  single bad cut-out.

## Setup

- **Machine:** Apple M5 (4 performance cores), 34 GB RAM, CPU only (ONNX Runtime).
- **Engine:** `bulk-bg-remove` 0.1.0, one worker, edge matting on (the default),
  everything else at defaults. Times are per image and include matting.
- **Models:** the 11 local models rembg ships with permissive licences.
  Excluded: `bria-rmbg` (non-commercial licence), `withoutbg` and `atlascloud`
  (cloud APIs, not local), `birefnet-general-fp16` (needs extra packages),
  `birefnet-cod` (camouflage research model), `sam` (needs prompts).

## Images (80 judged)

| Set | Images | What it tests |
|---|---|---|
| edges | 10 | hair, fur, wire, bristles, branches, a dandelion |
| materials | 8 | glass, chrome, black on black, white on white, hard shadows |
| composition | 9 | groups, crowds, a logo, a cartoon, a shelf, a landscape with no subject |
| formats | 18 | HEIC, AVIF, WebP, TIFF, BMP, CMYK, 16-bit grey, palette PNG, ICC, EXIF rotation, a wrong extension, existing alpha, 180 px, 45 MP, a 5:1 panorama |
| catalogue | 35 | real product catalogue photos (cleaning products, mostly on white) |

The 27 photos in the first three sets, and the sources of the 45 MP,
panorama and portrait format tests, are public (CC0, CC BY, CC BY-SA or public
domain, from Wikimedia Commons); authors, licences and links are in
[`benchmark/images.csv`](benchmark/images.csv). The catalogue photos belong to
a company and are not redistributed; they appear as `catalogue-NN`, and the
other 14 format tests were made from them.

## Judging

Each image got one comparison grid: the original plus the 11 cut-outs on a
checkerboard, labelled A–K in a fixed shuffled order, so the judge could not
tell which model made which cut-out. The judge, a vision model (Claude), also
looked at full-resolution crops of the hardest regions, on black, white and
checkerboard. Every image was zoomed into.

Scores are 0–10 for how usable the cut-out is as a professional product or
portrait asset: **10** clean, complete subject, fine edges kept, no background
left · **7** small fixable flaws · **4** visible flaws (halo, chunks of
background, missing parts) · **1** unusable · **0** empty.

Every score, with the image's content tags, is in
[`benchmark/scores.csv`](benchmark/scores.csv).

## Results

Mean score per set (0–10), images scored 3 or less, speed and RAM. RAM is the
model alone and the free RAM the planner asks for before a run on 12 MP photos
(model, image buffers, edge matting and a 1.5 GB reserve).

| Model | All (80) | edges (10) | materials (8) | composition (9) | formats (18) | catalogue (35) | ≤ 3 | s/img catalogue | s/img edges | Model RAM | Free RAM needed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `birefnet-general` | **8.0** | 7.0 | 7.8 | 6.8 | 8.4 | 8.4 | 1 | 5.6 | 9.6 | 11.6 GB | 14.3 GB |
| `birefnet-general-lite` (default) | **7.9** | 6.2 | 7.6 | 6.6 | 8.3 | 8.6 | 2 | 3.2 | 6.5 | 9.4 GB | 12.1 GB |
| `birefnet-hrsod` | **7.7** | 6.4 | 7.4 | 6.6 | 8.1 | 8.3 | 3 | 5.6 | 9.7 | 11.6 GB | 14.3 GB |
| `birefnet-massive` | **7.6** | 5.3 | 7.0 | 6.0 | 8.3 | 8.5 | 4 | 5.6 | 9.0 | 11.6 GB | 14.3 GB |
| `birefnet-dis` | **7.5** | 5.5 | 7.2 | 5.7 | 7.9 | 8.4 | 4 | 5.5 | 7.9 | 11.6 GB | 14.3 GB |
| `isnet-general-use` | **6.7** | 5.2 | 5.4 | 3.6 | 7.9 | 7.6 | 11 | 1.3 | 7.8 | 2.4 GB | 5.1 GB |
| `silueta` | **6.6** | 4.5 | 5.1 | 4.1 | 7.8 | 7.5 | 11 | 0.4 | 6.1 | 0.7 GB | 3.4 GB |
| `u2net` | **6.5** | 4.4 | 5.6 | 4.2 | 7.8 | 7.3 | 8 | 0.5 | 3.3 | 1.8 GB | 4.5 GB |
| `birefnet-portrait` | **6.5** | 5.6 | 3.5 | 4.0 | 7.7 | 7.4 | 15 | 5.5 | 12.4 | 11.6 GB | 14.3 GB |
| `u2net_human_seg` | **4.6** | 3.2 | 2.8 | 3.1 | 7.0 | 4.5 | 37 | 0.8 | 3.2 | 1.8 GB | 4.5 GB |
| `isnet-anime` | **2.2** | 0.5 | 2.0 | 2.2 | 1.4 | 3.1 | 64 | 1.2 | 1.3 | 2.2 GB | 4.9 GB |

All 11 models processed all 82 files with no failures (two catalogue files
sharing a name were timed but not judged).

### By kind of image (what `--model auto` uses)

Images grouped by their content tags into the profiles an agent picks from.
`auto` takes the highest score that fits in free RAM, and asks before using a
model more than 0.5 points below the best.

| Profile | n | Best | Next | Light models (~3.5–5 GB free) |
|---|---|---|---|---|
| `uniform` — products, objects | 39 | lite 8.6 · general 8.6 | hrsod 8.5 · massive 8.5 | u2net 8.1 · silueta 8.1 · isnet 7.7 |
| `complex` — hair, fur, plants, thin parts | 32 | general 7.6 | lite 7.4 · hrsod 7.3 | isnet 6.0 · silueta 5.2 · u2net 5.2 |
| `people` | 8 | general 7.8 | dis 7.4 · massive 7.2 | u2net 6.0 · silueta 5.8 · isnet 5.2 |
| `scene` — cluttered | 6 | general 6.3 | hrsod 5.7 | u2net 4.0 · silueta 4.0 · isnet 3.7 |
| `drawing` | 1 | isnet-anime 9.0 | lite 8.0 | — |

## RAM

Peak resident memory of a worker on the edge-heavy set, before and after
bounding edge matting and restarting workers whose heap had grown (both in
v0.1.0):

| Model | Before | After | Planner's figure |
|---|---|---|---|
| `birefnet-general-lite` | 15.7 GB | 12.3 GB | 12.6 GB |
| `birefnet-general` | 17.5 GB | 14.4 GB | 14.8 GB |
| `silueta` | 9.1 GB | 3.9 GB | 3.9 GB |
| `u2net` | 9.3 GB | 5.1 GB | 5.0 GB |

Edge matting costs ~0.35 GB per matted megapixel plus ~2.3 GB per million
pixels in the uncertain band, so a wire chair needs far more than a bottle.
18 of the 20 re-checked cut-outs came out pixel-identical after the change.

## The cut-out reviewer

Three reviewers (the skill's `bg-reviewer` guide, nothing else) looked at the
62 default-model cut-outs of the edges, materials, composition and catalogue
sets, without the judges' scores.

| | |
|---|---|
| Bad cut-outs (judge score ≤ 5) flagged | **8 of 9** |
| Good cut-outs (≥ 8) flagged | 5 of 45 (2 only for `--clean-edges`) |
| Proposed fixes that the judges score higher | 5 of 11 measurable (e.g. 5 → 8 on long fur, plants, curly hair) |
| …the same | 3 |
| …1 point lower | 3, all on cut-outs already ≥ 7 |

Because a second pass can make a good cut-out worse, the skill runs fixes into
a separate folder and keeps the new cut-out only when the reviewer judges it
better.

## Certification: which cut-outs can skip the review

Looking at cut-outs is what costs an agent tokens, so the engine certifies the
ones that provably need no look, and only those (`core/certify.py`). The photo
must have a plain background, the subject must stand out from it, and the
cut-out must match it like a chroma key: nothing unlike the background removed,
nothing half-transparent away from the cut line, no background colour on the
edge (even a faint halo) or kept anywhere inside (a handle, a ring), and the
same mask when the model sees the mirrored photo. Everything else is reviewed.

Cheaper signals were tried first on 225 cut-outs (45 hard images × 5 models):
edge softness, agreement with a second model, stray fragments. None could skip a
single hard cut-out without letting a bad one through (two models can make the
same mistake), so certification is limited to what a chroma-key check can prove.

Checked against the blind judge, default model, thresholds as shipped:

| Set | Certified | Certified ones the judge scored ≤ 6 | Lowest score certified |
|---|---|---|---|
| Catalogue (real product photos, 35 judged) | 18 (51%) | **0** | 7 |
| Hard sets (edges, materials, composition, formats; 45) | 2 | **0** | 9.5 |

The two hard ones are a photo that was already transparent and a flat logo; no
hair, fur, glass or scene was certified. White or clear products on white are
certified only when their whole outline still stands out (2 of the catalogue's
5 white-on-white products, both scored 9); the others go to review, because
their own white cannot be told from background left inside them. Every cut-out the
judge scored 6 or less failed at least one check. On a catalogue this spares
the reviewers about half the photos; 1 in 20 certified cut-outs still goes on
the sheets as an audit. The mirror check runs the model a second time, on a
1024 px copy, only for photos that pass the other checks.

## End-to-end: a fresh agent, no context

A new Claude Code session, with only the plugin installed and no other
context, got one request: *"use the bulk-bg-remove skill to remove the
background from every photo in this folder"*, on a 248-image test folder (the
benchmark sets plus 151 catalogue photos and a deliberately messy folder). It
was allowed to run the engine and to copy files, nothing else.

- It installed the engine, looked at the sample sheet, picked `uniform` for the
  folder and ran it in the background: **247 of 248 done**; the one failure was
  a truncated file, reported as damaged. Non-images, a GIF, a hidden file and an
  earlier output folder were listed as skipped, each with its reason.
- It split 21 review sheets across **4 `bg-reviewer` subagents** in parallel,
  redid the flagged images in a side folder, had a fresh reviewer compare old
  and new, and kept the new version for 4 of them (a handler's arm gone from a
  dog, a wine glass stem recovered, a cutter tip and a jug shoulder restored).
- It reported what it could not fix (a colour tint on a dandelion and a plant,
  two see-through metal handles) and what the user should decide (placeholder
  "no photo" images, scenes with no clear subject, a 180 px original).

What it surfaced, and what changed since: agent file readers ignore
transparency and show the original background in the output PNG, so
reviewers now get flattened close-ups on black and white (`zoom`); and fixes
now go to a `<name>-retry-no-bg` folder next to the output instead of `/tmp`,
which needed extra approval to copy from.

A second fresh session with those fixes, from a clean install, did the same
job with close-ups: **5 reviewers**, 214 cut-outs fine, 20 redone in one
round with four different fixes (`--profile complex`, `--model
birefnet-general`, `--clean-edges`, `--model birefnet-portrait`), **8 kept**
because they were clearly better (long fur, curly and loose hair, a cat, a
wine glass, three catalogue products), 12 kept as they were and reported.
Every redone file differed from the first pass; none was copied over without
a reviewer's `keep: new`.

A third fresh session tested the shop flow from a clean install. The only
request was *"use the bulk-bg-remove skill to remove the background from every
photo in this folder; they are for my online store"*. It picked `--shop` on its
own and processed **247 of 248**: every output was a 2000×2000 white JPEG
carrying the `--shop` finish in its metadata. Five reviewers found 17 to
redo. The agent redid them with the same finishing flags and kept **9** that
were clearly better. In its report it listed the 5 that a second pass did not
fix, the placeholders and scenes that do not belong in a shop, and the small
originals that `--size 2000` had to enlarge, offering `--size 1000` as an
option. The whole session cost about $5.70 in model usage, most of it for
looking at review close-ups.

## Low RAM: the same request at 4, 8 and 12 GiB

Fresh sessions got the shop request on 16 photos (8 catalogue products, a glass
bottle, a bicycle, a cat, curly hair, a group of people, a cartoon, a wine glass
and a 45 MP photo), with the engine in a Linux x86_64 container limited to 4, 8
or 12 GiB and no swap (the engine reads the container's limit, not the host's):

| Limit | What happened |
|---|---|
| 4 GiB | The plan blocked (the 45 MP photo needs 6.5 GB). The agent dry-ran each way to fit (`--fast-edges`, `--max-megapixels 24`, no finishing, the large photo alone) and offered the one that fits: the 15 others now, the large one apart |
| 8 GiB | All 16 done with `silueta` (8.1 on products, best 8.6), reviewed, 8 fine; redos for hair and scenes did not fit, and it said so with the choices |
| 12 GiB | Same model and outcome (the best one needs 15.2 GB with the shop finish for the 45 MP photo) |

No worker was killed for memory at any limit, and every output was a 2000 px
white JPEG. Each session cost under $1. Timings are not meaningful here: the
container emulates x86 on an ARM Mac.

## Caveats

- One judge (a vision model) per image, no second opinion: read gaps under
  ~0.5 points as ties.
- Small samples behind some rows: `drawing` has 1 image, `scene` 6, `people` 8.
- The catalogue set is one product line (cleaning products, mostly on white);
  fashion, food or jewellery may differ.
- One machine, CPU only, one worker. No GPU timings.
- `--clean-edges` and `--fast-edges` were not scored.

## Reproduce

```bash
uv sync
# 1. Run every model on a folder of photos (times, peak RAM, before/after sheets)
uv run python scripts/bench.py PHOTOS RESULTS --model birefnet-general-lite --model silueta ...
# 2. Blind grids for judging (models shown as letters; GRIDS/index.json maps them back)
uv run python scripts/bench_grids.py PHOTOS RESULTS GRIDS
# 3. Close-ups of one region while judging
uv run python scripts/bench_grids.py --zoom GRIDS wine-glass ORIGINAL,A,B 0.2 0.1 0.8 0.6
```

The public photos can be downloaded from the links in `benchmark/images.csv`.
