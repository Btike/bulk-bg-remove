"""Command-line entry point.

    bulk-bg-remove PATH [options]   process a photo or a folder (recursively)
    bulk-bg-remove doctor           check the install, the model and this machine

Exit codes: 0 all good · 1 some images failed · 2 nothing ran (blocked or bad
usage) · 130 interrupted.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from rich.prompt import Confirm

from . import __version__
from .bulk.events import Event
from .bulk.runner import DEFAULT_FACTORY
from .bulk.session import BulkOptions, BulkSession
from .console import JsonReporter, TerminalReporter
from .core.matting import EdgeMode
from .domain import OutputFormat
from .engine.model import DEFAULT_MODEL
from .formats import SUPPORTED_EXTENSIONS

EXIT_OK, EXIT_FAILED, EXIT_BLOCKED, EXIT_INTERRUPTED = 0, 1, 2, 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bulk-bg-remove",
        description=(
            "Remove image backgrounds in bulk, 100%% locally, without touching a single "
            "subject pixel. Output goes to a new <name>-no-bg/ folder next to the input."
        ),
        epilog=f"Supported inputs: {' '.join(SUPPORTED_EXTENSIONS)} (detected by content). "
        "Run `bulk-bg-remove doctor` to check your setup.",
    )
    parser.add_argument("path", type=Path, help="a photo or a folder (searched recursively)")
    parser.add_argument(
        "-o", "--output", type=Path, help="output folder (default: <name>-no-bg/ next to PATH)"
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=[f.value for f in OutputFormat],
        default=OutputFormat.PNG.value,
        help="output format, always lossless (default: png)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"default: {DEFAULT_MODEL}")
    edges = parser.add_mutually_exclusive_group()
    edges.add_argument(
        "--clean-edges",
        action="store_const",
        dest="edges",
        const=EdgeMode.CLEAN,
        help="also remove the old background's colour halo from edge pixels "
        "(changes the colour of semi-transparent edge pixels only)",
    )
    edges.add_argument(
        "--fast-edges",
        action="store_const",
        dest="edges",
        const=EdgeMode.MASK,
        help="skip edge matting (faster, coarser hair and fur)",
    )
    parser.set_defaults(edges=EdgeMode.MATTE)
    parser.add_argument("--workers", type=int, help="force a worker count (default: auto)")
    parser.add_argument(
        "--min-free-ram",
        type=float,
        metavar="GB",
        help="refuse to start below this much free RAM (default: what the model needs)",
    )
    parser.add_argument("--force", action="store_true", help="redo images already done")
    parser.add_argument("--dry-run", action="store_true", help="scan and plan, then stop")
    parser.add_argument("-y", "--yes", action="store_true", help="start without asking")
    parser.add_argument(
        "--json", action="store_true", help="emit JSON lines (for agents and scripts)"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run(argv: Sequence[str], *, model_factory: str = DEFAULT_FACTORY) -> int:
    args = build_parser().parse_args(argv)
    options = BulkOptions(
        model=args.model,
        output_format=OutputFormat(args.output_format),
        edges=args.edges,
        workers=args.workers,
        min_free_ram_gb=args.min_free_ram,
        force=args.force,
        model_factory=model_factory,
    )

    terminal = None if args.json else TerminalReporter()
    emit = JsonReporter() if args.json else terminal
    assert emit is not None
    if terminal:
        terminal.banner()

    session = BulkSession(args.path, options, emit, output_root=args.output)
    try:
        plan = session.prepare()
    except (FileNotFoundError, ValueError) as exc:
        if terminal:
            terminal.line(f"[bold red]✗ {exc}[/]")
        else:
            emit(Event("error", {"message": str(exc)}))
        return EXIT_BLOCKED

    if terminal:
        terminal.output(session.output_root)
    if not plan.ok:
        return EXIT_BLOCKED
    if args.dry_run:
        return EXIT_OK
    if plan.images and not args.yes:
        if not sys.stdin.isatty() or args.json:
            message = "add --yes to start without an interactive confirmation"
            if terminal:
                terminal.line(f"[yellow]{message}[/]")
            else:
                emit(Event("error", {"message": message}))
            return EXIT_BLOCKED
        if not Confirm.ask("               Start?", default=True):
            return EXIT_BLOCKED

    if terminal:
        terminal.start_run(plan.images)
    try:
        report = session.execute()
    except KeyboardInterrupt:
        if terminal:
            terminal.close()
            terminal.line("[yellow]interrupted — run the same command again to resume[/]")
        else:
            emit(Event("interrupted", {}))
        return EXIT_INTERRUPTED
    finally:
        if terminal:
            terminal.close()
    return report.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["doctor"]:
        from .doctor import doctor

        return doctor(args[1:])
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
