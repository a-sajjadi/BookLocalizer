import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cleaner import clean_text
from app.language_detection import detect_language
from app.epub_parser import extract_chapters


def test_gibberish_language_detection():
    text = 'asdfasdfasdf qwerqwerqwer'
    lang = detect_language(text)
    assert len(lang) == 2 or lang == 'unknown'


def test_clean_gibberish():
    cleaned = clean_text('@@@@@@#####')
    assert cleaned == ''


def test_mixed_language_detection():
    text = 'This is English. 这是中文。'
    lang = detect_language(text)
    assert lang in {'en', 'zh-cn', 'zh', 'unknown'}


def test_noise_prefix_detection():
    text = '!!!!!@@@@@##### This text is in English.'
    lang = detect_language(text)
    assert lang in {'en', 'unknown'}


def test_split_chapters_chinese(tmp_path):
    content = '第1章 开始\nHello\n第2章 继续\nWorld'
    p = tmp_path / 'sample.txt'
    p.write_text(content, encoding='utf-8')
    chapters = extract_chapters(str(p))
    assert len(chapters) == 2


def test_glossary_filter():
    from app.translator import parse_translation_and_glossary

    text = (
        "<<<START>>>Hello<<<END>>>\n"
        "<<<GLOSSARY_START>>>\nLord -> Lord\n英雄 -> hero\n<<<GLOSSARY_END>>>"
    )
    translation, updates = parse_translation_and_glossary(text)
    assert translation == "Hello"
    assert "Lord" not in updates
    assert updates.get("英雄") == "hero"


def test_split_sentences_ellipsis():
    from app.translator import split_sentences

    text = "I think ... maybe. Yes!"
    parts = split_sentences(text)
    assert parts == ["I think ...", "maybe.", "Yes!"]


def test_split_sentences_decimal():
    from app.translator import split_sentences

    text = "Value is 3.14. Next."
    parts = split_sentences(text)
    assert parts == ["Value is 3.14.", "Next."]


def test_extract_pdf_chapters(tmp_path):
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "sample.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(100, 750, "Chapter 1")
    c.showPage()
    c.drawString(100, 750, "Hello")
    c.showPage()
    c.drawString(100, 750, "Chapter 2")
    c.showPage()
    c.drawString(100, 750, "World")
    c.save()

    chapters = extract_chapters(str(pdf))
    assert len(chapters) == 2


def test_export_non_epub(tmp_path):
    from app.epub_parser import export_chapters

    text_file = tmp_path / "book.txt"
    text_file.write_text("Chapter 1\nHello", encoding="utf-8")
    chapters = {"Chapter 1": "Hello world"}
    out = tmp_path / "out.epub"
    export_chapters(str(text_file), chapters, str(out))
    assert out.exists()

