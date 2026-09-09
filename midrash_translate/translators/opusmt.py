"""Local Helsinki-NLP Opus-MT models. Free, offline, unlimited, no API key.

One small (~300MB) model per language pair. Int8 dynamic quantization roughly
halves CPU latency at a negligible quality cost, which matters when three
languages share one laptop.
"""
from __future__ import annotations

import logging
import threading

from .base import Translator

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "fr": "Helsinki-NLP/opus-mt-en-fr",
    "es": "Helsinki-NLP/opus-mt-en-es",
    "he": "Helsinki-NLP/opus-mt-en-he",
    "de": "Helsinki-NLP/opus-mt-en-de",
    "it": "Helsinki-NLP/opus-mt-en-it",
    "ru": "Helsinki-NLP/opus-mt-en-ru",
    "ar": "Helsinki-NLP/opus-mt-en-ar",
    "pt": "Helsinki-NLP/opus-mt-en-roa",
}


class OpusMTTranslator(Translator):
    def __init__(self, lang, glossary, models=None, quantize=True, beams=1,
                 cache_dir="models", max_length=512):
        super().__init__(lang, glossary)
        self.model_id = (models or {}).get(lang) or DEFAULT_MODELS.get(lang)
        if not self.model_id:
            raise ValueError(
                f"no Opus-MT model configured for language {lang!r}; add one under "
                f"translation.opusmt.models in config.yaml"
            )
        self.quantize = quantize
        self.beams = beams
        self.cache_dir = cache_dir
        self.max_length = max_length
        self._tok = None
        self._model = None
        # transformers generate() is not reentrant across threads for one model
        self._lock = threading.Lock()

    def warm(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import MarianMTModel, MarianTokenizer

        log.info("loading %s for %s", self.model_id, self.lang)
        self._tok = MarianTokenizer.from_pretrained(self.model_id, cache_dir=self.cache_dir)
        model = MarianMTModel.from_pretrained(self.model_id, cache_dir=self.cache_dir)
        model.eval()
        if self.quantize:
            try:
                model = torch.quantization.quantize_dynamic(
                    model, {torch.nn.Linear}, dtype=torch.qint8
                )
            except Exception as exc:  # quantization is an optimization, not a requirement
                log.warning("int8 quantization unavailable for %s: %s", self.lang, exc)
        self._model = model
        torch.set_num_threads(max(1, (torch.get_num_threads() or 4) // 2))

    def translate_batch(self, sentences: list[str]) -> list[str]:
        if not sentences:
            return []
        self.warm()
        import torch

        with self._lock, torch.inference_mode():
            batch = self._tok(sentences, return_tensors="pt", padding=True,
                              truncation=True, max_length=self.max_length)
            generated = self._model.generate(
                **batch,
                num_beams=self.beams,
                max_length=self.max_length,
                early_stopping=self.beams > 1,
            )
            return self._tok.batch_decode(generated, skip_special_tokens=True)

    @property
    def label(self) -> str:
        return f"opusmt:{self.model_id.split('/')[-1]}"
