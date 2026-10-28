"""One bulk run, end to end: scan -> plan -> run -> verify.

Shared by the CLI and the UI so both behave identically. `prepare()` covers the
first two phases (nothing is written yet), `execute()` the last two.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from ..core.finish import Finish
from ..core.matting import MATTING_MAX_GB, EdgeMode
from ..domain import OutputFormat
from ..engine.model import DEFAULT_MODEL, downloaded_model_path, ensure_model
from ..paths import OutputTarget, is_output_dir, map_outputs, output_root_for
from .events import Event, EventSink, JSONObject
from .models import AUTO_MODEL, ModelChoice, Profile, choose_model
from .plan import (
    DEFAULT_MAX_WORKERS,
    RECYCLE_MARGIN_GB,
    RESERVE_RAM_GB,
    Plan,
    PlanSettings,
    make_plan,
    model_peak_gb,
)
from .runner import DEFAULT_FACTORY, Job, Runner, RunSettings
from .sample import ReviewSheet, write_review_sheets
from .scan import ImageSource, Inventory, Skipped, discover, free_disk_gb, probe_hardware
from .verify import is_complete, verify_output

# Of the certified cut-outs (sorted by path), every AUDIT_EVERY-th still goes on the
# review sheets, marked as an audit: a spot check that the certification holds.
AUDIT_EVERY = 20


class ModelUnavailable(RuntimeError):
    """The model's weights could not be downloaded or its runtime is missing."""


@dataclass(frozen=True, slots=True)
class BulkOptions:
    model: str = DEFAULT_MODEL
    output_format: OutputFormat = OutputFormat.PNG
    edges: EdgeMode = EdgeMode.MATTE
    workers: int | None = None
    max_workers: int = DEFAULT_MAX_WORKERS
    min_free_ram_gb: float | None = None
    force: bool = False
    pause_timeout_s: float = 600.0
    model_factory: str = DEFAULT_FACTORY
    profile: Profile = Profile.UNIFORM  # what kind of images, for model="auto"
    allow_fallback: bool = False  # let "auto" take a clearly weaker model that fits
    only: tuple[str, ...] = ()  # redo just these (paths relative to the input, or names)
    review_sheets: bool = False  # after the run, write sheets for a visual review
    review_all: bool = False  # with review_sheets: also sheet the certified cut-outs
    max_megapixels: float | None = None  # leave larger photos for a run of their own
    finish: Finish = Finish()  # background, square, margin, size for shops (default: none)

    def __post_init__(self) -> None:
        if self.output_format is OutputFormat.JPG and not self.finish.opaque:
            raise ValueError("--format jpg needs --background: JPEG has no transparency")


@dataclass(frozen=True, slots=True)
class Report:
    output_root: Path
    images: int
    processed: int
    failed: tuple[tuple[str, str], ...]
    already_done: int
    not_processed: tuple[tuple[str, str], ...]
    warnings: tuple[tuple[str, str], ...]
    seconds: float
    certified: int | None = None  # cut-outs that passed the chroma-key check (None: not run)

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    def to_dict(self) -> JSONObject:
        return {
            "output": str(self.output_root),
            "images": self.images,
            "processed": self.processed,
            "already_done": self.already_done,
            "failed": [{"path": p, "reason": r} for p, r in self.failed],
            "not_processed": [{"path": p, "reason": r} for p, r in self.not_processed],
            "warnings": [{"path": p, "warning": w} for p, w in self.warnings],
            "seconds": round(self.seconds, 1),
            "certified": self.certified,
        }


