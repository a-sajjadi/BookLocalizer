import argparse
from pathlib import Path

from app.cleaner import clean_text
from app.language_detection import detect_language
from app.translator import (
    translate_with_context,
    translate_sentences,
    is_ollama_running,
    split_sentences,
)
from app.epub_parser import extract_chapters
from app.ui import run_ui


def process_epub(epub_path: Path, model: str, target_lang: str, backend: str = "hf"):
    chapters = extract_chapters(str(epub_path))
    use_ollama = backend == "ollama"
    if use_ollama and not is_ollama_running():
        raise SystemExit("Ollama backend selected but server is not running.")

    for title, text in chapters.items():
        print(f"\n## {title}\n")
        cleaned = clean_text(text)
        lang = detect_language(cleaned)
        print(f"Detected language: {lang}")
        sentences = split_sentences(cleaned)
        translated, updates, _ = translate_with_context(sentences, model, target_lang, use_ollama)
        print('\n'.join(translated))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Translate EPUB file")
    parser.add_argument('ebook', nargs='?', type=Path, help='ebook file path')
    parser.add_argument('--model', default='Helsinki-NLP/opus-mt-en-de')
    parser.add_argument('--target', default='en', help='target language code')
    parser.add_argument('--backend', default='hf', choices=['hf', 'ollama'], help='translation backend')
    parser.add_argument('--gui', action='store_true', help='launch graphical interface')
    args = parser.parse_args()
    target = args.target or 'en'
    if args.gui:
        run_ui(args.ebook, args.model, target, args.backend)
    else:
        if not args.ebook:
            parser.error('ebook path required in CLI mode')
        process_epub(args.ebook, args.model, target, args.backend)
