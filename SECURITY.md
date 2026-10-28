# Security policy

## Reporting a vulnerability

Please report it privately through
[GitHub's private vulnerability reporting](https://github.com/Btike/bulk-bg-remove/security/advisories/new),
never in a public issue, discussion or pull request. Include the version
(`bulk-bg-remove --version`), the platform and the steps or file that trigger it.

You will get a reply once the report has been reproduced, and credit in the advisory
if you want it.

## Supported versions

Fixes go into the latest release on [PyPI](https://pypi.org/project/bulk-bg-remove/)
only. Upgrade with `uv tool upgrade bulk-bg-remove`.

## What is in scope

bulk-bg-remove runs on your machine and uploads nothing. The parts worth a look:

- **The local UI** (`bulk-bg-remove` with no arguments): it listens on `127.0.0.1`
  only, every API call needs the random token from the URL the CLI opened, and the
  `Host` header must be the local address. A way around any of these is in scope.
- **Reading images**: a crafted image that runs code, writes outside the output folder
  or reads files it should not.
- **Paths**: any way an input or output path makes the engine modify, move or delete an
  original, or write outside the `<name>-no-bg/` folder.
- **The agent skill**: instructions in `SKILL.md` that would lead an agent to run more
  than `bulk-bg-remove` and `uv tool install|upgrade bulk-bg-remove`.

Model weights are downloaded by [rembg](https://github.com/danielgatis/rembg) on first
use; issues in rembg, ONNX Runtime, Pillow or pillow-heif themselves belong to those
projects, but tell us if bulk-bg-remove uses them unsafely.