class BulkSession:
    def __init__(
        self,
        input_path: Path,
        options: BulkOptions,
        emit: EventSink,
        *,
        output_root: Path | None = None,
    ) -> None:
        self._input = input_path
        self._options = options
        self._emit = emit
        self._output_root = output_root
        self._runner: Runner | None = None
        self._cancelled = False
        self.inventory: Inventory | None = None
        self.plan: Plan | None = None
        self._targets: list[OutputTarget] = []
        self._done_already: list[OutputTarget] = []
        self._considered = 0
        self.model = options.model
        self.model_choice: ModelChoice | None = None

    @property
    def output_root(self) -> Path:
        return self._output_root or output_root_for(self._input)

    def prepare(self) -> Plan:
        """Phases 1 and 2. Raises FileNotFoundError for a missing input."""
        hardware = probe_hardware()
        found = discover(self._input)
        _check_output_outside_input(self.output_root, found)
        # Names come from every image, so leaving large ones out never renames others.
        targets = map_outputs(
            [s.path for s in found.sources],
            found.input_root,
            self.output_root,
            self._options.output_format,
        )
        by_path = {s.path: s for s in found.sources}
        if self._options.only:
            targets = _select(targets, found.input_root, self._options.only)
        targets, large_done, inventory = self._leave_large(targets, found, by_path)
        self.inventory = inventory
        self._emit(
            Event(
                "scan",
                {
                    "hardware": hardware.to_dict(),
                    "inventory": inventory.to_dict(),
                    "output": str(self.output_root),
                },
            )
        )

        self._considered = len(targets) + len(large_done)
        if self._options.force or self._options.only:
            todo, done = targets, []
        else:
            finish = self._options.finish
            todo = [t for t in targets if not is_complete(t.destination, by_path[t.source], finish)]
            done = [t for t in targets if t not in todo]
        done = [*done, *large_done]
        self._targets, self._done_already = todo, done

        remaining = Inventory(
            inventory.input_path,
            inventory.input_root,
            tuple(by_path[t.source] for t in todo),
            inventory.skipped,
        )
        if self._options.model == AUTO_MODEL:
            self.model_choice = choose_model(
                self._options.profile,
                hardware.ram_available_gb,
                self._options.edges,
                remaining.max_megapixels,
                self._options.finish,
            )
            self.model = self.model_choice.model
        settings = PlanSettings(
            model=self.model,
            output_format=self._options.output_format,
            edges=self._options.edges,
            workers=self._options.workers,
            max_workers=self._options.max_workers,
            min_free_ram_gb=self._options.min_free_ram_gb,
            finish=self._options.finish,
        )
        finish = self._options.finish
        plan = make_plan(hardware, remaining, free_disk_gb(self.output_root), settings)
        choice = self.model_choice
        if (
            choice
            and todo
            and choice.is_fallback
            and choice.fits
            and not self._options.allow_fallback
        ):
            blocker = (
                f"for {choice.profile} images the best model is {choice.best_model} "
                f"({choice.best_score}/10), which needs {choice.best_needs_gb:.1f} GB free; "
                f"{hardware.ram_available_gb:.1f} GB only fits {choice.model} "
                f"({choice.score}/10) — close some apps and retry, or add --allow-fallback"
            )
            plan = replace(plan, blockers=(*plan.blockers, blocker))
        if not todo and done:
            # Everything is already done: nothing can block an empty run.
            plan = replace(plan, blockers=())
        elif not todo and len(inventory.skipped) > len(found.skipped):
            limit = self._options.max_megapixels or 0
            blocker = f"every image is larger than --max-megapixels {limit:g}"
            plan = replace(plan, blockers=(blocker,))
        self.plan = plan
        self._emit(
            Event(
                "plan",
                {
                    **plan.to_dict(),
                    "model": self.model,
                    "model_choice": choice.to_dict() if choice else None,
                    "edges": self._options.edges.value,
                    "output_format": self._options.output_format.value,
                    "finish": (
                        {
                            "background": finish.background_hex,
                            "square": finish.square,
                            "pad": finish.pad,
                            "size": finish.size,
                        }
                        if finish.active
                        else None
                    ),
                    "already_done": len(done),
                },
            )
        )
        return plan

    def cancel(self) -> None:
        self._cancelled = True
        if self._runner is not None:
            self._runner.cancel()

    def execute(self) -> Report:
        """Phases 3 and 4. Call after `prepare()` returned an ok plan."""
        if self.plan is None or self.inventory is None:
            raise RuntimeError("call prepare() first")
        started = time.monotonic()
        by_path = {s.path: s for s in self.inventory.sources}
        for target in self._done_already:
            self._emit(
                Event("job_skipped", {"source": str(target.source), "reason": "already done"})
            )

        if self._targets and not self._cancelled:
            self._ensure_model()
        # Review sheets judge the transparent cut-out, not the finished image: a
        # leftover of a white background is invisible on a white --background.
        previews = (
            Path(tempfile.mkdtemp(prefix="bulk-bg-remove-cutouts-"))
            if self._options.review_sheets and self._targets
            else None
        )
        jobs = [
            Job(i, t.source, t.destination, previews / f"{i}.png" if previews else None)
            for i, t in enumerate(self._targets)
        ]
        outcomes = []
        if jobs and not self._cancelled:
            self._runner = Runner(
                jobs,
                RunSettings(
                    model=self.model,
                    output_format=self._options.output_format,
                    edges=self._options.edges,
                    workers=self.plan.workers,
                    threads_per_worker=self.plan.threads_per_worker,
                    pause_below_gb=self.plan.pause_below_gb,
                    pause_timeout_s=self._options.pause_timeout_s,
                    model_factory=self._options.model_factory,
                    finish=self._options.finish,
                    certify=self._certifies,
                    recycle_above_gb=model_peak_gb(self.model) + RECYCLE_MARGIN_GB,
                    recycle_when_free_below_gb=RESERVE_RAM_GB + MATTING_MAX_GB + RECYCLE_MARGIN_GB,
                ),
                self._emit,
            )
            outcomes = self._runner.run()

        self._emit(Event("verify_started", {"outputs": sum(o.ok for o in outcomes)}))
        failed: list[tuple[str, str]] = []
        warnings: list[tuple[str, str]] = []
        processed = 0
        for outcome in outcomes:
            source: ImageSource = by_path[outcome.job.source]
            if not outcome.ok:
                failed.append((str(outcome.job.source), outcome.reason or "failed"))
                continue
            check = verify_output(outcome.job.destination, source, self._options.finish)
            if not check.ok:
                failed.append((str(outcome.job.source), check.problem or "verification failed"))
            else:
                processed += 1
                if check.warning:
                    warnings.append((str(outcome.job.destination), check.warning))
                if outcome.note:
                    warnings.append((str(outcome.job.destination), outcome.note))

        certified_count: int | None = None
        if self._options.review_sheets:
            verified = {Path(path) for path, _ in failed}
            ok = [o for o in outcomes if o.ok and o.job.source not in verified]
            certified = sorted((o for o in ok if o.certified), key=lambda o: str(o.job.source))
            certified_count = len(certified) if self._certifies else None
            audit = {o.job.id for o in certified[::AUDIT_EVERY]}
            entries = [
                (o.job.source, o.job.destination, _cutout(o.job), o.job.id in audit)
                for o in ok
                if not o.certified or o.job.id in audit
            ]
            if entries:
                try:
                    sheets = write_review_sheets(entries, self.output_root)
                except OSError as exc:
                    warnings.append(("", f"review sheets could not be written: {exc}"))
                else:
                    self._emit_review_sheets(sheets, len(certified), len(audit))
            if previews is not None:
                shutil.rmtree(previews, ignore_errors=True)

        report = Report(
            output_root=self.output_root,
            images=self._considered,
            processed=processed,
            failed=tuple(failed),
            already_done=len(self._done_already),
            not_processed=tuple((str(s.path), s.reason) for s in self.inventory.skipped),
            warnings=tuple(warnings),
            seconds=time.monotonic() - started,
            certified=certified_count,
        )
        self._emit(Event("finished", report.to_dict()))
        return report

    def _leave_large(
        self, targets: list[OutputTarget], found: Inventory, by_path: dict[Path, ImageSource]
    ) -> tuple[list[OutputTarget], list[OutputTarget], Inventory]:
        """With --max-megapixels, take larger photos out of this run so a machine
        short of RAM can do the rest now: ones already done count as done, the
        others are listed as skipped, to run on their own later."""
        limit = self._options.max_megapixels
        if limit is None:
            return targets, [], found
        large = [t for t in targets if by_path[t.source].megapixels > limit]
        redo = self._options.force or bool(self._options.only)
        finish = self._options.finish
        done = [
            t for t in large if not redo and is_complete(t.destination, by_path[t.source], finish)
        ]
        left = [
            Skipped(
                t.source,
                f"larger than --max-megapixels {limit:g} "
                f"({by_path[t.source].megapixels:.0f} MP): run it on its own",
            )
            for t in large
            if t not in done
        ]
        out = {t.source for t in large}
        keep = tuple(s for s in found.sources if s.path not in out)
        inventory = Inventory(found.input_path, found.input_root, keep, (*found.skipped, *left))
        return [t for t in targets if t.source not in out], done, inventory

    @property
    def _certifies(self) -> bool:
        """Certification only saves work when a review follows, and --review-all
        asks to review everything anyway."""
        return self._options.review_sheets and not self._options.review_all

    def _emit_review_sheets(self, sheets: list[ReviewSheet], certified: int, audit: int) -> None:
        self._emit(
            Event(
                "review_sheets",
                {
                    "sheets": [
                        {
                            "path": str(sheet.path),
                            "images": [
                                {
                                    "n": e.number,
                                    "label": e.label,
                                    "source": str(e.source),
                                    "output": str(e.output),
                                    "zoom": str(e.zoom) if e.zoom else None,
                                    "audit": e.audit,
                                }
                                for e in sheet.entries
                            ],
                        }
                        for sheet in sheets
                    ],
                    "certified": certified,
                    "audit": audit,
                },
            )
        )

    def _ensure_model(self) -> None:
        if self._options.model_factory != DEFAULT_FACTORY:
            return  # a custom (test) model brings its own weights
        model = self.model
        if downloaded_model_path(model) is None:
            self._emit(Event("model_download", {"model": model}))
            try:
                ensure_model(model)
            except Exception as exc:  # network, disk, or a missing optional runtime
                raise ModelUnavailable(f"model {model} is not available: {exc}") from exc
            self._emit(Event("model_ready", {"model": model}))


