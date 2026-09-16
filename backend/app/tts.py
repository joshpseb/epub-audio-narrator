from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import platform
import subprocess
from typing import Literal, cast
import urllib.request
from xml.sax.saxutils import escape

from google.cloud import texttospeech
from kokoro_onnx import Kokoro
from kokoro_onnx.config import MAX_PHONEME_LENGTH
from mutagen.mp3 import MP3
import numpy as np
import onnxruntime as rt

KOKORO_SAMPLE_RATE = 24000

# Kokoro runs one ONNX inference per phoneme batch, with significant fixed
# overhead per call. Packing many short sentences into a single inference (up to
# the model's phoneme window) cuts the number of inferences by several times.
# Leave a small margin below the hard limit so the model's internal splitter
# never has to re-split a pack we built.
_PHONEME_PACK_BUDGET = max(
    64, min(MAX_PHONEME_LENGTH, int(os.getenv("KOKORO_PHONEME_PACK", str(MAX_PHONEME_LENGTH - 10))))
)
KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
DEFAULT_VOICE = "af_heart"
STATIC_VOICES: list[str] = [
    "af_heart",
    "af_bella",
    "af_nicole",
    "af_sarah",
    "am_michael",
    "am_adam",
    "bf_emma",
    "bf_isabella",
    "bm_george",
    "bm_lewis",
]

DEFAULT_VOICE_MAP: dict[str, str] = {
    "en": DEFAULT_VOICE,
}


@dataclass
class TtsSentenceChunk:
    id: str
    text: str


@dataclass
class SynthResult:
    audio_bytes: bytes
    marks: dict[str, float]
    duration_seconds: float = 0.0


@dataclass
class SynthPcmResult:
    samples: np.ndarray
    marks: dict[str, float]
    duration_seconds: float = 0.0


def mp3_duration_seconds(data: bytes) -> float:
    """Decode MP3 length from bytes (avoids ffprobe per segment)."""
    if not data:
        return 0.0
    try:
        return float(MP3(BytesIO(data)).info.length)
    except Exception:
        return 0.0


def _duration_fallback_from_marks(marks: dict[str, float]) -> float:
    if not marks:
        return 0.0
    return float(max(marks.values())) + 0.25


@dataclass
class VoiceInfo:
    name: str
    gender: str
    language_codes: list[str]
    natural_sample_rate_hertz: int
    tier: Literal["Studio", "Neural2", "Wavenet", "Standard", "Chirp", "Other"]


def _ensure_kokoro_files() -> tuple[Path, Path]:
    model_override = os.getenv("KOKORO_MODEL_PATH", "").strip()
    voices_override = os.getenv("KOKORO_VOICES_PATH", "").strip()
    if model_override and voices_override:
        return Path(model_override).expanduser().resolve(), Path(voices_override).expanduser().resolve()

    cache_dir = Path(os.getenv("KOKORO_CACHE_DIR", str(Path.home() / ".cache" / "kokoro-onnx"))).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_path = Path(model_override).expanduser().resolve() if model_override else (cache_dir / "kokoro-v1.0.onnx")
    voices_path = Path(voices_override).expanduser().resolve() if voices_override else (cache_dir / "voices-v1.0.bin")

    for url, path in ((KOKORO_MODEL_URL, model_path), (KOKORO_VOICES_URL, voices_path)):
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, path)

    return model_path, voices_path


def _coreml_unavailable_marker() -> Path:
    cache_dir = Path(os.getenv("KOKORO_CACHE_DIR", str(Path.home() / ".cache" / "kokoro-onnx"))).expanduser()
    return cache_dir / "coreml_unavailable"


