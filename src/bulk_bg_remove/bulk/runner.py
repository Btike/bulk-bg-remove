"""Phase 3 — run: N worker processes pull images from one shared queue.

Each worker loads the model once and processes one image at a time. The
dispatcher hands an image to a worker only when that worker is idle and free
RAM is above the pause threshold, so a busy machine pauses the run instead of
swapping. A broken file fails on its own; the rest of the batch carries on.
Outputs are written to a temporary name and renamed when complete, so an
interrupted run never leaves a half-written file that looks finished.
"""

from __future__ import annotations

import importlib
import multiprocessing as mp
import os
import queue
import signal
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from pathlib import Path

from ..core.matting import MATTING_MAX_GB, MATTING_MIN_GB, EdgeMode, fullres_matting_gb
from ..core.pipeline import Stage, process_image
from ..domain import OutputFormat
from ..engine.model import MaskModel
from .events import Event, EventSink, JSONObject
from .plan import RESERVE_RAM_GB
from .scan import available_ram_gb, process_rss_gb

DEFAULT_FACTORY = "bulk_bg_remove.engine.model:BackgroundRemover"

# Progress (%) at the start of each stage, for the per-image progress bars.
STAGE_PROGRESS: dict[Stage, int] = {
    Stage.LOADING: 2,
    Stage.REMOVING: 10,
    Stage.REFINING: 70,
    Stage.COMPOSITING: 88,
    Stage.SAVING: 92,
}


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    source: Path
    destination: Path


@dataclass(frozen=True, slots=True)
class RunSettings:
    model: str
    output_format: OutputFormat
    edges: EdgeMode
    workers: int
    threads_per_worker: int
    pause_below_gb: float
    pause_timeout_s: float = 600.0
    model_factory: str = DEFAULT_FACTORY
    # Restart a worker whose memory grew past this after an image (None: never).
    # Images of different sizes fragment the heap, so a long-lived worker's RSS
    # creeps up to ~3x a single image's peak; a fresh process starts clean.
    recycle_above_gb: float | None = None
    # ...but only while free RAM is below this (None: always). Reloading a model
    # costs ~2 s, so a worker keeps its grown heap while the machine has room.
    recycle_when_free_below_gb: float | None = None


@dataclass(frozen=True, slots=True)
class JobOutcome:
    job: Job
    ok: bool
    seconds: float
    width: int | None = None
    height: int | None = None
    reason: str | None = None
    note: str | None = None


# Messages from workers are plain tuples so they pickle cheaply:
#   ("ready", worker) | ("init_failed", worker, reason) | ("stage", worker, job_id, stage)
#   ("done", worker, job_id, width, height, seconds, note)
#   ("failed", worker, job_id, reason, seconds)
WorkerMessage = tuple[str | int | float, ...]


def _load_factory(spec: str) -> Callable[[str], MaskModel]:
    module_name, _, attr = spec.partition(":")
    factory: Callable[[str], MaskModel] = getattr(importlib.import_module(module_name), attr)
    return factory


def _temp_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.part")


def _stage_reporter(
    messages: Queue[WorkerMessage], worker: int, job_id: int
) -> Callable[[Stage], None]:
    def report(stage: Stage) -> None:
        messages.put(("stage", worker, job_id, stage.value))

    return report


def _matting_budget_gb(megapixels: float, edges: EdgeMode) -> float:
    """What edge matting may use now: free RAM minus the reserve and the
    full-resolution step, within its bounds."""
    free = available_ram_gb() - RESERVE_RAM_GB - fullres_matting_gb(megapixels, edges)
    return min(MATTING_MAX_GB, max(MATTING_MIN_GB, free))


def _should_retire(settings: RunSettings) -> bool:
    limit = settings.recycle_above_gb
    if limit is None:
        return False
    try:
        if process_rss_gb() <= limit:
            return False
        tight = settings.recycle_when_free_below_gb
        return tight is None or available_ram_gb() < tight
    except Exception:  # noqa: BLE001 — a failed probe must not lose a finished image
        return False


