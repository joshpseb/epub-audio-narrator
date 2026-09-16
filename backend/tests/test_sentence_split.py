from __future__ import annotations

from app.language_detect import split_sentences


def test_split_sentences_merges_after_dr_abbreviation() -> None:
    text = "Dr. Smith arrived. Another line."
    parts = split_sentences(text)
    assert len(parts) == 2
    assert parts[0] == "Dr. Smith arrived."
    assert parts[1] == "Another line."


def test_split_sentences_keeps_decimal() -> None:
    text = "Version 3.14 is pi-ish. Done."
    parts = split_sentences(text)
    assert len(parts) == 2
