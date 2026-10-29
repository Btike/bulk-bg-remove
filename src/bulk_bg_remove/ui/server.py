"""Local UI server: drop, paste or type a path -> plan -> run, with live progress.

Security: it listens on 127.0.0.1 only, every API call needs the random token
from the URL the CLI opened, and the Host header must be the local address.
That blocks other websites in the same browser (CSRF, DNS rebinding) from
driving it. Images never leave the machine.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from PIL import Image
from pydantic import BaseModel

from ..bulk.events import Event, EventSink, JSONObject, JSONValue
from ..bulk.models import AUTO_MODEL, Profile
from ..bulk.plan import MODEL_PEAK_GB, minimum_free_ram_gb
from ..bulk.runner import DEFAULT_FACTORY
from ..bulk.scan import probe_hardware
from ..bulk.session import BulkOptions, BulkSession
from ..core.io import load_source
from ..core.matting import EdgeMode
from ..domain import OutputFormat
from ..engine.model import DEFAULT_MODEL
from ..formats import SUPPORTED_EXTENSIONS
from ..paths import OUTPUT_SUFFIX
from .redo import Candidate, keep, ranked_models

UI_MODELS = ("birefnet-general-lite", "birefnet-general", "isnet-general-use")
PREVIEW_MAX_SIDE = 900
PREVIEW_MIN_SIDE = 32
PREVIEW_DECODES = 2  # photos decoded at once for previews and close-ups
REDO_STOP_WAIT_S = 30.0  # on shutdown, how long a cancelled "Try again" may take to clean up
ZOOM_MAX_SIDE = 2048  # the viewer's close-up: a crop of the full-resolution image
# Previews of a job never change (job ids are unique, outputs are written atomically).
PREVIEW_CACHE = "private, max-age=3600"
# The page has no background option yet, and JPEG has no transparency.
JPG_IN_UI = "JPG needs a background colour: use PNG or WebP here"


class RunOptions(BaseModel):
    edges: EdgeMode = EdgeMode.MATTE
    model: str = DEFAULT_MODEL
    output_format: OutputFormat = OutputFormat.PNG


class PathRequest(RunOptions):
    path: str


class RedoRequest(BaseModel):
    image: int
    profile: Profile | None = None
    model: str | None = None
    edges: EdgeMode | None = None


class CandidateRequest(BaseModel):
    image: int
    candidate: str


Subscriber = tuple[asyncio.AbstractEventLoop, asyncio.Queue[JSONObject | None]]


@dataclass
class Job:
    id: str
    name: str
    session: BulkSession
    staging: Path | None
    input_path: Path
    options: RunOptions
    events: list[JSONObject] = field(default_factory=list)
    # Each live SSE stream with the event loop it runs on (events come from worker threads).
    subscribers: list[Subscriber] = field(default_factory=list)
    status: str = "planned"  # planned | running | finished
    files: dict[int, tuple[Path, Path]] = field(default_factory=dict)
    # Folders of review sheets the engine wrote: the page does not show them.
    review_dirs: set[Path] = field(default_factory=set)
    # "Try again" cut-outs waiting to be kept or discarded, one per image at most.
    candidates: dict[int, Candidate] = field(default_factory=dict)

    def drop_candidates(self) -> None:
        for candidate in self.candidates.values():
            candidate.drop()
        self.candidates.clear()


def reveal_in_file_manager(path: Path) -> None:
    if sys.platform == "darwin":
        command = ["open", str(path)]
    elif sys.platform == "win32":
        command = ["explorer", str(path)]
    else:
        command = ["xdg-open", str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _safe_relative(raw: str) -> PurePosixPath:
    """A browser-supplied relative path, refusing anything that could escape staging."""
    path = PurePosixPath(raw.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(p in ("", ".", "..") for p in path.parts):
        raise HTTPException(400, f"invalid file path: {raw!r}")
    return path


def _display(path: Path) -> str:
    """Shorten the home folder to ~ for display."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return path.name
    return relative if relative != "." else path.name


