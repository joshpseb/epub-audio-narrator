from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import SynthesizeRequest
from app.synthesizer import _build_sentence_contexts, _collect_batches_for_chapter, _ensure_batch_audio, _voice_for_lang
from app.tts import KokoroTTS


def _load_chapter_text(data_dir: Path, book_id: str | None, chapter_id: str | None, max_chars: int) -> tuple[str, str, str]:
    if book_id:
        manifests = [data_dir / book_id / "manifest.json"]
    else:
        manifests = sorted(data_dir.glob("*/manifest.json"))
    if not manifests:
        raise RuntimeError(f"No manifest.json files found in {data_dir}")
    for manifest_path in manifests:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        chapters = payload.get("chapters", [])
        chosen = None
        if chapter_id:
            for chapter in chapters:
                if chapter.get("id") == chapter_id:
                    chosen = chapter
                    break
        else:
            chapters_sorted = sorted(chapters, key=lambda c: int(c.get("word_count", 0)), reverse=True)
            chosen = chapters_sorted[0] if chapters_sorted else None
        if not chosen:
            continue
        text = (chosen.get("plain_text") or "").strip()
        if not text:
            continue
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars]
        return payload.get("book_id", manifest_path.parent.name), chosen.get("id", "unknown"), text
    raise RuntimeError("No chapter text found for benchmark")


def _run_single(chapter_text: str, parallel: int) -> dict[str, float]:
    request = SynthesizeRequest()
    voice_map = _voice_for_lang(request)
    chapter_id = "bench-chapter"
    chapter_sentences = _build_sentence_contexts(chapter_text, chapter_id)
    pending = _collect_batches_for_chapter(chapter_sentences, chapter_id=chapter_id, voice_map=voice_map)
    tts_client = KokoroTTS()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="kokoro-bench-") as temp_dir:
        cache_root = Path(temp_dir)
        if parallel <= 1:
            batch_audios = [_ensure_batch_audio(work, cache_root=cache_root, tts_client=tts_client) for work in pending]
        else:
            worker = partial(_ensure_batch_audio, cache_root=cache_root, tts_client=tts_client)
            with ThreadPoolExecutor(max_workers=parallel) as executor:
                batch_audios = list(executor.map(worker, pending))
    elapsed = time.perf_counter() - started
    audio_seconds = sum(batch.duration for batch in batch_audios)
    rtf = (audio_seconds / elapsed) if elapsed > 0 else 0.0
    return {
        "elapsed_seconds": round(elapsed, 3),
        "audio_seconds": round(audio_seconds, 3),
        "rtf": round(rtf, 3),
        "batch_count": float(len(pending)),
    }


def _run_worker(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).resolve()
    _, _, chapter_text = _load_chapter_text(data_dir, args.book_id, args.chapter_id, args.max_chars)
    result = _run_single(chapter_text=chapter_text, parallel=args.parallel)
    print(json.dumps(result))
    return 0


def _default_matrix(cpu_count: int) -> list[dict[str, str | int]]:
    coreml_parallel = [1, 2, 3]
    cpu_parallel = [1, 2, 4, 6]
    return [
        *[
            {
                "name": f"CPU-P{parallel}",
                "provider": "CPUExecutionProvider",
                "parallel": parallel,
                "intra_op": max(1, cpu_count // max(1, parallel)),
            }
            for parallel in cpu_parallel
        ],
        *[
            {
                "name": f"CoreML-P{parallel}",
                "provider": "CoreMLExecutionProvider",
                "parallel": parallel,
                "intra_op": 1,
            }
            for parallel in coreml_parallel
        ],
    ]


def _run_matrix(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).resolve()
    book_id, chapter_id, chapter_text = _load_chapter_text(data_dir, args.book_id, args.chapter_id, args.max_chars)
    print(f"Benchmark chapter: book_id={book_id} chapter_id={chapter_id} chars={len(chapter_text)}", flush=True)
    cpu_count = os.cpu_count() or 1
    matrix = _default_matrix(cpu_count)
    worker_args = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--data-dir",
        str(data_dir),
        "--book-id",
        book_id,
        "--chapter-id",
        chapter_id,
        "--max-chars",
        str(args.max_chars),
    ]
    rows: list[dict[str, str | int | float]] = []
    for combo in matrix:
        env = os.environ.copy()
        env["ONNX_PROVIDER"] = str(combo["provider"])
        env["ONNX_INTRA_OP_THREADS"] = str(combo["intra_op"])
        try:
            proc = subprocess.run(
                worker_args + ["--parallel", str(combo["parallel"])],
                capture_output=True,
                text=True,
                env=env,
                check=False,
                timeout=args.combo_timeout,
            )
        except subprocess.TimeoutExpired:
            rows.append(
                {
                    "name": combo["name"],
                    "provider": combo["provider"],
                    "parallel": combo["parallel"],
                    "intra_op": combo["intra_op"],
                    "status": "timeout",
                    "detail": f"timed out after {args.combo_timeout}s",
                }
            )
            print(
                f"[{combo['name']}] provider={combo['provider']} parallel={combo['parallel']} "
                f"intra_op={combo['intra_op']} status=timeout",
                flush=True,
            )
            continue
        if proc.returncode != 0:
            rows.append(
                {
                    "name": combo["name"],
                    "provider": combo["provider"],
                    "parallel": combo["parallel"],
                    "intra_op": combo["intra_op"],
                    "status": "error",
                    "detail": proc.stderr.strip() or proc.stdout.strip(),
                }
            )
            continue
        metric = json.loads(proc.stdout.strip().splitlines()[-1])
        rows.append(
            {
                "name": combo["name"],
                "provider": combo["provider"],
                "parallel": combo["parallel"],
                "intra_op": combo["intra_op"],
                "status": "ok",
                **metric,
            }
        )
        print(
            f"[{combo['name']}] provider={combo['provider']} parallel={combo['parallel']} "
            f"intra_op={combo['intra_op']} elapsed={metric['elapsed_seconds']}s rtf={metric['rtf']}",
            flush=True,
        )
    successful = [row for row in rows if row.get("status") == "ok"]
    if successful:
        best = min(successful, key=lambda row: float(row["elapsed_seconds"]))
        print(
            f"BEST name={best['name']} provider={best['provider']} "
            f"parallel={best['parallel']} intra_op={best['intra_op']} "
            f"elapsed={best['elapsed_seconds']}s rtf={best['rtf']}",
            flush=True,
        )
    print("RESULTS_JSON")
    print(json.dumps(rows, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Kokoro synthesis configurations.")
    parser.add_argument("--worker", action="store_true", help="Run one benchmark worker invocation.")
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="Path to backend data directory.")
    parser.add_argument("--book-id", default=None, help="Book ID to benchmark.")
    parser.add_argument("--chapter-id", default=None, help="Chapter ID to benchmark.")
    parser.add_argument("--max-chars", type=int, default=15000, help="Trim chapter text to this many chars.")
    parser.add_argument("--parallel", type=int, default=1, help="Parallel batch workers (worker mode).")
    parser.add_argument("--combo-timeout", type=int, default=600, help="Max seconds per matrix combo.")
    args = parser.parse_args()
    if args.worker:
        return _run_worker(args)
    return _run_matrix(args)


if __name__ == "__main__":
    raise SystemExit(main())
