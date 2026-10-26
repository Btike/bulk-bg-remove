#!/usr/bin/env bash
# Local CI — the same gates as a hosted CI, run on this machine:
#   lint, format, types, lock file, tests on Python 3.11/3.12/3.13, package build,
#   plugin manifests, and the test suite on Linux in Docker when Docker is running.
#
#   scripts/ci.sh            # everything
#   scripts/ci.sh --quick    # lint, types and tests on the dev Python only
set -euo pipefail
cd "$(dirname "$0")/.."

quick=false
[[ "${1:-}" == "--quick" ]] && quick=true

step() { printf '\n\033[1;32m▶ %s\033[0m\n' "$*"; }

step "lock file is up to date"
uv lock --check

step "ruff"
uv run ruff check .
uv run ruff format --check .

step "mypy (strict)"
uv run mypy

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

if command -v docker > /dev/null && docker info > /dev/null 2>&1; then
  step "tests on Linux (Docker, Python 3.12)"
  docker run --rm -v "$PWD":/src:ro -w /src ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
    sh -c 'cp -r /src /work && cd /work && rm -rf .venv .ci && uv run --locked pytest -q'
else
  printf '\n\033[33m! Docker is not running: Linux tests skipped (Windows is not covered locally)\033[0m\n'
fi

step "all green"
