import re
from langdetect import detect_langs, LangDetectException


def detect_language(text: str) -> str:
    """Return ISO 639-1 language code or 'unknown'. Attempts to skip noise."""

    candidates = [text, text[50:], text[100:]]
    for snippet in candidates:
        snippet = re.sub(r"\s+", " ", snippet.strip())[:1000]
        if not snippet:
            continue
        try:
            langs = detect_langs(snippet)
            if langs:
                best = langs[0]
                if best.prob >= 0.7:
                    return best.lang
        except LangDetectException:
            continue
    return 'unknown'
