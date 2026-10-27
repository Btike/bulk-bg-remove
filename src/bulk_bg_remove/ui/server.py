"""Local UI server: drop, paste or type a path -> plan -> run, with live progress.

Security: it listens on 127.0.0.1 only, every API call needs the random token
from the URL the CLI opened, and the Host header must be the local address.
That blocks other websites in the same browser (CSRF, DNS rebinding) from
driving it. Images never leave the machine.
"""

from __future__ import annotations

import asyncio
import json
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

UI_MODELS = ("birefnet-general-lite", "birefnet-general", "isnet-general-use")
PREVIEW_MAX_SIDE = 900
# The page has no background option yet, and JPEG has no transparency.
JPG_IN_UI = "JPG needs a background colour: use PNG or WebP here"


class RunOptions(BaseModel):
    edges: EdgeMode = EdgeMode.MATTE
    model: str = DEFAULT_MODEL
    output_format: OutputFormat = OutputFormat.PNG


class PathRequest(RunOptions):
    path: str


Subscriber = tuple[asyncio.AbstractEventLoop, asyncio.Queue[JSONObject | None]]


@dataclass
class Job:
    id: str
    name: str
    session: BulkSession
    staging: Path | None
    events: list[JSONObject] = field(default_factory=list)
    # Each live SSE stream with the event loop it runs on (events come from worker threads).
    subscribers: list[Subscriber] = field(default_factory=list)
    status: str = "planned"  # planned | running | finished
    files: dict[int, tuple[Path, Path]] = field(default_factory=dict)


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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for job in jobs.values():  # server stopping: stop work, drop uploaded copies
            job.session.cancel()
            if job.staging is not None and job.status != "running":
                shutil.rmtree(job.staging, ignore_errors=True)

    app = FastAPI(
        title="bulk-bg-remove", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    downloads = downloads_dir or Path.home() / "Downloads"
    lock = threading.Lock()
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
        with events_lock:
            job.events.append(payload)
            subscribers = list(job.subscribers)
        if echo is not None:
            echo(event)
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

    def active_job() -> Job | None:
        return next((j for j in jobs.values() if j.status == "running"), None)

    def create_job(
        name: str, input_path: Path, output: Path, options: RunOptions, staging: Path | None
    ) -> JSONObject:
        if options.output_format is OutputFormat.JPG:
            raise HTTPException(400, JPG_IN_UI)
        with lock:
            if active_job() is not None:
                raise HTTPException(409, "a run is already in progress — wait or stop it first")
            for old in [j for j in jobs.values() if j.status != "running"]:
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
            )
            created: list[Job] = []
            session = BulkSession(
                input_path, bulk_options, lambda e: broadcast(created[0], e), output_root=output
            )
            job = Job(job_id, name, session, staging)
            created.append(job)
            jobs[job_id] = job
        try:
            plan = job.session.prepare()
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "job": job_id,
            "name": name,
            "output": str(output),
            "output_display": _display(output),
            "ok": plan.ok,
            "events": list(job.events),
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
        return await asyncio.to_thread(create_job, name, root, output, options, staging)

    @app.post("/api/jobs/{job_id}/start", response_model=None)
    def start(job_id: str) -> JSONObject:
        job = get_job(job_id)
        with lock:
            if active_job() is not None:
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
        job_id: str, image: Annotated[int, Query(alias="image")], side: str = "after"
    ) -> Response:
        job = get_job(job_id)
        if image not in job.files or side not in ("before", "after"):
            raise HTTPException(404, "no such image")
        source, destination = job.files[image]
        try:
            if side == "before":
                picture = load_source(source).rgb
            else:
                with Image.open(destination) as opened:
                    picture = opened.convert("RGBA")
        except (OSError, FileNotFoundError) as exc:
            raise HTTPException(404, "image not available") from exc
        picture.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
        buffer = BytesIO()
        picture.save(buffer, format="PNG" if side == "after" else "JPEG", quality=85)
        media = "image/png" if side == "after" else "image/jpeg"
        return Response(buffer.getvalue(), media_type=media, headers={"Cache-Control": "no-store"})

    return app