def worker_main(
    worker: int,
    tasks: Queue[tuple[int, str, str] | None],
    messages: Queue[WorkerMessage],
    settings: RunSettings,
) -> None:
    """Entry point of a worker process."""
    # Ctrl+C is handled by the parent, which lets in-flight images finish cleanly.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # stdout belongs to the parent (`--json` lines). Libraries such as pymatting
    # print harmless warnings there ("incomplete Cholesky ... failed"), so this
    # process's stdout goes nowhere; real errors still reach stderr.
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.close(devnull)
    # rembg sizes ONNX threads from this; without it N workers oversubscribe the CPU.
    os.environ["OMP_NUM_THREADS"] = str(settings.threads_per_worker)
    try:
        model = _load_factory(settings.model_factory)(settings.model)
    except Exception as exc:  # noqa: BLE001 — reported to the user, not swallowed
        messages.put(("init_failed", worker, f"{type(exc).__name__}: {exc}"))
        return
    messages.put(("ready", worker))

    while (task := tasks.get()) is not None:
        job_id, source, destination = task
        started = time.monotonic()
        final = Path(destination)
        temp = _temp_path(final)
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            result = process_image(
                model,
                Path(source),
                temp,
                output_format=settings.output_format,
                edges=settings.edges,
                on_stage=_stage_reporter(messages, worker, job_id),
                matting_budget_gb=_matting_budget_gb,
            )
            os.replace(temp, final)
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the batch
            temp.unlink(missing_ok=True)
            reason = f"{type(exc).__name__}: {exc}"
            retire = _should_retire(settings)
            elapsed = time.monotonic() - started
            messages.put(("failed", worker, job_id, reason, elapsed, retire))
        else:
            elapsed = time.monotonic() - started
            note = result.note or ""
            retire = _should_retire(settings)
            messages.put(
                ("done", worker, job_id, result.width, result.height, elapsed, note, retire)
            )
        if retire:
            return  # the runner starts a fresh process for this worker slot


