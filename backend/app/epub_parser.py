from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub

from .models import BookManifest, Chapter


def _clean_title(raw: str | None, fallback: str) -> str:
    if not raw:
        return fallback
    title = " ".join(raw.split())
    return title or fallback


def _compact_chapter_label(title: str) -> str:
    patterns = [
        r"((?:Book|Volume|Part)\s+\d+\s*,?\s*Chapter\s+\d+)",
        r"(Chapter\s+\d+)",
        r"(第[一二三四五六七八九十百千零0-9]+卷\s*第[一二三四五六七八九十百千零0-9]+章)",
        r"(第[一二三四五六七八九十百千零0-9]+章)",
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return " ".join(match.group(1).split())
    return title


_BOOK_CHAPTER_INLINE = re.compile(
    r"\b(?:Book|Volume|Part)\s+(\d+)\s*[,:\-]?\s*Chapter\s+(\d+)\b",
    re.IGNORECASE,
)
_CHAPTER_NUM = re.compile(r"\bChapter\s+(\d+)\b", re.IGNORECASE)
_BOOK_NUM = re.compile(r"\b(?:Book|Volume|Part)\s+(\d+)\b", re.IGNORECASE)


def _extract_book_chapter_label(text: str) -> str | None:
    match = _BOOK_CHAPTER_INLINE.search(text)
    if not match:
        return None
    return f"Book {int(match.group(1))}, Chapter {int(match.group(2))}"


def _infer_title(soup: BeautifulSoup, fallback: str) -> str:
    # Prefer body headings: soup.find() traverses in document order and would
    # return <title> from <head> before <h1> in <body>.  The <title> tag often
    # contains a generic string like "Chapter 1" that lacks the book number.
    body = soup.body or soup
    heading = body.find(["h1", "h2", "h3"]) or soup.find("title")
    plain_text = " ".join(soup.get_text(" ", strip=True).split())
    body_label = _extract_book_chapter_label(plain_text[:320])
    if heading:
        heading_title = _clean_title(heading.get_text(" ", strip=True), fallback)
        compact_heading = _compact_chapter_label(heading_title)
        # If heading is only "Chapter N" but body starts with "Book X, Chapter N",
        # prefer the richer body label.
        if body_label and not _HAS_BOOK.search(compact_heading):
            return body_label
        return compact_heading

    if not plain_text:
        return fallback
    if body_label:
        return body_label

    # Try to capture explicit chapter-like prefixes from plain text.
    chapter_prefix = re.match(
        r"^((?:Book|Volume|Part)\s+\d+[^\n]{0,100}|Chapter\s+\d+[^\n]{0,100}|第[一二三四五六七八九十百千零0-9]+[章节卷部][^\n]{0,100})",
        plain_text,
        re.IGNORECASE,
    )
    if chapter_prefix:
        candidate = chapter_prefix.group(1).strip(" -:;,.")
        if len(candidate) >= 6:
            return _compact_chapter_label(_clean_title(candidate, fallback))

    # Fallback: first ~14 words gives a useful chapter header in many EPUBs.
    words = plain_text.split()
    candidate = " ".join(words[:14]).strip(" -:;,.")
    if len(candidate) >= 6:
        return _compact_chapter_label(_clean_title(candidate, fallback))
    return fallback


_VOL_MARKER = re.compile(r"^\s*(Book|Volume|Part)\s+(\d+)\s*$", re.IGNORECASE)
_HAS_BOOK = re.compile(r"\b(Book|Volume|Part)\s+\d+\b", re.IGNORECASE)


def _apply_volume_prefixes(chapters: list[Chapter]) -> list[Chapter]:
    """When the EPUB uses short 'Book N' / 'Volume N' separator chapters, prefix
    subsequent 'Chapter X' titles so they read 'Book N, Chapter X'."""
    current_vol: str | None = None
    result: list[Chapter] = []
    for ch in chapters:
        m = _VOL_MARKER.match(ch.title)
        if m and ch.word_count <= 30:
            current_vol = f"{m.group(1).title()} {m.group(2)}"
            result.append(ch)
        elif current_vol and not _HAS_BOOK.search(ch.title):
            result.append(ch.model_copy(update={"title": f"{current_vol}, {ch.title}"}))
        else:
            result.append(ch)
    return result


def _chapter_number(title: str) -> int | None:
    match = _CHAPTER_NUM.search(title)
    if match:
        return int(match.group(1))
    return None


def _book_number(title: str) -> int | None:
    match = _BOOK_NUM.search(title)
    if match:
        return int(match.group(1))
    return None


def normalize_chapter_titles(chapters: list[Chapter]) -> list[Chapter]:
    """Normalize chapter titles to keep book/chapter labels consistent.

    Passes:
    1) Prefix after explicit short markers ("Book 2").
    2) Prefer inline body labels ("Book 2, Chapter 1") when heading is only "Chapter 1".
    3) Infer missing book numbers when chapter numbering resets (e.g. 23 -> 1).
    """
    prefixed = _apply_volume_prefixes(chapters)
    result: list[Chapter] = []
    current_book = 1
    last_chapter_num: int | None = None

    for chapter in prefixed:
        title = chapter.title
        explicit_book = _book_number(title)
        if explicit_book is not None:
            current_book = explicit_book

        if explicit_book is None and _chapter_number(title) is not None:
            body_label = _extract_book_chapter_label((chapter.plain_text or "")[:320])
            if body_label:
                title = body_label
                explicit_book = _book_number(title)
                if explicit_book is not None:
                    current_book = explicit_book

        chapter_num = _chapter_number(title)
        if chapter_num is not None and last_chapter_num is not None:
            # Typical multi-book transition: Chapter 20+ then Chapter 1.
            if explicit_book is None and last_chapter_num >= 10 and chapter_num <= 3:
                current_book += 1
        if chapter_num is not None:
            last_chapter_num = chapter_num

        if chapter_num is not None and _book_number(title) is None:
            title = f"Book {current_book}, Chapter {chapter_num}"

        if title != chapter.title:
            result.append(chapter.model_copy(update={"title": title}))
        else:
            result.append(chapter)

    return result


def _hoist_trailing_heading(body: BeautifulSoup) -> None:
    """If the body ends with <hr><hN>title</hN> and nothing after, move that heading
    to the top and remove the <hr>. Common in web-novel EPUBs where the chapter name
    appears after a divider at the end of the HTML section."""
    hrs = body.find_all("hr")
    if not hrs:
        return
    last_hr = hrs[-1]
    heading = last_hr.find_next_sibling(["h1", "h2", "h3"])
    if not heading:
        return
    trailing_content = [
        s for s in heading.next_siblings
        if hasattr(s, "get_text") and s.get_text(strip=True)
    ]
    if trailing_content:
        return
    heading.extract()
    last_hr.extract()
    body.insert(0, heading)


def parse_epub(epub_path: str | Path, book_id: str) -> BookManifest:
    book = epub.read_epub(str(epub_path))

    metadata_title = book.get_metadata("DC", "title")
    title = metadata_title[0][0] if metadata_title else "Untitled"
    metadata_authors = book.get_metadata("DC", "creator")
    authors = [item[0] for item in metadata_authors if item and item[0]]

    chapters: list[Chapter] = []
    for idx, item in enumerate(book.get_items_of_type(ITEM_DOCUMENT), start=1):
        html_raw = item.get_content().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html_raw, "lxml-xml")
        chapter_title = _infer_title(soup, f"Chapter {idx}")
        plain_text = soup.get_text(" ", strip=True)
        body = soup.body or soup
        first_heading = body.find(["h1", "h2", "h3"])
        if first_heading:
            first_heading.extract()
        _hoist_trailing_heading(body)
        chapters.append(
            Chapter(
                id=f"ch{idx}",
                title=chapter_title,
                html=str(body),
                plain_text=plain_text,
                word_count=len(plain_text.split()),
            )
        )

    if not chapters:
        raise ValueError("No readable document sections found in EPUB.")

    chapters = normalize_chapter_titles(chapters)
    return BookManifest(book_id=book_id, title=_clean_title(title, "Untitled"), authors=authors, chapters=chapters)
