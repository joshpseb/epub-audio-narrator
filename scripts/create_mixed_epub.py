from __future__ import annotations

from pathlib import Path

from ebooklib import epub


def main() -> None:
    out_path = Path(__file__).resolve().parent / "mixed-language-sample.epub"
    book = epub.EpubBook()
    book.set_identifier("mixed-language-demo")
    book.set_title("Mixed Language Sample")
    book.set_language("en")
    book.add_author("Demo Author")

    chapter = epub.EpubHtml(title="Chapter 1", file_name="chap_1.xhtml", lang="en")
    chapter.content = """
    <h1>Chapter 1</h1>
    <p>This is an English sentence introducing the scene.</p>
    <p>Here is Chinese: 你好，欢迎来到这本书的演示段落。</p>
    <p>Here is Korean: 안녕하세요, 이 문장은 한국어 발음을 확인하기 위한 예시입니다.</p>
    <p>Mixing scripts in one line: We say 감사합니다 and then continue in English.</p>
    """
    book.add_item(chapter)

    book.toc = [chapter]
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(out_path), book)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
