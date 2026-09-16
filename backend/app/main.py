from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, Response, StreamingResponse

from .audiobook_export import export_m4b
from .epub_parser import normalize_chapter_titles, parse_epub
from .models import (
    BookManifest,
    BookStructure,
    CleanupConfig,
    JobState,
    SynthesisManifest,
    SynthesizeRequest,
    VoiceInfoModel,
    VoicePreviewRequest,
)
from .storage import Storage
from .synthesizer import synthesize_book
from .text_cleaner import clean_manifest
from .tts import get_tts_for_provider
from .volumes import detect_volumes

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
storage = Storage(DATA_DIR)
jobs: dict[str, JobState] = {}
job_finished_at: dict[str, float] = {}
voice_preview_dir = DATA_DIR / "_voice_previews"
voice_preview_dir.mkdir(parents=True, exist_ok=True)
API_TOKEN = os.getenv("APP_API_TOKEN", "").strip()
JOB_RETENTION_SEC = 60 * 60

DEFAULT_PREVIEW_TEXTS: dict[str, str] = {
    "en": "The quick brown fox jumps over the lazy dog.",
    "zh": "你好，欢迎来听这本有声书。",
    "ko": "안녕하세요, 오디오북 시청을 환영합니다.",
    "ja": "こんにちは、これは音声プレビューです。",
}

app = FastAPI(title="EPUB Audio Narrator API")
cors_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
extra_origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "").split(",") if item.strip()]
if extra_origins:
    cors_origins.extend(extra_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_api_token(request: Request, call_next):  # type: ignore[no-untyped-def]
    if not API_TOKEN:
        return await call_next(request)
    if request.url.path == "/health":
        return await call_next(request)
    provided = request.headers.get("x-api-key", "") or request.query_params.get("api_key", "")
    if provided != API_TOKEN:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


def _normalize_book_titles(book: BookManifest) -> BookManifest:
    normalized = normalize_chapter_titles(book.chapters)
    return book.model_copy(update={"chapters": normalized})


def _prune_jobs(now: float | None = None) -> None:
    current = now if now is not None else time.time()
    stale = [book_id for book_id, ts in job_finished_at.items() if current - ts > JOB_RETENTION_SEC]
    for book_id in stale:
        jobs.pop(book_id, None)
        job_finished_at.pop(book_id, None)


def _refresh_titles(book_id: str) -> tuple[BookManifest, bool, int]:
    """Normalize and persist manifest + synthesis chapter titles.

    Returns:
      (normalized_book, manifest_changed, synthesis_titles_changed_count)
    """
    manifest_path = _book_manifest_path(book_id)
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Book not found")

    state_path = storage.book_dir(book_id) / ".title_refresh_state.json"
    manifest_mtime = manifest_path.stat().st_mtime_ns
    synth_path = _synthesis_path(book_id)
    synth_mtime = synth_path.stat().st_mtime_ns if synth_path.exists() else 0
    if state_path.exists():
        try:
            state = storage.read_json(state_path)
            if (
                int(state.get("manifest_mtime", -1)) == manifest_mtime
                and int(state.get("synth_mtime", -1)) == synth_mtime
            ):
                cached_book = BookManifest.model_validate(storage.read_json(manifest_path))
                return cached_book, False, 0
        except Exception:
            pass

    original = BookManifest.model_validate(storage.read_json(manifest_path))
    normalized_book = _normalize_book_titles(original)
    manifest_changed = any(
        before.title != after.title
        for before, after in zip(original.chapters, normalized_book.chapters, strict=False)
    )
    if manifest_changed:
        storage.save_manifest(normalized_book)

    synth_changed = 0
    if synth_path.exists():
        synthesis = SynthesisManifest.model_validate(storage.read_json(synth_path))
        title_by_id = {chapter.id: chapter.title for chapter in normalized_book.chapters}
        updated_chapters = []
        for chapter in synthesis.chapters:
            normalized_title = title_by_id.get(chapter.chapter_id, chapter.chapter_title)
            if normalized_title != chapter.chapter_title:
                synth_changed += 1
                updated_chapters.append(chapter.model_copy(update={"chapter_title": normalized_title}))
            else:
                updated_chapters.append(chapter)
        if synth_changed > 0:
            storage.save_synthesis_manifest(synthesis.model_copy(update={"chapters": updated_chapters}))

    # Persist refresh state so read endpoints can skip repeated normalization.
    try:
        latest_manifest_mtime = manifest_path.stat().st_mtime_ns
        latest_synth_mtime = synth_path.stat().st_mtime_ns if synth_path.exists() else 0
        storage.write_json(
            state_path,
            {"manifest_mtime": latest_manifest_mtime, "synth_mtime": latest_synth_mtime},
        )
    except Exception:
        pass

    return normalized_book, manifest_changed, synth_changed


def _book_manifest_path(book_id: str) -> Path:
    return storage.book_dir(book_id) / "manifest.json"


def _synthesis_path(book_id: str) -> Path:
    return storage.book_dir(book_id) / "timing.json"


def _preview_text(language_code: str, custom_text: str | None) -> str:
    if custom_text and custom_text.strip():
        return custom_text.strip()
    lang = language_code.split("-")[0].lower()
    return DEFAULT_PREVIEW_TEXTS.get(lang, DEFAULT_PREVIEW_TEXTS["en"])


def _preview_cache_path(payload: VoicePreviewRequest) -> Path:
    key_input = json.dumps(payload.model_dump(), sort_keys=True, ensure_ascii=False)
    key = hashlib.sha256(key_input.encode("utf-8")).hexdigest()
    return voice_preview_dir / f"{key}.mp3"


def _extract_chapter_number(title: str) -> int | None:
    latin = re.search(r"\bchapter\s+(\d+)\b", title, re.IGNORECASE)
    if latin:
        return int(latin.group(1))
    cn = re.search(r"第\s*(\d+)\s*章", title)
    if cn:
        return int(cn.group(1))
    return None


def _extract_book_chapter_locator(title: str) -> tuple[int | None, int | None]:
    match = re.search(r"\b(?:book|volume|part)\s+(\d+)\s*,?\s*chapter\s+(\d+)\b", title, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))
    chapter_only = _extract_chapter_number(title)
    return None, chapter_only


