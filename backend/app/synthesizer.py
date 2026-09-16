from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .language_detect import split_sentences, split_text_to_segments
from .models import (
    BookManifest,
    ChapterAudioManifest,
    CleanupConfig,
    SentenceTiming,
    SynthesisManifest,
    SynthesizeRequest,
)
from .storage import Storage
from .text_cleaner import clean_manifest
from .tts import DEFAULT_VOICE_MAP, KOKORO_SAMPLE_RATE, TtsSentenceChunk
from .volumes import filter_chapters_by_selection

ProgressHook = Callable[[float, str], None]

_MAX_BATCH_CHARS = max(400, int(os.getenv("TTS_MAX_BATCH_CHARS", "2500")))
_MAX_BATCH_SENTENCES = max(2, int(os.getenv("TTS_MAX_BATCH_SENTENCES", "25")))


def _default_tts_parallel() -> int:
    cpu_count = max(1, os.cpu_count() or 1)
    env_provider = os.getenv("ONNX_PROVIDER", "").strip()
    active_provider = env_provider
    if not active_provider and platform.system() == "Darwin" and platform.machine() == "arm64":
        active_provider = "CoreMLExecutionProvider"
    if active_provider == "CoreMLExecutionProvider":
        return max(1, min(2, cpu_count))
    return max(1, min(4, cpu_count))


_TTS_PARALLEL = max(1, int(os.getenv("TTS_PARALLEL", str(_default_tts_parallel()))))


@dataclass
class _SentenceCtx:
    text: str
    lang: str
    text_start: int
    text_end: int
    sid: str


@dataclass
class _BatchWork:
    batch: list[_SentenceCtx]
    chapter_id: str
    language_code: str
    lang: str
    voice_name: str
    rate: float
    pitch: float


@dataclass
class _BatchAudio:
    path: Path
    duration: float
    marks: dict[str, float]
    batch: list[_SentenceCtx]
    chapter_id: str


def _voice_language_code(voice_name: str, fallback_lang: str) -> str:
    parts = voice_name.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return fallback_lang


def _voice_for_lang(request: SynthesizeRequest) -> dict[str, tuple[str, float, float]]:
    table: dict[str, tuple[str, float, float]] = {}
    for lang, voice in DEFAULT_VOICE_MAP.items():
        table[lang] = (voice, 1.0, 0.0)
    for voice in request.voices:
        table[voice.language] = (voice.voice_name, voice.speaking_rate, voice.pitch)
    return table


