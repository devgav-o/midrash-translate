"""Configuration loading. Plain dataclasses over config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Target:
    code: str
    name: str
    rtl: bool = False


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path

    @property
    def document(self) -> str:
        return self.raw["source"]["document"]

    @property
    def poll_s(self) -> float:
        return self.raw["source"]["poll_ms"] / 1000.0

    @property
    def debounce_s(self) -> float:
        return self.raw["source"]["debounce_ms"] / 1000.0

    @property
    def speaker_labels(self) -> list[str]:
        return self.raw.get("speakers", {}).get("labels", []) or []

    @property
    def speaker_names(self) -> dict:
        return self.raw.get("speakers", {}).get("names", {}) or {}

    @property
    def targets(self) -> list[Target]:
        return [Target(**t) for t in self.raw["targets"]]

    @property
    def lang_codes(self) -> list[str]:
        return [t.code for t in self.targets]

    def backend_for(self, lang: str) -> str:
        tr = self.raw["translation"]
        return tr.get("backend_by_lang", {}).get(lang, tr["backend"])

    def backend_opts(self, backend: str) -> dict[str, Any]:
        return self.raw["translation"].get(backend, {}) or {}

    @property
    def preserve_scripts(self) -> list[str]:
        return self.raw["translation"].get("preserve_scripts", ["hebrew"])

    @property
    def isolate_bidi(self) -> bool:
        return self.raw["translation"].get("isolate_bidi", True)

    @property
    def cache_size(self) -> int:
        return self.raw["translation"].get("cache_size", 20000)

    @property
    def glossary_path(self) -> Path | None:
        name = self.raw["translation"].get("glossary")
        if not name:
            return None
        p = self.root / name
        return p if p.exists() else None

    @property
    def host(self) -> str:
        return self.raw["server"]["host"]

    @property
    def port(self) -> int:
        return int(os.environ.get("PORT", self.raw["server"]["port"]))

    @property
    def display(self) -> dict[str, Any]:
        return self.raw.get("display", {})

    @property
    def export_dir(self) -> Path:
        return self.root / self.raw.get("export", {}).get("dir", "sessions")


def load(path: str | Path = "config.yaml") -> Config:
    p = Path(path).resolve()
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Config(raw=raw, root=p.parent)
