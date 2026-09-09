"""Source capture.

The Word backend attaches to the already-running Word instance over COM and
reads the document text. It never writes, so it cannot corrupt or lock the
transcriber's document, and it needs no macro installed -- start it before or
during class, restart it any time.

Reading Document.Content.Text is a single COM marshal for the whole document,
which is why we can afford to poll it several times a second. Iterating the
Paragraphs collection instead would be one cross-process call per paragraph and
would fall over on a long lecture.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Word control characters: paragraph mark, line break, cell/row mark, page break
_PARA_SPLIT = ("\r", "\x0b", "\n")
_STRIP = str.maketrans("", "", "\x07\x0c\x1e\x02")


def split_word_text(raw: str) -> list[str]:
    text = raw.translate(_STRIP)
    for ch in _PARA_SPLIT[1:]:
        text = text.replace(ch, "\r")
    parts = text.split("\r")
    # Word always terminates the document with a final empty paragraph mark
    if parts and not parts[-1].strip():
        parts.pop()
    return [p.strip() for p in parts]


class SourceWatcher(threading.Thread):
    """Polls a source and pushes paragraph lists into a callback."""

    def __init__(self, reader, on_paragraphs, poll_s: float = 0.4):
        super().__init__(name="watcher", daemon=True)
        self.reader = reader
        self.on_paragraphs = on_paragraphs
        self.poll_s = poll_s
        self._stop = threading.Event()
        self.connected = False
        self.last_error: str | None = None
        self.polls = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.reader.setup()
        backoff = self.poll_s
        while not self._stop.is_set():
            try:
                raw = self.reader.read()
                if raw is None:
                    self.connected = False
                    self._stop.wait(min(backoff, 3.0))
                    backoff = min(backoff * 1.5, 3.0)
                    continue
                if not self.connected:
                    log.info("connected to source: %s", self.reader.describe())
                self.connected = True
                self.last_error = None
                backoff = self.poll_s
                self.polls += 1
                self.on_paragraphs(split_word_text(raw))
            except Exception as exc:
                self.connected = False
                self.last_error = str(exc)
                log.warning("source read failed: %s", exc)
                self.reader.reset()
                self._stop.wait(1.0)
                continue
            self._stop.wait(self.poll_s)
        self.reader.teardown()

    def status(self) -> dict:
        return {
            "source": self.reader.describe(),
            "connected": self.connected,
            "polls": self.polls,
            "error": self.last_error,
        }


class WordReader:
    """Reads a live Microsoft Word document on Windows via COM."""

    def __init__(self, document: str = "active"):
        self.document = document
        self._app = None
        self._com_ready = False

    def setup(self) -> None:
        import pythoncom  # noqa: F401  (import proves we are on Windows w/ pywin32)
        pythoncom.CoInitialize()
        self._com_ready = True

    def teardown(self) -> None:
        if self._com_ready:
            import pythoncom
            pythoncom.CoUninitialize()
            self._com_ready = False

    def reset(self) -> None:
        self._app = None

    def _connect(self):
        if self._app is not None:
            return self._app
        import win32com.client
        try:
            self._app = win32com.client.GetActiveObject("Word.Application")
        except Exception:
            return None
        return self._app

    def _document(self, app):
        if self.document == "active":
            return app.ActiveDocument
        needle = self.document.lower()
        for doc in app.Documents:
            if needle in doc.Name.lower():
                return doc
        raise LookupError(f"no open Word document matching {self.document!r}")

    def read(self) -> str | None:
        app = self._connect()
        if app is None:
            return None  # Word not running yet
        try:
            if app.Documents.Count == 0:
                return None
            return self._document(app).Content.Text or ""
        except Exception:
            # Word was closed or is mid-dialog; drop the handle and retry
            self._app = None
            raise

    def describe(self) -> str:
        return f"Word document ({self.document})"


class FileReader:
    """Reads a plain text file. Used for testing the pipeline without Word, and
    as a fallback if the transcriber would rather type into a text editor."""

    def __init__(self, path: str):
        self.path = Path(path)
        self._mtime = None
        self._cached = ""

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def reset(self) -> None:
        self._mtime = None

    def read(self) -> str | None:
        if not self.path.exists():
            return None
        mtime = self.path.stat().st_mtime_ns
        if mtime != self._mtime:
            self._mtime = mtime
            self._cached = self.path.read_text(encoding="utf-8", errors="replace")
        return self._cached.replace("\n", "\r")

    def describe(self) -> str:
        return f"text file ({self.path})"


def build_reader(cfg, override: str | None = None):
    """override: None -> Word; a filesystem path -> FileReader."""
    if override:
        return FileReader(override)
    return WordReader(cfg.document)
