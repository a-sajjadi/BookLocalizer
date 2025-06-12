from typing import Iterable, List, Dict, Callable
from threading import Event

START_MARK = "<<<START>>>"
END_MARK = "<<<END>>>"
TITLE_START = "<<<TITLE_START>>>"
TITLE_END = "<<<TITLE_END>>>"
GLOSSARY_START = "<<<GLOSSARY_START>>>"
GLOSSARY_END = "<<<GLOSSARY_END>>>"
import re
import json

from contextlib import suppress

try:
    from transformers import pipeline
except ImportError as e:  # pragma: no cover - runtime dependency
    raise SystemExit("transformers is required for translation") from e

import requests
import subprocess
import time
import platform
import psutil


def prune_marked(text: str, start: str = START_MARK, end: str = END_MARK) -> str:
    """Return text between start and end markers if present."""
    m = re.search(re.escape(start) + r"(.*?)" + re.escape(end), text, re.S)
    if m:
        return m.group(1).strip()
    return text.strip()


def apply_glossary(text: str, glossary: Dict[str, str] | None) -> str:
    """Replace occurrences in text based on glossary mapping."""
    if not glossary:
        return text
    for src, dst in glossary.items():
        text = text.replace(src, dst)
    return text


def split_sentences(text: str) -> List[str]:
    """Return a list of sentences, ignoring ellipses as boundaries."""
    sentences: List[str] = []
    buffer = ""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        buffer += ch
        if ch in ".!?":
            if i + 1 < n and text[i + 1] in ".!?":
                i += 1
                continue
            if (
                ch == "."
                and i > 0
                and text[i - 1].isdigit()
                and i + 1 < n
                and text[i + 1].isdigit()
            ):
                i += 1
                continue
            sentences.append(buffer.strip())
            buffer = ""
        i += 1
    if buffer.strip():
        sentences.append(buffer.strip())
    return sentences


def parse_translation_and_glossary(
    text: str,
    glossary: Dict[str, str] | None = None,
    start_mark: str = START_MARK,
    end_mark: str = END_MARK,
) -> tuple[str, Dict[str, str]]:
    """Return translation and filtered glossary updates from a raw LLM response."""
    translation = prune_marked(text, start_mark, end_mark)
    glossary_block = re.search(
        re.escape(GLOSSARY_START) + r"(.*?)" + re.escape(GLOSSARY_END),
        text,
        re.S,
    )
    updates: Dict[str, str] = {}
    if glossary_block:
        for line in glossary_block.group(1).splitlines():
            if "->" not in line:
                continue
            src, dst = map(str.strip, line.split("->", 1))
            if not (src and dst) or src.lower() == dst.lower():
                continue
            if glossary and src in glossary:
                continue
            if len(src) <= 1:
                continue
            if not (src[0].isupper() or not src.isascii()):
                continue
            updates[src] = dst
    return translation, updates


def is_ollama_running(host: str = "http://localhost:11434") -> bool:
    """Return True if an Ollama server is reachable."""
    with suppress(Exception):
        resp = requests.get(f"{host}/api/version", timeout=2)
        return resp.status_code == 200
    return False


def list_local_ollama_models(host: str = "http://localhost:11434") -> List[str]:
    """Return a list of model names already pulled into the local Ollama server."""
    with suppress(Exception):
        resp = requests.get(f"{host}/api/tags", timeout=3)
        resp.raise_for_status()
        data = resp.json()
        return [m.get("name") for m in data.get("models", [])]
    return []


def ensure_ollama_running(host: str = "http://localhost:11434") -> bool:
    """Start the Ollama server if it's not running."""
    if is_ollama_running(host):
        return True
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return False
    for _ in range(10):
        if is_ollama_running(host):
            return True
        time.sleep(1)
    return False


FEATURED_OLLAMA_MODELS = [
    "qwen:3b",
    "qwen:7b",
    "llama2:7b",
    "gemma:2b",
    "gemma:7b",
]


_MODEL_RAM_REQUIREMENTS = {
    "qwen:3b": 6,
    "qwen:7b": 12,
    "llama2:7b": 8,
    "gemma:2b": 4,
    "gemma:7b": 12,
}


def is_model_supported(model: str) -> bool:
    """Return False if the system likely cannot run the given model."""
    mem_gb = psutil.virtual_memory().total / (1024 ** 3)
    required = _MODEL_RAM_REQUIREMENTS.get(model, 0)
    if required and mem_gb < required:
        return False
    arch = platform.machine().lower()
    if arch not in {"x86_64", "amd64", "arm64", "aarch64"}:
        return False
    return True


def pull_ollama_model(model_name: str, host: str = "http://localhost:11434") -> bool:
    """Attempt to pull a model from the Ollama registry."""
    try:
        resp = requests.post(
            f"{host}/api/pull",
            json={"name": model_name},
            timeout=600,
            stream=True,
        )
        resp.raise_for_status()
        # consume the streaming response so the download completes
        for _ in resp.iter_lines():
            pass
        return resp.status_code == 200
    except requests.RequestException:
        return False


