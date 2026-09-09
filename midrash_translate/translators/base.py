"""Translator interface, sentence-level caching, and the backend registry."""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict

log = logging.getLogger(__name__)

from .glossary import Glossary

# Split on sentence boundaries so that editing the tail of a paragraph reuses the
# cached translations of the sentences before it, and so each model call stays in
# the length range Marian was trained on.
_SPLIT = re.compile(r'(?<=[.!?:;])\s+(?=[^\s])')


class Translator:
    """One backend for one language pair. Implementations override translate_batch."""

    def __init__(self, lang: str, glossary: Glossary):
        self.lang = lang
        self.glossary = glossary

    def warm(self) -> None:
        """Load models / open connections. Called once before serving."""

    def translate_batch(self, sentences: list[str]) -> list[str]:
        raise NotImplementedError

    @property
    def label(self) -> str:
        return type(self).__name__


class CachingTranslator:
    """Wraps a Translator with sentence splitting, glossary handling and an LRU."""

    def __init__(self, inner: Translator, cache_size: int = 20000,
                 fallback: Translator | None = None):
        self.inner = inner
        self.fallback = fallback
        self.lang = inner.lang
        self.glossary = inner.glossary
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.lost_markers = 0
        # set when the primary backend cannot be used at all (no API key, model
        # missing) but a fallback exists -- the language keeps working, degraded
        self.degraded = False

    def warm(self) -> None:
        try:
            self.inner.warm()
        except Exception as exc:
            if not self.fallback:
                raise
            # a missing GEMINI_API_KEY must not take a language off the
            # projector; drop to the local model and say so
            log.warning("%s: primary backend %s unavailable (%s); using %s",
                        self.lang, self.inner.label, exc, self.fallback.label)
            self.degraded = True
        if self.fallback:
            self.fallback.warm()

    def _cache_get(self, key: str) -> str | None:
        with self._lock:
            val = self._cache.get(key)
            if val is not None:
                self._cache.move_to_end(key)
            return val

    def _cache_put(self, key: str, val: str) -> None:
        with self._lock:
            self._cache[key] = val
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    def translate(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""

        sentences = [s for s in _SPLIT.split(text) if s.strip()] or [text]
        out: list[str | None] = [None] * len(sentences)
        todo: list[tuple[int, str, list[str]]] = []  # index, masked, protected

        for i, sent in enumerate(sentences):
            cached = self._cache_get(sent)
            if cached is not None:
                out[i] = cached
                self.hits += 1
                continue
            masked, restores = self.glossary.mask(sent, self.lang)
            if not self.glossary.has_translatable_text(masked):
                # e.g. a line that is entirely a Hebrew quotation. Handing the
                # model nothing but markers makes it hallucinate.
                final = self.glossary.unmask(masked, restores) or sent
                out[i] = final
                self._cache_put(sent, final)
                self.hits += 1
                continue
            self.misses += 1
            todo.append((i, masked, restores))

        if todo:
            payload = [m for _, m, _ in todo]
            if self.degraded:
                results = self.fallback.translate_batch(payload)
            else:
                try:
                    results = self.inner.translate_batch(payload)
                except Exception as exc:
                    if not self.fallback:
                        raise
                    # transient: quota spent, network dropped mid-lecture
                    log.warning("%s: %s failed (%s); falling back to %s",
                                self.lang, self.inner.label, exc, self.fallback.label)
                    results = self.fallback.translate_batch(payload)

            for (i, masked, restores), raw in zip(todo, results):
                restored = self.glossary.unmask(raw, restores)
                if restored is None:
                    # The model swallowed a marker. Do NOT retranslate the
                    # sentence unprotected: for a line containing quoted Hebrew
                    # that is exactly what produces rows of dashes. Retry the
                    # masked text once, then give up and show the source, which
                    # is at least readable and has the quotations intact.
                    self.lost_markers += 1
                    try:
                        retry = self.inner.translate_batch([masked])[0]
                        restored = self.glossary.unmask(retry, restores)
                    except Exception:
                        restored = None
                    if restored is None:
                        log.warning("%s: markers lost for %r; showing source",
                                    self.lang, sentences[i][:60])
                        restored = sentences[i]
                final = restored.strip()
                out[i] = final
                self._cache_put(sentences[i], final)

        return " ".join(s for s in out if s)

    @property
    def label(self) -> str:
        if self.degraded and self.fallback:
            return f"{self.fallback.label} (fallback from {self.inner.label})"
        base = self.inner.label
        return f"{base} -> {self.fallback.label}" if self.fallback else base


def build(lang: str, backend: str, opts: dict, glossary: Glossary) -> Translator:
    # "fallback" is consumed by the engine, not by the backend itself
    opts = {k: v for k, v in (opts or {}).items() if k != "fallback"}
    if backend == "opusmt":
        from .opusmt import OpusMTTranslator
        return OpusMTTranslator(lang, glossary, **opts)
    if backend == "gemini":
        from .gemini import GeminiTranslator
        return GeminiTranslator(lang, glossary, **opts)
    if backend == "echo":
        from .echo import EchoTranslator
        return EchoTranslator(lang, glossary, **opts)
    raise ValueError(f"unknown translation backend: {backend!r}")
