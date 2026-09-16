from __future__ import annotations

import argparse
import csv
import json
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import soundfile as sf
from mutagen.mp3 import MP3

from app.language_detect import split_sentences
from app.models import BookManifest, Chapter
from app.tts import KokoroTTS

# Setup notes:
# - pip install kokoro-onnx soundfile  (no espeak-ng or spacy required)
# - On first run kokoro-onnx downloads ~300MB of ONNX model weights.
#
# This script is intentionally standalone. It does not change runtime app behavior.
# It compares Google and Kokoro one sentence at a time so we can derive per-sentence
# timing without SSML marks on Kokoro output.

GOOGLE_COST_PER_MILLION_CHARS_USD = 16.0
KOKORO_SAMPLE_RATE = 24000

_KOKORO_CACHE = Path.home() / ".cache" / "kokoro-onnx"
_KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"


def _ensure_kokoro_files() -> tuple[Path, Path]:
    _KOKORO_CACHE.mkdir(parents=True, exist_ok=True)
    model_path = _KOKORO_CACHE / "kokoro-v1.0.onnx"
    voices_path = _KOKORO_CACHE / "voices-v1.0.bin"
    for url, path in [(_KOKORO_MODEL_URL, model_path), (_KOKORO_VOICES_URL, voices_path)]:
        if not path.exists():
            print(f"Downloading {path.name} (~{310 if 'onnx' in path.name else 27}MB)…")
            urllib.request.urlretrieve(url, path)
            print(f"  saved to {path}")
    return model_path, voices_path

DEFAULT_SAMPLE_TEXT = """
The rain had finally stopped, and the city sounded different after midnight.
Streetlights reflected in the wet pavement while a single train crossed the bridge.
She opened the old notebook and found a map sketched in blue ink.
It pointed to a small library that had been closed for years.
At dawn, they packed a thermos, locked the apartment, and followed the map anyway.
"""


@dataclass
class SentenceMetric:
    index: int
    text: str
    char_count: int
    google_latency_s: float
    google_duration_s: float
    google_t_start: float
    google_t_end: float
    kokoro_latency_s: float
    kokoro_duration_s: float
    kokoro_t_start: float
    kokoro_t_end: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Kokoro-82M vs Google Cloud TTS.")
    parser.add_argument("--book", help="Book ID under backend/data/<book_id>.", default=None)
    parser.add_argument("--chapter-id", help="Optional chapter ID when --book is set.", default=None)
    parser.add_argument("--max-sentences", type=int, default=20, help="Number of sentences to compare.")
    parser.add_argument("--output-dir", default="out/tts_compare", help="Output directory under backend/.")
    parser.add_argument("--no-warmup", action="store_true", help="Skip warm-up synthesis.")
    parser.add_argument("--google-language-code", default="en-US")
    parser.add_argument("--google-voice", default="en-US-Neural2-D")
    parser.add_argument("--google-speaking-rate", type=float, default=1.0)
    parser.add_argument("--google-pitch", type=float, default=0.0)
    parser.add_argument("--kokoro-voice", default="af_heart")
    parser.add_argument("--kokoro-speed", type=float, default=1.0)
    args = parser.parse_args()
    args.warmup = not args.no_warmup
    return args


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    return _backend_root() / "data"


def _normalize_sentences(raw_sentences: Iterable[str], max_sentences: int) -> list[str]:
    cleaned = [s.strip() for s in raw_sentences if s and s.strip()]
    if max_sentences > 0:
        cleaned = cleaned[:max_sentences]
    return cleaned


def _load_chapter(book: BookManifest, chapter_id: str | None) -> Chapter:
    if chapter_id:
        for chapter in book.chapters:
            if chapter.id == chapter_id:
                return chapter
        raise ValueError(f"Chapter '{chapter_id}' not found in book '{book.book_id}'.")
    if not book.chapters:
        raise ValueError(f"Book '{book.book_id}' has no chapters.")
    return book.chapters[0]


def load_sentences(args: argparse.Namespace) -> list[str]:
    if not args.book:
        return _normalize_sentences(split_sentences(DEFAULT_SAMPLE_TEXT), args.max_sentences)

    manifest_path = _data_dir() / args.book / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing manifest at {manifest_path}")
    book = BookManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    chapter = _load_chapter(book, args.chapter_id)
    return _normalize_sentences(split_sentences(chapter.plain_text), args.max_sentences)


