import json
from pathlib import Path
import time
from tkinter import (
    Tk, Text, Frame, Button, Checkbutton, IntVar, StringVar,
    OptionMenu, filedialog, messagebox, LEFT, Toplevel,
    Listbox, END, BOTH, Label, Entry, DISABLED, NORMAL,
)
from tkinter.ttk import Combobox, Progressbar
from threading import Thread, Event

import difflib
from .cleaner import clean_text
from .epub_parser import extract_chapters, export_chapters
from .translator import (
    translate_with_context,
    translate_sentences,
    list_local_ollama_models,
    pull_ollama_model,
    ensure_ollama_running,
    is_model_supported,
    apply_glossary,
    prune_marked,
    TITLE_START,
    TITLE_END,
    split_sentences,
)
from .language_detection import detect_language


class TranslatorUI:
    def __init__(self, epub_path: Path | None, model: str, target_lang: str, backend: str = "hf"):
        self.epub_path = epub_path
        self.model = model
        self.target_lang = target_lang
        self.backend = backend
        self.chapters = extract_chapters(str(epub_path)) if epub_path else {}
        self.translations: dict[str, str] = {}
        self.translated_titles: dict[str, str] = {}
        self.raw_translations: dict[str, str] = {}
        self.full_raw_translations: dict[str, str] = {}
        self.removed_content: dict[str, list[str]] = {}
        self.glossary_path = Path("glossary.json")
        self.glossary: dict[str, str] = {}
        self.state_path = Path("user_data.json")
        self.total_chars = 0
        self.book_tokens = 0
        self.source_chars = 0
        self.source_tokens = 0
        self.chapter_chars = 0
        self.chapter_tokens = 0
        self.custom_default = ""
        self._load_state()
        self._update_glossary_path()

        self.local_models = list_local_ollama_models()
        if (
            self.backend == "ollama"
            and self.model not in self.local_models
            and not getattr(self, "first_run", False)
        ):
            if self.local_models:
                self.model = self.local_models[0]

        self.root = Tk()
        self.root.title("BookLocalizer - Local LLM-based Book Translation App")

        self.container = Frame(self.root)
        self.container.pack(fill="both", expand=True)

        self.left_pane = Frame(self.container, width=250)
        self.left_pane.pack(side=LEFT, fill="y")

        self.main_pane = Frame(self.container)
        self.main_pane.pack(side=LEFT, fill="both", expand=True)

        btn_texts = [
            "Open Book",
            "Remove Book",
            "Save EPUB",
            "Table of Contents",
            "Glossary",
            "Reset State",
            "Translate Chapter",
            "Translate Range",
            "Toggle Controls",
        ]
        self.btn_width = max(len(t) for t in btn_texts)

        Button(self.left_pane, text="Open Book", width=self.btn_width, command=self.open_book).pack(fill="x")
        Button(self.left_pane, text="Remove Book", width=self.btn_width, command=self.remove_book).pack(fill="x")

        self.chapter_var = StringVar()
        self.combo = Combobox(self.left_pane, values=list(self.chapters.keys()), textvariable=self.chapter_var, state="readonly")
        self.combo.pack(fill="x")
        self.combo.bind("<<ComboboxSelected>>", self._on_chapter_select)
        Button(self.left_pane, text="Table of Contents", width=self.btn_width, command=self.choose_chapter).pack(fill="x")

        self.clean_var = IntVar(value=0)
        Checkbutton(self.left_pane, text="Clean Source", variable=self.clean_var, command=self._display_current_text).pack(anchor="w")
        self.prune_var = IntVar(value=0)
        Checkbutton(self.left_pane, text="Prune Translation", variable=self.prune_var, command=self._display_current_text).pack(anchor="w")

        backend_frame = Frame(self.left_pane)
        backend_frame.pack(fill="x")
        self.backend_var = StringVar(value=self.backend)
        OptionMenu(backend_frame, self.backend_var, "hf", "ollama", command=self._backend_changed).pack(side=LEFT)

        self.ollama_var = StringVar(value=self.model)
        self.custom_var = StringVar(value=self.custom_default)
        self.ollama_menu = OptionMenu(backend_frame, self.ollama_var, "", command=self.set_model)
        self.ollama_menu.pack(side=LEFT)
        Button(backend_frame, text="Refresh", command=self.refresh_models).pack(side=LEFT)
        self.custom_entry = Entry(backend_frame, textvariable=self.custom_var, width=15)
        self.download_btn = Button(backend_frame, text="Download", command=self.download_custom)

        self.hf_entry = Text(backend_frame, height=1, width=30)
        self.hf_entry.insert("1.0", self.model)
        self.hf_entry.pack(side=LEFT)

        self._build_model_menu()
        self._backend_changed()

        # advanced options
        self.adv_var = IntVar(value=0)
        Checkbutton(backend_frame, text="Translation Options", variable=self.adv_var, command=self._toggle_adv).pack(side=LEFT)
        self.model_adv_var = IntVar(value=0)
        Checkbutton(backend_frame, text="Model Settings", variable=self.model_adv_var, command=self._toggle_model_adv).pack(side=LEFT)

        self.adv_frame = Frame(self.left_pane)
        Label(self.adv_frame, text="Window:").pack(side=LEFT)
        self.window_var = StringVar(value="50")
        self.window_entry = Entry(self.adv_frame, textvariable=self.window_var, width=4)
        self.window_entry.pack(side=LEFT)
        Label(self.adv_frame, text="Overlap:").pack(side=LEFT)
        self.overlap_var = StringVar(value="10")
        self.overlap_entry = Entry(self.adv_frame, textvariable=self.overlap_var, width=4)
        self.overlap_entry.pack(side=LEFT)
        self.force_window_var = IntVar(value=0)
        Checkbutton(
            self.adv_frame,
            text="Single Window",
            variable=self.force_window_var,
            command=self._toggle_force_window,
        ).pack(side=LEFT)
        self._toggle_force_window()

        self.model_frame = Frame(self.left_pane)
        Label(self.model_frame, text="Temperature:").pack(side=LEFT)
        self.temp_var = StringVar(value="1.0")
        Entry(self.model_frame, textvariable=self.temp_var, width=5).pack(side=LEFT)
        Label(self.model_frame, text="Context:").pack(side=LEFT)
        self.ctx_var = StringVar(value="32000")
        Entry(self.model_frame, textvariable=self.ctx_var, width=6).pack(side=LEFT)
        Label(self.model_frame, text="top_k:").pack(side=LEFT)
        self.top_k_var = StringVar(value="64")
        Entry(self.model_frame, textvariable=self.top_k_var, width=4).pack(side=LEFT)
        Label(self.model_frame, text="top_p:").pack(side=LEFT)
        self.top_p_var = StringVar(value="0.95")
        Entry(self.model_frame, textvariable=self.top_p_var, width=5).pack(side=LEFT)
        Label(self.model_frame, text="min_p:").pack(side=LEFT)
        self.min_p_var = StringVar(value="0.0")
        Entry(self.model_frame, textvariable=self.min_p_var, width=5).pack(side=LEFT)

        self._toggle_adv()
        self._toggle_model_adv()

        lang_frame = Frame(self.left_pane)
        lang_frame.pack(fill="x")
        self.lang_map = {
            "English": "en",
            "German": "de",
            "French": "fr",
            "Spanish": "es",
            "Italian": "it",
            "Japanese": "ja",
            "Korean": "ko",
            "Chinese": "zh",
            "Russian": "ru",
            "Persian": "fa",
            "Arabic": "ar",
            "Dutch": "nl",
            "Swahili": "sw",
            "Indonesian": "id",
            "Hindi": "hi",
            "Portuguese": "pt",
            "Tagalog": "tl",
            "Telugu": "te",
        }
        names = list(self.lang_map.keys()) + ["Custom"]
        self.source_lang_display = StringVar(value="Auto")
        self.target_lang_display = StringVar(value=names[0])
        Label(lang_frame, text="Source:").pack(side=LEFT)
        self.source_menu = OptionMenu(lang_frame, self.source_lang_display, "Auto", *names,
                                      command=self._source_changed)
        self.source_menu.pack(side=LEFT)
        self.custom_source_var = StringVar()
        self.source_custom_entry = Entry(lang_frame, textvariable=self.custom_source_var, width=5)
        self.detect_label = Label(lang_frame, text="detected: unknown")
        self.detect_label.pack(side=LEFT)
        self.detected_var = StringVar(value="unknown")

        Label(lang_frame, text="Target:").pack(side=LEFT)
        self.target_menu = OptionMenu(lang_frame, self.target_lang_display, *names,
                                      command=self._target_changed)
        self.target_menu.pack(side=LEFT)
        self.custom_target_var = StringVar()
        self.target_custom_entry = Entry(lang_frame, textvariable=self.custom_target_var, width=5)
        self._source_changed()
        self._target_changed()

        range_frame = Frame(self.left_pane)
        range_frame.pack(fill="x")
        titles = list(self.chapters.keys())
        first_title = titles[0] if titles else ""
        self.start_var = StringVar(value=first_title)
        self.end_var = StringVar(value=first_title)
        Label(range_frame, text="Range Start:").pack(side=LEFT)
        self.start_menu = OptionMenu(range_frame, self.start_var, *(titles or [" "]))
        self.start_menu.pack(side=LEFT)
        Label(range_frame, text="Range End:").pack(side=LEFT)
        self.end_menu = OptionMenu(range_frame, self.end_var, *(titles or [" "]))
        self.end_menu.pack(side=LEFT)
        Button(range_frame, text="Translate Range", width=self.btn_width, command=self.translate_range).pack(side=LEFT)
        self._update_range_menus()

        top_row = Frame(self.main_pane)
        top_row.pack(fill="x")
        Button(top_row, text="Toggle Controls", width=self.btn_width, command=self._toggle_left_pane).pack(side=LEFT)
        Button(top_row, text="<", width=2, command=self.prev_chapter).pack(side=LEFT)
        Button(top_row, text=">", width=2, command=self.next_chapter).pack(side=LEFT)
        self.view_var = StringVar(value="Source")
        OptionMenu(top_row, self.view_var, "Source", "Translation", command=lambda _: self._display_current_text()).pack(side=LEFT)
        self.diff_var = IntVar(value=0)
        Checkbutton(top_row, text="Show Diff", variable=self.diff_var, command=self._display_current_text).pack(side=LEFT)
        Button(top_row, text="Copy", width=self.btn_width, command=self.copy_text).pack(side=LEFT)

        self.text = Text(self.main_pane, height=20)
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")
        self.text.tag_config("add", background="lightgreen")
        self.text.tag_config("remove", background="lightcoral")

        self.count_var = StringVar(value="Src: 0 | Est: ~0 | Trans: 0 | Used: ~0")
        Label(self.main_pane, textvariable=self.count_var, anchor="e").pack(fill="x")

        self.timer_var = StringVar(value="")
        Label(self.main_pane, textvariable=self.timer_var, anchor="e").pack(fill="x")

        Button(self.left_pane, text="Save EPUB", width=self.btn_width, command=self.save_epub).pack(fill="x")
        Button(self.left_pane, text="Glossary", width=self.btn_width, command=self.open_glossary).pack(fill="x")
        Button(self.left_pane, text="Reset State", width=self.btn_width, command=self.clear_state).pack(fill="x")

        bottom = Frame(self.main_pane)
        bottom.pack(fill="x")
        Button(bottom, text="Translate Chapter", width=self.btn_width, command=self.translate_current).pack(side=LEFT)
        self.progress = Progressbar(bottom, mode="determinate")
        self.progress.pack(side=LEFT, fill="x", expand=True)
        self.progress.pack_forget()

        control = Frame(self.left_pane)
        control.pack(fill="x")
        self.pause_btn = Button(control, text="Pause", width=int(self.btn_width/2), command=self.toggle_pause)
        self.pause_btn.pack(side=LEFT)
        Button(control, text="Cancel", width=int(self.btn_width/2), command=self.cancel_translation).pack(side=LEFT)

        self.cancel_event = Event()
        self.pause_event = Event()
        self.translate_thread = None

        self._timer_start = 0.0
        self._timer_running = False

        self._update_count(0)
        self.chapter_chars = 0
        self.chapter_tokens = 0
        self._load_chapter(0)

    def _start_progress(self, mode: str = "determinate"):
        self.progress.configure(mode=mode)
        self.progress['value'] = 0
        self.progress.pack(fill="x")
        if mode == "indeterminate":
            self.progress.start()

    def _update_progress(self, fraction: float):
        if self.progress['mode'] != "indeterminate":
            self.progress['value'] = max(0, min(100, fraction * 100))
        self.root.update_idletasks()

    def _stop_progress(self):
        if self.progress['mode'] == "indeterminate":
            self.progress.stop()
        self.progress.pack_forget()

    def _start_timer(self):
        self._timer_start = time.time()
        self._timer_running = True
        self.timer_var.set("Elapsed: 0.0s")
        self._update_timer()

    def _update_timer(self):
        if self._timer_running:
            elapsed = time.time() - self._timer_start
            self.timer_var.set(f"Elapsed: {elapsed:.1f}s")
            self.root.after(100, self._update_timer)

    def _stop_timer(self):
        if self._timer_running:
            self._timer_running = False
            elapsed = time.time() - self._timer_start
            self.timer_var.set(f"Elapsed: {elapsed:.1f}s")

    def _update_count(self, current: int | None = None):
        if current is not None:
            self.chapter_chars = current
            self.chapter_tokens = current // 4
        trans_chars = self.chapter_chars
        trans_tokens = self.chapter_tokens
        overall = self.book_tokens + self.chapter_tokens
        self.count_var.set(
            f"Src: {self.source_chars} | Est: ~{self.source_tokens} | "
            f"Trans: {trans_chars} | Used: ~{trans_tokens} | Total: ~{overall}"
        )

    def _stream_translation(self, text: str):
        self.translations[self.current_title] = text
        if self.view_var.get() == "Translation":
            self.text.configure(state="normal")
            self.text.delete('1.0', 'end')
            self.text.insert('1.0', text)
            self.text.see('end')
            self.text.update_idletasks()
            self.text.configure(state="disabled")
        self._update_count(len(text))

    def cancel_translation(self):
        self.cancel_event.set()

    def toggle_pause(self):
        if not self.translate_thread or not self.translate_thread.is_alive():
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_btn.configure(text="Pause")
        else:
            self.pause_event.set()
            self.pause_btn.configure(text="Resume")

    def _update_detect_label(self):
        lang = getattr(self, 'detected_var', StringVar()).get()
        self.detect_label.configure(text=f"detected: {lang}")

    def _update_range_menus(self):
        titles = list(self.chapters.keys())
        menu = self.start_menu['menu']
        menu.delete(0, 'end')
        for t in titles:
            menu.add_command(label=t, command=lambda v=t: self.start_var.set(v))
        menu2 = self.end_menu['menu']
        menu2.delete(0, 'end')
        for t in titles:
            menu2.add_command(label=t, command=lambda v=t: self.end_var.set(v))
        if titles:
            self.start_var.set(titles[0])
            self.end_var.set(titles[0])

    def _toggle_left_pane(self):
        if self.left_pane.winfo_ismapped():
            self.left_pane.pack_forget()
        else:
            self.left_pane.pack(side=LEFT, fill="y")
            self.left_pane.pack_propagate(True)

    def prev_chapter(self):
        idx = self.combo.current()
        if idx > 0:
            self._load_chapter(idx - 1)

    def next_chapter(self):
        idx = self.combo.current()
        if idx < len(self.chapters) - 1:
            self._load_chapter(idx + 1)

    def copy_text(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.text.get('1.0', 'end').strip())

    def open_glossary(self):
        win = Toplevel(self.root)
        win.title("Glossary")

        Label(win, text="Add terms as 'source -> translation' to keep names and places consistent.").pack(fill="x")

        listbox = Listbox(win)
        listbox.pack(side=LEFT, fill=BOTH, expand=True)

        entry_frame = Frame(win)
        entry_frame.pack(side=LEFT, fill=BOTH, expand=True)
        src_var = StringVar()
        dst_var = StringVar()
        Entry(entry_frame, textvariable=src_var, width=20).pack()
        Entry(entry_frame, textvariable=dst_var, width=20).pack()

        def refresh():
            listbox.delete(0, END)
            for k, v in self.glossary.items():
                listbox.insert(END, f"{k} -> {v}")

        def on_add():
            s = src_var.get().strip()
            d = dst_var.get().strip()
            if s:
                self.glossary[s] = d
                src_var.set("")
                dst_var.set("")
                refresh()
                self._reapply_glossary()

        def on_edit():
            if not listbox.curselection():
                return
            idx = listbox.curselection()[0]
            key = list(self.glossary.keys())[idx]
            src_var.set(key)
            dst_var.set(self.glossary[key])

        def on_remove():
            if not listbox.curselection():
                return
            idx = listbox.curselection()[0]
            key = list(self.glossary.keys())[idx]
            self.glossary.pop(key, None)
            refresh()
            self._reapply_glossary()

        btn_frame = Frame(entry_frame)
        btn_frame.pack()
        Button(btn_frame, text="Add", command=on_add).pack(side=LEFT)
        Button(btn_frame, text="Edit", command=on_edit).pack(side=LEFT)
        Button(btn_frame, text="Remove", command=on_remove).pack(side=LEFT)

        def on_close():
            self._save_glossary()
            self._reapply_glossary()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        refresh()

    def _build_model_menu(self):
        models = []
        self.display_to_model = {}
        for m in self.local_models:
            state = "normal" if is_model_supported(m) else "disabled"
            models.append((m, state))
            self.display_to_model[m] = m

        models.append(("Custom", "normal"))

        menu = self.ollama_menu['menu']
        menu.delete(0, 'end')
        for label, state in models:
            menu.add_command(label=label, command=lambda v=label: self.set_model(v))
            if state == 'disabled':
                menu.entryconfig(label, state='disabled')

    def refresh_models(self):
        self.local_models = list_local_ollama_models()
        self._build_model_menu()
        self._backend_changed()

    def download_custom(self):
        name = self.custom_var.get().strip()
        if not name:
            return
        if name in self.local_models:
            messagebox.showinfo("Model Exists", f"{name} already installed")
            return
        if not ensure_ollama_running():
            messagebox.showerror("Error", "Failed to start Ollama server")
            return
        self._start_progress("indeterminate")

        def task():
            success = pull_ollama_model(name)
            self.root.after(0, lambda: self._on_download_complete_gui(name, success))

        Thread(target=task, daemon=True).start()

    def _on_download_complete_gui(self, name: str, success: bool):
        self._stop_progress()
        if not success:
            messagebox.showerror("Error", f"Failed to download {name}")
            return
        self.refresh_models()
        self.ollama_var.set(name)
        self.custom_entry.pack_forget()
        self.download_btn.pack_forget()
        self.hf_entry.delete("1.0", "end")
        self.hf_entry.insert("1.0", name)

    def _backend_changed(self, *_):
        backend = self.backend_var.get()
        if backend == 'ollama':
            self.hf_entry.pack_forget()
            self.ollama_menu.pack(side=LEFT)
            if self.ollama_var.get() == 'Custom':
                self.custom_entry.pack(side=LEFT)
                self.download_btn.pack(side=LEFT)
            else:
                self.custom_entry.pack_forget()
                self.download_btn.pack_forget()
            self.model = self.ollama_var.get()
        else:
            self.ollama_menu.pack_forget()
            self.custom_entry.pack_forget()
            self.download_btn.pack_forget()
            self.hf_entry.pack(side=LEFT)
            self.model = self.hf_entry.get("1.0", "end").strip()

    def _toggle_adv(self):
        if self.adv_var.get():
            self.adv_frame.pack(fill="x")
        else:
            self.adv_frame.pack_forget()

    def _toggle_model_adv(self):
        if self.model_adv_var.get():
            self.model_frame.pack(fill="x")
        else:
            self.model_frame.pack_forget()

    def _toggle_force_window(self):
        state = DISABLED if self.force_window_var.get() else NORMAL
        self.window_entry.configure(state=state)
        self.overlap_entry.configure(state=state)

    def _source_changed(self, *_):
        if hasattr(self, 'source_custom_entry'):
            if self.source_lang_display.get() == "Custom":
                self.source_custom_entry.pack(side=LEFT)
            else:
                self.source_custom_entry.pack_forget()
        self._update_detect_label()

    def _target_changed(self, *_):
        if hasattr(self, 'target_custom_entry'):
            if self.target_lang_display.get() == "Custom":
                self.target_custom_entry.pack(side=LEFT)
            else:
                self.target_custom_entry.pack_forget()

    def _display_current_text(self):
        title = getattr(self, 'current_title', None)
        if not title:
            return
        view = self.view_var.get()
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        if view == 'Translation' and title in self.translations:
            if self.diff_var.get() and title in self.full_raw_translations:
                raw_lines = self.full_raw_translations[title].splitlines()
                final_lines = self.translations[title].splitlines()
                diff_lines = difflib.unified_diff(
                    raw_lines,
                    final_lines,
                    fromfile='raw',
                    tofile='pruned',
                    lineterm='',
                )
                for line in diff_lines:
                    tag = (
                        'add'
                        if line.startswith('+') and not line.startswith('+++')
                        else 'remove'
                        if line.startswith('-') and not line.startswith('---')
                        else ''
                    )
                    self.text.insert('end', line + '\n', tag)
                self.text.configure(state='disabled')
                self._update_count(None)
                return
            content = self.translations[title] if self.prune_var.get() else self.full_raw_translations.get(title, self.translations[title])
        else:
            content = self.chapters.get(title, '')
            if self.clean_var.get():
                content = clean_text(content)
            if self.diff_var.get():
                raw = self.chapters.get(title, '')
                cleaned = clean_text(raw)
                sentences = split_sentences(cleaned)
                with_ids = '. '.join(f"[{i}] {s}" for i, s in enumerate(sentences))
                diff_lines = difflib.unified_diff(
                    raw.splitlines(),
                    with_ids.splitlines(),
                    fromfile='raw',
                    tofile='cleaned+ids',
                    lineterm='',
                )
                for line in diff_lines:
                    tag = (
                        'add'
                        if line.startswith('+') and not line.startswith('+++')
                        else 'remove'
                        if line.startswith('-') and not line.startswith('---')
                        else ''
                    )
                    self.text.insert('end', line + '\n', tag)
                self.text.configure(state='disabled')
                self._update_count(None)
                return
        self.text.insert('1.0', content)
        self.text.configure(state='disabled')
        self._update_count(None)

    def _load_state(self):
        self.first_run = not self.state_path.exists()
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text())
                self.translations = data.get("translations", {})
                self.translated_titles = data.get("translated_titles", {})
                self.raw_translations = data.get("raw_translations", {})
                self.full_raw_translations = data.get("full_raw_translations", {})
                self.backend = data.get("backend", self.backend)
                self.model = data.get("model", self.model)
                self.total_chars = data.get("total_chars", 0)
                self.book_tokens = data.get("total_tokens", 0)
                last_book = data.get("book")
                if last_book and Path(last_book).exists():
                    self.epub_path = Path(last_book)
                    self.chapters = extract_chapters(str(self.epub_path))
            except Exception:
                pass
        else:
            self.backend = "ollama"
            self.model = "Custom"
            self.custom_default = "gemma3:12b-it"

        self._update_glossary_path()
        self._load_glossary()

    def _update_glossary_path(self):
        if self.epub_path:
            stem = Path(self.epub_path).stem
            self.glossary_path = Path(f"{stem}_glossary.json")
        else:
            self.glossary_path = Path("glossary.json")

    def _save_state(self):
        data = {
            "translations": self.translations,
            "translated_titles": self.translated_titles,
            "raw_translations": self.raw_translations,
            "full_raw_translations": self.full_raw_translations,
            "backend": self.backend_var.get(),
            "model": self.model,
            "book": str(self.epub_path) if self.epub_path else None,
            "total_chars": self.total_chars,
            "total_tokens": self.book_tokens,
        }
        self.state_path.write_text(json.dumps(data))
        self._save_glossary()

    def _load_glossary(self):
        if self.glossary_path.exists():
            try:
                self.glossary = json.loads(self.glossary_path.read_text())
            except Exception:
                self.glossary = {}

    def _save_glossary(self):
        try:
            self.glossary_path.write_text(json.dumps(self.glossary))
        except Exception:
            pass

    def _reapply_glossary(self):
        for title, raw in self.full_raw_translations.items():
            cleaned = prune_marked(raw)
            self.translations[title] = apply_glossary(cleaned, self.glossary)
        for t, text in list(self.translated_titles.items()):
            self.translated_titles[t] = apply_glossary(text, self.glossary)
        self._display_current_text()

    def _on_chapter_select(self, _):
        idx = self.combo.current()
        self._load_chapter(idx)

    def _load_chapter(self, index: int):
        titles = list(self.chapters.keys())
        if not titles:
            return
        title = titles[index]
        self.chapter_var.set(title)
        self.current_title = title
        raw_text = self.chapters.get(title, "")
        cleaned = clean_text(raw_text) if self.clean_var.get() else raw_text
        self.source_chars = len(cleaned)
        self.source_tokens = self.source_chars // 4
        self.chapter_chars = len(self.translations.get(title, ""))
        self.chapter_tokens = self.chapter_chars // 4
        lang = detect_language(cleaned)
        self.detected_var.set(lang)
        self._update_detect_label()
        self._display_current_text()
        self._update_count(None)

    def set_model(self, value: str):
        model_name = self.display_to_model.get(value, value)
        self.ollama_var.set(model_name)
        self.model = model_name
        if value == "Custom":
            self.custom_entry.pack(side=LEFT)
            self.download_btn.pack(side=LEFT)
        else:
            self.custom_entry.pack_forget()
            self.download_btn.pack_forget()
            self.hf_entry.delete("1.0", "end")
            self.hf_entry.insert("1.0", model_name)
        self._backend_changed()

    def translate_current(self):
        backend = self.backend_var.get()
        if backend == "hf":
            if messagebox.askyesno("Model Download", "Model may be downloaded from HuggingFace. Continue?") is False:
                return
        self.cancel_event.clear()
        self.pause_event.clear()
        self.translate_thread = Thread(target=self._translate_current_task, daemon=True)
        self.translate_thread.start()

    def _translate_current_task(self):
        title = self.chapter_var.get()
        self.translations.pop(title, None)
        self.raw_translations.pop(title, None)
        self.full_raw_translations.pop(title, None)
        self.removed_content.pop(title, None)
        raw = self.chapters[title]
        if self.clean_var.get():
            cleaned, removed = clean_text(raw, return_removed=True)
            self.removed_content[title] = removed
        else:
            cleaned = raw
        sentences = split_sentences(cleaned)
        lang_display = self.source_lang_display.get()
        if lang_display == 'Custom':
            lang_override = self.custom_source_var.get().strip() or 'auto'
        else:
            lang_override = self.lang_map.get(lang_display, 'auto') if lang_display != 'Auto' else 'auto'
        if lang_override == "auto":
            detected = detect_language(cleaned)
        else:
            detected = lang_override
        target_display = self.target_lang_display.get()
        if target_display == 'Custom':
            self.target_lang = self.custom_target_var.get().strip() or 'en'
        else:
            self.target_lang = self.lang_map.get(target_display, 'en')
        self.detected_var.set(detected)
        self._update_detect_label()
        backend = self.backend_var.get()
        options = None
        if backend == "ollama":
            if not ensure_ollama_running():
                self.root.after(0, lambda: messagebox.showerror("Error", "Failed to start Ollama server"))
                return
            if self.model_adv_var.get():
                try:
                    options = {
                        "temperature": float(self.temp_var.get()),
                        "num_ctx": int(self.ctx_var.get()),
                        "top_k": int(self.top_k_var.get()),
                        "top_p": float(self.top_p_var.get()),
                        "min_p": float(self.min_p_var.get()),
                    }
                except ValueError:
                    options = None
        if backend == "hf":
            if messagebox.askyesno("Model Download", "Model may be downloaded from HuggingFace. Continue?") is False:
                return
        if backend == "hf":
            model_name = self.hf_entry.get("1.0", "end").strip()
        else:
            model_name = self.custom_var.get().strip() if self.ollama_var.get() == "Custom" else self.ollama_var.get()

        title_translation = title
        raw_title = title
        try:
            outs, upd, raws = translate_sentences(
                [title],
                model_name,
                self.target_lang,
                backend == "ollama",
                options=options if backend == "ollama" else None,
                glossary=self.glossary,
                cancel_event=self.cancel_event,
                pause_event=self.pause_event,
                start_mark=TITLE_START,
                end_mark=TITLE_END,
            )
            title_translation = outs[0]
            raw_title = raws[0]
            if upd:
                for k, v in upd.items():
                    if k not in self.glossary:
                        self.glossary[k] = v
        except Exception:
            pass
        self.translated_titles[title] = title_translation

        self.root.after(0, lambda: self._start_progress("determinate"))
        self.root.after(0, self._start_timer)
        self._update_count(0)

        def progress_cb(frac: float):
            self.root.after(0, lambda f=frac: self._update_progress(f))

        partial = ["" for _ in sentences]

        def token_cb(idx: int, text_piece: str):
            partial[idx] = text_piece
            joined = ". ".join(partial).strip()
            self.root.after(0, lambda j=joined: self._stream_translation(j))

        try:
            win = int(self.window_var.get() or 50) if self.adv_var.get() else 50
            ov = int(self.overlap_var.get() or 10) if self.adv_var.get() else 10
            if self.force_window_var.get():
                win = len(sentences)
                ov = 0
            translations, updates, raw_sents = translate_with_context(
            sentences,
            model_name,
            self.target_lang,
            backend == "ollama",
            window=win,
            overlap=ov,
            options=options if backend == "ollama" else None,
            progress_callback=progress_cb,
            token_callback=token_cb if backend == "ollama" else None,
            glossary=self.glossary,
            cancel_event=self.cancel_event,
            pause_event=self.pause_event,
            )
        except Exception as exc:
            self.root.after(0, self._stop_progress)
            self.root.after(0, self._stop_timer)
            self.root.after(0, lambda: messagebox.showerror("Error", str(exc)))
            return

        self.root.after(0, self._stop_progress)
        self.root.after(0, self._stop_timer)
        if self.cancel_event.is_set():
            self.pause_event.clear()
            self.root.after(0, lambda: self.pause_btn.configure(text="Pause"))
            return
        
        result = title_translation + "\n\n" + '. '.join(translations)
        raw_full = raw_title + "\n\n" + '. '.join(raw_sents)
        if updates:
            for k, v in updates.items():
                if k not in self.glossary:
                    self.glossary[k] = v
        self.translations[title] = result
        self.raw_translations[title] = '. '.join(partial) if backend == "ollama" else result
        self.full_raw_translations[title] = raw_full
        self.total_chars += len(result)
        self.chapter_chars = len(result)
        self.chapter_tokens = len(result) // 4
        self.book_tokens += self.chapter_tokens
        self._display_current_text()
        self._save_state()
        self.pause_event.clear()
        self.root.after(0, lambda: self.pause_btn.configure(text="Pause"))

    def clear_state(self):
        if not messagebox.askyesno("Confirm", "Clear all saved data?"):
            return
        for p in [self.state_path, self.glossary_path]:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        self.translations.clear()
        self.raw_translations.clear()
        self.full_raw_translations.clear()
        self.removed_content.clear()
        self.translated_titles.clear()
        self.glossary.clear()
        self.total_chars = 0
        self.book_tokens = 0
        self.source_chars = 0
        self.source_tokens = 0
        self.chapter_chars = 0
        self.chapter_tokens = 0
        self.epub_path = None
        self.chapters = {}
        self.combo['values'] = []
        self._update_range_menus()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._stop_timer()
        self._update_count(None)

    def translate_range(self):
        backend = self.backend_var.get()
        if backend == "hf":
            if messagebox.askyesno("Model Download", "Model may be downloaded from HuggingFace. Continue?") is False:
                return
        self.cancel_event.clear()
        self.pause_event.clear()
        self.translate_thread = Thread(target=self._translate_range_task, daemon=True)
        self.translate_thread.start()

    def _translate_range_task(self):
        titles = list(self.chapters.keys())
        if not titles:
            return
        try:
            start = titles.index(self.start_var.get())
            end = titles.index(self.end_var.get())
        except ValueError:
            return
        if start > end:
            start, end = end, start
        for idx in range(start, end + 1):
            if self.cancel_event.is_set():
                break
            self._load_chapter(idx)
            self._translate_current_task()
        self._display_current_text()

    def choose_chapter(self):
        win = Toplevel(self.root)
        win.title("Table of Contents")
        lb = Listbox(win)
        for title in self.chapters:
            lb.insert(END, title)
        lb.pack(fill=BOTH, expand=True)

        def on_select(_):
            if lb.curselection():
                idx = lb.curselection()[0]
                self._load_chapter(idx)
                win.destroy()

        lb.bind("<<ListboxSelect>>", on_select)

    def open_book(self):
        path = filedialog.askopenfilename(filetypes=[
            ("Ebooks", "*.epub *.pdf *.txt *.docx"),
            ("EPUB", "*.epub"),
            ("PDF", "*.pdf"),
            ("Text", "*.txt"),
            ("Word", "*.docx"),
        ])
        if not path:
            return
        self.epub_path = Path(path)
        self._update_glossary_path()
        self._load_glossary()
        self.chapters = extract_chapters(path)
        self.combo['values'] = list(self.chapters.keys())
        self._update_range_menus()
        if self.chapters:
            self._load_chapter(0)

    def remove_book(self):
        self.epub_path = None
        self.chapters = {}
        self._update_glossary_path()
        self.glossary = {}
        self.combo['values'] = []
        self._update_range_menus()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.source_chars = 0
        self.source_tokens = 0
        self._update_count(0)

    def show_diff(self):
        self.diff_var.set(1)
        self._display_current_text()

    def save_epub(self):
        if not self.translations:
            messagebox.showinfo("Nothing to save", "No chapters translated yet")
            return
        dest = filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[("EPUB files", "*.epub")])
        if not dest:
            return
        export_chapters(str(self.epub_path), self.translations, dest, self.translated_titles)
        messagebox.showinfo("Saved", f"Exported to {dest}")

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._save_state)
        self.root.mainloop()


def run_ui(epub: Path | None, model: str, target: str, backend: str):
    ui = TranslatorUI(epub, model, target, backend)
    ui.run()
