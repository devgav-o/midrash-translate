"""Document state: stable paragraph ids across edits, plus translation slots.

The watcher hands us a flat list of paragraph strings on every poll. We diff it
against the previous list so that inserting a paragraph in the middle shifts ids
rather than invalidating every paragraph after it -- otherwise one Enter keypress
would re-translate the whole lecture.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

SENTENCE_END = re.compile(r'[.!?:;׃؟]["\')\]”’]?\s*$')


@dataclass
class Para:
    id: int
    text: str
    # the line minus its speaker label, which is what actually gets translated
    body: str = ""
    speaker: str | None = None
    # bumped on every text change; translation results carrying an older version
    # are stale and get dropped
    version: int = 0
    changed_at: float = field(default_factory=time.monotonic)
    # lang -> translated text
    translations: dict[str, str] = field(default_factory=dict)
    # lang -> version of the source text that translation was produced from
    translated_version: dict[str, int] = field(default_factory=dict)

    def pending(self, lang: str) -> bool:
        return self.translated_version.get(lang, -1) != self.version


@dataclass
class Change:
    """A paragraph that needs (re)translating, or that disappeared."""
    para_id: int
    text: str
    version: int
    deleted: bool = False


class DocState:
    """Thread-safe store of the source paragraphs and their translations."""

    def __init__(self, langs: list[str], debounce_s: float = 0.7, speakers=None):
        self.langs = list(langs)
        self.debounce_s = debounce_s
        self.speakers = speakers
        self._lock = threading.RLock()
        self._paras: list[Para] = []
        self._by_id: dict[int, Para] = {}
        self._next_id = 1
        # paragraphs whose text changed but that are not settled enough to send
        self._dirty: set[int] = set()

    def _set_text(self, para: "Para", text: str) -> None:
        para.text = text
        if self.speakers:
            para.speaker, para.body = self.speakers.split(text)
        else:
            para.speaker, para.body = None, text

    # ---------------------------------------------------------------- ingest

    def update_source(self, texts: list[str]) -> list[Change]:
        """Apply a fresh paragraph list from Word. Returns deletions immediately;
        edits are held back until they settle (see collect_ready)."""
        texts = [t for t in texts]
        with self._lock:
            old = [p.text for p in self._paras]
            if old == texts:
                return []

            now = time.monotonic()
            matcher = SequenceMatcher(a=old, b=texts, autojunk=False)
            new_paras: list[Para] = []
            reused: set[int] = set()

            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    for off in range(i2 - i1):
                        p = self._paras[i1 + off]
                        reused.add(p.id)
                        new_paras.append(p)
                elif tag in ("replace", "delete", "insert"):
                    olds = self._paras[i1:i2]
                    news = texts[j1:j2]
                    # reuse ids positionally so an in-place edit keeps its id
                    for k, text in enumerate(news):
                        if k < len(olds):
                            p = olds[k]
                            if p.text != text:
                                self._set_text(p, text)
                                p.version += 1
                                p.changed_at = now
                                self._dirty.add(p.id)
                            reused.add(p.id)
                            new_paras.append(p)
                        else:
                            p = Para(id=self._next_id, text=text, version=1, changed_at=now)
                            self._set_text(p, text)
                            self._next_id += 1
                            self._by_id[p.id] = p
                            self._dirty.add(p.id)
                            reused.add(p.id)
                            new_paras.append(p)

            removed = [p for p in self._paras if p.id not in reused]
            for p in removed:
                self._by_id.pop(p.id, None)
                self._dirty.discard(p.id)

            self._paras = new_paras
            return [Change(p.id, "", p.version, deleted=True) for p in removed]

    def collect_ready(self) -> list[Change]:
        """Dirty paragraphs that have settled: quiet for debounce_s, or ending in
        sentence punctuation, or no longer the paragraph being typed in."""
        with self._lock:
            if not self._dirty:
                return []
            now = time.monotonic()
            last_id = self._paras[-1].id if self._paras else None
            ready: list[Change] = []
            for pid in list(self._dirty):
                p = self._by_id.get(pid)
                if p is None:
                    self._dirty.discard(pid)
                    continue
                if not p.body.strip():
                    # blank line: nothing to translate, but it still holds a slot
                    p.translations = {lang: "" for lang in self.langs}
                    p.translated_version = {lang: p.version for lang in self.langs}
                    self._dirty.discard(pid)
                    continue
                settled = (
                    p.id != last_id
                    or (now - p.changed_at) >= self.debounce_s
                    or bool(SENTENCE_END.search(p.body))
                )
                if settled:
                    ready.append(Change(p.id, p.body, p.version))
                    self._dirty.discard(pid)
            return ready

    # --------------------------------------------------------------- results

    def apply_translation(self, para_id: int, lang: str, version: int, text: str) -> bool:
        """Store a translation. False if the paragraph is gone or the result is
        stale (the source changed again while we were translating)."""
        with self._lock:
            p = self._by_id.get(para_id)
            if p is None or version < p.version:
                return False
            p.translations[lang] = text
            p.translated_version[lang] = version
            return True

    # ----------------------------------------------------------------- views

    def snapshot(self, lang: str) -> list[dict]:
        with self._lock:
            return [self._as_line(p, lang) for p in self._paras]

    def order(self) -> list[int]:
        with self._lock:
            return [p.id for p in self._paras]

    def line(self, para_id: int, lang: str) -> dict | None:
        with self._lock:
            p = self._by_id.get(para_id)
            return self._as_line(p, lang) if p else None

    def _as_line(self, p: Para, lang: str) -> dict:
        speaker = (self.speakers.display(p.speaker, lang)
                   if (self.speakers and p.speaker) else None)
        return {
            "id": p.id,
            "text": p.translations.get(lang, ""),
            "src": p.body,
            "speaker": speaker,
            "pending": p.pending(lang),
        }

    def all_lines(self, lang: str) -> list[tuple[str, str, str | None]]:
        """(translated, source, speaker) triples, for export."""
        with self._lock:
            return [(p.translations.get(lang, ""), p.body,
                     self.speakers.display(p.speaker, lang)
                     if (self.speakers and p.speaker) else None)
                    for p in self._paras]

    def stats(self) -> dict:
        with self._lock:
            return {
                "paragraphs": len(self._paras),
                "dirty": len(self._dirty),
                "pending": {
                    lang: sum(1 for p in self._paras if p.body.strip() and p.pending(lang))
                    for lang in self.langs
                },
            }
