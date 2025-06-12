# BookLocalizer
Local Open-Source LLM-based Book Translator App

BookLocalizer helps translate books on your own machine. It works with EPUB, PDF, text and Word files. The app cleans up the source text, detects its language and then translates it using models from HuggingFace or a local Ollama server.

## Getting Started

```bash
pip install -r requirements.txt
python main.py --gui                      # start without a book
```

## Features

* **Chapter Extraction**: Parses chapters from EPUB files using **ebooklib**.
* **Smart Cleaning**: Removes hashes, ASCII art, and code blocks with toggleable cleaning.
* **Language Detection**: Auto-detects source language via **langdetect** (manual override supported).
* **Accurate Translation**: Sentence-by-sentence translation using HuggingFace `transformers`, with context-aware sliding window to avoid duplication.
* **Local Model Support**:

  * Seamless translation via **Ollama** backend.
  * Auto-detects installed models (Gemma, Qwen, etc.) with compatibility checks and download indicators.
  * Starts Ollama server automatically if needed.
* **Multilingual Support**: Supports English (default), Chinese, Persian, German, French, and more via ISO codes (model dependent).
* **Custom Glossary**: Define term replacements, with LLM-suggested updates between markers.
* **UI Highlights**:

  * Intuitive chapter browser and table of contents.
  * Toggle source/translated views with inline diff and sentence IDs.
  * Editable text pane with appearance settings that persist.
  * Upload EPUB, PDF, TXT, or DOCX files directly in the app.
  * Export translated chapters to EPUB.
* **Performance & UX**:

  * Translations and model downloads run in the background.
  * Real-time translation streaming with Ollama.
  * Progress bars, timers, and character/token counters.
  * Batch chapter translation support.
  * Copy button for quick access to text.
  * Persistent settings for selected model, backend, and glossary.

## Recommended Models

On a 32GB M1 Macbook Pro, I get excellent translation results with the GUI comparable to 4o when using gemma3:12b-it-qat, with about 11.6 GB of unified memory usage. A test of one chapter with 2150 source Chinese characters resulted in 7352 English characters and took 116.1s on my device. 

For quick tests a small model like `gemma:2b` works decently and needs about 4GB of RAM, but debatable how much improvement it is over google translate api output. When using HuggingFace models, the `opus-mt` series such as 'Helsinki-NLP/opus-mt-zh-en' (Chinese to English) covers many language pairs with low memory requirements (WIP still buggy).

A window size of 50 sentences with an overlap of 10 gave the best balance between context and speed during our experiments, but for large context window models like gemma3, you can choose single window and disable overlap for slightly faster performance (minimize roundtrips to the model).

## Launching the Interface

```bash
python main.py --gui                      # start without a book
python main.py path/to/book.epub --gui    # open a specific book
python main.py path/to/book.epub --model Helsinki-NLP/opus-mt-en-de  # translate via command line
```
The interface lets you tweak model settings, translate individual chapters or a range, and export the result as a new EPUB.
Add `--backend ollama` to use your own Ollama server when using command line. If you omit `--target`, translations default to English.