def _mp3_duration_seconds(audio_bytes: bytes) -> float:
    if not audio_bytes:
        return 0.0
    return float(MP3(BytesIO(audio_bytes)).info.length)


def _synthesize_kokoro_sentence(kokoro: Any, sentence: str, voice: str, speed: float) -> list[float]:
    samples, _sr = kokoro.create(sentence, voice=voice, speed=speed, lang="en-us")
    if hasattr(samples, "flatten"):
        samples = samples.flatten()
    return [float(x) for x in samples]


def _concat_google_mp3(sentence_paths: list[Path], output_path: Path) -> None:
    if not sentence_paths:
        output_path.write_bytes(b"")
        return
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        list_path = Path(handle.name)
        for path in sentence_paths:
            escaped = str(path.resolve()).replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    finally:
        list_path.unlink(missing_ok=True)


def _print_summary(aggregate: dict[str, float]) -> None:
    print("")
    print("Aggregate")
    print(f"  total_chars: {int(aggregate['total_chars'])}")
    print(f"  google_total_latency_s: {aggregate['google_total_latency_s']:.3f}")
    print(f"  kokoro_total_latency_s: {aggregate['kokoro_total_latency_s']:.3f}")
    print(f"  google_total_audio_s: {aggregate['google_total_audio_s']:.3f}")
    print(f"  kokoro_total_audio_s: {aggregate['kokoro_total_audio_s']:.3f}")
    print(f"  google_mean_latency_s: {aggregate['google_mean_latency_s']:.3f}")
    print(f"  kokoro_mean_latency_s: {aggregate['kokoro_mean_latency_s']:.3f}")
    print(f"  google_rtf_audio_over_synth: {aggregate['google_rtf_audio_over_synth']:.3f}")
    print(f"  kokoro_rtf_audio_over_synth: {aggregate['kokoro_rtf_audio_over_synth']:.3f}")
    print(f"  google_estimated_cost_usd: {aggregate['google_estimated_cost_usd']:.4f}")
    print("  kokoro_estimated_cost_usd: 0.0000")


