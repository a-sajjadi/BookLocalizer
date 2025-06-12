import re
from pathlib import Path

from ebooklib import epub, ITEM_DOCUMENT
from pypdf import PdfReader
from bs4 import BeautifulSoup
from docx import Document
import html


CHAPTER_PATTERNS = [
    r'^(?:chapter|ch|prologue|epilogue)\b.*',
    r'^\d+\s*(?:화|章|节|回|卷)',
    r'^第\s*\d+\s*(?:章|节|回|卷)',
]


def _flatten_toc(toc) -> dict[str, str]:
    """Return mapping of href to human readable titles."""
    mapping = {}

    def _walk(entries):
        for entry in entries:
            if isinstance(entry, epub.Link):
                href = entry.href.split('#')[0]
                mapping[href] = entry.title
            elif isinstance(entry, epub.Section):
                _walk(entry.subitems)

    _walk(toc)
    return mapping


def _split_into_chapters(text: str) -> dict[str, str]:
    """Split raw text into chapters based on common heading patterns."""
    lines = text.splitlines()
    chapters: dict[str, list[str]] = {}
    title = "Chapter 1"
    buf: list[str] = []
    pattern = re.compile('|'.join(CHAPTER_PATTERNS), re.IGNORECASE)
    for line in lines:
        stripped = line.strip()
        if pattern.match(stripped):
            if buf:
                chapters[title] = '\n'.join(buf).strip()
            title = stripped
            buf = []
        else:
            buf.append(line)
    if buf:
        chapters[title] = '\n'.join(buf).strip()
    return chapters if chapters else {"Text": text}


def _extract_epub(path: str) -> dict[str, str]:
    book = epub.read_epub(path)
    toc_map = _flatten_toc(book.toc)
    chapters = {}
    idx = 1
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        name = item.get_name()
        title = toc_map.get(name, f"Chapter {idx}")
        idx += 1
        html = item.get_content().decode('utf-8')
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n").lstrip()
        chapters[title] = text
    return chapters


def _extract_pdf(path: str) -> dict[str, str]:
    reader = PdfReader(path)
    pages = [page.extract_text() or '' for page in reader.pages]
    try:
        outlines = reader.outline
    except Exception:
        outlines = []

    chapters: dict[str, str] = {}
    items: list[tuple[int, str]] = []

    def _walk(objs):
        for obj in objs:
            if isinstance(obj, list):
                _walk(obj)
            else:
                try:
                    page_no = reader.get_destination_page_number(obj)
                    items.append((page_no, obj.title))
                except Exception:
                    pass

    if outlines:
        _walk(outlines)
        items.sort()
        for i, (pno, title) in enumerate(items):
            end = items[i + 1][0] if i + 1 < len(items) else len(pages)
            chapters[title] = '\n'.join(pages[pno:end]).strip()
        if chapters:
            return chapters

    text = '\n'.join(pages)
    chapters = _split_into_chapters(text)
    if list(chapters.keys()) == ["Text"]:
        for idx, page in enumerate(pages, 1):
            chapters[f"Page {idx}"] = page.strip()
        chapters.pop("Text", None)
    return chapters


def _extract_txt(path: str) -> dict[str, str]:
    text = Path(path).read_text(encoding='utf-8', errors='ignore')
    return _split_into_chapters(text)


def _extract_docx(path: str) -> dict[str, str]:
    doc = Document(path)
    text = '\n'.join(p.text for p in doc.paragraphs)
    return _split_into_chapters(text)


def extract_chapters(path: str) -> dict[str, str]:
    """Return mapping of chapter titles to text for various ebook formats."""
    ext = Path(path).suffix.lower()
    if ext == '.epub':
        return _extract_epub(path)
    if ext == '.pdf':
        return _extract_pdf(path)
    if ext == '.txt':
        return _extract_txt(path)
    if ext == '.docx':
        return _extract_docx(path)
    raise ValueError(f'Unsupported format: {path}')


def export_chapters(
    original: str,
    new_texts: dict[str, str],
    output: str,
    titles: dict[str, str] | None = None,
) -> None:
    """Write an updated EPUB with translated chapters and titles."""
    book = epub.EpubBook()
    book.set_identifier("id")
    book.set_title(Path(original).stem)
    book.set_language("en")

    chapters = []
    for i, (orig_title, text) in enumerate(new_texts.items(), 1):
        new_title = titles.get(orig_title, orig_title) if titles else orig_title
        item = epub.EpubHtml(title=new_title, file_name=f"chap_{i}.xhtml")
        safe = html.escape(text).replace("\n", "<br/>")
        item.content = f"<html><body><h1>{html.escape(new_title)}</h1><p>{safe}</p></body></html>"
        book.add_item(item)
        chapters.append(item)

    book.toc = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters
    epub.write_epub(output, book)
