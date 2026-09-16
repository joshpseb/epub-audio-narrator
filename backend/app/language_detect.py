from __future__ import annotations

import re
from typing import Iterable

from lingua import Language, LanguageDetectorBuilder

from .models import Segment

_detector = LanguageDetectorBuilder.from_languages(
    Language.ENGLISH,
    Language.CHINESE,
    Language.KOREAN,
    Language.JAPANESE,
    Language.FRENCH,
    Language.GERMAN,
    Language.SPANISH,
).with_preloaded_language_models().build()

_sentence_re = re.compile(r"(?<=[.!?。！？])\s+")
_ABBREVIATIONS = {
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "prof.",
    "sr.",
    "jr.",
    "st.",
    "vs.",
    "etc.",
    "e.g.",
    "i.e.",
}


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _sentence_re.split(text) if p.strip()]
    if len(parts) <= 1:
        return parts

    merged: list[str] = []
    for part in parts:
        if not merged:
            merged.append(part)
            continue
        prev = merged[-1].rstrip().lower()
        last_token = prev.split()[-1] if prev.split() else prev
        # Avoid splitting at common abbreviations and decimal points.
        if last_token in _ABBREVIATIONS or re.search(r"\d+\.$", prev):
            merged[-1] = f"{merged[-1]} {part}".strip()
        else:
            merged.append(part)
    return merged


def _script_language(char: str) -> str | None:
    code = ord(char)
    if 0x4E00 <= code <= 0x9FFF:
        return "zh"
    if 0xAC00 <= code <= 0xD7AF:
        return "ko"
    if (0x3040 <= code <= 0x30FF) or (0x31F0 <= code <= 0x31FF):
        return "ja"
    if char.isascii() and char.isalpha():
        return "en"
    return None


def _guess_language(text: str) -> str:
    guess = _detector.detect_language_of(text)
    if guess == Language.CHINESE:
        return "zh"
    if guess == Language.KOREAN:
        return "ko"
    if guess == Language.JAPANESE:
        return "ja"
    if guess == Language.FRENCH:
        return "fr"
    if guess == Language.GERMAN:
        return "de"
    if guess == Language.SPANISH:
        return "es"
    return "en"


def split_by_language_runs(text: str, base_offset: int = 0) -> list[Segment]:
    if not text.strip():
        return []

    chunks: list[Segment] = []
    current = []
    start = 0
    active_lang: str | None = None

    def flush(end: int) -> None:
        nonlocal current, start, active_lang
        if not current:
            return
        raw = "".join(current)
        lang = active_lang or _guess_language(raw)
        chunks.append(
            Segment(
                text=raw,
                lang=lang,
                start=base_offset + start,
                end=base_offset + end,
            )
        )
        current = []
        active_lang = None

    for idx, char in enumerate(text):
        char_lang = _script_language(char)
        if active_lang is None:
            active_lang = char_lang
            start = idx
            current.append(char)
            continue
        if char_lang is not None and active_lang is not None and char_lang != active_lang:
            flush(idx)
            start = idx
            active_lang = char_lang
            current = [char]
        else:
            current.append(char)

    flush(len(text))

    normalized: list[Segment] = []
    for item in chunks:
        stripped = item.text.strip()
        if not stripped:
            continue
        shift_left = item.text.find(stripped)
        normalized.append(
            item.model_copy(
                update={
                    "text": stripped,
                    "start": item.start + shift_left,
                    "end": item.start + shift_left + len(stripped),
                }
            )
        )
    return normalized


def split_text_to_segments(text: str) -> list[Segment]:
    segments: list[Segment] = []
    cursor = 0
    for sentence in split_sentences(text):
        idx = text.find(sentence, cursor)
        if idx < 0:
            idx = cursor
        segments.extend(split_by_language_runs(sentence, idx))
        cursor = idx + len(sentence)
    return segments


def group_segments(segments: Iterable[Segment]) -> list[Segment]:
    grouped: list[Segment] = []
    for seg in segments:
        if not grouped or grouped[-1].lang != seg.lang:
            grouped.append(seg)
            continue
        prev = grouped[-1]
        grouped[-1] = prev.model_copy(
            update={
                "text": f"{prev.text} {seg.text}".strip(),
                "end": seg.end,
            }
        )
    return grouped
