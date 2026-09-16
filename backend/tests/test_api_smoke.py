from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epub_helpers import minimal_epub_bytes


@pytest.fixture()
def authenticated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    import app.main as main

    monkeypatch.setattr(main, "API_TOKEN", "integration-secret-token")
    data_dir = tmp_path / "auth_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    previews = data_dir / "_voice_previews"
    previews.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "storage", main.Storage(data_dir))
    monkeypatch.setattr(main, "jobs", {})
    monkeypatch.setattr(main, "job_finished_at", {})
    monkeypatch.setattr(main, "voice_preview_dir", previews)

    with TestClient(main.app) as client:
        yield client


def test_health_returns_ok(isolated_client: TestClient) -> None:
    r = isolated_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_requires_epub_extension(isolated_client: TestClient) -> None:
    r = isolated_client.post(
        "/books",
        files={"epub_file": ("readme.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400


def test_upload_manifest_structure_refresh_list_delete_roundtrip(isolated_client: TestClient) -> None:
    epub_bytes = minimal_epub_bytes(chapter_title="Book 2, Chapter 5")
    up = isolated_client.post(
        "/books",
        files={"epub_file": ("book.epub", epub_bytes, "application/epub+zip")},
    )
    assert up.status_code == 200, up.text
    book = up.json()
    book_id = book["book_id"]
    assert book["title"] == "API Smoke Title"
    assert len(book["chapters"]) == 1

    lst = isolated_client.get("/books")
    assert lst.status_code == 200
    rows = lst.json()
    assert any(row["book_id"] == book_id for row in rows)

    sf = isolated_client.get(f"/books/{book_id}/structure")
    assert sf.status_code == 200
    st = sf.json()
    assert st["chapter_summaries"][0]["title"] == "Book 2, Chapter 5"
    assert st["volumes"][0]["name"] == "Book 2"
    assert st["synthesized_chapter_ids"] == []

    ref = isolated_client.post(f"/books/{book_id}/refresh-titles")
    assert ref.status_code == 200
    ref_body = ref.json()
    assert ref_body["manifest_updated"] in (False, True)
    assert ref_body["synthesis_chapters_updated"] == 0

    gm = isolated_client.get(f"/books/{book_id}/manifest")
    assert gm.status_code == 404

    audio = isolated_client.get(f"/books/{book_id}/chapter/ch1")
    assert audio.status_code == 404

    dl = isolated_client.get(f"/books/{book_id}/export.m4b")
    assert dl.status_code == 404

    dl_vol = isolated_client.get(f"/books/{book_id}/export.m4b", params={"volume_index": 0})
    assert dl_vol.status_code == 404

    delete = isolated_client.delete(f"/books/{book_id}")
    assert delete.status_code == 200
    missing = isolated_client.delete(f"/books/{book_id}")
    assert missing.status_code == 404


def test_api_token_protects_but_not_health(authenticated_client: TestClient) -> None:
    assert authenticated_client.get("/health").status_code == 200
    assert authenticated_client.get("/books").status_code == 401
    hdr = {"x-api-key": "integration-secret-token"}
    ep = minimal_epub_bytes()
    ok = authenticated_client.post(
        "/books",
        files={"epub_file": ("z.epub", ep, "application/epub+zip")},
        headers=hdr,
    )
    assert ok.status_code == 200


def test_synthesize_queues_without_touching_when_immediately_checked(
    isolated_client: TestClient, sample_epub: bytes, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthesis reaches GCP in the background — stub the worker so this test stays offline-safe."""
    import app.main as main

    monkeypatch.setattr(main, "_run_synthesis", lambda *_a, **_k: None)

    up = isolated_client.post(
        "/books",
        files={"epub_file": ("e.epub", sample_epub, "application/epub+zip")},
    )
    book_id = up.json()["book_id"]
    payload = {
        "cleanup": {
            "min_chapter_words": 1,
            "drop_frontmatter": False,
            "drop_backmatter": False,
            "drop_toc": False,
        },
        "voices": [{"language": "en", "voice_name": "en-US-Neural2-D", "speaking_rate": 1, "pitch": 0}],
        "selection": {"chapter_ids": ["ch1"]},
        "incremental": True,
    }
    r = isolated_client.post(f"/books/{book_id}/synthesize", json=payload)
    assert r.status_code == 200
    assert r.json().get("status") == "queued"
