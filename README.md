# EPUB Audio Narrator

Local app to convert EPUB books into narrated audio, read along with sentence highlighting, and export chapterized M4B files.

## Features

- EPUB upload + chapter parsing with title normalization (`Book N, Chapter M`)
- Cleanup controls for TOC/front/back matter + minimum chapter length
- Kokoro-82M local TTS synthesis with sentence-level timing marks
- Incremental synthesis (skip already-synthesized chapters)
- Synthesis scope:
  - next N chapters
  - chapter range
  - by book/volume
  - whole book
- M4B export with chapter markers, AAC-LC + faststart, audiobook genre, and **iTunes Media Kind (`stik`) tagging** (`mutagen`) so **iPhone Apple Books imports match macOS reliability** — re-download exports created before this tagging if iOS rejects them.
- Reader with click-to-seek, chapter list, keyboard controls, resume state, and autoplay-next-chapter
- Voice preview + free local synthesis

## Project structure

- `backend/` - FastAPI API and synthesis pipeline
- `frontend/` - Vite + React UI

## Prerequisites

- Python 3.11+
- Node.js 20+
- `ffmpeg` and `ffprobe` on `PATH`
- First synthesis run downloads Kokoro model files (~340MB total)
## Kokoro model setup

The backend downloads Kokoro model files automatically on first TTS use:

- `kokoro-v1.0.onnx` (~310MB)
- `voices-v1.0.bin` (~27MB)

Optional env var overrides:

```bash
KOKORO_MODEL_PATH=/absolute/path/to/kokoro-v1.0.onnx
KOKORO_VOICES_PATH=/absolute/path/to/voices-v1.0.bin
KOKORO_CACHE_DIR=/custom/cache/dir
```

## Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m uvicorn app.main:app --reload --port 8000
```

Use `python -m uvicorn` (not the bare `uvicorn` command) so the server always runs on the same interpreter as your activated venv. With Conda `base` still enabled, `uvicorn` on `PATH` can resolve to Conda’s copy and miss packages such as `mutagen` installed only in `.venv`.

Optional backend env vars:

- `APP_API_TOKEN` - require `x-api-key` (or `api_key` query param for SSE)
- `CORS_ORIGINS` - comma-separated extra allowed origins
- `TTS_PARALLEL` - batch worker count (default: `4` on CPU, `2` on CoreML)
- `TTS_MAX_BATCH_CHARS` - max characters per synthesis batch (default: `2500`)
- `TTS_MAX_BATCH_SENTENCES` - max sentences per synthesis batch (default: `25`)
- `ONNX_PROVIDER` - override provider (`CPUExecutionProvider` or `CoreMLExecutionProvider`)
- `ONNX_INTRA_OP_THREADS` - ONNX intra-op thread count (default: `2` on CPU, `1` on CoreML)
- `ONNX_INTER_OP_THREADS` - ONNX inter-op thread count (default: `1`)

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Optional frontend env vars (`frontend/.env`):

```bash
VITE_API_BASE=http://localhost:8000
VITE_API_TOKEN=your-shared-token
```

Open `http://localhost:5173`.

## Reader shortcuts

- `Space` play/pause
- `J` previous sentence
- `K` next sentence
- `ArrowLeft` previous chapter
- `ArrowRight` next chapter
- `[` slower playback
- `]` faster playback
