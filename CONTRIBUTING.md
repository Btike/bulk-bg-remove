# Contributing

Thanks for helping. Bug reports, bad cut-outs and agent transcripts are as useful as
code: open them with the [issue forms](https://github.com/Btike/bulk-bg-remove/issues/new/choose).

## Set up

You need [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Btike/bulk-bg-remove
cd bulk-bg-remove
uv sync
uv run bulk-bg-remove doctor
```

## Before a pull request

```sh
scripts/ci.sh            # lock, ruff, mypy (macOS, Linux, Windows), tests on 3.11–3.13, wheel, manifests
scripts/ci.sh --quick    # lint, types and tests only, while you work
scripts/ci.sh --linux    # adds Linux x86_64 in Docker with the real model (~10 min)
```

The same checks run on every pull request on Linux, macOS and Windows, and must pass
to merge.

## How the project works

[`AGENTS.md`](AGENTS.md) is the guide for people and coding agents alike: structure,
the quality contract the engine never breaks, conventions and the gotchas behind
the RAM and model numbers. Read it before changing the engine.

In short:

- Commits follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), one change per commit.
- Strict typing (mypy strict), no new dependency without a reason.
- New logic ships with tests.
- A change in model choice or quality is measured on the benchmark, not argued.

## Security

Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).
