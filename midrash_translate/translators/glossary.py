"""Protected terms and forced renderings.

Machine translation mangles transliterated terms and proper nouns, and it renders
them inconsistently across languages. Both problems are handled by the same
mechanism: before translating, the term is replaced with a numeric marker the
model passes through untouched; afterwards the marker is swapped for the exact
string we want in that language.

  protect: restore the English term verbatim (good where Latin script is fine)
  replace: restore a per-language rendering instead (needed for Hebrew, where a
           Latin-script word mid-sentence looks wrong)

Post-translation string replacement does not work for this -- once the model has
translated "the sages" to "les sages" there is nothing left to match on.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# The marker must survive the round trip through the model. This is not a matter
# of taste: measured on the three Opus-MT models, "%0%" and "[0]" survive 12/12
# probes while "⟦0⟧" survives 0/12 -- unusual Unicode becomes <unk> and is
# dropped. Re-run scripts/check_markers.py if you change models.
_MARK = "%{}%"
_MARK_RE = re.compile(r"%(\d+)%")


class Glossary:
    def __init__(self, protect: list[str] | None = None,
                 replace: dict[str, dict[str, str]] | None = None):
        self.protect = [t for t in (protect or []) if t and t.strip()]
        self.replace = {k: v for k, v in (replace or {}).items()
                        if isinstance(v, dict) and not k.startswith("_")}
        # longest first so "the sages" wins over a hypothetical "sages" entry
        terms = sorted(set(self.protect) | set(self.replace),
                       key=len, reverse=True)
        self._term_re = (
            re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b",
                       re.IGNORECASE)
            if terms else None
        )
        self._replace_ci = {k.lower(): v for k, v in self.replace.items()}

    @classmethod
    def load(cls, path: Path | None) -> "Glossary":
        if not path:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(protect=data.get("protect"), replace=data.get("replace"))

    def _restore_value(self, matched: str, lang: str) -> str:
        """What this occurrence should read as in the output."""
        forced = self._replace_ci.get(matched.lower(), {}).get(lang)
        return forced if forced else matched

    def mask(self, text: str, lang: str) -> tuple[str, list[str]]:
        """Returns the text with terms replaced by markers, plus the strings to
        substitute back in (already resolved for `lang`)."""
        if not self._term_re:
            return text, []
        # a stray "%1%" typed in the lecture would collide with our numbering
        text = _MARK_RE.sub(lambda m: m.group(0).replace("%", "% "), text)
        restores: list[str] = []

        def sub(m: re.Match) -> str:
            restores.append(self._restore_value(m.group(0), lang))
            return _MARK.format(len(restores) - 1)

        return self._term_re.sub(sub, text), restores

    def unmask(self, text: str, restores: list[str]) -> str | None:
        """Substitute the markers back. None if the model lost one, so the caller
        can retranslate that sentence unprotected rather than emit garbage."""
        if not restores:
            return text
        seen = {int(m.group(1)) for m in _MARK_RE.finditer(text)}
        if seen != set(range(len(restores))):
            return None
        return _MARK_RE.sub(lambda m: restores[int(m.group(1))], text)

    def strip_markers(self, text: str, restores: list[str]) -> str:
        """Last-resort cleanup so a marker never reaches the projector."""
        return _MARK_RE.sub(
            lambda m: restores[int(m.group(1))] if int(m.group(1)) < len(restores) else "",
            text,
        )

    def prompt_hint(self, lang: str) -> str:
        bits = []
        if self.protect:
            bits.append("Leave these terms exactly as written, untranslated: "
                        + ", ".join(self.protect) + ".")
        pairs = [f'"{k}" -> "{v[lang]}"' for k, v in self.replace.items()
                 if v.get(lang)]
        if pairs:
            bits.append("Use exactly these renderings: " + "; ".join(pairs) + ".")
        return " ".join(bits)
