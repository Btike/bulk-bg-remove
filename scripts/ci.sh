#!/usr/bin/env bash
# Local CI — the same gates as a hosted CI, run on this machine:
#   lint, format, types, lock file, tests on Python 3.11/3.12/3.13, package build and
#   plugin manifests; with --linux, also the suite on Linux x86_64 in Docker, including
#   the real-model smoke test (~10 min under emulation: run it before a merge or release).
#
#   scripts/ci.sh            # everything but Linux
#   scripts/ci.sh --linux    # everything, plus Linux x86_64 in Docker
#   scripts/ci.sh --quick    # lint, types and tests on the dev Python only
set -euo pipefail
cd "$(dirname "$0")/.."

quick=false linux=false
[[ "${1:-}" == "--quick" ]] && quick=true
[[ "${1:-}" == "--linux" ]] && linux=true

step() { printf '\n\033[1;32m▶ %s\033[0m\n' "$*"; }

step "lock file is up to date"
uv lock --check

step "ruff"
uv run ruff check .
uv run ruff format --check .

step "mypy (strict) as macOS, Linux and Windows"
# mypy skips code it thinks this platform never runs (e.g. the Linux cgroup probe on a
# Mac), so check as each platform the hosted CI tests on.
uv run mypy
uv run mypy --platform linux
uv run mypy --platform win32

if $quick; then
  step "tests (dev Python)"
  uv run pytest -q
  exit 0
fi

for py in 3.11 3.12 3.13; do
  step "tests on Python $py"
  UV_PROJECT_ENVIRONMENT=".ci/py$py" uv run --python "$py" --locked pytest -q
done

step "package builds and ships the UI page"
rm -rf .ci/dist
uv build -q --out-dir .ci/dist
python3 - <<'PY'
import sys, zipfile, glob
wheel = glob.glob(".ci/dist/*.whl")[0]
names = zipfile.ZipFile(wheel).namelist()
needed = ["bulk_bg_remove/ui/static/index.html", "bulk_bg_remove/cli.py"]
missing = [n for n in needed if n not in names]
if missing:
    sys.exit(f"wheel is missing {missing}")
print(f"ok: {wheel}")
PY

step "plugin manifests"
for f in .claude-plugin/*.json .codex-plugin/*.json .agents/plugins/*.json; do
  python3 -m json.tool "$f" > /dev/null && echo "ok: $f"
done
if command -v claude > /dev/null; then
  claude plugin validate . | tail -1
fi

if $linux; then
  if ! command -v docker > /dev/null || ! docker info > /dev/null 2>&1; then
    echo "Docker is not running: start Docker Desktop and retry" >&2
    exit 1
  fi
  # x86_64 on purpose: Linux arm64 VMs on Apple M4/M5 advertise SVE the chip cannot
  # run, so numba and ONNX Runtime die with SIGILL there (not a bug in this project).
  step "tests on Linux x86_64 (Docker, Python 3.12), with the real-model smoke test"
  docker run --rm --platform linux/amd64 -e BULK_BG_REMOVE_REAL_MODEL=1 \
    -v "$PWD":/src:ro -w /src ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
    sh -c 'cp -r /src /work && cd /work && rm -rf .venv .ci && uv run -q --locked pytest -q'
else
  printf '\n\033[33m! Linux not tested: run scripts/ci.sh --linux (Docker) before a merge\033[0m\n'
fi

step "all green"
