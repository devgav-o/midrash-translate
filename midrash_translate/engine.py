"""Translation dispatch: one worker thread per language, with coalescing.

If a paragraph is edited three times while its translation is in flight, only the
newest text is ever translated -- the queue is a dict keyed by paragraph id, not
a list. That is what keeps the display near-realtime when the transcriber is
backspacing.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .state import Change, DocState
from .translators import CachingTranslator, Glossary, build

log = logging.getLogger(__name__)

# lang, para_id -> called after a translation lands or a paragraph disappears
OnUpdate = Callable[[str, int], None]


class LanguageWorker(threading.Thread):
    def __init__(self, lang: str, translator: CachingTranslator, state: DocState,
                 on_update: OnUpdate):
        super().__init__(name=f"translate-{lang}", daemon=True)
        self.lang = lang
        self.translator = translator
        self.state = state
        self.on_update = on_update
        self._pending: dict[int, tuple[str, int]] = {}  # para_id -> (text, version)
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self.ready = False
        self.last_error: str | None = None
        self.translated = 0
        self.empty_results = 0

    def submit(self, change: Change) -> None:
        with self._cv:
            self._pending[change.para_id] = (change.text, change.version)
            self._cv.notify()

    def drop(self, para_id: int) -> None:
        with self._cv:
            self._pending.pop(para_id, None)

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()

    def depth(self) -> int:
        with self._cv:
            return len(self._pending)

    def run(self) -> None:
        try:
            self.translator.warm()
            self.ready = True
            log.info("%s ready (%s)", self.lang, self.translator.label)
        except Exception as exc:
            self.last_error = f"failed to load: {exc}"
            log.error("%s backend unavailable: %s", self.lang, exc)
            return

        while not self._stop.is_set():
            with self._cv:
                while not self._pending and not self._stop.is_set():
                    self._cv.wait(timeout=0.5)
                if self._stop.is_set():
                    return
                # oldest paragraph first, so the display fills in reading order
                para_id = min(self._pending)
                text, version = self._pending.pop(para_id)

            started = time.monotonic()
            try:
                result = self.translator.translate(text)
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("%s translation failed for para %s: %s", self.lang, para_id, exc)
                continue

            self.translated += 1
            elapsed = (time.monotonic() - started) * 1000
            log.debug("%s para=%s v=%s %.0fms  %r -> %r",
                      self.lang, para_id, version, elapsed, text, result)

            if text.strip() and not result.strip():
                # never blank a line that has real source text: a projector
                # showing nothing reads as a broken system. Keep whatever the
                # line had and surface the count in /api/status.
                self.empty_results += 1
                log.warning("%s: empty translation for para %s (%r); keeping "
                            "previous text", self.lang, para_id, text[:60])
                continue
            if elapsed > 1500:
                log.info("%s slow translation: %.0fms for %d chars",
                         self.lang, elapsed, len(text))

            if self.state.apply_translation(para_id, self.lang, version, result):
                self.on_update(self.lang, para_id)


class Engine:
    """Owns one worker per target language."""

    def __init__(self, cfg, state: DocState, on_update: OnUpdate):
        self.cfg = cfg
        self.state = state
        self.on_update = on_update
        self.glossary = Glossary.load(
            cfg.glossary_path,
            rtl_langs={t.code for t in cfg.targets if t.rtl},
            preserve_scripts=cfg.preserve_scripts,
            isolate_bidi=cfg.isolate_bidi,
        )
        self.workers: dict[str, LanguageWorker] = {}

        for target in cfg.targets:
            lang = target.code
            backend = cfg.backend_for(lang)
            opts = cfg.backend_opts(backend)

            fallback = None
            fb_name = opts.get("fallback")
            if fb_name and fb_name != backend:
                try:
                    fallback = build(lang, fb_name, cfg.backend_opts(fb_name), self.glossary)
                except Exception as exc:
                    log.warning("no fallback for %s: %s", lang, exc)

            inner = build(lang, backend, opts, self.glossary)
            translator = CachingTranslator(inner, cfg.cache_size, fallback)
            self.workers[lang] = LanguageWorker(lang, translator, state, on_update)

    def start(self) -> None:
        for w in self.workers.values():
            w.start()

    def stop(self) -> None:
        for w in self.workers.values():
            w.stop()

    def dispatch(self, changes: list[Change]) -> None:
        for change in changes:
            for w in self.workers.values():
                if change.deleted:
                    w.drop(change.para_id)
                else:
                    w.submit(change)

    def status(self) -> dict:
        return {
            lang: {
                "backend": w.translator.label,
                "ready": w.ready,
                "queue": w.depth(),
                "translated": w.translated,
                "empty_results": w.empty_results,
                "cache_hits": w.translator.hits,
                "cache_misses": w.translator.misses,
                "lost_markers": w.translator.lost_markers,
                "error": w.last_error,
            }
            for lang, w in self.workers.items()
        }
