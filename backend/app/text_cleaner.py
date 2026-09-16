from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .models import BookManifest, Chapter, CleanupConfig

TOC_PATTERNS = re.compile(r"\b(contents|table of contents|toc|index)\b", re.IGNORECASE)
FRONT_PATTERNS = re.compile(r"\b(copyright|license|isbn|edition|publisher|colophon)\b", re.IGNORECASE)
BACK_PATTERNS = re.compile(r"\b(about the author|acknowledg|references|further reading)\b", re.IGNORECASE)


def _strip_dom_noise(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    for node in soup.find_all(attrs={"aria-hidden": "true"}):
        node.decompose()
    return str(soup.body or soup)


def _normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("\u00a0", " ")


def clean_manifest(manifest: BookManifest, config: CleanupConfig) -> BookManifest:
    cleaned: list[Chapter] = []
    total = len(manifest.chapters)

    for idx, ch in enumerate(manifest.chapters):
        title_key = f"{ch.title} {ch.plain_text[:200]}"
        low = title_key.lower()

        if config.drop_toc and TOC_PATTERNS.search(low):
            continue
        if config.drop_frontmatter and idx < max(2, total // 8) and FRONT_PATTERNS.search(low):
            continue
        if config.drop_backmatter and idx > total - max(3, total // 8) and BACK_PATTERNS.search(low):
            continue

        html = _strip_dom_noise(ch.html)
        text = _normalize_text(BeautifulSoup(html, "lxml").get_text(" ", strip=True))
        words = len(text.split())
        if words < config.min_chapter_words:
            continue

        cleaned.append(
            ch.model_copy(
                update={
                    "html": html,
                    "plain_text": text,
                    "word_count": words,
                }
            )
        )

    if not cleaned:
        cleaned = manifest.chapters

    return manifest.model_copy(update={"chapters": cleaned})