def _resolve_onnx_providers() -> list[str]:
    env_provider = os.getenv("ONNX_PROVIDER", "").strip()
    if env_provider:
        return [env_provider]
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        if not _coreml_unavailable_marker().exists():
            return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _resolve_onnx_provider_options(providers: list[str]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    cache_dir = Path(os.getenv("KOKORO_CACHE_DIR", str(Path.home() / ".cache" / "kokoro-onnx"))).expanduser()
    coreml_cache = cache_dir / "coreml-model-cache"
    coreml_cache.mkdir(parents=True, exist_ok=True)
    for provider in providers:
        if provider == "CoreMLExecutionProvider":
            options.append(
                {
                    "ModelFormat": "MLProgram",
                    # ANE ("ALL") triggers a zero-element iSTFT shape error on
                    # this Kokoro model; CPUAndGPU avoids the ANE path.
                    "MLComputeUnits": "CPUAndGPU",
                    "ModelCacheDirectory": str(coreml_cache),
                }
            )
        else:
            options.append({})
    return options


def _default_intra_op_threads() -> int:
    providers = _resolve_onnx_providers()
    if providers and providers[0] == "CoreMLExecutionProvider":
        return 1
    return 2


def _build_onnx_session(model_path: Path, providers: list[str] | None = None) -> rt.InferenceSession:
    resolved_providers = providers if providers else _resolve_onnx_providers()
    opts = rt.SessionOptions()
    opts.intra_op_num_threads = max(1, int(os.getenv("ONNX_INTRA_OP_THREADS", str(_default_intra_op_threads()))))
    opts.inter_op_num_threads = max(1, int(os.getenv("ONNX_INTER_OP_THREADS", "1")))
    opts.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
    opts.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
    return rt.InferenceSession(
        str(model_path),
        sess_options=opts,
        providers=resolved_providers,
        provider_options=_resolve_onnx_provider_options(resolved_providers),
    )


class KokoroTTS:
    def __init__(self) -> None:
        self._voices_cache: dict[str, list[VoiceInfo]] = {}
        self._warmed_up = False
        model_path, voices_path = _ensure_kokoro_files()
        self._model_path = model_path
        self._voices_path = voices_path
        requested = _resolve_onnx_providers()
        self.model = self._build_model(requested)
        # If CoreML was requested but ONNX RT already fell back internally
        # (e.g. invalid MLComputeUnits or unsupported EP), record that so
        # future startups skip the CoreML probe entirely.
        if "CoreMLExecutionProvider" in requested and not self._uses_coreml():
            try:
                _coreml_unavailable_marker().touch()
            except OSError:
                pass

    def _build_model(self, providers: list[str]) -> Kokoro:
        session = _build_onnx_session(self._model_path, providers=providers)
        return Kokoro.from_session(session, str(self._voices_path))

    def _uses_coreml(self) -> bool:
        try:
            providers = self.model.sess.get_providers()
        except Exception:
            return False
        return any(provider == "CoreMLExecutionProvider" for provider in providers)

    def _fallback_to_cpu(self) -> None:
        # Persist the failure so future startups skip the CoreML probe entirely.
        try:
            _coreml_unavailable_marker().touch()
        except OSError:
            pass
        self.model = self._build_model(["CPUExecutionProvider"])
        self._warmed_up = False

    def warmup(self) -> None:
        if self._warmed_up:
            return
        self._synthesize_samples("warm up", voice_name=DEFAULT_VOICE, speaking_rate=1.0)
        self._warmed_up = True

    def list_voices(self, language_code: str) -> list[VoiceInfo]:
        lang_key = language_code.strip().lower()
        if lang_key in self._voices_cache:
            return self._voices_cache[lang_key]
        voices = [
            VoiceInfo(
                name=name,
                gender="FEMALE" if "_f" in name or name.startswith(("af_", "bf_")) else "MALE",
                language_codes=["en-US"] if name.startswith(("af_", "am_")) else ["en-GB"],
                natural_sample_rate_hertz=KOKORO_SAMPLE_RATE,
                tier="Standard",
            )
            for name in STATIC_VOICES
        ]
        self._voices_cache[lang_key] = voices
        return voices

    def _voice_candidates(self, language_code: str) -> list[str]:
        return [voice.name for voice in self.list_voices(language_code)]

    def _resolve_voice_name(self, language_code: str, requested_voice: str) -> str:
        candidates = self._voice_candidates(language_code)
        if not candidates:
            return requested_voice
        if requested_voice and requested_voice in candidates:
            return requested_voice
        return DEFAULT_VOICE if DEFAULT_VOICE in candidates else candidates[0]

    def _phonemize(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return ""
        return self.model.tokenizer.phonemize(stripped, "en-us")

    def _synthesize_samples(
        self, text: str, *, voice_name: str, speaking_rate: float, is_phonemes: bool = False
    ) -> np.ndarray:
        if not text.strip():
            return np.empty(0, dtype=np.float32)
        try:
            samples, _ = self.model.create(
                text,
                voice=voice_name,
                speed=speaking_rate,
                lang="en-us",
                is_phonemes=is_phonemes,
            )
        except Exception:
            if self._uses_coreml():
                self._fallback_to_cpu()
                samples, _ = self.model.create(
                    text,
                    voice=voice_name,
                    speed=speaking_rate,
                    lang="en-us",
                    is_phonemes=is_phonemes,
                )
                flattened = samples.flatten() if hasattr(samples, "flatten") else samples
                return np.ascontiguousarray(flattened, dtype=np.float32)
            if voice_name == DEFAULT_VOICE:
                raise
            samples, _ = self.model.create(
                text,
                voice=DEFAULT_VOICE,
                speed=speaking_rate,
                lang="en-us",
                is_phonemes=is_phonemes,
            )
        flattened = samples.flatten() if hasattr(samples, "flatten") else samples
        return np.ascontiguousarray(flattened, dtype=np.float32)

    def _encode_mp3(self, samples: np.ndarray) -> bytes:
        if samples.size == 0:
            return b""
        # Encode through ffmpeg so downstream pipeline stays mp3-compatible.
        pcm = np.ascontiguousarray(samples, dtype=np.float32).tobytes()
        proc = subprocess.run(
            [
                "ffmpeg",
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
                "pipe:1",
            ],
            input=pcm,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg mp3 encode failed: {stderr}")
        return bytes(proc.stdout)

    def synthesize_with_marks(
        self,
        *,
        language_code: str,
        voice_name: str,
        chunks: list[TtsSentenceChunk],
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
    ) -> SynthResult:
        pcm_result = self.synthesize_with_marks_pcm(
            language_code=language_code,
            voice_name=voice_name,
            chunks=chunks,
            speaking_rate=speaking_rate,
            pitch=pitch,
        )
        raw = self._encode_mp3(pcm_result.samples)
        dur = mp3_duration_seconds(raw)
        if dur <= 0.0:
            dur = pcm_result.duration_seconds
        return SynthResult(audio_bytes=raw, marks=pcm_result.marks, duration_seconds=dur)

    def synthesize_with_marks_pcm(
        self,
        *,
        language_code: str,
        voice_name: str,
        chunks: list[TtsSentenceChunk],
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
    ) -> SynthPcmResult:
        if not chunks:
            return SynthPcmResult(samples=np.empty(0, dtype=np.float32), marks={}, duration_seconds=0.0)
        chosen_voice = self._resolve_voice_name(language_code, voice_name)

        # Phonemize each sentence once up front so we can pack several sentences
        # into a single model inference instead of paying per-sentence overhead.
        phonemized: list[tuple[str, str]] = [(chunk.id, self._phonemize(chunk.text)) for chunk in chunks]

        # Greedily group consecutive sentences until adding the next would exceed
        # the phoneme budget. Each group becomes one ONNX inference.
        packs: list[list[tuple[str, str]]] = []
        current: list[tuple[str, str]] = []
        current_len = 0
        for sid, phonemes in phonemized:
            extra = len(phonemes) + 1  # +1 for the space joiner
            if current and current_len + extra > _PHONEME_PACK_BUDGET:
                packs.append(current)
                current = []
                current_len = 0
            current.append((sid, phonemes))
            current_len += extra
        if current:
            packs.append(current)

        marks: dict[str, float] = {}
        all_samples: list[np.ndarray] = []
        cursor = 0.0
        for pack in packs:
            joined = " ".join(phonemes for _, phonemes in pack if phonemes)
            if not joined.strip():
                # Whole pack is silent/empty text; anchor marks at the cursor.
                for sid, _ in pack:
                    marks[sid] = cursor
                continue
            samples = self._synthesize_samples(
                joined, voice_name=chosen_voice, speaking_rate=speaking_rate, is_phonemes=True
            )
            pack_duration = len(samples) / KOKORO_SAMPLE_RATE
            # Distribute the pack's duration across its sentences by phoneme share
            # so highlight marks stay close to real sentence boundaries.
            total_phonemes = sum(len(phonemes) for _, phonemes in pack) or 1
            local = cursor
            for sid, phonemes in pack:
                marks[sid] = local
                local += pack_duration * (len(phonemes) / total_phonemes)
            all_samples.append(samples)
            cursor += pack_duration

        merged = np.concatenate(all_samples) if all_samples else np.empty(0, dtype=np.float32)
        dur = len(merged) / KOKORO_SAMPLE_RATE
        if dur <= 0.0:
            dur = _duration_fallback_from_marks(marks)
        return SynthPcmResult(samples=merged, marks=marks, duration_seconds=dur)

    def synthesize_plain(
        self,
        *,
        text: str,
        language_code: str,
        voice_name: str,
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
    ) -> bytes:
        chosen_voice = self._resolve_voice_name(language_code, voice_name)
        samples = self._synthesize_samples(text, voice_name=chosen_voice, speaking_rate=speaking_rate)
        return self._encode_mp3(samples)


def _google_tier_from_voice_name(name: str) -> Literal["Studio", "Neural2", "Wavenet", "Standard", "Chirp", "Other"]:
    upper = name.upper()
    if "STUDIO" in upper:
        return "Studio"
    if "NEURAL2" in upper:
        return "Neural2"
    if "WAVENET" in upper:
        return "Wavenet"
    if "CHIRP" in upper:
        return "Chirp"
    if "STANDARD" in upper:
        return "Standard"
    return "Other"


class GoogleTTS:
    def __init__(self) -> None:
        self._voices_cache: dict[str, list[VoiceInfo]] = {}
        self._client = texttospeech.TextToSpeechClient()
        self._warmed_up = False

    def warmup(self) -> None:
        self._warmed_up = True

    def list_voices(self, language_code: str) -> list[VoiceInfo]:
        lang_key = language_code.strip().lower()
        if lang_key in self._voices_cache:
            return self._voices_cache[lang_key]
        response = self._client.list_voices(language_code=language_code)
        voices = [
            VoiceInfo(
                name=voice.name,
                gender=texttospeech.SsmlVoiceGender(voice.ssml_gender).name,
                language_codes=list(voice.language_codes),
                natural_sample_rate_hertz=int(voice.natural_sample_rate_hertz),
                tier=_google_tier_from_voice_name(voice.name),
            )
            for voice in response.voices
        ]
        self._voices_cache[lang_key] = voices
        return voices

    def _voice_candidates(self, language_code: str) -> list[str]:
        return [voice.name for voice in self.list_voices(language_code)]

    def _resolve_voice_name(self, language_code: str, requested_voice: str) -> str:
        candidates = self._voice_candidates(language_code)
        if not candidates:
            return requested_voice
        if requested_voice and requested_voice in candidates:
            return requested_voice
        default_for_lang = DEFAULT_VOICE_MAP.get(language_code.split("-")[0].lower(), "")
        if default_for_lang and default_for_lang in candidates:
            return default_for_lang
        return candidates[0]

    def _build_marked_ssml(self, chunks: list[TtsSentenceChunk]) -> str:
        parts: list[str] = ["<speak>"]
        for chunk in chunks:
            parts.append(f'<mark name="{escape(chunk.id)}"/>')
            parts.append(escape(chunk.text))
        parts.append("</speak>")
        return "".join(parts)

    def _pcm_from_linear16(self, audio_bytes: bytes) -> np.ndarray:
        if not audio_bytes:
            return np.empty(0, dtype=np.float32)
        pcm_i16 = np.frombuffer(audio_bytes, dtype="<i2")
        if pcm_i16.size == 0:
            return np.empty(0, dtype=np.float32)
        return np.ascontiguousarray(pcm_i16.astype(np.float32) / 32768.0, dtype=np.float32)

    def _encode_mp3(self, samples: np.ndarray) -> bytes:
        if samples.size == 0:
            return b""
        pcm = np.ascontiguousarray(samples, dtype=np.float32).tobytes()
        proc = subprocess.run(
            [
                "ffmpeg",
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
                "pipe:1",
            ],
            input=pcm,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg mp3 encode failed: {stderr}")
        return bytes(proc.stdout)

    def synthesize_with_marks(
        self,
        *,
        language_code: str,
        voice_name: str,
        chunks: list[TtsSentenceChunk],
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
    ) -> SynthResult:
        pcm_result = self.synthesize_with_marks_pcm(
            language_code=language_code,
            voice_name=voice_name,
            chunks=chunks,
            speaking_rate=speaking_rate,
            pitch=pitch,
        )
        raw = self._encode_mp3(pcm_result.samples)
        dur = mp3_duration_seconds(raw)
        if dur <= 0.0:
            dur = pcm_result.duration_seconds
        return SynthResult(audio_bytes=raw, marks=pcm_result.marks, duration_seconds=dur)

    def synthesize_with_marks_pcm(
        self,
        *,
        language_code: str,
        voice_name: str,
        chunks: list[TtsSentenceChunk],
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
    ) -> SynthPcmResult:
        if not chunks:
            return SynthPcmResult(samples=np.empty(0, dtype=np.float32), marks={}, duration_seconds=0.0)
        chosen_voice = self._resolve_voice_name(language_code, voice_name)
        response = self._client.synthesize_speech(
            input=texttospeech.SynthesisInput(ssml=self._build_marked_ssml(chunks)),
            voice=texttospeech.VoiceSelectionParams(language_code=language_code, name=chosen_voice),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=KOKORO_SAMPLE_RATE,
                speaking_rate=speaking_rate,
                pitch=pitch,
            ),
        )
        samples = self._pcm_from_linear16(response.audio_content)
        duration_seconds = len(samples) / KOKORO_SAMPLE_RATE
        marks: dict[str, float] = {}
        # v1 API doesn't expose SSML timepoints; estimate via character ratio
        cursor = 0.0
        total_chars = max(1, sum(len(chunk.text.strip()) for chunk in chunks))
        for chunk in chunks:
            marks[chunk.id] = cursor
            cursor += duration_seconds * (len(chunk.text.strip()) / total_chars)
        if duration_seconds <= 0.0:
            duration_seconds = _duration_fallback_from_marks(marks)
        return SynthPcmResult(samples=samples, marks=marks, duration_seconds=duration_seconds)

    def synthesize_plain(
        self,
        *,
        text: str,
        language_code: str,
        voice_name: str,
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
    ) -> bytes:
        chosen_voice = self._resolve_voice_name(language_code, voice_name)
        response = self._client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(language_code=language_code, name=chosen_voice),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=speaking_rate,
                pitch=pitch,
            ),
        )
        return bytes(response.audio_content)


_GLOBAL_TTS: dict[str, KokoroTTS | GoogleTTS] = {}


def get_tts_for_provider(provider: str) -> KokoroTTS | GoogleTTS:
    key = provider.strip().lower() or "kokoro"
    if key not in {"kokoro", "google"}:
        raise ValueError(f"Unsupported tts provider: {provider}")
    if key not in _GLOBAL_TTS:
        _GLOBAL_TTS[key] = GoogleTTS() if key == "google" else KokoroTTS()
        _GLOBAL_TTS[key].warmup()
    return _GLOBAL_TTS[key]


def get_tts() -> KokoroTTS:
    return cast(KokoroTTS, get_tts_for_provider("kokoro"))
