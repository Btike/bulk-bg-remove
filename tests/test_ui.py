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


def run_to_end(client: TestClient, job: str) -> list[dict]:
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
