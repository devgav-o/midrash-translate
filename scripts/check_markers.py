#!/usr/bin/env python3
"""Verify the glossary marker survives the round trip through the translation
models. Run this if you change models or edit the marker in glossary.py.

A marker that the model drops means every protected sentence silently falls back
to unprotected translation -- the glossary looks configured but does nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from midrash_translate import config as config_mod  # noqa: E402
from midrash_translate.translators.glossary import _MARK  # noqa: E402
from midrash_translate.translators.opusmt import DEFAULT_MODELS  # noqa: E402

PROBES = [
    "Now, if we look at what {} say in the next passage, we find something surprising.",
    "{} explains that the word here carries a double meaning.",
    "The {} asks a very simple question about this verse.",
    "According to {}, the answer is found much later.",
]


def main() -> int:
    cfg = config_mod.load(Path(__file__).resolve().parents[1] / "config.yaml")
    opts = cfg.backend_opts("opusmt")
    models = opts.get("models", {})
    cache_dir = opts.get("cache_dir", "models")
    marker = _MARK.format(0)

    import torch
    from transformers import MarianMTModel, MarianTokenizer

    print(f"marker: {marker!r}\n")
    bad = []
    for target in cfg.targets:
        model_id = models.get(target.code) or DEFAULT_MODELS.get(target.code)
        if not model_id:
            continue
        tok = MarianTokenizer.from_pretrained(model_id, cache_dir=cache_dir)
        mdl = MarianMTModel.from_pretrained(model_id, cache_dir=cache_dir)
        mdl.eval()
        probes = [p.format(marker) for p in PROBES]
        with torch.inference_mode():
            batch = tok(probes, return_tensors="pt", padding=True)
            out = tok.batch_decode(mdl.generate(**batch, num_beams=1, max_length=256),
                                   skip_special_tokens=True)
        survived = sum(1 for o in out if marker in o)
        flag = "ok" if survived == len(PROBES) else "PROBLEM"
        print(f"  {target.code}: {survived}/{len(PROBES)} survived  [{flag}]")
        if survived < len(PROBES):
            bad.append(target.code)
            for o in out:
                if marker not in o:
                    print(f"      dropped in: {o}")

    if bad:
        print(f"\nMarker is unreliable for: {', '.join(bad)}")
        print("Pick another in midrash_translate/translators/glossary.py (_MARK).")
        print("Measured good on Opus-MT: '%0%', '[0]', 'TERM0'.  Bad: '⟦0⟧'.")
        return 1
    print("\nMarker survives on every configured model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