def translate_with_ollama(
    sentences: Iterable[str],
    model_name: str,
    target_lang: str,
    host: str = "http://localhost:11434",
    options: dict | None = None,
    glossary: Dict[str, str] | None = None,
    *,
    stream: bool = False,
    token_callback: Callable[[int, str], None] | None = None,
    cancel_event: Event | None = None,
    pause_event: Event | None = None,
    start_mark: str = START_MARK,
    end_mark: str = END_MARK,
) -> tuple[List[str], Dict[str, str], List[str]]:
    """Translate sentences using a local Ollama server."""
    outputs: List[str] = []
    raw_outputs: List[str] = []
    new_terms: Dict[str, str] = {}
    glossary_str = "\n".join(f"{k} -> {v}" for k, v in (glossary or {}).items())
    prompt_tmpl = (
        "You are an expert literary translator. Your goal is to produce fluent, natural English while preserving tone. "
        "Use the glossary consistently. Avoid adding explanations. Use double line breaks between paragraphs.\n"
        "--- Glossary ---\n{glossary}\n---\n"
        "Translate the text to {target_lang}. Place your translation between {start} and {end}. "
        "After that, list any new names or terms as 'source -> translation' between {gstart} and {gend}.\n{text}"
    )
    for idx, sentence in enumerate(sentences):
        if cancel_event and cancel_event.is_set():
            break
        if pause_event:
            while pause_event.is_set():
                if cancel_event and cancel_event.is_set():
                    break
                time.sleep(0.1)
        prompt = prompt_tmpl.format(
            glossary=glossary_str,
            start=start_mark,
            end=end_mark,
            gstart=GLOSSARY_START,
            gend=GLOSSARY_END,
            text=sentence,
            target_lang=target_lang,
        )
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": stream,
        }
        if options:
            payload["options"] = options
        try:
            resp = requests.post(
                f"{host}/api/generate",
                json=payload,
                timeout=120,
                stream=stream,
            )
            resp.raise_for_status()
            if stream:
                raw = ""
                for line in resp.iter_lines():
                    if not line:
                        continue
                    part = json.loads(line.decode("utf-8"))
                    delta = part.get("response", "")
                    if delta:
                        raw += delta
                        if token_callback:
                            token_callback(idx, prune_marked(raw, start_mark, end_mark))
                    if part.get("done"):
                        break
            else:
                data = resp.json()
                raw = data.get("response", "")
            cleaned, updates = parse_translation_and_glossary(raw, glossary, start_mark, end_mark)
            if token_callback and not stream:
                token_callback(idx, cleaned)
            outputs.append(cleaned)
            raw_outputs.append(raw)
            new_terms.update(updates)
        except requests.RequestException as exc:  # pragma: no cover - runtime
            raise RuntimeError(f"Ollama translation failed: {exc}") from exc
    return outputs, new_terms, raw_outputs


def translate_sentences(
    sentences: Iterable[str],
    model_name: str,
    target_lang: str,
    use_ollama: bool = False,
    *,
    options: dict | None = None,
    token_callback: Callable[[int, str], None] | None = None,
    glossary: Dict[str, str] | None = None,
    cancel_event: Event | None = None,
    pause_event: Event | None = None,
    start_mark: str = START_MARK,
    end_mark: str = END_MARK,
) -> tuple[List[str], Dict[str, str], List[str]]:
    """Translate a list of sentences with the specified backend."""
    if use_ollama:
        return translate_with_ollama(
            sentences,
            model_name,
            target_lang,
            options=options,
            glossary=glossary,
            stream=token_callback is not None,
            token_callback=token_callback,
            cancel_event=cancel_event,
            pause_event=pause_event,
            start_mark=start_mark,
            end_mark=end_mark,
        )

    translator = pipeline("translation", model=model_name, tgt_lang=target_lang)
    outs = []
    for sent in sentences:
        if cancel_event and cancel_event.is_set():
            break
        if pause_event:
            while pause_event.is_set():
                if cancel_event and cancel_event.is_set():
                    break
                time.sleep(0.1)
        outs.append(translator(sent)[0]["translation_text"])
    return (
        outs,
        {},
        outs,
    )


def translate_with_context(
    sentences: List[str],
    model_name: str,
    target_lang: str,
    use_ollama: bool = False,
    window: int = 50,
    overlap: int = 10,
    options: dict | None = None,
    progress_callback: Callable[[float], None] | None = None,
    token_callback: Callable[[int, str], None] | None = None,
    glossary: Dict[str, str] | None = None,
    cancel_event: Event | None = None,
    pause_event: Event | None = None,
) -> tuple[List[str], Dict[str, str], List[str]]:
    """Translate sentences while keeping context intact.

    The function processes a fixed-size slice of the input and then moves the
    window forward.  I originally tried translating each sentence on its own but
    found the output was far too choppy.  Using an overlap lets the model see a
    little of the prior text so repeated phrases are less likely to appear.
    """
    if not sentences:
        return ([], {}, [])

    translated: Dict[int, str] = {}
    raw_map: Dict[int, str] = {}
    all_updates: Dict[str, str] = {}
    step = max(1, window - overlap)
    total = len(sentences)
    processed = 0
    for start in range(0, total, step):
        if cancel_event and cancel_event.is_set():
            break
        if pause_event:
            while pause_event.is_set():
                if cancel_event and cancel_event.is_set():
                    break
                time.sleep(0.1)
        window_sents = [f"[{start + i}] {s}" for i, s in enumerate(sentences[start : start + window])]
        outs, updates, raws = translate_sentences(
            window_sents,
            model_name,
            target_lang,
            use_ollama,
            options=options,
            token_callback=(
                (lambda idx, text, base=start: token_callback(base + idx, text))
                if token_callback
                else None
            ),
            glossary=glossary,
            cancel_event=cancel_event,
            pause_event=pause_event,
        )
        for idx, (out, raw) in enumerate(zip(outs, raws)):
            sent_id = start + idx
            cleaned = re.sub(r"^\[\d+\]\s*", "", out).strip()
            cleaned = apply_glossary(cleaned, glossary)
            translated.setdefault(sent_id, cleaned)
            raw_map.setdefault(sent_id, raw)
        if updates:
            all_updates.update(updates)
        processed = min(total, start + step)
        if progress_callback:
            progress_callback(processed / total)

    return (
        [translated[i] for i in range(len(sentences))],
        all_updates,
        [raw_map[i] for i in range(len(sentences))],
    )
