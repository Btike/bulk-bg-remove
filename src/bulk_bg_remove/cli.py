"""Command-line entry point.

    bulk-bg-remove                  open the drag-and-drop UI in your browser
    bulk-bg-remove PATH [options]   process a photo or a folder (recursively)
    bulk-bg-remove PATH --ui        same, but plan and watch it in the UI
    bulk-bg-remove doctor           check the install, the model and this machine

Exit codes: 0 all good · 1 some images failed · 2 nothing ran (blocked or bad
usage) · 130 interrupted.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from rich.prompt import Confirm

from . import __version__
from .bulk.events import Event
from .bulk.models import AUTO_MODEL, Profile
from .bulk.runner import DEFAULT_FACTORY
from .bulk.sample import write_sample_sheet
from .bulk.session import BulkOptions, BulkSession, ModelUnavailable
from .console import JsonReporter, TerminalReporter
from .core.finish import Finish, parse_colour
from .core.matting import EdgeMode
from .domain import OutputFormat
from .engine.model import DEFAULT_MODEL
from .formats import SUPPORTED_EXTENSIONS

EXIT_OK, EXIT_FAILED, EXIT_BLOCKED, EXIT_INTERRUPTED = 0, 1, 2, 130

# --shop: what marketplaces ask for: white, square, the product filling ~85%,
# 2000 px (zoomable), JPEG.
SHOP_PRESET = ("--background white", "--square", "--pad 7.5", "--size 2000", "--format jpg")


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
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        help="a photo or a folder (searched recursively); omit it to open the UI",
    )
    parser.add_argument("--ui", action="store_true", help="plan and watch the run in the browser")
    parser.add_argument(
        "--no-browser", action="store_true", help="with the UI: print its URL instead of opening it"
    )
    parser.add_argument(
        "-o", "--output", type=Path, help="output folder (default: <name>-no-bg/ next to PATH)"
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=[f.value for f in OutputFormat],
        help="png (default) or webp, both lossless; jpg (quality 95) needs --background",
    )
    shop = parser.add_argument_group(
        "ready for a shop", "Optional finishing; without it the cut-out is saved as is."
    )
    shop.add_argument(
        "--background",
        metavar="COLOUR",
        help="put the cut-out on a solid colour: white, black or #RRGGBB",
    )
    shop.add_argument(
        "--square", action="store_true", default=None, help="a square canvas around the subject"
    )
    shop.add_argument(
        "--pad",
        type=float,
        metavar="PCT",
        help="crop to the subject and leave this margin on each side, in %% of the canvas",
    )
    shop.add_argument(
        "--size", type=int, metavar="PX", help="scale the result so its longest side is PX"
    )
    shop.add_argument(
        "--shop",
        action="store_true",
        help=f"shortcut for {' '.join(SHOP_PRESET)} (any of them can still be set)",
    )
    parser.add_argument(
        "--model",
        default=AUTO_MODEL,
        help="a model name, or 'auto' (default): the best model for --profile that fits "
        f"in free RAM. Without auto the usual choice is {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--profile",
        choices=[p.value for p in Profile],
        default=Profile.UNIFORM.value,
        help="what the images are, for --model auto: uniform (products, objects; default), "
        "complex (hair, fur, plants, wires), people, scene (cluttered), drawing",
    )
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="with --model auto: accept a clearly weaker model when the best one does not fit",
    )
    parser.add_argument(
        "--sample-sheet",
        action="store_true",
        help="write a contact sheet of up to 12 of the images (to judge --profile) and report it",
    )
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
    parser.add_argument(
        "--max-megapixels",
        type=float,
        metavar="MP",
        help="leave photos larger than this for another run (they need more RAM)",
    )
    parser.add_argument("--force", action="store_true", help="redo images already done")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="IMAGE",
        help="redo just this image (a path relative to PATH, or a file name); repeatable",
    )
    parser.add_argument(
        "--review-sheets",
        action="store_true",
        help="after the run, write sheets for review: cut-outs that need it, on black and white "
        "(ones a chroma-key check certified are skipped, but for a few audits)",
    )
    parser.add_argument(
        "--review-all",
        action="store_true",
        help="with --review-sheets: sheet every cut-out, certified or not",
    )
    parser.add_argument("--dry-run", action="store_true", help="scan and plan, then stop")
    parser.add_argument("-y", "--yes", action="store_true", help="start without asking")
    parser.add_argument(
        "--json", action="store_true", help="emit JSON lines (for agents and scripts)"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run(argv: Sequence[str], *, model_factory: str = DEFAULT_FACTORY) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.path is None or args.ui:
        if args.json:
            parser.error("--json needs a PATH (the UI is for people; agents use the CLI)")
        finishing = (args.shop, args.background, args.square, args.pad, args.size)
        if any(v is not None and v is not False for v in finishing):
            parser.error("--shop, --background, --square, --pad and --size need a PATH, not the UI")
        return run_ui(args)
    if args.shop:
        args.background = args.background or "white"
        args.square = True if args.square is None else args.square
        args.pad = 7.5 if args.pad is None else args.pad
        args.size = 2000 if args.size is None else args.size
        args.output_format = args.output_format or OutputFormat.JPG.value
    output_format = OutputFormat(args.output_format or OutputFormat.PNG.value)
    try:
        finish = Finish(
            background=parse_colour(args.background) if args.background else None,
            square=bool(args.square),
            pad=args.pad or 0.0,
            size=args.size,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if output_format is OutputFormat.JPG and not finish.opaque:
        parser.error("--format jpg needs --background: JPEG has no transparency")
    if args.max_megapixels is not None and not (
        math.isfinite(args.max_megapixels) and args.max_megapixels > 0
    ):
        parser.error("--max-megapixels must be a number above 0")
    options = BulkOptions(
        model=args.model,
        output_format=output_format,
        edges=args.edges,
        workers=args.workers,
        min_free_ram_gb=args.min_free_ram,
        force=args.force,
        model_factory=model_factory,
        profile=Profile(args.profile),
        allow_fallback=args.allow_fallback,
        only=tuple(args.only),
        review_sheets=args.review_sheets,
        review_all=args.review_all,
        max_megapixels=args.max_megapixels,
        finish=finish,
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
    if args.sample_sheet and session.inventory is not None and session.inventory.sources:
        sheet = write_sample_sheet([s.path for s in session.inventory.sources])
        emit(Event("sample_sheet", {"path": str(sheet.path), "images": list(sheet.names)}))
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
    except ModelUnavailable as exc:
        if terminal:
            terminal.close()
            terminal.line(f"[bold red]✗ {exc}[/]")
        else:
            emit(Event("error", {"message": str(exc)}))
        return EXIT_FAILED
    finally:
        if terminal:
            terminal.close()
    return report.exit_code


def run_ui(args: argparse.Namespace) -> int:
    from .ui.launch import serve

    terminal = TerminalReporter()
    terminal.banner()
    defaults = {"m": args.model, "e": str(args.edges), "f": args.output_format or "png"}
    try:
        serve(
            echo=terminal,
            initial_path=args.path,
            defaults=defaults,
            open_browser=not args.no_browser,
            announce=terminal,
        )
    except KeyboardInterrupt:
        pass
    finally:
        terminal.close()
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["doctor"]:
        from .doctor import doctor

        return doctor(args[1:])
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
