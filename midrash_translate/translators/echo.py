"""No-op backend. Lets you exercise the whole pipeline -- Word capture, diffing,
websockets, projector pages -- without downloading any models."""
from __future__ import annotations

from .base import Translator


class EchoTranslator(Translator):
    def __init__(self, lang, glossary, prefix=True):
        super().__init__(lang, glossary)
        self.prefix = prefix

    def translate_batch(self, sentences: list[str]) -> list[str]:
        tag = f"[{self.lang}] " if self.prefix else ""
        return [tag + s for s in sentences]

    @property
    def label(self) -> str:
        return f"echo:{self.lang}"