class Runner:
    """Runs jobs across worker processes and reports progress as events."""

    def __init__(
        self,
        jobs: Sequence[Job],
        settings: RunSettings,
        emit: EventSink,
        *,
        ram_probe: Callable[[], float] = available_ram_gb,
        poll_seconds: float = 0.25,
    ) -> None:
        self._jobs = {job.id: job for job in jobs}
        self._pending: deque[Job] = deque(jobs)
        self._settings = settings
        self._emit = emit
        self._ram_probe = ram_probe
        self._poll = poll_seconds
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Stop handing out new images; images already in progress finish."""
        self._cancel.set()

    def _finished(
        self, kind: str, job: Job, outcomes: dict[int, JobOutcome], data: JSONObject
    ) -> None:
        """job_done / job_failed, with the source and overall progress for agents."""
        self._emit(
            Event(
                kind,
                {
                    "job": job.id,
                    **data,
                    "source": str(job.source),
                    "done": len(outcomes),
                    "total": len(self._jobs),
                },
            )
        )

    def run(self) -> list[JobOutcome]:
        ctx = mp.get_context("spawn")
        messages: Queue[WorkerMessage] = ctx.Queue()
        count = max(1, min(self._settings.workers, len(self._jobs)))
        inboxes: list[Queue[tuple[int, str, str] | None]] = [ctx.Queue() for _ in range(count)]

        def start(worker: int) -> BaseProcess:
            process = ctx.Process(
                target=worker_main,
                args=(worker, inboxes[worker], messages, self._settings),
                name=f"bulk-bg-remove-worker-{worker}",
                daemon=True,
            )
            process.start()
            self._emit(Event("worker_loading", {"worker": worker}))
            return process

        processes: list[BaseProcess] = [start(worker) for worker in range(count)]
        retiring: set[int] = set()

        outcomes: dict[int, JobOutcome] = {}
        idle: list[int] = []
        busy: dict[int, Job] = {}
        alive = set(range(count))
        stopped: set[int] = set()
        paused_since: float | None = None

        def stop_worker(worker: int) -> None:
            if worker not in stopped:
                inboxes[worker].put(None)
                stopped.add(worker)

        def fail_pending(reason: str) -> None:
            while self._pending:
                job = self._pending.popleft()
                outcomes[job.id] = JobOutcome(job, ok=False, seconds=0.0, reason=reason)
                self._finished("job_failed", job, outcomes, {"worker": None, "reason": reason})

        try:
            while alive:
                try:
                    message = messages.get(timeout=self._poll)
                except queue.Empty:
                    message = None
                if message is not None:
                    self._handle(message, idle, busy, alive, outcomes, retiring)
                for worker in sorted(retiring):
                    retiring.discard(worker)
                    old = processes[worker]
                    old.join(timeout=30)
                    if old.is_alive():
                        old.terminate()
                        old.join(timeout=5)
                    if self._pending and not self._cancel.is_set():
                        processes[worker] = start(worker)
                    else:
                        alive.discard(worker)  # nothing left for it to do

                # A worker that died without reporting (e.g. killed by the OS for memory).
                for worker in list(alive):
                    if not processes[worker].is_alive() and messages.empty():
                        alive.discard(worker)
                        if worker in idle:
                            idle.remove(worker)
                        job = busy.pop(worker, None)
                        if job is not None:
                            reason = "worker process crashed (out of memory?)"
                            outcomes[job.id] = JobOutcome(job, ok=False, seconds=0.0, reason=reason)
                            self._finished(
                                "job_failed", job, outcomes, {"worker": worker, "reason": reason}
                            )

                if self._cancel.is_set() and self._pending:
                    fail_pending("cancelled")

                # Dispatch while there is an idle worker, work left, and RAM to spare.
                while idle and self._pending:
                    free = self._ram_probe()
                    if free < self._settings.pause_below_gb:
                        if paused_since is None:
                            paused_since = time.monotonic()
                            self._emit(
                                Event(
                                    "paused",
                                    {
                                        "free_ram_gb": round(free, 2),
                                        "needed_gb": round(self._settings.pause_below_gb, 2),
                                    },
                                )
                            )
                        elif (
                            not busy
                            and time.monotonic() - paused_since > self._settings.pause_timeout_s
                        ):
                            fail_pending("not enough free RAM (pause timed out)")
                        break
                    if paused_since is not None:
                        paused_since = None
                        self._emit(Event("resumed", {"free_ram_gb": round(free, 2)}))
                    worker = idle.pop(0)
                    job = self._pending.popleft()
                    busy[worker] = job
                    inboxes[worker].put((job.id, str(job.source), str(job.destination)))
                    self._emit(
                        Event(
                            "job_started",
                            {
                                "job": job.id,
                                "worker": worker,
                                "source": str(job.source),
                                "destination": str(job.destination),
                            },
                        )
                    )

                if not self._pending:
                    for worker in list(idle):
                        stop_worker(worker)
                        idle.remove(worker)
        finally:
            for worker in range(count):
                stop_worker(worker)
            for proc in processes:
                proc.join(timeout=10)
                if proc.is_alive():
                    proc.terminate()

        # Workers that never became ready leave their jobs unprocessed.
        fail_pending("no worker could load the model")
        return [outcomes[job_id] for job_id in self._jobs if job_id in outcomes]

    def _handle(
        self,
        message: WorkerMessage,
        idle: list[int],
        busy: dict[int, Job],
        alive: set[int],
        outcomes: dict[int, JobOutcome],
        retiring: set[int],
    ) -> None:
        kind, worker = str(message[0]), int(message[1])
        if kind == "ready":
            idle.append(worker)
            self._emit(Event("worker_ready", {"worker": worker}))
        elif kind == "init_failed":
            alive.discard(worker)
            self._emit(Event("worker_failed", {"worker": worker, "reason": str(message[2])}))
        elif kind == "stage":
            stage = Stage(str(message[3]))
            self._emit(
                Event(
                    "job_stage",
                    {
                        "job": int(message[2]),
                        "worker": worker,
                        "stage": stage.value,
                        "progress": STAGE_PROGRESS[stage],
                    },
                )
            )
        elif kind in ("done", "failed"):
            job = self._jobs[int(message[2])]
            busy.pop(worker, None)
            if bool(message[-1]):
                retiring.add(worker)  # it exits now; run() starts its replacement
            else:
                idle.append(worker)
            if kind == "done":
                width, height, seconds = int(message[3]), int(message[4]), float(message[5])
                note = str(message[6]) or None
                outcomes[job.id] = JobOutcome(job, True, seconds, width, height, note=note)
                self._finished(
                    "job_done",
                    job,
                    outcomes,
                    {
                        "worker": worker,
                        "width": width,
                        "height": height,
                        "seconds": round(seconds, 2),
                        "note": note,
                    },
                )
            else:
                reason, seconds = str(message[3]), float(message[4])
                outcomes[job.id] = JobOutcome(job, False, seconds, reason=reason)
                self._finished("job_failed", job, outcomes, {"worker": worker, "reason": reason})