def _format_locator(book_num: int | None, chapter_num: int | None) -> str:
    if book_num is not None and chapter_num is not None:
        return f"B{book_num}C{chapter_num}"
    if chapter_num is not None:
        return f"C{chapter_num}"
    return "C?"


def _export_title_and_filename(
    book_title: str,
    synthesis: SynthesisManifest,
    custom_title: str | None,
    *,
    volume_name: str | None = None,
) -> tuple[str, str]:
    if custom_title and custom_title.strip():
        title = custom_title.strip()
    elif volume_name and volume_name.strip():
        # Volume export: "{Series title} Book N" (embedded title + filename).
        base = (book_title.strip() or synthesis.title.strip() or "Audiobook").strip()
        title = f"{base} {volume_name.strip()}"
    elif synthesis.chapters:
        start_book, start_ch = _extract_book_chapter_locator(synthesis.chapters[0].chapter_title)
        end_book, end_ch = _extract_book_chapter_locator(synthesis.chapters[-1].chapter_title)
        bt = (book_title.strip() or synthesis.title.strip() or "Audiobook").strip()
        title = f"{bt} {_format_locator(start_book, start_ch)}-{_format_locator(end_book, end_ch)}"
    else:
        bt = (book_title.strip() or synthesis.title.strip() or "Audiobook").strip()
        title = f"{bt} C1-C1"

    safe = re.sub(r"[^A-Za-z0-9._ -]+", "", title).strip()
    safe = re.sub(r"\s+", " ", safe)
    if not safe:
        safe = "audiobook"
    return title, f"{safe}.m4b"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/books")
def list_books() -> list[dict[str, Any]]:
    return storage.list_books()


@app.get("/voices")
def list_voices(
    language_code: str = Query(..., description="e.g. en-US, cmn-CN, ko-KR"),
    provider: str = Query("kokoro", description="kokoro | google"),
) -> list[VoiceInfoModel]:
    tts_client = get_tts_for_provider(provider)
    voices = tts_client.list_voices(language_code=language_code)
    return [VoiceInfoModel(**voice.__dict__) for voice in voices]


