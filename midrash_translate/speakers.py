"""Speaker labels.

The transcriber prefixes lines to say who is talking -- "RY: today we learn
רשי", "T: can you repeat that?". The label is not part of the sentence: fed to
a translation model it corrupts the output ("RY:" becomes "RY :" in French, or
gets absorbed into the first word), and it belongs on the projector as a visible
attribution rather than as text.

So the label is split off before translation and re-attached for display.
"""
from __future__ import annotations

import re


class SpeakerParser:
    def __init__(self, labels: list[str] | None = None,
                 names: dict[str, dict[str, str]] | None = None):
        self.labels = [l for l in (labels or []) if l and l.strip()]
        self.names = names or {}
        # longest first so "RY" wins over a hypothetical "R"
        self._re = (
            # case-insensitive: a transcriber typing at speed writes "ry:" as
            # often as "RY:". The canonical form is restored for display.
            re.compile(r"^\s*(" + "|".join(re.escape(l) for l in
                       sorted(self.labels, key=len, reverse=True)) + r")\s*:\s*",
                       re.IGNORECASE)
            if self.labels else None
        )
        self._canonical = {l.lower(): l for l in self.labels}

    def split(self, text: str) -> tuple[str | None, str]:
        """('RY', 'today we learn') for 'RY: today we learn'; (None, text) if the
        line carries no label."""
        if not self._re:
            return None, text
        m = self._re.match(text)
        if not m:
            return None, text
        label = self._canonical.get(m.group(1).lower(), m.group(1))
        return label, text[m.end():]

    def display(self, label: str, lang: str) -> str:
        """How the label is written on a given language's projector. Defaults to
        the label itself, so it stays recognisable across all four screens."""
        return self.names.get(label, {}).get(lang, label)

    def prompt_hint(self) -> str:
        if not self.labels:
            return ""
        return ("Speaker labels like " + ", ".join(f'"{l}:"' for l in self.labels)
                + " are attributions, not content.")
