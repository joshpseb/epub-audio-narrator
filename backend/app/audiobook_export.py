from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from mutagen.mp4 import MP4

from .models import ChapterAudioManifest, SynthesisManifest


def _stamp_apple_media_kind_audiobook(path: Path) -> None:
    """Write iTunes-compatible Media Kind audiobook atom (``stik=2``).

    FFmpeg does not reliably write ``stik`` for MPEG-4 output; ``-metadata
    stik=2`` is dropped. Books on macOS often plays such files anyway, while
    Books on iOS frequently refuses imports without this atom.
    """
    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    audio.tags["stik"] = [2]
    audio.save(str(path))


def _audio_duration_ms(path: Path) -> int:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    seconds = float((proc.stdout or "0").strip() or 0.0)
    return max(1, int(round(seconds * 1000)))


def _chapter_duration_ms(chapter: ChapterAudioManifest, chapter_path: Path) -> int:
    if chapter.sentences:
        te = max(s.t_end for s in chapter.sentences)
        if te > 0:
            return max(1, int(round(te * 1000)))
    return _audio_duration_ms(chapter_path)


def _escape_ffmeta(value: str) -> str:
    return value.replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#").strip()


def _export_fingerprint(
    synthesis: SynthesisManifest,
    audio_dir: Path,
    effective_title: str,
    artist: str,
) -> str:
    lines = [effective_title, artist]
    for ch in synthesis.chapters:
        p = (audio_dir / ch.audio_file).resolve()
        if p.exists():
            st = p.stat()
            lines.append(f"{p.as_posix()}\t{st.st_mtime_ns}")
        else:
            lines.append(f"missing\t{ch.audio_file}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def export_m4b(
    book_dir: Path,
    synthesis: SynthesisManifest,
    authors: list[str] | None = None,
    title_override: str | None = None,
    output_slug: str | None = None,
) -> Path:
    audio_dir = book_dir / "audio"
    export_dir = book_dir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    slug = output_slug or "book"
    concat_file = export_dir / f"{slug}.concat.txt"
    metadata_file = export_dir / f"{slug}.ffmeta"
    output_m4b = export_dir / f"{slug}.m4b"
    meta_path = export_dir / f"{slug}.export_meta.json"

    artist = ", ".join(authors or []) or "Unknown"
    effective_title = (title_override or synthesis.title).strip() or synthesis.title
    fingerprint = _export_fingerprint(synthesis, audio_dir, effective_title, artist)

    if output_m4b.exists() and meta_path.exists():
        try:
            prev = json.loads(meta_path.read_text(encoding="utf-8"))
            if prev.get("fingerprint") == fingerprint:
                return output_m4b
        except Exception:
            pass

    with concat_file.open("w", encoding="utf-8") as f:
        for chapter in synthesis.chapters:
            f.write(f"file '{(audio_dir / chapter.audio_file).resolve().as_posix()}'\n")

    ms_cursor = 0
    lines = [";FFMETADATA1", f"title={_escape_ffmeta(effective_title)}", f"artist={_escape_ffmeta(artist)}"]
    for chapter in synthesis.chapters:
        chapter_path = (audio_dir / chapter.audio_file).resolve()
        chapter_duration = _chapter_duration_ms(chapter, chapter_path)
        chapter_start = ms_cursor
        chapter_end = chapter_start + chapter_duration
        lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={chapter_start}",
                f"END={max(chapter_start + 1, chapter_end - 1)}",
                f"title={_escape_ffmeta(chapter.chapter_title)}",
            ]
        )
        ms_cursor = chapter_end

    metadata_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Encode directly from the concat list at 48 kbps mono AAC (+44.1 kHz AAC-LC for wide device cover).
    # Apple Books: map 0:a, +faststart, genre=Audiobooks; stik=2 applied post-encode (see
    # `_stamp_apple_media_kind_audiobook`).
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-threads",
            "0",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-i",
            str(metadata_file),
            "-map",
            "0:a",
            "-map_metadata",
            "1",
            "-c:a",
            "aac",
            "-profile:a",
            "aac_low",
            "-b:a",
            "48k",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-metadata",
            "genre=Audiobooks",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(output_m4b),
        ],
        check=True,
        capture_output=True,
    )
    _stamp_apple_media_kind_audiobook(output_m4b)
    try:
        meta_path.write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
    except Exception:
        pass
    return output_m4b