def main() -> None:
    args = parse_args()
    sentences = load_sentences(args)
    if not sentences:
        raise RuntimeError("No sentences found for comparison.")

    out_dir = (_backend_root() / args.output_dir).resolve()
    google_dir = out_dir / "google"
    kokoro_dir = out_dir / "kokoro"
    google_dir.mkdir(parents=True, exist_ok=True)
    kokoro_dir.mkdir(parents=True, exist_ok=True)

    from kokoro_onnx import Kokoro  # Deferred import so script can still print setup errors clearly.

    google_client = KokoroTTS()
    model_path, voices_path = _ensure_kokoro_files()
    kokoro_client = Kokoro(str(model_path), str(voices_path))

    if args.warmup:
        warmup_sentence = sentences[0]
        google_client.synthesize_plain(
            text=warmup_sentence,
            language_code=args.google_language_code,
            voice_name=args.google_voice,
            speaking_rate=args.google_speaking_rate,
            pitch=args.google_pitch,
        )
        _synthesize_kokoro_sentence(
            kokoro_client,
            warmup_sentence,
            voice=args.kokoro_voice,
            speed=args.kokoro_speed,
        )

    rows: list[SentenceMetric] = []
    google_sentence_paths: list[Path] = []
    kokoro_all_samples: list[float] = []

    google_cursor = 0.0
    kokoro_cursor = 0.0
    for idx, sentence in enumerate(sentences, start=1):
        google_start = time.perf_counter()
        google_audio = google_client.synthesize_plain(
            text=sentence,
            language_code=args.google_language_code,
            voice_name=args.google_voice,
            speaking_rate=args.google_speaking_rate,
            pitch=args.google_pitch,
        )
        google_latency = time.perf_counter() - google_start
        google_duration = _mp3_duration_seconds(google_audio)
        google_path = google_dir / f"s{idx:03d}.mp3"
        google_path.write_bytes(google_audio)
        google_sentence_paths.append(google_path)

        kokoro_start = time.perf_counter()
        kokoro_samples = _synthesize_kokoro_sentence(
            kokoro_client,
            sentence,
            voice=args.kokoro_voice,
            speed=args.kokoro_speed,
        )
        kokoro_latency = time.perf_counter() - kokoro_start
        kokoro_duration = len(kokoro_samples) / KOKORO_SAMPLE_RATE if kokoro_samples else 0.0
        kokoro_path = kokoro_dir / f"s{idx:03d}.wav"
        sf.write(kokoro_path, kokoro_samples, KOKORO_SAMPLE_RATE)
        kokoro_all_samples.extend(kokoro_samples)

        row = SentenceMetric(
            index=idx,
            text=sentence,
            char_count=len(sentence),
            google_latency_s=google_latency,
            google_duration_s=google_duration,
            google_t_start=google_cursor,
            google_t_end=google_cursor + google_duration,
            kokoro_latency_s=kokoro_latency,
            kokoro_duration_s=kokoro_duration,
            kokoro_t_start=kokoro_cursor,
            kokoro_t_end=kokoro_cursor + kokoro_duration,
        )
        rows.append(row)
        google_cursor = row.google_t_end
        kokoro_cursor = row.kokoro_t_end

    google_full_path = out_dir / "google_full.mp3"
    kokoro_full_path = out_dir / "kokoro_full.wav"
    _concat_google_mp3(google_sentence_paths, google_full_path)
    sf.write(kokoro_full_path, kokoro_all_samples, KOKORO_SAMPLE_RATE)

    total_chars = sum(r.char_count for r in rows)
    google_total_latency = sum(r.google_latency_s for r in rows)
    kokoro_total_latency = sum(r.kokoro_latency_s for r in rows)
    google_total_audio = sum(r.google_duration_s for r in rows)
    kokoro_total_audio = sum(r.kokoro_duration_s for r in rows)

    aggregate = {
        "total_chars": float(total_chars),
        "google_total_latency_s": google_total_latency,
        "kokoro_total_latency_s": kokoro_total_latency,
        "google_total_audio_s": google_total_audio,
        "kokoro_total_audio_s": kokoro_total_audio,
        "google_mean_latency_s": google_total_latency / len(rows),
        "kokoro_mean_latency_s": kokoro_total_latency / len(rows),
        "google_rtf_audio_over_synth": (google_total_audio / google_total_latency) if google_total_latency else 0.0,
        "kokoro_rtf_audio_over_synth": (kokoro_total_audio / kokoro_total_latency) if kokoro_total_latency else 0.0,
        "google_estimated_cost_usd": (total_chars / 1_000_000.0) * GOOGLE_COST_PER_MILLION_CHARS_USD,
    }

    results_csv = out_dir / "results.csv"
    with results_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "index",
                "text",
                "char_count",
                "google_latency_s",
                "google_duration_s",
                "google_t_start",
                "google_t_end",
                "kokoro_latency_s",
                "kokoro_duration_s",
                "kokoro_t_start",
                "kokoro_t_end",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    results_json = out_dir / "results.json"
    payload = {
        "config": {
            "book": args.book,
            "chapter_id": args.chapter_id,
            "max_sentences": args.max_sentences,
            "google_language_code": args.google_language_code,
            "google_voice": args.google_voice,
            "google_speaking_rate": args.google_speaking_rate,
            "google_pitch": args.google_pitch,
            "kokoro_voice": args.kokoro_voice,
            "kokoro_speed": args.kokoro_speed,
            "warmup": args.warmup,
        },
        "aggregate": aggregate,
        "sentence_metrics": [asdict(row) for row in rows],
        "artifacts": {
            "google_dir": str(google_dir),
            "kokoro_dir": str(kokoro_dir),
            "google_full": str(google_full_path),
            "kokoro_full": str(kokoro_full_path),
            "results_csv": str(results_csv),
            "results_json": str(results_json),
        },
    }
    results_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Sentence metrics")
    print("idx chars g_lat g_dur k_lat k_dur")
    for row in rows:
        print(
            f"{row.index:>3} {row.char_count:>5} "
            f"{row.google_latency_s:>5.2f} {row.google_duration_s:>5.2f} "
            f"{row.kokoro_latency_s:>5.2f} {row.kokoro_duration_s:>5.2f}"
        )
    _print_summary(aggregate)
    print("")
    print(f"Wrote: {results_json}")


if __name__ == "__main__":
    main()
