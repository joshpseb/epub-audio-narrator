from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Chapter(BaseModel):
    id: str
    title: str
    html: str
    plain_text: str
    word_count: int


class BookManifest(BaseModel):
    book_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    chapters: list[Chapter]


class CleanupConfig(BaseModel):
    min_chapter_words: int = 20
    drop_frontmatter: bool = True
    drop_backmatter: bool = True
    drop_toc: bool = True


class VoiceConfig(BaseModel):
    language: str
    voice_name: str
    speaking_rate: float = 1.0
    pitch: float = 0.0


class ChapterSelection(BaseModel):
    chapter_ids: list[str] = Field(default_factory=list)


class SynthesizeRequest(BaseModel):
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    voices: list[VoiceConfig] = Field(default_factory=list)
    selection: ChapterSelection = Field(default_factory=ChapterSelection)
    incremental: bool = True
    tts_provider: Literal["kokoro", "google"] = "kokoro"


class Volume(BaseModel):
    index: int
    name: str
    chapter_ids: list[str]
    start_index: int
    end_index: int


class BookStructure(BaseModel):
    book_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    chapter_summaries: list[dict] = Field(default_factory=list)
    volumes: list[Volume] = Field(default_factory=list)
    synthesized_chapter_ids: list[str] = Field(default_factory=list)


class VoiceInfoModel(BaseModel):
    name: str
    gender: str
    language_codes: list[str] = Field(default_factory=list)
    natural_sample_rate_hertz: int = 0
    tier: str


class VoicePreviewRequest(BaseModel):
    voice_name: str
    language_code: str
    provider: str = "kokoro"
    text: str | None = None
    speaking_rate: float = 1.0
    pitch: float = 0.0


class Segment(BaseModel):
    text: str
    lang: str
    start: int
    end: int


class SentenceTiming(BaseModel):
    id: str
    text: str
    lang: str
    text_start: int
    text_end: int
    t_start: float
    t_end: float
    chapter_id: str


class ChapterAudioManifest(BaseModel):
    chapter_id: str
    chapter_title: str
    audio_file: str
    sentences: list[SentenceTiming]


class SynthesisManifest(BaseModel):
    book_id: str
    title: str
    chapters: list[ChapterAudioManifest]


class JobState(BaseModel):
    status: Literal["queued", "running", "done", "error"] = "queued"
    progress: float = 0.0
    message: str = "Queued"
    error: str | None = None