@app.post("/voices/preview")
def preview_voice(request: VoicePreviewRequest) -> Response:
    tts_client = get_tts_for_provider(request.provider)
    text = _preview_text(request.language_code, request.text)
    payload = request.model_copy(update={"text": text})
    cache_path = _preview_cache_path(payload)
    if cache_path.exists():
        return Response(content=cache_path.read_bytes(), media_type="audio/mpeg")

    audio_bytes = tts_client.synthesize_plain(
        text=text,
        language_code=request.language_code,
        voice_name=request.voice_name,
        speaking_rate=request.speaking_rate,
        pitch=request.pitch,
    )
    cache_path.write_bytes(audio_bytes)
    return Response(content=audio_bytes, media_type="audio/mpeg")


@app.post("/books")
async def upload_book(epub_file: UploadFile = File(...)) -> dict[str, Any]:
    if not epub_file.filename or not epub_file.filename.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="Please upload an .epub file")
    content = await epub_file.read()
    book_id = storage.create_book_id()
    epub_path = storage.save_original_epub(book_id, content)
    manifest = parse_epub(epub_path, book_id=book_id)
    manifest = clean_manifest(
        manifest,
        CleanupConfig(
            drop_frontmatter=True,
            drop_toc=True,
            drop_backmatter=False,
            min_chapter_words=0,
        ),
    )
    storage.save_manifest(manifest)
    return manifest.model_dump()


@app.get("/books/{book_id}")
def get_book_manifest(book_id: str) -> dict[str, Any]:
    book, _, _ = _refresh_titles(book_id)
    return book.model_dump()


@app.delete("/books/{book_id}")
def delete_book(book_id: str) -> dict[str, Any]:
    deleted = storage.delete_book(book_id)
    jobs.pop(book_id, None)
    job_finished_at.pop(book_id, None)
    if not deleted:
        raise HTTPException(status_code=404, detail="Book not found")
    return {"book_id": book_id, "status": "deleted"}


def _run_synthesis(book_id: str, request: SynthesizeRequest) -> None:
    jobs[book_id] = JobState(status="running", progress=0.0, message="Starting")
    job_finished_at.pop(book_id, None)
    try:
        book, _, _ = _refresh_titles(book_id)
        tts_client = get_tts_for_provider(request.tts_provider)

        def update(progress: float, message: str) -> None:
            jobs[book_id] = JobState(status="running", progress=progress, message=message)

        synthesis = synthesize_book(
            book=book,
            request=request,
            storage=storage,
            tts_client=tts_client,
            progress=update,
        )
        storage.save_synthesis_manifest(synthesis)
        jobs[book_id] = JobState(status="done", progress=1.0, message="Completed")
        job_finished_at[book_id] = time.time()
    except Exception as exc:  # noqa: BLE001
        jobs[book_id] = JobState(status="error", progress=0.0, message="Failed", error=str(exc))
        job_finished_at[book_id] = time.time()


