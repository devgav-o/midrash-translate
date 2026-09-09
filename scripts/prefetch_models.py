#!/usr/bin/env python3
"""Download the Opus-MT models ahead of class. Run once, on the classroom machine,
while you still have a network -- after this the translation runs fully offline.

    python scripts/prefetch_models.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from midrash_translate import config as config_mod  # noqa: E402
from midrash_translate.translators.opusmt import DEFAULT_MODELS  # noqa: E402


def main() -> int:
    cfg = config_mod.load(Path(__file__).resolve().parents[1] / "config.yaml")
    opts = cfg.backend_opts("opusmt")
    models = opts.get("models", {})
    cache_dir = opts.get("cache_dir", "models")

    from transformers import MarianMTModel, MarianTokenizer

    failed = []
    for target in cfg.targets:
        model_id = models.get(target.code) or DEFAULT_MODELS.get(target.code)
        if not model_id:
            print(f"[skip] {target.code}: no model configured")
            continue
        print(f"[..]   {target.code}: {model_id}")
        try:
            MarianTokenizer.from_pretrained(model_id, cache_dir=cache_dir)
            MarianMTModel.from_pretrained(model_id, cache_dir=cache_dir)
            print(f"[ok]   {target.code}")
        except Exception as exc:
            print(f"[FAIL] {target.code}: {exc}")
            failed.append((target.code, model_id, exc))

    if failed:
        print("\nSome models could not be downloaded. Check the model id on")
        print("https://huggingface.co/Helsinki-NLP and update config.yaml, or point")
        print("that language at the gemini backend instead.")
        return 1
    print("\nAll models cached. Translation will now work offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