def _cache_key(*, lang: str, voice: str, rate: float, pitch: float, payload: list[str]) -> str:
    joined = json.dumps(
        {"lang": lang, "voice": voice, "rate": rate, "pitch": pitch, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _load_cached(cache_root: Path, key: str) -> tuple[Path, dict[str, float], float] | None:
    pcm = cache_root / f"{key}.f32"
    marks = cache_root / f"{key}.json"
    if not pcm.exists() or not marks.exists():
        return None
    payload = json.loads(marks.read_text(encoding="utf-8"))
    mark_payload = payload.get("marks", {})
    duration = float(payload.get("duration", 0.0))
    return pcm, {k: float(v) for k, v in mark_payload.items()}, duration


def _save_cached(cache_root: Path, key: str, samples: bytes, marks: dict[str, float], duration: float) -> None:
    (cache_root / f"{key}.f32").write_bytes(samples)
    (cache_root / f"{key}.json").write_text(
        json.dumps({"marks": marks, "duration": duration}, indent=2),
        encoding="utf-8",
    )


def _build_sentence_contexts(chapter_text: str, chapter_id: str) -> list[_SentenceCtx]:
    segments = split_text_to_segments(chapter_text)
    out: list[_SentenceCtx] = []
    serial = 0
    for seg in segments:
        local_cursor = 0
        for sentence in split_sentences(seg.text):
            local_idx = seg.text.find(sentence, local_cursor)
            if local_idx < 0:
                local_idx = local_cursor
            start = seg.start + local_idx
            end = start + len(sentence)
            sid = f"{chapter_id}-s{serial}"
            serial += 1
            out.append(_SentenceCtx(text=sentence, lang=seg.lang, text_start=start, text_end=end, sid=sid))
            local_cursor = local_idx + len(sentence)
    return out


def _load_existing_synthesis(storage: Storage, book_id: str) -> dict[str, ChapterAudioManifest]:
    path = storage.book_dir(book_id) / "timing.json"
    if not path.exists():
        return {}
    try:
        payload = storage.read_json(path)
        manifest = SynthesisManifest.model_validate(payload)
    except Exception:
        return {}
    return {ch.chapter_id: ch for ch in manifest.chapters}


def _collect_batches_for_chapter(
    chapter_sentences: list[_SentenceCtx],
    *,
    chapter_id: str,
    voice_map: dict[str, tuple[str, float, float]],
) -> list[_BatchWork]:
    pending: list[_BatchWork] = []
    i = 0
    while i < len(chapter_sentences):
        lang = chapter_sentences[i].lang
        group: list[_SentenceCtx] = [chapter_sentences[i]]
        i += 1
        while i < len(chapter_sentences) and chapter_sentences[i].lang == lang:
            group.append(chapter_sentences[i])
            i += 1

        voice_name, rate, pitch = voice_map.get(lang, voice_map["en"])
        language_code = _voice_language_code(voice_name, lang)

        batch: list[_SentenceCtx] = []
        char_count = 0
        for sentence in group:
            if batch and (
                char_count + len(sentence.text) > _MAX_BATCH_CHARS or len(batch) >= _MAX_BATCH_SENTENCES
            ):
                pending.append(
                    _BatchWork(
                        batch=list(batch),
                        chapter_id=chapter_id,
                        language_code=language_code,
                        lang=lang,
                        voice_name=voice_name,
                        rate=rate,
                        pitch=pitch,
                    )
                )
                batch = []
                char_count = 0
            batch.append(sentence)
            char_count += len(sentence.text)

        if batch:
            pending.append(
                _BatchWork(
                    batch=list(batch),
                    chapter_id=chapter_id,
                    language_code=language_code,
                    lang=lang,
                    voice_name=voice_name,
                    rate=rate,
                    pitch=pitch,
                )
            )

    return pending


def _ensure_batch_audio(
    work: _BatchWork,
    *,
    cache_root: Path,
    tts_client: Any,
) -> _BatchAudio:
    batch = work.batch
    text_payload = [item.text for item in batch]
    key = _cache_key(
        lang=work.lang, voice=work.voice_name, rate=work.rate, pitch=work.pitch, payload=text_payload
    )

    cached = _load_cached(cache_root, key)
    if cached:
        segment_file, marks, duration = cached
        if duration <= 0.0 and marks:
            duration = float(max(marks.values())) + 0.25
    else:
        chunks = [TtsSentenceChunk(id=item.sid, text=item.text) for item in batch]
        result = tts_client.synthesize_with_marks_pcm(
            language_code=work.language_code,
            voice_name=work.voice_name,
            chunks=chunks,
            speaking_rate=work.rate,
            pitch=work.pitch,
        )
        raw_samples = result.samples.astype("float32", copy=False).tobytes()
        _save_cached(cache_root, key, raw_samples, result.marks, result.duration_seconds)
        marks = result.marks
        duration = result.duration_seconds
        if duration <= 0.0 and marks:
            duration = float(max(marks.values())) + 0.25
        segment_file = cache_root / f"{key}.f32"

    return _BatchAudio(
        path=segment_file,
        duration=duration,
        marks=marks,
        batch=batch,
        chapter_id=work.chapter_id,
    )


def _append_batch_timings(
    ba: _BatchAudio,
    *,
    chapter_offset: float,
    chapter_timings: list[SentenceTiming],
) -> None:
    marks_in_order = [(ctx.sid, ba.marks.get(ctx.sid, 0.0)) for ctx in ba.batch]
    for idx, ctx in enumerate(ba.batch):
        mark_time = marks_in_order[idx][1]
        if idx + 1 < len(marks_in_order):
            end_time = marks_in_order[idx + 1][1]
        else:
            end_time = ba.duration
        chapter_timings.append(
            SentenceTiming(
                id=ctx.sid,
                text=ctx.text,
                lang=ctx.lang,
                text_start=ctx.text_start,
                text_end=ctx.text_end,
                t_start=chapter_offset + mark_time,
                t_end=chapter_offset + max(mark_time, end_time),
                chapter_id=ba.chapter_id,
            )
        )


def synthesize_book(
    *,
    book: BookManifest,
    request: SynthesizeRequest,
    storage: Storage,
    tts_client: Any,
    progress: ProgressHook | None = None,
) -> SynthesisManifest:
    selection_ids = list(request.selection.chapter_ids) if request.selection else []
    cleanup = request.cleanup if isinstance(request.cleanup, CleanupConfig) else CleanupConfig()
    cleaned = clean_manifest(book, cleanup)
    if selection_ids:
        # When users explicitly pick chapter IDs, preserve exact numbering/selection.
        explicit = filter_chapters_by_selection(book, selection_ids)
        explicit_cleanup = cleanup.model_copy(
            update={
                "drop_frontmatter": False,
                "drop_backmatter": False,
                "drop_toc": False,
                "min_chapter_words": 0,
            }
        )
        selected = clean_manifest(explicit, explicit_cleanup)
    else:
        selected = cleaned
    voice_map = _voice_for_lang(request)
    existing = _load_existing_synthesis(storage, book.book_id)
    chapters_audio: list[ChapterAudioManifest] = []
    total = max(1, len(selected.chapters))
    pending_all: list[tuple[str, int, _BatchWork]] = []

    for cidx, chapter in enumerate(selected.chapters):
        if progress:
            progress(cidx / total, f"Preparing {chapter.title}")

        if request.incremental and chapter.id in existing:
            audio_path = storage.audio_dir(book.book_id) / f"{chapter.id}.mp3"
            if audio_path.exists():
                chapters_audio.append(existing[chapter.id])
                continue

        chapter_sentences = _build_sentence_contexts(chapter.plain_text, chapter.id)
        pending = _collect_batches_for_chapter(chapter_sentences, chapter_id=chapter.id, voice_map=voice_map)
        for pidx, work in enumerate(pending):
            pending_all.append((chapter.id, pidx, work))

    cache_root = storage.cache_dir(book.book_id)
    chapter_batch_audio_map: dict[str, list[tuple[int, _BatchAudio]]] = {ch.id: [] for ch in selected.chapters}
    total_batches = len(pending_all)
    if total_batches:
        if progress:
            progress(0.0, f"Synthesizing {total_batches} batches")
        if _TTS_PARALLEL <= 1:
            completed = 0
            for chapter_id, batch_idx, work in pending_all:
                ba = _ensure_batch_audio(work, cache_root=cache_root, tts_client=tts_client)
                chapter_batch_audio_map[chapter_id].append((batch_idx, ba))
                completed += 1
                if progress:
                    progress(completed / total_batches, f"Synthesizing batches ({completed}/{total_batches})")
        else:
            worker = partial(_ensure_batch_audio, cache_root=cache_root, tts_client=tts_client)
            future_meta = {}
            completed = 0
            with ThreadPoolExecutor(max_workers=_TTS_PARALLEL) as executor:
                for chapter_id, batch_idx, work in pending_all:
                    fut = executor.submit(worker, work)
                    future_meta[fut] = (chapter_id, batch_idx)
                for fut in as_completed(future_meta):
                    chapter_id, batch_idx = future_meta[fut]
                    chapter_batch_audio_map[chapter_id].append((batch_idx, fut.result()))
                    completed += 1
                    if progress:
                        progress(completed / total_batches, f"Synthesizing batches ({completed}/{total_batches})")

    for chapter in selected.chapters:
        if request.incremental and chapter.id in existing:
            audio_path = storage.audio_dir(book.book_id) / f"{chapter.id}.mp3"
            if audio_path.exists():
                continue

        chapter_timings: list[SentenceTiming] = []
        sequence_files: list[Path] = []
        chapter_offset = 0.0
        ordered_batch_audio = sorted(chapter_batch_audio_map.get(chapter.id, []), key=lambda item: item[0])
        for _, ba in ordered_batch_audio:
            _append_batch_timings(ba, chapter_offset=chapter_offset, chapter_timings=chapter_timings)
            sequence_files.append(ba.path)
            chapter_offset += ba.duration

        chapter_file = storage.audio_dir(book.book_id) / f"{chapter.id}.mp3"
        _encode_pcm_parts_to_mp3(sequence_files, chapter_file)
        for pcm in sequence_files:
            pcm.unlink(missing_ok=True)
            pcm.with_suffix(".json").unlink(missing_ok=True)
        chapters_audio.append(
            ChapterAudioManifest(
                chapter_id=chapter.id,
                chapter_title=chapter.title,
                audio_file=chapter_file.name,
                sentences=chapter_timings,
            )
        )

    if existing:
        merged: dict[str, ChapterAudioManifest] = dict(existing)
        for ch in chapters_audio:
            merged[ch.chapter_id] = ch
        chapter_order = {ch.id: idx for idx, ch in enumerate(book.chapters)}
        ordered = sorted(merged.values(), key=lambda ch: chapter_order.get(ch.chapter_id, 10**9))
        chapters_audio = ordered

    if progress:
        progress(1.0, "Synthesis complete")
    return SynthesisManifest(book_id=book.book_id, title=book.title, chapters=chapters_audio)


def _encode_pcm_parts_to_mp3(parts: list[Path], output: Path) -> None:
    if not parts:
        output.write_bytes(b"")
        return
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "f32le",
            "-ar",
            str(KOKORO_SAMPLE_RATE),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-f",
            "mp3",
            str(output),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    for part in parts:
        proc.stdin.write(part.read_bytes())
    proc.stdin.close()
    _, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg chapter encode failed: {stderr.decode('utf-8', errors='replace')}")