def _cutout(job: Job) -> Path:
    """The transparent cut-out to review: the preview if the worker kept one."""
    return job.preview if job.preview is not None and job.preview.is_file() else job.destination


def _select(targets: list[OutputTarget], root: Path, only: tuple[str, ...]) -> list[OutputTarget]:
    """The targets named in `only`: an absolute path, a path relative to the input
    folder, or a bare file name that matches exactly one image. Case-insensitive,
    like macOS and Windows file systems. Raises ValueError for a name that matches
    nothing or (bare names) more than one image."""
    by_rel = {t.source.relative_to(root).as_posix().casefold(): t for t in targets}
    selected: dict[Path, OutputTarget] = {}
    for raw in only:
        name = raw.replace("\\", "/")
        as_path = Path(name).expanduser()
        if as_path.is_absolute():
            try:
                name = as_path.resolve().relative_to(root).as_posix()
            except ValueError:
                raise ValueError(f"--only: {raw} is not inside the input folder") from None
        key = PurePosixPath(name).as_posix().removeprefix("./").casefold()
        if key in by_rel:
            target = by_rel[key]
        else:
            matches = [t for t in targets if t.source.name.casefold() == key]
            if not matches:
                raise ValueError(f"--only: no such image in the input: {raw}")
            if len(matches) > 1:
                where = ", ".join(t.source.relative_to(root).as_posix() for t in matches)
                raise ValueError(f"--only: {raw} matches several images ({where}); give its path")
            target = matches[0]
        selected[target.source] = target
    return list(selected.values())


def _check_output_outside_input(output_root: Path, inventory: Inventory) -> None:
    """An output folder inside the input would be scanned as photos next time,
    unless it is named like one (`*-no-bg`), which the scan skips."""
    if not inventory.input_path.is_dir():
        return
    try:
        inside = output_root.expanduser().resolve().relative_to(inventory.input_root)
    except ValueError:
        return
    if not any(is_output_dir(Path(part)) for part in inside.parts):
        raise ValueError(
            f"-o {output_root} is inside the input folder: put it elsewhere, or end its "
            "name with -no-bg so later runs skip it"
        )
