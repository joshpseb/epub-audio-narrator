# Validation Steps

## 1) English baseline EPUB

- Download a public-domain EPUB (for example from Project Gutenberg).
- Upload it in the app and run synthesis.
- Confirm chapter list loads and speech sounds natural.

## 2) Mixed-language EPUB

- Generate a test book:
  - `cd backend && source .venv/bin/activate && python ../scripts/create_mixed_epub.py`
- Upload `scripts/mixed-language-sample.epub`.
- Confirm Chinese spans are spoken with Mandarin voice and Korean spans with Korean voice.

## 3) Sync checks

- Open reader and play chapter audio.
- Verify active sentence highlight tracks playback (target within ~150ms).
- Click a sentence and verify playback seeks to that sentence.

## 4) M4B export checks

- Click **Export M4B**.
- Open resulting file in VLC or Apple Books.
- Verify chapter boundaries and titles are present.
