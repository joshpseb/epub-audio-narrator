from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epub_helpers import minimal_epub_bytes


@pytest.fixture()
def isolated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """Use an empty temp data dir and isolated job maps for HTTP tests."""
    import app.main as main

    data_dir = tmp_path
    previews = data_dir / "_voice_previews"
    previews.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "storage", main.Storage(data_dir))
    monkeypatch.setattr(main, "jobs", {})
    monkeypatch.setattr(main, "job_finished_at", {})
    monkeypatch.setattr(main, "voice_preview_dir", previews)
    monkeypatch.setenv("APP_API_TOKEN", "")

    with TestClient(main.app) as client:
        yield client


@pytest.fixture()
def sample_epub() -> bytes:
    return minimal_epub_bytes()
