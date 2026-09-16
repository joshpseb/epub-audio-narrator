from __future__ import annotations

import re
from typing import Iterable

from .models import BookManifest, Chapter, Volume

_LATIN_VOLUME = re.compile(
    r"^\s*(volume|book|part|tome)\s+(?:[0-9]+|[ivxlcdm]+)\b",
    re.IGNORECASE,
)
_CHINESE_VOLUME = re.compile(r"第\s*[一二三四五六七八九十百千零0-9]+\s*[卷部册]")
_KOREAN_VOLUME = re.compile(r"(?:제\s*)?\d+\s*권")
_LATIN_BOOK_NUM = re.compile(r"\b(volume|book|part|tome)\s+([0-9]+|[ivxlcdm]+)\b", re.IGNORECASE)


def _looks_like_volume_marker(chapter: Chapter) -> bool:
    title = (chapter.title or "").strip()
    if not title:
        return False
    if _LATIN_VOLUME.search(title):
        return True
    if _CHINESE_VOLUME.search(title):
        return True
    if _KOREAN_VOLUME.search(title):
        return True
    return False


def _short_marker(chapter: Chapter) -> bool:
    return chapter.word_count <= 30


def _roman_to_int(value: str) -> int | None:
    digits = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    text = value.upper()
    if not text or any(ch not in digits for ch in text):
        return None
    total = 0
    prev = 0
    for ch in reversed(text):
        current = digits[ch]
        if current < prev:
            total -= current
        else:
            total += current
            prev = current
    return total if total > 0 else None


def _extract_latin_book_number(title: str) -> int | None:
    match = _LATIN_BOOK_NUM.search(title)
    if not match:
        return None
    raw = match.group(2)
    if raw.isdigit():
        return int(raw)
    return _roman_to_int(raw)


def _volume_name_for_book_number(book_number: int) -> str:
    return f"Book {book_number}"


def detect_volumes(chapters: Iterable[Chapter]) -> list[Volume]:
    chapter_list = list(chapters)
    if not chapter_list:
        return []

    markers: list[tuple[int, str]] = []
    current_book_num: int | None = None
    for idx, chapter in enumerate(chapter_list):
        title = chapter.title.strip()
        book_num = _extract_latin_book_number(title)

        if _looks_like_volume_marker(chapter) and (_short_marker(chapter) or idx == 0):
            if book_num is not None:
                markers.append((idx, _volume_name_for_book_number(book_num)))
                current_book_num = book_num
            else:
                markers.append((idx, title))
            continue

        # Some EPUBs embed the book number in every chapter title
        # (e.g. "Book 2, Chapter 1"), without standalone "Book 2" marker pages.
        # Detect transitions by book number changes.
        if book_num is not None and book_num != current_book_num:
            markers.append((idx, _volume_name_for_book_number(book_num)))
            current_book_num = book_num

    # De-duplicate markers by chapter index while preserving order.
    deduped: list[tuple[int, str]] = []
    seen_idx: set[int] = set()
    for idx, name in markers:
        if idx in seen_idx:
            continue
        seen_idx.add(idx)
        deduped.append((idx, name))
    markers = deduped

    if not markers:
        return [
            Volume(
                index=0,
                name="All chapters",
                chapter_ids=[ch.id for ch in chapter_list],
                start_index=0,
                end_index=len(chapter_list) - 1,
            )
        ]

    if markers[0][0] != 0:
        markers.insert(0, (0, "Front matter"))

    volumes: list[Volume] = []
    for vol_idx, (start, name) in enumerate(markers):
        end = markers[vol_idx + 1][0] - 1 if vol_idx + 1 < len(markers) else len(chapter_list) - 1
        chapter_ids = [chapter_list[i].id for i in range(start, end + 1)]
        volumes.append(
            Volume(
                index=vol_idx,
                name=name,
                chapter_ids=chapter_ids,
                start_index=start,
                end_index=end,
            )
        )
    return volumes


def filter_chapters_by_selection(
    book: BookManifest,
    chapter_ids: list[str] | None,
) -> BookManifest:
    if not chapter_ids:
        return book
    keep = set(chapter_ids)
    filtered = [ch for ch in book.chapters if ch.id in keep]
    return book.model_copy(update={"chapters": filtered})
