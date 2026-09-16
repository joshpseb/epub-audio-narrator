from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import BookManifest, SynthesisManifest


class Storage:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_book_id(self) -> str:
        return uuid4().hex[:12]

    def book_dir(self, book_id: str) -> Path:
        path = self.base_dir / book_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def chapters_dir(self, book_id: str) -> Path:
        path = self.book_dir(book_id) / "chapters"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def audio_dir(self, book_id: str) -> Path:
        path = self.book_dir(book_id) / "audio"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cache_dir(self, book_id: str) -> Path:
        path = self.book_dir(book_id) / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    def read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def save_original_epub(self, book_id: str, content: bytes) -> Path:
        path = self.book_dir(book_id) / "source.epub"
        path.write_bytes(content)
        return path

    def save_manifest(self, book: BookManifest) -> Path:
        path = self.book_dir(book.book_id) / "manifest.json"
        self.write_json(path, book.model_dump())
        return path

    def save_synthesis_manifest(self, synth: SynthesisManifest) -> Path:
        path = self.book_dir(synth.book_id) / "timing.json"
        self.write_json(path, synth.model_dump())
        return path

    def list_books(self) -> list[dict[str, Any]]:
        books: list[dict[str, Any]] = []
        for candidate in sorted(self.base_dir.glob("*")):
            if not candidate.is_dir():
                continue
            manifest = candidate / "manifest.json"
            if not manifest.exists():
                continue
            payload = self.read_json(manifest)
            books.append(
                {
                    "book_id": payload.get("book_id"),
                    "title": payload.get("title"),
                    "authors": payload.get("authors", []),
                }
            )
        return books

    def delete_book(self, book_id: str) -> bool:
        path = self.base_dir / book_id
        if not path.exists() or not path.is_dir():
            return False
        shutil.rmtree(path)
        return True
