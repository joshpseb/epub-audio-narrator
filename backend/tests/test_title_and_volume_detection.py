from app.epub_parser import normalize_chapter_titles
from app.models import Chapter
from app.volumes import detect_volumes


def _chapter(ch_id: str, title: str, plain_text: str, words: int = 120) -> Chapter:
    return Chapter(
        id=ch_id,
        title=title,
        html=f"<p>{plain_text}</p>",
        plain_text=plain_text,
        word_count=words,
    )


def test_normalize_chapter_titles_uses_inline_book_chapter_and_reset_inference() -> None:
    chapters = [
        _chapter("ch1", "Book 1, Chapter 21", "Book 1, Chapter 21 - text"),
        _chapter("ch2", "Chapter 22", "Book 1, Chapter 22 - text"),
        _chapter("ch3", "Chapter 1", "Book 2, Chapter 1 - text"),
        _chapter("ch4", "Chapter 2", "Chapter 2 continuation"),
    ]
    normalized = normalize_chapter_titles(chapters)
    titles = [ch.title for ch in normalized]
    assert titles == [
        "Book 1, Chapter 21",
        "Book 1, Chapter 22",
        "Book 2, Chapter 1",
        "Book 2, Chapter 2",
    ]


def test_detect_volumes_from_embedded_book_numbers() -> None:
    chapters = [
        _chapter("ch1", "Book 1, Chapter 1", "text"),
        _chapter("ch2", "Book 1, Chapter 2", "text"),
        _chapter("ch3", "Book 2, Chapter 1", "text"),
        _chapter("ch4", "Book 2, Chapter 2", "text"),
    ]
    volumes = detect_volumes(chapters)
    assert len(volumes) == 2
    assert volumes[0].name == "Book 1"
    assert volumes[0].chapter_ids == ["ch1", "ch2"]
    assert volumes[1].name == "Book 2"
    assert volumes[1].chapter_ids == ["ch3", "ch4"]