def _drop_review_sheets(job: Job) -> None:
    """The UI certifies cut-outs (that needs review sheets) but never shows the
    sheets: delete them so a UI run leaves nothing in the temp folder."""
    for folder in job.review_dirs:
        shutil.rmtree(folder, ignore_errors=True)
    job.review_dirs.clear()


def _load_side(job: Job, image: int, side: str) -> Image.Image:
    """The original photo (RGB), the cut-out (RGBA) or the "Try again" candidate
    (RGBA) of one image of a job."""
    if image not in job.files or side not in ("before", "after", "candidate"):
        raise HTTPException(404, "no such image")
    source, destination = job.files[image]
    if side == "candidate":
        candidate = job.candidates.get(image)
        if candidate is None or candidate.status != "done" or candidate.output is None:
            raise HTTPException(404, "no new cut-out for this image")
        destination = candidate.output
    try:
        if side == "before":
            return load_source(source).rgb
        with Image.open(destination) as opened:
            return opened.convert("RGBA")
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(404, "image not available") from exc


def _image_response(picture: Image.Image, side: str, headers: dict[str, str]) -> Response:
    buffer = BytesIO()
    if side != "before":
        picture.save(buffer, format="PNG")
        media = "image/png"
    else:
        picture.save(buffer, format="JPEG", quality=90)
        media = "image/jpeg"
    return Response(buffer.getvalue(), media_type=media, headers=headers)


def _unique_output(parent: Path, name: str) -> Path:
    candidate = parent / f"{name}{OUTPUT_SUFFIX}"
    counter = 2
    while candidate.exists():
        candidate = parent / f"{name}{OUTPUT_SUFFIX}-{counter}"
        counter += 1
    return candidate


