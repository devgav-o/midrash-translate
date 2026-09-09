"""Google Gemini free tier. Better quality than the local models, Hebrew
especially, at the cost of a network round trip and a daily request quota.

Set GEMINI_API_KEY in the environment. Get a key at https://aistudio.google.com/apikey
"""
from __future__ import annotations

import json
import logging
import os

import requests

from .base import Translator

log = logging.getLogger(__name__)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

LANG_NAMES = {
    "fr": "French", "es": "Spanish", "he": "Hebrew", "de": "German",
    "it": "Italian", "ru": "Russian", "ar": "Arabic", "pt": "Portuguese",
}

SYSTEM = (
    "You translate live classroom lecture transcript into {target}. "
    "Translate every numbered line. Preserve meaning and register; this is a "
    "lecture, so keep it plain and readable. Do not add commentary, notes or "
    "explanations. Do not merge or split lines. {glossary}"
    "Reply with a JSON array of strings, one per input line, in order, and "
    "nothing else."
)


class GeminiTranslator(Translator):
    def __init__(self, lang, glossary, model="gemini-2.0-flash", timeout_s=12,
                 api_key_env="GEMINI_API_KEY"):
        super().__init__(lang, glossary)
        self.model = model
        self.timeout_s = timeout_s
        self.api_key_env = api_key_env
        self._session = requests.Session()

    def warm(self) -> None:
        if not os.environ.get(self.api_key_env):
            raise RuntimeError(
                f"{self.api_key_env} is not set; the gemini backend needs a free "
                f"API key from https://aistudio.google.com/apikey"
            )

    def translate_batch(self, sentences: list[str]) -> list[str]:
        if not sentences:
            return []
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(f"{self.api_key_env} is not set")

        target = LANG_NAMES.get(self.lang, self.lang)
        hint = self.glossary.prompt_hint(self.lang)
        numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))

        body = {
            "systemInstruction": {
                "parts": [{"text": SYSTEM.format(
                    target=target, glossary=(hint + " ") if hint else "")}]
            },
            "contents": [{"role": "user", "parts": [{"text": numbered}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        resp = self._session.post(
            ENDPOINT.format(model=self.model),
            params={"key": key},
            json=body,
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"unexpected Gemini response: {json.dumps(data)[:300]}") from exc

        out = json.loads(text)
        if not isinstance(out, list):
            raise RuntimeError("Gemini did not return a JSON array")
        out = [str(x) for x in out]
        if len(out) != len(sentences):
            # line count drift would silently mis-pair sentences with paragraphs
            raise RuntimeError(
                f"Gemini returned {len(out)} lines for {len(sentences)} inputs")
        return out

    @property
    def label(self) -> str:
        return f"gemini:{self.model}"
