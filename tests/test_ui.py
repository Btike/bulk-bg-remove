"""Local UI server: security guard, the three ways in, and a full run over SSE."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

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
