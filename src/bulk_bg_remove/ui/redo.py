"""Try again: redo one image of a finished UI run with other settings, then keep
the better cut-out. The same loop the agent skill runs with `--only` and `-o` to a
temporary folder: the engine makes the new cut-out (so the quality contract holds),
and keeping it swaps one output file. Originals are never touched."""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..bulk.events import Event, JSONObject, JSONValue
from ..bulk.models import PROFILE_SCORES, Profile
from ..bulk.plan import minimum_free_ram_gb
from ..bulk.session import BulkSession
from ..core.matting import EdgeMode

# Models offered under "Other model": the ones every photo profile of the bench ranks.
PHOTO_PROFILES = (Profile.UNIFORM, Profile.COMPLEX, Profile.PEOPLE, Profile.SCENE)
REDO_MODELS: tuple[str, ...] = tuple(
    sorted(
        set.intersection(*(set(PROFILE_SCORES[p]) for p in PHOTO_PROFILES)),
        key=lambda m: -sum(PROFILE_SCORES[p][m] for p in PHOTO_PROFILES),
    )
)


def ranked_models(free_ram_gb: float, edges: EdgeMode, megapixels: float) -> list[JSONValue]:
    """The bench's photo models, best average score first, with the free RAM each
    needs for this photo and whether it fits now."""
    out: list[JSONValue] = []
    for model in REDO_MODELS:
        score = sum(PROFILE_SCORES[p][model] for p in PHOTO_PROFILES) / len(PHOTO_PROFILES)
        needs = minimum_free_ram_gb(model, edges, megapixels)
        out.append(
            {
                "id": model,
                "score": round(score, 1),
                "needs_gb": round(needs, 1),
                "fits": needs <= free_ram_gb,
            }
        )
    return out


@dataclass
class Candidate:
    """A new cut-out of one image, waiting in its own temporary folder."""

    id: str
    image: int
    folder: Path
    label: str
    status: str = "running"  # running | done | failed
    stage: str = "starting"
    progress: int = 0
    model: str = ""
    output: Path | None = None
    certified: bool | None = None
    check: str | None = None
    reason: str | None = None
    session: BulkSession | None = None
    thread: threading.Thread | None = None
    dropped: bool = False
    review_dirs: set[Path] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def on_event(self, event: Event) -> None:
        data = event.data
        if event.type == "model_download":
            self.stage = "downloading the model (first time only)"
        elif event.type == "worker_loading":
            self.stage = "loading the model"
        elif event.type == "job_started":
            self.output = Path(str(data["destination"]))
            self.stage = "loading"
        elif event.type == "job_stage":
            self.stage = str(data["stage"])
            progress = data["progress"]
            self.progress = int(progress) if isinstance(progress, int | float) else 0
        elif event.type == "job_done":
            certified = data.get("certified")
            self.certified = certified if isinstance(certified, bool) else None
            check = data.get("check")
            self.check = str(check) if check is not None else None
            self.stage, self.progress = "checking", 96
        elif event.type == "job_failed":
            self.reason = str(data["reason"])
        elif event.type == "review_sheets":
            sheets = data["sheets"] if isinstance(data["sheets"], list) else []
            for sheet in sheets:
                if isinstance(sheet, dict):
                    self.review_dirs.add(Path(str(sheet["path"])).parent)
        elif event.type == "finished":
            failed = data["failed"] if isinstance(data["failed"], list) else []
            first = failed[0] if failed and isinstance(failed[0], dict) else None
            if first is not None:
                self.reason = self.reason or str(first.get("reason", "failed"))
            elif self.output is None or not self.output.is_file():
                self.reason = self.reason or "no cut-out was written"

    def execute(self) -> None:
        """Runs on its own thread: the engine's run phases for this one image."""
        try:
            if self.session is not None:
                self.session.execute()
        except Exception as exc:  # noqa: BLE001 — shown in the viewer, not swallowed
            self.reason = f"{type(exc).__name__}: {exc}"
        finally:
            for folder in self.review_dirs:
                shutil.rmtree(folder, ignore_errors=True)
            with self.lock:
                self.status = "failed" if self.reason else "done"
                if not self.reason:
                    self.progress = 100
                dropped = self.dropped
            if dropped:
                shutil.rmtree(self.folder, ignore_errors=True)

    def drop(self) -> None:
        """Forget this candidate: delete its folder now, or when its run ends."""
        with self.lock:
            self.dropped = True
            running = self.status == "running"
        if running and self.session is not None:
            self.session.cancel()
        if not running:
            shutil.rmtree(self.folder, ignore_errors=True)

    def to_dict(self) -> JSONObject:
        return {
            "candidate": self.id,
            "image": self.image,
            "label": self.label,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "model": self.model,
            "certified": self.certified,
            "check": self.check,
            "reason": self.reason,
        }


def keep(candidate: Candidate, destination: Path) -> None:
    """Replace `destination` with the candidate's cut-out in one step: the copy is
    written next to it first (same folder, so the final rename is atomic)."""
    if candidate.output is None:
        raise FileNotFoundError("the new cut-out is missing")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.keep-{candidate.id}")
    try:
        shutil.copyfile(candidate.output, partial)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
