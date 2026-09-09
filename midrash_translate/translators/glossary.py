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
# The marker must survive the round trip through every model, with SEVERAL in
# one sentence -- a single-marker probe is not enough, and neither is testing
# only French. Measured over the three Opus-MT models, 5 multi-marker sentences
# each (scripts/check_markers.py):
#
#   Z0     15/15    (0)  15/15    bare digits 15/15
#   TERM0  14/15    Q0   14/15    @0  13/15
#   X0X    14/15  -- en->he transliterates it to "אקס-או-אקס"
#   900000 10/15    %0%   6/15 (0/5 Hebrew)    ##0  9/15
#
# Bare digits collide with real numbers in a lecture and "(0)" with numbered
# lists, so "Z0" wins on collision-safety among the perfect scorers.
_MARK = "Z{}"
_MARK_RE = re.compile(r"Z(\d+)")

# Script ranges that must survive translation verbatim. The transcriber types
# English and drops in quoted terms in their original script -- "the רשי says
# that the גמרא asks" -- and those are quotations, not words to translate. Fed
# to an en->fr model they become <unk> and come back as rows of dashes; fed to
# en->he they are silently dropped.
SCRIPTS = {
    "hebrew": "\u0590-\u05FF\uFB1D-\uFB4F",
    "arabic": "\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF",
    "greek": "\u0370-\u03FF\u1F00-\u1FFF",
    "cyrillic": "\u0400-\u04FF",
}

# Characters allowed *between* two same-script characters without breaking the
# run, so ר?ש"י stays one unit and multi-word phrases are masked as a whole.
# Deliberately excludes "." so a sentence-ending period is not swallowed.
_RUN_INNER = "\\s\"'\u05be\u05f3\u05f4\u2010-\u2015-"

# Bidi isolation, so a Hebrew run inside a French sentence does not drag the
# surrounding punctuation to the wrong side of the line.
_FSI, _PDI = "\u2068", "\u2069"
_RTL_RE = re.compile("[\u0590-\u05FF\u0600-\u06FF\uFB1D-\uFEFF]")


class Glossary:
    def __init__(self, protect: list[str] | None = None,
                 replace: dict[str, dict[str, str]] | None = None,
                 preserve_scripts: list[str] | None = None,
                 rtl_langs: set[str] | None = None,
                 isolate_bidi: bool = True):
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

        self.preserve_scripts = list(preserve_scripts or ["hebrew"])
        self.rtl_langs = set(rtl_langs or ())
        self.isolate_bidi = isolate_bidi
        ranges = "".join(SCRIPTS[name] for name in self.preserve_scripts
                         if name in SCRIPTS)
        self._script_re = (
            re.compile(f"[{ranges}](?:[{_RUN_INNER}]*[{ranges}])*")
            if ranges else None
        )

    @classmethod
    def load(cls, path: Path | None, rtl_langs: set[str] | None = None,
             preserve_scripts: list[str] | None = None,
             isolate_bidi: bool = True) -> "Glossary":
        data = {}
        if path:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            protect=data.get("protect"),
            replace=data.get("replace"),
            preserve_scripts=preserve_scripts or data.get("preserve_scripts"),
            rtl_langs=rtl_langs,
            isolate_bidi=isolate_bidi,
        )

    def _restore_value(self, matched: str, lang: str) -> str:
        """What this occurrence should read as in the output."""
        forced = self._replace_ci.get(matched.lower(), {}).get(lang)
        return forced if forced else matched

    def _isolated(self, run: str, lang: str) -> str:
        """Wrap an RTL run embedded in an LTR target so the bidi algorithm keeps
        the surrounding punctuation on the correct side."""
        if (self.isolate_bidi and lang not in self.rtl_langs
                and _RTL_RE.search(run)):
            return _FSI + run + _PDI
        return run

    def mask(self, text: str, lang: str) -> tuple[str, list[str]]:
        """Returns the text with preserved runs and glossary terms replaced by
        markers, plus the strings to substitute back in (resolved for `lang`)."""
        if not self._term_re and not self._script_re:
            return text, []
        # a stray "Z1" typed in the lecture would collide with our numbering
        text = _MARK_RE.sub(lambda m: m.group(0).replace("Z", "Z "), text)
        restores: list[str] = []

        def take(value: str) -> str:
            restores.append(value)
            return _MARK.format(len(restores) - 1)

        # Preserved scripts first: a Hebrew run is passed through verbatim to
        # every target language, Hebrew included -- it is already in the script
        # the audience reads, and translating a quotation is wrong anyway.
        if self._script_re:
            text = self._script_re.sub(
                lambda m: take(self._isolated(m.group(0), lang)), text)

        if self._term_re:
            text = self._term_re.sub(
                lambda m: take(self._restore_value(m.group(0), lang)), text)

        return text, restores

    def has_translatable_text(self, masked: str) -> bool:
        """False when masking consumed everything worth sending to a model --
        a line that is entirely a Hebrew quotation, say. Sending only markers
        makes Marian hallucinate, so the caller should skip the model."""
        return bool(re.search(r"[A-Za-z]", _MARK_RE.sub(" ", masked)))

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