@app.post("/books/{book_id}/synthesize")
def synthesize_endpoint(book_id: str, request: SynthesizeRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    _prune_jobs()
    if not _book_manifest_path(book_id).exists():
        raise HTTPException(status_code=404, detail="Book not found")
    active = jobs.get(book_id)
    if active and active.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Synthesis already in progress for this book")
    jobs[book_id] = JobState(status="queued", progress=0.0, message="Queued")
    background_tasks.add_task(_run_synthesis, book_id, request)
    return {"book_id": book_id, "status": "queued"}


@app.get("/books/{book_id}/progress")
async def stream_progress(book_id: str) -> StreamingResponse:
    async def event_stream() -> Any:
        while True:
            _prune_jobs()
            state = jobs.get(book_id, JobState(status="queued", message="Idle"))
            payload = json.dumps(state.model_dump())
            yield f"data: {payload}\n\n"
            if state.status in {"done", "error"}:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/books/{book_id}/structure")
def get_book_structure(book_id: str) -> dict[str, Any]:
    book, _, _ = _refresh_titles(book_id)
    chapters_prefixed = book.chapters
    chapter_summaries = [
        {"id": ch.id, "title": ch.title, "word_count": ch.word_count}
        for ch in chapters_prefixed
    ]
    volumes = detect_volumes(chapters_prefixed)

    synth_path = _synthesis_path(book_id)
    synthesized: list[str] = []
    if synth_path.exists():
        try:
            synth = SynthesisManifest.model_validate(storage.read_json(synth_path))
            synthesized = [c.chapter_id for c in synth.chapters]
        except Exception:
            synthesized = []

    structure = BookStructure(
        book_id=book.book_id,
        title=book.title,
        authors=book.authors,
        chapter_summaries=chapter_summaries,
        volumes=volumes,
        synthesized_chapter_ids=synthesized,
    )
    return structure.model_dump()


@app.get("/books/{book_id}/manifest")
def get_audio_manifest(book_id: str) -> dict[str, Any]:
    _refresh_titles(book_id)
    manifest_path = _synthesis_path(book_id)
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Synthesis manifest not found")
    return storage.read_json(manifest_path)


@app.post("/books/{book_id}/refresh-titles")
def refresh_titles_endpoint(book_id: str) -> dict[str, Any]:
    _, manifest_changed, synthesis_changed = _refresh_titles(book_id)
    return {
        "book_id": book_id,
        "status": "ok",
        "manifest_updated": manifest_changed,
        "synthesis_chapters_updated": synthesis_changed,
    }


@app.get("/books/{book_id}/chapter/{chapter_id}")
def get_chapter_audio(book_id: str, chapter_id: str) -> FileResponse:
    path = storage.audio_dir(book_id) / f"{chapter_id}.mp3"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Chapter audio not found")
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@app.get("/books/{book_id}/export.m4b")
def export_endpoint(
    book_id: str,
    title: str | None = Query(None, description="Optional custom export title"),
    volume_index: int | None = Query(None, description="Export only chapters from this volume index"),
    start_index: int | None = Query(None, ge=1, description="1-indexed first chapter in book.chapters"),
    end_index: int | None = Query(None, ge=1, description="1-indexed last chapter in book.chapters (inclusive)"),
) -> FileResponse:
    synth_path = _synthesis_path(book_id)
    manifest_path = _book_manifest_path(book_id)
    if not synth_path.exists():
        raise HTTPException(status_code=404, detail="Synthesize first")
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Book not found")
    book, _, _ = _refresh_titles(book_id)
    synthesis = SynthesisManifest.model_validate(storage.read_json(synth_path))

    has_range = start_index is not None or end_index is not None
    if volume_index is not None and has_range:
        raise HTTPException(
            status_code=400,
            detail="Use either volume_index or start_index/end_index, not both",
        )
    if has_range and (start_index is None or end_index is None):
        raise HTTPException(status_code=400, detail="Both start_index and end_index are required for range export")
    if start_index is not None and end_index is not None and start_index > end_index:
        raise HTTPException(status_code=400, detail="start_index must be <= end_index")

    output_slug = "book"
    volume_name: str | None = None
    if volume_index is not None:
        volumes = detect_volumes(book.chapters)
        vol = next((v for v in volumes if v.index == volume_index), None)
        if vol:
            vol_ids = set(vol.chapter_ids)
            filtered = [ch for ch in synthesis.chapters if ch.chapter_id in vol_ids]
            if not filtered:
                raise HTTPException(status_code=404, detail="No synthesized chapters in this volume yet")
            synthesis = synthesis.model_copy(update={"chapters": filtered})
            output_slug = re.sub(r"[^a-z0-9]+", "_", vol.name.lower()).strip("_") or f"vol{volume_index}"
            volume_name = vol.name

    if start_index is not None and end_index is not None:
        total_ch = len(book.chapters)
        if total_ch == 0:
            raise HTTPException(status_code=400, detail="Book has no chapters")
        si = max(0, start_index - 1)
        ei = min(total_ch - 1, end_index - 1)
        if si > ei:
            raise HTTPException(status_code=400, detail="Chapter range does not overlap this book")
        range_ids = {book.chapters[i].id for i in range(si, ei + 1)}
        filtered = [ch for ch in synthesis.chapters if ch.chapter_id in range_ids]
        if not filtered:
            raise HTTPException(status_code=404, detail="No synthesized chapters in this range yet")
        synthesis = synthesis.model_copy(update={"chapters": filtered})
        output_slug = f"ch{start_index}_{end_index}"

    export_title, export_filename = _export_title_and_filename(book.title, synthesis, title, volume_name=volume_name)
    output = export_m4b(
        storage.book_dir(book_id),
        synthesis,
        authors=book.authors,
        title_override=export_title,
        output_slug=output_slug,
    )
    return FileResponse(output, media_type="audio/mp4", filename=export_filename)
