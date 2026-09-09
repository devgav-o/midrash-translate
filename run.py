#!/usr/bin/env python3
"""Entry point.

    python run.py                      # watch the active Word document
    python run.py --file draft.txt     # watch a text file instead (no Word needed)
    python run.py --backend echo       # no models, just prove the plumbing works
"""
from __future__ import annotations

import argparse
import logging
import socket

import uvicorn

from midrash_translate import config as config_mod
from midrash_translate.server import create_app


def local_ip() -> str:
    """Best-effort LAN address, so the projector machines know where to point."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Live lecture translation")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--file", help="watch this text file instead of Word")
    ap.add_argument("--backend", help="override translation.backend (opusmt/gemini/echo)")
    ap.add_argument("--document", help="override which Word document to watch")
    ap.add_argument("--port", type=int)
    ap.add_argument("--show-source", action="store_true",
                    help="include the English source in exports")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = config_mod.load(args.config)
    if args.backend:
        cfg.raw["translation"]["backend"] = args.backend
        cfg.raw["translation"].pop("backend_by_lang", None)
    if args.document:
        cfg.raw["source"]["document"] = args.document
    if args.port:
        cfg.raw["server"]["port"] = args.port

    app = create_app(cfg, reader_override=args.file, include_source=args.show_source)

    ip = local_ip()
    print()
    print(f"  control panel   http://{ip}:{cfg.port}/")
    for t in cfg.targets:
        print(f"  {t.name:<14}  http://{ip}:{cfg.port}/display/{t.code}")
    print()

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
