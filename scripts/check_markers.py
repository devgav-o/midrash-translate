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

# Several markers in one sentence, because that is where markers actually fail:
# "%0%" survives a single-marker probe on all three models but drops 0/4 Hebrew
# sentences carrying two. A real lecture line quotes more than one term.
PROBES = [
    ("Now, if we look at what {0} say in the next passage, we find {1}.", 2),
    ("{0} explains that {1} carries a double meaning.", 2),
    ("The {0} asks a simple question about {1} and {2}.", 3),
    ("According to {0}, the answer is found in {1} much later.", 2),
    ("We will learn {0} today.", 1),
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
        marks = [_MARK.format(i) for i in range(3)]
        probes = [tmpl.format(*marks) for tmpl, _ in PROBES]
        with torch.inference_mode():
            batch = tok(probes, return_tensors="pt", padding=True)
            out = tok.batch_decode(mdl.generate(**batch, num_beams=1, max_length=256),
                                   skip_special_tokens=True)
        survived = sum(
            1 for o, (_, n) in zip(out, PROBES)
            if all(_MARK.format(i) in o for i in range(n))
        )
        flag = "ok" if survived == len(PROBES) else "PROBLEM"
        print(f"  {target.code}: {survived}/{len(PROBES)} sentences kept every "
              f"marker  [{flag}]")
        if survived < len(PROBES):
            bad.append(target.code)
            for o, (_, n) in zip(out, PROBES):
                if not all(_MARK.format(i) in o for i in range(n)):
                    print(f"      dropped in: {o}")

    if bad:
        print(f"\nMarker is unreliable for: {', '.join(bad)}")
        print("Pick another in midrash_translate/translators/glossary.py (_MARK).")
        print("Measured good on Opus-MT multi-marker sentences: 'X0X', 'TERM0'.")
        print("Bad: '%0%' (0/4 Hebrew), '[0]', '#0#', '<0>', '\u27e60\u27e7' (0/12).")
        return 1
    print("\nMarker survives on every configured model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
