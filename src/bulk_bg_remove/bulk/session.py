"""One bulk run, end to end: scan -> plan -> run -> verify.

Shared by the CLI and the UI so both behave identically. `prepare()` covers the
first two phases (nothing is written yet), `execute()` the last two.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path

from ..domain import OutputFormat
from ..engine.model import DEFAULT_MODEL, downloaded_model_path, ensure_model
from ..paths import OutputTarget, map_outputs, output_root_for
from .events import Event, EventSink, JSONObject
from .plan import DEFAULT_MAX_WORKERS, Plan, PlanSettings, make_plan
from .runner import DEFAULT_FACTORY, Job, Runner, RunSettings
from .scan import ImageSource, Inventory, discover, free_disk_gb, probe_hardware
from .verify import is_complete, verify_output


@dataclass(frozen=True, slots=True)
class BulkOptions:
    model: str = DEFAULT_MODEL
    output_format: OutputFormat = OutputFormat.PNG
    alpha_matting: bool = False
    workers: int | None = None
    max_workers: int = DEFAULT_MAX_WORKERS
    min_free_ram_gb: float | None = None
    force: bool = False
    pause_timeout_s: float = 600.0
    model_factory: str = DEFAULT_FACTORY


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

    @property
    def output_root(self) -> Path:
        return self._output_root or output_root_for(self._input)

    def prepare(self) -> Plan:
        """Phases 1 and 2. Raises FileNotFoundError for a missing input."""
        hardware = probe_hardware()
        inventory = discover(self._input)
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

        targets = map_outputs(
            [s.path for s in inventory.sources],
            inventory.input_root,
            self.output_root,
            self._options.output_format,
        )
        by_path = {s.path: s for s in inventory.sources}
        if self._options.force:
            todo, done = targets, []
        else:
            todo = [t for t in targets if not is_complete(t.destination, by_path[t.source])]
            done = [t for t in targets if t not in todo]
        self._targets, self._done_already = todo, done

        remaining = Inventory(
            inventory.input_path,
            inventory.input_root,
            tuple(by_path[t.source] for t in todo),
            inventory.skipped,
        )
        settings = PlanSettings(
            model=self._options.model,
            output_format=self._options.output_format,
            alpha_matting=self._options.alpha_matting,
            workers=self._options.workers,
            max_workers=self._options.max_workers,
            min_free_ram_gb=self._options.min_free_ram_gb,
        )
        plan = make_plan(hardware, remaining, free_disk_gb(self.output_root), settings)
        if not todo and done:
            # Everything is already done: nothing can block an empty run.
            plan = replace(plan, blockers=())
        self.plan = plan
        self._emit(Event("plan", {**plan.to_dict(), "already_done": len(done)}))
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
        jobs = [Job(i, t.source, t.destination) for i, t in enumerate(self._targets)]
        outcomes = []
        if jobs and not self._cancelled:
            self._runner = Runner(
                jobs,
                RunSettings(
                    model=self._options.model,
                    output_format=self._options.output_format,
                    alpha_matting=self._options.alpha_matting,
                    workers=self.plan.workers,
                    threads_per_worker=self.plan.threads_per_worker,
                    pause_below_gb=self.plan.pause_below_gb,
                    pause_timeout_s=self._options.pause_timeout_s,
                    model_factory=self._options.model_factory,
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
            check = verify_output(outcome.job.destination, source)
            if not check.ok:
                failed.append((str(outcome.job.source), check.problem or "verification failed"))
            else:
                processed += 1
                if check.warning:
                    warnings.append((str(outcome.job.destination), check.warning))

        report = Report(
            output_root=self.output_root,
            images=len(self.inventory.sources),
            processed=processed,
            failed=tuple(failed),
            already_done=len(self._done_already),
            not_processed=tuple((str(s.path), s.reason) for s in self.inventory.skipped),
            warnings=tuple(warnings),
            seconds=time.monotonic() - started,
        )
        self._emit(Event("finished", report.to_dict()))
        return report

    def _ensure_model(self) -> None:
        if self._options.model_factory != DEFAULT_FACTORY:
            return  # a custom (test) model brings its own weights
        if downloaded_model_path(self._options.model) is None:
            self._emit(Event("model_download", {"model": self._options.model}))
            ensure_model(self._options.model)
            self._emit(Event("model_ready", {"model": self._options.model}))
