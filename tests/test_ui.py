"""Local UI server: security guard, the three ways in, and a full run over SSE."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from bulk_bg_remove.ui.server import create_app
from fakes import FAKE_FACTORY

TOKEN = "test-token"


@pytest.fixture
def opened() -> list[Path]:
    return []


@pytest.fixture
def client(tmp_path: Path, opened: list[Path]) -> TestClient:
    app = create_app(
        TOKEN,
        allowed_hosts={"testserver"},
        downloads_dir=tmp_path / "Downloads",
        model_factory=FAKE_FACTORY,
        min_free_ram_gb=0.1,
        open_folder=opened.append,
    )
    (tmp_path / "Downloads").mkdir()
    return TestClient(app, headers={"X-Token": TOKEN})


def jpeg(size: tuple[int, int] = (64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (40, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


def upload(client: TestClient, paths: list[str], kind: str) -> dict:
    files = [("files", (Path(p).name, jpeg(), "image/jpeg")) for p in paths]
    data = {"paths": paths, "kind": kind}
    response = client.post("/api/jobs/upload", files=files, data=data)
    assert response.status_code == 200, response.text
    return response.json()


def run_to_end(client: TestClient, job: str, *, start: bool = True) -> list[dict]:
    if start:
        assert client.post(f"/api/jobs/{job}/start").status_code == 200
    events = []
    with client.stream("GET", f"/api/jobs/{job}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1]["type"] == "finished":
                    break
    return events


def test_rejects_missing_token(tmp_path: Path) -> None:
    app = create_app(TOKEN, allowed_hosts={"testserver"}, downloads_dir=tmp_path)
    assert TestClient(app).get("/api/system").status_code == 403


def test_rejects_foreign_host(tmp_path: Path) -> None:
    app = create_app(TOKEN, allowed_hosts={"127.0.0.1:9999"}, downloads_dir=tmp_path)
    response = TestClient(app, headers={"X-Token": TOKEN}).get("/api/system")
    assert response.status_code == 403  # e.g. a DNS-rebinding page


def test_serves_the_page_and_system_info(client: TestClient) -> None:
    assert "BULK-BG-REMOVE" in client.get("/").text
    info = client.get("/api/system").json()
    assert info["default_model"] == "birefnet-general-lite"
    assert ".heic" in info["extensions"]


def test_single_photo_upload_runs_end_to_end(client: TestClient, tmp_path: Path) -> None:
    created = upload(client, ["shoe.jpg"], "files")
    assert created["output"] == str(tmp_path / "Downloads" / "shoe-no-bg")
    events = run_to_end(client, created["job"])
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["scan", "plan"]
    assert "job_done" in kinds
    assert events[-1]["processed"] == 1
    assert (tmp_path / "Downloads" / "shoe-no-bg" / "shoe.png").is_file()

    preview = client.get(
        f"/api/jobs/{created['job']}/preview", params={"image": 0, "side": "after"}
    )
    assert preview.headers["content-type"] == "image/png"

    archive = client.get(f"/api/jobs/{created['job']}/zip")
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert names == ["shoe-no-bg/shoe.png"]


def test_folder_upload_keeps_layout_and_names_output_after_folder(
    client: TestClient, tmp_path: Path
) -> None:
    created = upload(client, ["products/a.jpg", "products/shoes/b.jpg"], "folder")
    out = tmp_path / "Downloads" / "products-no-bg"
    assert created["output"] == str(out)
    run_to_end(client, created["job"])
    assert (out / "a.png").is_file()
    assert (out / "shoes" / "b.png").is_file()


def test_pasted_image_gets_a_timestamped_folder(client: TestClient) -> None:
    created = upload(client, ["image.png"], "paste")
    assert Path(created["output"]).name.startswith("pasted-")
    assert created["output"].endswith("-no-bg")


def test_existing_output_folder_is_not_reused_for_uploads(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "Downloads" / "shoe-no-bg").mkdir()
    created = upload(client, ["shoe.jpg"], "files")
    assert created["output"].endswith("shoe-no-bg-2")


def test_path_traversal_is_rejected(client: TestClient) -> None:
    files = [("files", ("x.jpg", jpeg(), "image/jpeg"))]
    response = client.post(
        "/api/jobs/upload", files=files, data={"paths": ["../../evil.jpg"], "kind": "folder"}
    )
    assert response.status_code == 400


def test_path_input_writes_next_to_the_original(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "shoot"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(jpeg())
    created = client.post("/api/jobs/path", json={"path": str(folder)}).json()
    assert created["output"] == str(tmp_path / "shoot-no-bg")
    run_to_end(client, created["job"])
    assert (tmp_path / "shoot-no-bg" / "a.png").is_file()


def test_unknown_path_is_a_clear_error(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/api/jobs/path", json={"path": str(tmp_path / "nope")})
    assert response.status_code == 400
    assert "no such file or folder" in response.json()["detail"]


def test_a_run_cannot_be_started_twice(client: TestClient) -> None:
    created = upload(client, ["a.jpg"], "files")
    run_to_end(client, created["job"])
    assert client.post(f"/api/jobs/{created['job']}/start").status_code == 409


def test_open_folder_uses_the_file_manager(client: TestClient, opened: list[Path]) -> None:
    created = upload(client, ["a.jpg"], "files")
    run_to_end(client, created["job"])
    assert client.post(f"/api/jobs/{created['job']}/open").status_code == 200
    assert opened == [Path(created["output"])]


def product_shot() -> bytes:
    """A subject exactly where the fake model looks, on a plain backdrop: certified.
    Odd sides keep the fake's rectangle symmetric, so the mirror check agrees."""
    image = Image.new("RGB", (161, 121), (250, 250, 250))
    ImageDraw.Draw(image).rectangle((40, 30, 120, 90), fill=(60, 140, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def busy_scene() -> bytes:
    buffer = io.BytesIO()
    Image.effect_noise((161, 121), 90).convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def upload_bytes(client: TestClient, named: dict[str, bytes]) -> dict:
    files = [("files", (name, data, "image/png")) for name, data in named.items()]
    data = {"paths": list(named), "kind": "files"}
    response = client.post("/api/jobs/upload", files=files, data=data)
    assert response.status_code == 200, response.text
    return response.json()


def test_plan_lists_the_queue_and_previews_it_before_the_run(client: TestClient) -> None:
    created = upload(client, ["shoes/a.jpg", "shoes/b.jpg"], "folder")
    assert created["queue"] == [{"job": 0, "name": "a.jpg"}, {"job": 1, "name": "b.jpg"}]
    url = f"/api/jobs/{created['job']}/preview"
    before = client.get(url, params={"image": 1, "side": "before", "size": 32})
    assert before.status_code == 200 and before.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(before.content)) as thumb:
        assert max(thumb.size) == 32
    assert client.get(url, params={"image": 1, "side": "after"}).status_code == 404  # not yet
    assert client.get(url, params={"image": 0, "size": 8}).status_code == 422


def test_ui_runs_are_certified_and_leave_no_review_sheets(client: TestClient) -> None:
    created = upload_bytes(client, {"bottle.png": product_shot(), "scene.png": busy_scene()})
    events = run_to_end(client, created["job"])
    done = {Path(e["source"]).name: e for e in events if e["type"] == "job_done"}
    assert done["bottle.png"]["certified"] is True
    assert done["bottle.png"]["check"] == "certified"
    assert done["scene.png"]["certified"] is False
    assert done["scene.png"]["check"] == "background is not plain"
    sheets = next(e for e in events if e["type"] == "review_sheets")["sheets"]
    assert sheets and all(not Path(sheet["path"]).parent.exists() for sheet in sheets)
    assert events[-1]["certified"] == 1


def test_zoom_returns_a_full_resolution_crop(client: TestClient, tmp_path: Path) -> None:
    created = upload_bytes(client, {"bottle.png": product_shot()})
    run_to_end(client, created["job"])
    url = f"/api/jobs/{created['job']}/zoom"
    crop = client.get(url, params={"image": 0, "x": 0.25, "y": 0.25, "w": 40, "h": 30})
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/png"
    assert crop.headers["x-image-size"] == "161 121"
    left, top, width, height = map(int, crop.headers["x-region"].split())
    assert (width, height) == (40, 30)
    with Image.open(tmp_path / "Downloads" / "bottle-no-bg" / "bottle.png") as output:
        expected = output.convert("RGBA").crop((left, top, left + width, top + height))
    with Image.open(io.BytesIO(crop.content)) as got:
        assert got.tobytes() == expected.tobytes()  # native pixels, not resampled

    edge = client.get(url, params={"image": 0, "x": 1, "y": 1, "w": 2048, "h": 50})
    assert edge.headers["x-region"] == "0 71 161 50"  # clamped inside the image
    before = client.get(
        url, params={"image": 0, "x": 0.5, "y": 0.5, "w": 9, "h": 9, "side": "before"}
    )
    assert before.headers["content-type"] == "image/jpeg"
    assert client.get(url, params={"image": 5, "x": 0, "y": 0, "w": 9, "h": 9}).status_code == 404
    assert client.get(url, params={"image": 0, "x": 2, "y": 0, "w": 9, "h": 9}).status_code == 422
    stranger = TestClient(client.app)  # no token
    assert stranger.get(url, params={"image": 0, "x": 0, "y": 0, "w": 9, "h": 9}).status_code == 403


def test_jpg_is_refused_before_anything_is_staged(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    made: list[str] = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: made.append("x") or real(**kw))
    files = [("files", ("a.jpg", jpeg(), "image/jpeg"))]
    data = {"paths": ["a.jpg"], "kind": "files", "output_format": "jpg"}
    response = client.post("/api/jobs/upload", files=files, data=data)
    assert response.status_code == 400 and "background" in response.text
    assert made == []


@pytest.fixture
def redo_dirs(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every temporary folder a "Try again" makes."""
    import tempfile

    made: list[Path] = []
    real = tempfile.mkdtemp

    def tracking(**kwargs: str) -> str:
        folder = real(**kwargs)
        if kwargs.get("prefix", "").startswith("bulk-bg-remove-redo-"):
            made.append(Path(folder))
        return folder

    monkeypatch.setattr(tempfile, "mkdtemp", tracking)
    return made


def finished_run(client: TestClient, named: dict[str, bytes]) -> str:
    created = upload_bytes(client, named)
    run_to_end(client, created["job"])
    import time

    for _ in range(100):  # the run thread marks the job finished just after the event
        if not client.get("/api/system").json()["busy"]:
            break
        time.sleep(0.05)
    return str(created["job"])


def wait_redo(client: TestClient, job: str, image: int) -> dict:
    import time

    for _ in range(600):
        state = client.get(f"/api/jobs/{job}/redo", params={"image": image}).json()
        if state["status"] != "running":
            return dict(state)
        time.sleep(0.1)
    raise AssertionError("the redo did not finish")


def test_redo_makes_a_candidate_and_leaves_the_output_alone(
    client: TestClient, tmp_path: Path, redo_dirs: list[Path]
) -> None:
    job = finished_run(client, {"scene.png": busy_scene()})
    output = tmp_path / "Downloads" / "scene-no-bg" / "scene.png"
    before = output.read_bytes()
    started = client.post(f"/api/jobs/{job}/redo", json={"image": 0, "edges": "clean"})
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "running"
    done = wait_redo(client, job, 0)
    assert done["status"] == "done" and done["certified"] is False
    assert done["check"] == "background is not plain"
    assert output.read_bytes() == before  # nothing is replaced until the user keeps it
    new = client.get(f"/api/jobs/{job}/preview", params={"image": 0, "side": "candidate"})
    assert new.status_code == 200 and new.headers["content-type"] == "image/png"
    assert len(redo_dirs) == 1 and redo_dirs[0].is_dir()
    keep = {"image": 0, "candidate": started.json()["candidate"]}
    assert client.post(f"/api/jobs/{job}/discard", json=keep).status_code == 200


def test_keep_replaces_the_output_with_the_candidate(
    client: TestClient, tmp_path: Path, redo_dirs: list[Path]
) -> None:
    job = finished_run(client, {"bottle.png": busy_scene()})
    output = tmp_path / "Downloads" / "bottle-no-bg" / "bottle.png"
    candidate = client.post(f"/api/jobs/{job}/redo", json={"image": 0, "edges": "mask"}).json()
    wait_redo(client, job, 0)
    expected = next(redo_dirs[0].rglob("bottle.png")).read_bytes()
    assert expected != output.read_bytes()
    kept = client.post(
        f"/api/jobs/{job}/keep", json={"image": 0, "candidate": candidate["candidate"]}
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["certified"] is False
    assert output.read_bytes() == expected
    assert sorted(p.name for p in output.parent.iterdir()) == ["bottle.png"]  # no partial file
    assert not redo_dirs[0].exists()
    assert client.get(f"/api/jobs/{job}/redo", params={"image": 0}).status_code == 404


def test_discard_deletes_the_candidate(client: TestClient, redo_dirs: list[Path]) -> None:
    job = finished_run(client, {"bottle.png": product_shot()})
    candidate = client.post(f"/api/jobs/{job}/redo", json={"image": 0, "edges": "mask"}).json()
    assert wait_redo(client, job, 0)["certified"] is True
    wrong = client.post(f"/api/jobs/{job}/discard", json={"image": 0, "candidate": "nope"})
    assert wrong.status_code == 404
    discarded = client.post(
        f"/api/jobs/{job}/discard", json={"image": 0, "candidate": candidate["candidate"]}
    )
    assert discarded.status_code == 200
    assert not redo_dirs[0].exists()
    side = client.get(f"/api/jobs/{job}/preview", params={"image": 0, "side": "candidate"})
    assert side.status_code == 404


def test_redo_is_refused_while_anything_else_runs(
    client: TestClient, redo_dirs: list[Path]
) -> None:
    job = finished_run(client, {"a.png": product_shot(), "b.png": product_shot()})
    first = client.post(f"/api/jobs/{job}/redo", json={"image": 0, "edges": "mask"})
    assert first.status_code == 200
    second = client.post(f"/api/jobs/{job}/redo", json={"image": 1, "edges": "mask"})
    assert second.status_code == 409  # one engine at a time
    import tempfile

    staged = set(Path(tempfile.gettempdir()).glob("bulk-bg-remove-ui-*"))
    files = [("files", ("c.png", product_shot(), "image/png"))]
    new_run = client.post("/api/jobs/upload", files=files, data={"paths": ["c.png"]})
    assert new_run.status_code == 409  # nor a new run while a photo is redone
    assert set(Path(tempfile.gettempdir()).glob("bulk-bg-remove-ui-*")) == staged  # copies gone
    wait_redo(client, job, 0)

    batch = upload_bytes(client, {"c.png": product_shot()})
    assert not redo_dirs[0].exists()  # a new run drops the old run's candidates
    assert client.post(f"/api/jobs/{batch['job']}/start").status_code == 200
    busy = client.post(f"/api/jobs/{batch['job']}/redo", json={"image": 0, "edges": "mask"})
    assert busy.status_code == 409  # the batch is still running
    run_to_end(client, batch["job"], start=False)


def test_stopping_the_server_drops_candidates(tmp_path: Path, redo_dirs: list[Path]) -> None:
    (tmp_path / "Downloads").mkdir()
    app = create_app(
        TOKEN,
        allowed_hosts={"testserver"},
        downloads_dir=tmp_path / "Downloads",
        model_factory=FAKE_FACTORY,
        min_free_ram_gb=0.1,
    )
    with TestClient(app, headers={"X-Token": TOKEN}) as client:  # runs the lifespan
        job = finished_run(client, {"a.png": product_shot()})
        assert client.post(f"/api/jobs/{job}/redo", json={"image": 0}).status_code == 200
        wait_redo(client, job, 0)
        assert redo_dirs[0].is_dir()
    assert not redo_dirs[0].exists()


def test_redo_endpoints_need_the_token(client: TestClient) -> None:
    job = finished_run(client, {"a.png": product_shot()})
    stranger = TestClient(client.app)  # no token
    assert stranger.post(f"/api/jobs/{job}/redo", json={"image": 0}).status_code == 403
    assert stranger.get(f"/api/jobs/{job}/redo/options", params={"image": 0}).status_code == 403
    keep = {"image": 0, "candidate": "x"}
    assert stranger.post(f"/api/jobs/{job}/keep", json=keep).status_code == 403
    assert stranger.post(f"/api/jobs/{job}/discard", json=keep).status_code == 403


def test_redo_options_rank_the_models(client: TestClient) -> None:
    job = finished_run(client, {"a.png": product_shot()})
    options = client.get(f"/api/jobs/{job}/redo/options", params={"image": 0}).json()
    ids = [m["id"] for m in options["models"]]
    assert ids[0] == "birefnet-general" and "silueta" in ids
    assert all(isinstance(m["fits"], bool) for m in options["models"])
    bad = client.post(f"/api/jobs/{job}/redo", json={"image": 0, "model": "nope"})
    assert bad.status_code == 400