def create_app(
    token: str,
    *,
    allowed_hosts: set[str],
    downloads_dir: Path | None = None,
    model_factory: str = DEFAULT_FACTORY,
    min_free_ram_gb: float | None = None,
    echo: EventSink | None = None,
    open_folder: Callable[[Path], None] = reveal_in_file_manager,
) -> FastAPI:
    jobs: dict[str, Job] = {}
    redos: list[Candidate] = []  # "Try again" runs, so only one engine runs at a time

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for job in jobs.values():  # server stopping: stop work, drop uploaded copies
            job.session.cancel()
            job.drop_candidates()
            if job.staging is not None and job.status != "running":
                shutil.rmtree(job.staging, ignore_errors=True)
        for candidate in redos:  # a redo still running deletes its folder when it ends
            if candidate.thread is not None:
                await asyncio.to_thread(candidate.thread.join, REDO_STOP_WAIT_S)

    app = FastAPI(
        title="bulk-bg-remove", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    downloads = downloads_dir or Path.home() / "Downloads"
    lock = threading.Lock()
    # A full photo decoded per preview: a page asking for dozens of thumbnails at once
    # must not take the RAM the run needs.
    decoding = threading.BoundedSemaphore(PREVIEW_DECODES)
    events_lock = threading.Lock()  # history + subscriber list must change together
    page = resources.files("bulk_bg_remove.ui").joinpath("static/index.html").read_text("utf-8")

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host", "") not in allowed_hosts:
            return Response("forbidden host", status_code=403)
        supplied = request.headers.get("x-token") or request.query_params.get("t")
        if supplied != token:
            return Response("missing or wrong token", status_code=403)
        return await call_next(request)

    def broadcast(job: Job, event: Event) -> None:
        payload = event.to_dict()
        if event.type == "job_started":
            job_id = int(str(payload["job"]))
            job.files[job_id] = (Path(str(payload["source"])), Path(str(payload["destination"])))
        elif event.type == "review_sheets":
            sheets = payload["sheets"] if isinstance(payload["sheets"], list) else []
            for sheet in sheets:
                if isinstance(sheet, dict):
                    job.review_dirs.add(Path(str(sheet["path"])).parent)
        elif event.type == "finished":
            _drop_review_sheets(job)
        with events_lock:
            job.events.append(payload)
            subscribers = list(job.subscribers)
        if echo is not None:
            echo(event)
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

    def active_job() -> Job | None:
        return next((j for j in jobs.values() if j.status == "running"), None)

    def redo_running() -> bool:
        # Includes discarded ones still finishing their image: one engine at a time.
        redos[:] = [c for c in redos if c.status == "running"]
        return bool(redos)

    def create_job(
        name: str, input_path: Path, output: Path, options: RunOptions, staging: Path | None
    ) -> JSONObject:
        if options.output_format is OutputFormat.JPG:
            raise HTTPException(400, JPG_IN_UI)
        with lock:
            if active_job() is not None:
                raise HTTPException(409, "a run is already in progress — wait or stop it first")
            if redo_running():
                raise HTTPException(409, "a photo is being redone: wait for it to finish")
            for old in [j for j in jobs.values() if j.status != "running"]:
                old.drop_candidates()
                if old.staging is not None:
                    shutil.rmtree(old.staging, ignore_errors=True)
                jobs.pop(old.id)
            job_id = f"{int(time.time() * 1000):x}"
            bulk_options = BulkOptions(
                model=options.model,
                output_format=options.output_format,
                edges=options.edges,
                model_factory=model_factory,
                min_free_ram_gb=min_free_ram_gb,
                # Certifies each cut-out, so the page can tell "passed" from "needs a
                # look". The sheets this also writes are deleted when the run ends.
                review_sheets=True,
            )
            created: list[Job] = []
            session = BulkSession(
                input_path, bulk_options, lambda e: broadcast(created[0], e), output_root=output
            )
            job = Job(job_id, name, session, staging, input_path, options)
            created.append(job)
            jobs[job_id] = job
        try:
            plan = job.session.prepare()
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        # Known before the run starts, so the page can show the queue with previews.
        job.files = dict(enumerate(job.session.queue))
        inventory = job.session.inventory
        root = inventory.input_root if inventory is not None else input_path
        queue: list[JSONValue] = [
            {"job": index, "name": _relative(source, root)}
            for index, (source, _) in job.files.items()
        ]
        return {
            "job": job_id,
            "name": name,
            "output": str(output),
            "output_display": _display(output),
            "ok": plan.ok,
            "events": list(job.events),
            "queue": queue,
        }

    def get_job(job_id: str) -> Job:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/api/system", response_model=None)
    def system() -> JSONObject:
        hardware = probe_hardware()
        models: list[JSONValue] = [
            {
                "id": model,
                "peak_ram_gb": MODEL_PEAK_GB.get(model),
                "min_free_ram_gb": round(minimum_free_ram_gb(model), 1),
            }
            for model in UI_MODELS
        ]
        return {
            "hardware": hardware.to_dict(),
            "models": models,
            "default_model": DEFAULT_MODEL,
            "extensions": list(SUPPORTED_EXTENSIONS),
            "downloads": _display(downloads),
            "busy": active_job() is not None,
        }

    @app.post("/api/jobs/path", response_model=None)
    def from_path(request: PathRequest) -> JSONObject:
        path = Path(request.path.strip().strip("'\"")).expanduser()
        if not path.exists():
            raise HTTPException(400, f"no such file or folder: {path}")
        resolved = path.resolve()
        name = resolved.stem if resolved.is_file() else resolved.name
        output = resolved.parent / f"{name}{OUTPUT_SUFFIX}"
        return create_job(name, resolved, output, request, None)

    @app.post("/api/jobs/upload", response_model=None)
    async def from_upload(
        files: Annotated[list[UploadFile], File()],
        paths: Annotated[list[str], Form()],
        kind: Annotated[str, Form()] = "files",
        edges: Annotated[EdgeMode, Form()] = EdgeMode.MATTE,
        model: Annotated[str, Form()] = DEFAULT_MODEL,
        output_format: Annotated[OutputFormat, Form()] = OutputFormat.PNG,
    ) -> JSONObject:
        if not files or len(files) != len(paths):
            raise HTTPException(400, "files and paths must match")
        if output_format is OutputFormat.JPG:
            raise HTTPException(400, JPG_IN_UI)
        relatives = [_safe_relative(p) for p in paths]
        staging = Path(tempfile.mkdtemp(prefix="bulk-bg-remove-ui-"))
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        tops = {r.parts[0] for r in relatives}
        if kind == "folder" and len(tops) == 1 and all(len(r.parts) > 1 for r in relatives):
            # One dropped folder: keep its layout, name the output after it.
            name = next(iter(tops))
            root, targets = staging / name, [staging / r for r in relatives]
        elif kind == "paste":
            name = f"pasted-{stamp}"
            root = staging / "pasted"
            targets = [root / r.name for r in relatives]
        elif len(relatives) == 1:
            name = PurePosixPath(relatives[0].name).stem or "photo"
            root = staging / relatives[0].name
            targets = [root]
        else:
            name = f"batch-{stamp}"
            root = staging / "batch"
            targets = [root / r for r in relatives]
        for upload, target in zip(files, targets, strict=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                while chunk := await upload.read(1 << 20):
                    handle.write(chunk)
        output = _unique_output(downloads, name)
        options = RunOptions(edges=edges, model=model, output_format=output_format)
        try:
            return await asyncio.to_thread(create_job, name, root, output, options, staging)
        except HTTPException:
            # Refused (busy, blocked input): the copies would otherwise stay behind.
            if not any(job.staging == staging for job in jobs.values()):
                shutil.rmtree(staging, ignore_errors=True)
            raise

    @app.post("/api/jobs/{job_id}/start", response_model=None)
    def start(job_id: str) -> JSONObject:
        job = get_job(job_id)
        with lock:
            if active_job() is not None or redo_running():
                raise HTTPException(409, "a run is already in progress")
            if job.status != "planned":
                raise HTTPException(409, "this run already started")
            plan = job.session.plan
            if plan is None or not plan.ok:
                raise HTTPException(
                    409, "the plan is blocked: " + "; ".join(plan.blockers if plan else ())
                )
            job.status = "running"

        def work() -> None:
            try:
                broadcast(job, Event("run_started", {"images": plan.images}))
                job.session.execute()
            except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
                broadcast(job, Event("error", {"message": f"{type(exc).__name__}: {exc}"}))
            finally:
                _drop_review_sheets(job)
                with events_lock:
                    job.status = "finished"
                    subscribers = list(job.subscribers)
                for loop, queue in subscribers:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=work, name=f"run-{job_id}", daemon=True).start()
        return {"job": job_id, "status": job.status}

    @app.post("/api/jobs/{job_id}/cancel", response_model=None)
    def cancel(job_id: str) -> JSONObject:
        job = get_job(job_id)
        job.session.cancel()
        return {"job": job_id, "status": "cancelling"}

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str) -> StreamingResponse:
        job = get_job(job_id)
        queue: asyncio.Queue[JSONObject | None] = asyncio.Queue()
        subscriber: Subscriber = (asyncio.get_running_loop(), queue)
        with events_lock:
            backlog = list(job.events)
            job.subscribers.append(subscriber)
            finished = job.status == "finished"

        async def stream() -> AsyncIterator[str]:
            try:
                for payload in backlog:
                    yield f"data: {json.dumps(payload)}\n\n"
                if finished:
                    return
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if item is None:
                        return
                    yield f"data: {json.dumps(item)}\n\n"
            finally:
                with events_lock:
                    job.subscribers.remove(subscriber)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/jobs/{job_id}/open", response_model=None)
    def open_output(job_id: str) -> JSONObject:
        output = get_job(job_id).session.output_root
        if not output.is_dir():
            raise HTTPException(404, "the output folder does not exist yet")
        open_folder(output)
        return {"opened": str(output)}

    @app.get("/api/jobs/{job_id}/zip")
    def download_zip(job_id: str) -> FileResponse:
        job = get_job(job_id)
        output = job.session.output_root
        if not output.is_dir():
            raise HTTPException(404, "nothing to download yet")
        archive = Path(tempfile.mkdtemp(prefix="bulk-bg-remove-zip-")) / f"{output.name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as bundle:
            for file in sorted(output.rglob("*")):
                if file.is_file() and not file.name.startswith("."):
                    bundle.write(file, file.relative_to(output.parent))
        return FileResponse(archive, filename=archive.name, media_type="application/zip")

    @app.get("/api/jobs/{job_id}/preview")
    def preview(
        job_id: str,
        image: Annotated[int, Query(alias="image")],
        side: str = "after",
        size: Annotated[int, Query(ge=PREVIEW_MIN_SIDE, le=PREVIEW_MAX_SIDE)] = PREVIEW_MAX_SIDE,
    ) -> Response:
        """A downscaled copy for display (thumbnails, the viewer): never the output."""
        job = get_job(job_id)
        with decoding:
            picture = _load_side(job, image, side)
            picture.thumbnail((size, size))
        return _image_response(picture, side, {"Cache-Control": PREVIEW_CACHE})

    @app.get("/api/jobs/{job_id}/zoom")
    def zoom(
        job_id: str,
        image: Annotated[int, Query(alias="image")],
        x: Annotated[float, Query(ge=0, le=1)],
        y: Annotated[float, Query(ge=0, le=1)],
        w: Annotated[int, Query(ge=1, le=ZOOM_MAX_SIDE)],
        h: Annotated[int, Query(ge=1, le=ZOOM_MAX_SIDE)],
        side: str = "after",
    ) -> Response:
        """A full-resolution crop for the viewer's close-up: up to w x h pixels centred
        on (x, y) (fractions of the width and height), kept inside the image. The
        region it covers comes back in X-Region: left top width height."""
        job = get_job(job_id)
        with decoding:
            picture = _load_side(job, image, side)
            full_w, full_h = picture.size
            w, h = min(w, full_w), min(h, full_h)
            left = min(max(round(x * full_w - w / 2), 0), full_w - w)
            top = min(max(round(y * full_h - h / 2), 0), full_h - h)
            crop = picture.crop((left, top, left + w, top + h))
            del picture
        headers = {
            "Cache-Control": PREVIEW_CACHE,
            "X-Region": f"{left} {top} {w} {h}",
            "X-Image-Size": f"{full_w} {full_h}",
        }
        return _image_response(crop, side, headers)

    def finished_job(job_id: str, image: int) -> Job:
        job = get_job(job_id)
        if job.status != "finished":
            raise HTTPException(409, "the run has not finished yet")
        if image not in job.files:
            raise HTTPException(404, "no such image")
        return job

    @app.get("/api/jobs/{job_id}/redo/options", response_model=None)
    def redo_options(job_id: str, image: Annotated[int, Query()]) -> JSONObject:
        """Models "Try again" can offer for this photo, and which fit in free RAM now."""
        job = finished_job(job_id, image)
        source = job.files[image][0]
        inventory = job.session.inventory
        found = [s for s in inventory.sources if s.path == source] if inventory else []
        megapixels = found[0].megapixels if found else 12.0
        free = probe_hardware().ram_available_gb
        return {
            "free_ram_gb": free,
            "current_model": job.session.model,
            "models": ranked_models(free, job.options.edges, megapixels),
        }

    @app.post("/api/jobs/{job_id}/redo", response_model=None)
    def redo(job_id: str, request: RedoRequest) -> JSONObject:
        """Redo one image with other settings into a temporary folder (the CLI's
        `--only` with `-o <temp>`), certified like the run. Poll GET .../redo."""
        job = finished_job(job_id, request.image)
        model = request.model or (AUTO_MODEL if request.profile else job.options.model)
        if model != AUTO_MODEL and model not in MODEL_PEAK_GB:
            raise HTTPException(400, f"unknown model: {model}")
        source = job.files[request.image][0]
        options = BulkOptions(
            model=model,
            output_format=job.options.output_format,
            edges=request.edges or job.options.edges,
            model_factory=model_factory,
            min_free_ram_gb=min_free_ram_gb,
            profile=request.profile or Profile.UNIFORM,
            only=(str(source),),
            review_sheets=True,
        )
        label = ", ".join(
            part
            for part in (
                f"{request.profile.value} profile" if request.profile else "",
                f"{request.edges.value} edges" if request.edges else "",
                request.model or "",
            )
            if part
        )
        with lock:
            if active_job() is not None:
                raise HTTPException(409, "a run is in progress: try again when it ends")
            if redo_running():
                raise HTTPException(409, "another photo is being redone: wait for it")
            folder = Path(tempfile.mkdtemp(prefix="bulk-bg-remove-redo-"))
            candidate = Candidate(secrets.token_hex(6), request.image, folder, label or "again")
            session = BulkSession(
                job.input_path, options, candidate.on_event, output_root=folder / "redo-no-bg"
            )
            try:
                plan = session.prepare()
            except (FileNotFoundError, ValueError) as exc:
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(400, str(exc)) from exc
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            if not plan.ok:
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(409, "; ".join(plan.blockers))
            candidate.session, candidate.model = session, session.model
            previous = job.candidates.pop(request.image, None)
            if previous is not None:
                previous.drop()
            job.candidates[request.image] = candidate
            redos.append(candidate)
        candidate.thread = threading.Thread(
            target=candidate.execute, name=f"redo-{job_id}", daemon=True
        )
        candidate.thread.start()
        return candidate.to_dict()

    @app.get("/api/jobs/{job_id}/redo", response_model=None)
    def redo_status(job_id: str, image: Annotated[int, Query()]) -> JSONObject:
        candidate = get_job(job_id).candidates.get(image)
        if candidate is None:
            raise HTTPException(404, "no new cut-out for this image")
        return candidate.to_dict()

    def take_candidate(job_id: str, request: CandidateRequest) -> tuple[Job, Candidate]:
        job = finished_job(job_id, request.image)
        candidate = job.candidates.get(request.image)
        if candidate is None or candidate.id != request.candidate:
            raise HTTPException(404, "no such new cut-out")
        return job, candidate

    @app.post("/api/jobs/{job_id}/keep", response_model=None)
    def keep_new(job_id: str, request: CandidateRequest) -> JSONObject:
        """Replace the image's output with the new cut-out, in one atomic step."""
        job, candidate = take_candidate(job_id, request)
        if candidate.status != "done" or candidate.output is None:
            raise HTTPException(409, "the new cut-out is not ready")
        destination = job.files[request.image][1]
        if candidate.output.suffix != destination.suffix:
            raise HTTPException(409, "the new cut-out has another file format")
        with lock:
            if active_job() is not None:
                raise HTTPException(409, "a run is in progress: keep it when the run ends")
            if job.candidates.get(request.image) is not candidate:
                raise HTTPException(404, "no such new cut-out")  # replaced meanwhile
            try:
                keep(candidate, destination)
            except OSError as exc:
                raise HTTPException(500, f"could not replace the cut-out: {exc}") from exc
            del job.candidates[request.image]
        candidate.drop()
        return {"image": request.image, "certified": candidate.certified, "check": candidate.check}

    @app.post("/api/jobs/{job_id}/discard", response_model=None)
    def discard(job_id: str, request: CandidateRequest) -> JSONObject:
        job, candidate = take_candidate(job_id, request)
        with lock:
            if job.candidates.get(request.image) is candidate:
                del job.candidates[request.image]
        candidate.drop()
        return {"image": request.image, "discarded": candidate.id}

    return app
