# `--json` events

With `--json`, `bulk-bg-remove` prints one JSON object per line on stdout and
nothing else. Every event has `type` and `at` (Unix time, seconds). Fields are
only ever added, never renamed or removed.

## Order

```
scan → plan → [model_download → model_ready] → worker_loading → worker_ready
     → job_started → job_stage… → job_done | job_failed   (per image)
     → verify_started → finished
```

`--dry-run` stops after `plan`. `paused`/`resumed` can appear at any time
during the run. `error` or `interrupted` end the stream early.

## Events

| type | fields |
|---|---|
| `scan` | `hardware` {`platform`, `cpu_name`, `cpu_logical`, `cpu_performance`, `ram_total_gb`, `ram_available_gb`, `device`} · `inventory` {`input`, `images`, `total_mb`, `max_megapixels`, `formats` {format: count}, `skipped` [{`path`, `reason`}]: files that are not supported images, hidden image files (`hidden file`) and earlier output folders (`earlier output folder (-no-bg)`)} · `output` |
| `plan` | `images`, `mode` (`queue`/`parallel`), `workers`, `threads_per_worker`, `binding_limit` (`RAM`/`CPU`), `ram_per_worker_gb`, `min_free_ram_gb`, `pause_below_gb`, `estimated_output_gb`, `free_disk_gb`, `eta_seconds`, `blockers` [str], `warnings` [str], `model`, `edges` (`matte`/`clean`/`mask`), `already_done` |
| `model_download` | `model` (first run only: weights are being downloaded) |
| `model_ready` | `model` |
| `worker_loading` / `worker_ready` | `worker` (a worker that grew too large in memory is restarted mid-run: expect these again for the same `worker`) |
| `worker_failed` | `worker`, `reason` |
| `job_started` | `job`, `worker`, `source`, `destination` |
| `job_stage` | `job`, `worker`, `stage`, `progress` (0–1) |
| `job_done` | `job`, `worker`, `width`, `height`, `seconds`, `note` (null or a short remark: `edge matting skipped: …`, or `edge matting at N MP to fit free RAM`), `source`, `done`, `total` (images finished so far, done or failed, out of the run's total) |
| `job_failed` | `job`, `worker`, `reason`, `source`, `done`, `total` |
| `job_skipped` | `source`, `reason` (`already done`) |
| `paused` | `free_ram_gb`, `needed_gb`: free RAM dropped below the reserve, new images wait (if nothing frees up, pending images fail with `not enough free RAM (pause timed out)`) |
| `resumed` | `free_ram_gb` |
| `verify_started` | `outputs` |
| `finished` | `output`, `images`, `processed`, `already_done`, `failed` [{`path`, `reason`}], `not_processed` [{`path`, `reason`}], `warnings` [{`path`, `warning`}], `seconds` |
| `error` | `message` (the run stopped) |
| `interrupted` | (Ctrl+C) |

## `doctor --json`

A single object, not a stream:

```json
{
  "version": "0.1.0",
  "python": "3.12.7",
  "hardware": {"platform": "Darwin arm64", "cpu_name": "Apple M5", "cpu_logical": 10,
               "cpu_performance": 4, "ram_total_gb": 34.4, "ram_available_gb": 14.6,
               "device": "cpu"},
  "model": "birefnet-general-lite",
  "model_path": "/Users/me/.u2net/birefnet-general-lite.onnx",
  "model_peak_ram_gb": 9.5,
  "min_free_ram_gb": 11.8,
  "checks": {"heic": true, "avif": true, "webp": true,
             "model_downloaded": true, "enough_free_ram": true},
  "ready": true
}
```

`ready` ignores `model_downloaded` (the model downloads on first run). Exit
code `2` when not ready.
