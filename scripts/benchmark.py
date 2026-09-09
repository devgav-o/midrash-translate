#!/usr/bin/env python3
"""Measure translation latency on this machine, so you know before class whether
one laptop keeps up with three languages.

    python scripts/benchmark.py
    python scripts/benchmark.py --backend gemini

The number that matters is p95 per sentence. A lecturer speaks roughly one
sentence every 4 seconds, so anything under ~1500ms leaves plenty of headroom.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from midrash_translate import config as config_mod  # noqa: E402
from midrash_translate.translators import CachingTranslator, Glossary, build  # noqa: E402

SAMPLE = [
    "Good morning everyone, please open your books to the second chapter.",
    "The Midrash asks a very simple question about this verse.",
    "Rashi explains that the word here carries a double meaning.",
    "Now, if we look at what the sages say in the next passage, we find something surprising.",
    "This is the whole difficulty, and it is not resolved until much later in the discussion.",
    "Let me repeat that, because it is the central point of today's class.",
    "The answer depends entirely on how you read the first three words.",
    "Are there any questions before we move on to the next section?",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--backend")
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    cfg = config_mod.load(Path(__file__).resolve().parents[1] / args.config)
    glossary = Glossary.load(cfg.glossary_path)

    for target in cfg.targets:
        backend = args.backend or cfg.backend_for(target.code)
        print(f"\n=== {target.name} ({target.code}) via {backend} ===")
        try:
            inner = build(target.code, backend, cfg.backend_opts(backend), glossary)
            tr = CachingTranslator(inner, cfg.cache_size)
            t0 = time.monotonic()
            tr.warm()
            print(f"load: {time.monotonic() - t0:.1f}s")
        except Exception as exc:
            print(f"unavailable: {exc}")
            continue

        timings = []
        for r in range(args.rounds):
            for sent in SAMPLE:
                probe = sent if r == 0 else sent + f" (variation {r})"
                t0 = time.monotonic()
                out = tr.translate(probe)
                timings.append((time.monotonic() - t0) * 1000)
                if r == 0:
                    print(f"  {out}")

        timings.sort()
        p95 = timings[int(len(timings) * 0.95) - 1]
        print(f"  median {statistics.median(timings):6.0f} ms   "
              f"p95 {p95:6.0f} ms   max {timings[-1]:6.0f} ms")
        if p95 > 1500:
            print("  ^ slower than a comfortable margin; try beams: 1, quantize: true,")
            print("    or point this language at the gemini backend.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
