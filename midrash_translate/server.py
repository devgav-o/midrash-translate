"""FastAPI app: websocket fan-out to the projector pages, plus status and export.

Threads (watcher, translation workers) hand updates to the asyncio loop through
Hub.publish, which is the only thread-safe crossing point in the process.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import export as export_mod
from .engine import Engine
from .speakers import SpeakerParser
from .state import DocState
from .watcher import SourceWatcher, build_reader

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class Hub:
    """Per-language websocket fan-out."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.conns: dict[str, set[WebSocket]] = {}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def add(self, lang: str, ws: WebSocket) -> None:
        self.conns.setdefault(lang, set()).add(ws)

    def remove(self, lang: str, ws: WebSocket) -> None:
        self.conns.get(lang, set()).discard(ws)

    def count(self, lang: str) -> int:
        return len(self.conns.get(lang, ()))

    def publish(self, lang: str, message: dict) -> None:
        """Callable from any thread."""
        if self.loop is None or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self._fanout, lang, message)
        except RuntimeError:
            pass  # loop shutting down

    def _fanout(self, lang: str, message: dict) -> None:
        targets = list(self.conns.get(lang, ()))
        log.debug("send %s -> %d viewer(s): %d line(s)", lang, len(targets),
                  len(message.get("lines", [])))
        if not targets:
            return
        payload = json.dumps(message, ensure_ascii=False)
        for ws in targets:
            asyncio.create_task(self._send(lang, ws, payload))

    async def _send(self, lang: str, ws: WebSocket, payload: str) -> None:
        try:
            await ws.send_text(payload)
        except Exception:
            self.remove(lang, ws)


class Pipeline:
    """Wires watcher -> state -> engine -> hub."""

    def __init__(self, cfg, reader_override: str | None = None):
        self.cfg = cfg
        self.hub = Hub()
        self.speakers = SpeakerParser(cfg.speaker_labels, cfg.speaker_names)
        self.state = DocState(cfg.lang_codes, cfg.debounce_s, self.speakers)
        self.engine = Engine(cfg, self.state, self._on_translation)
        self.watcher = SourceWatcher(
            build_reader(cfg, reader_override), self._on_paragraphs, cfg.poll_s
        )
        self._last_order: list[int] = []

    # ---- callbacks from worker threads

    def _on_paragraphs(self, paragraphs: list[str]) -> None:
        deletions = self.state.update_source(paragraphs)
        if deletions:
            self.engine.dispatch(deletions)
        ready = self.state.collect_ready()
        if ready:
            self.engine.dispatch(ready)

        order = self.state.order()
        order_changed = order != self._last_order
        if not (order_changed or deletions or ready):
            return
        self._last_order = order

        touched = [c.para_id for c in ready]
        for lang in self.cfg.lang_codes:
            lines = [ln for ln in (self.state.line(pid, lang) for pid in touched) if ln]
            self.hub.publish(lang, {"type": "patch", "order": order, "lines": lines})

    def _on_translation(self, lang: str, para_id: int) -> None:
        line = self.state.line(para_id, lang)
        if line:
            self.hub.publish(lang, {"type": "patch", "lines": [line]})

    # ---- lifecycle

    def start(self) -> None:
        self.engine.start()
        self.watcher.start()

    def stop(self) -> None:
        self.watcher.stop()
        self.engine.stop()

    def status(self) -> dict:
        return {
            "source": self.watcher.status(),
            "languages": self.engine.status(),
            "state": self.state.stats(),
            "viewers": {lang: self.hub.count(lang) for lang in self.cfg.lang_codes},
        }


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _print_section(name: str, stamp: str, lines, rtl: bool,
                   include_source: bool) -> str:
    """One language as printable HTML. Escaped by hand rather than templated --
    a lecture transcript is arbitrary typed text and lands here verbatim."""
    esc = html.escape
    parts = [f'<section dir="{"rtl" if rtl else "ltr"}">',
             f"<h1>{esc(name)}</h1>",
             f'<p class="stamp">{esc(stamp)}</p>']
    written = 0
    for translated, source, speaker in lines:
        text = translated.strip() or source.strip()
        if not text:
            continue
        written += 1
        prefix = f"<b>{esc(speaker)}:</b> " if speaker else ""
        parts.append(f'<p class="line">{prefix}{esc(text)}</p>')
        if include_source and source.strip() and translated.strip():
            parts.append(f'<p class="src">{esc(source.strip())}</p>')
    if not written:
        parts.append('<p class="empty">Nothing has been transcribed yet.</p>')
    parts.append("</section>")
    return "".join(parts)


def _render(template, /, **subs: str) -> str:
    """Positional-only so that a substitution key can be called `name`."""
    html = (STATIC / template).read_text(encoding="utf-8")
    for key, value in subs.items():
        html = html.replace("{{" + key + "}}", value)
    return html


def create_app(cfg, reader_override: str | None = None,
               include_source: bool = False) -> FastAPI:
    pipeline = Pipeline(cfg, reader_override)
    targets = {t.code: t for t in cfg.targets}
    # the English source is downloadable too, though it is nobody's translation
    downloadable = [t.code for t in cfg.targets] + ["en"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pipeline.hub.bind(asyncio.get_running_loop())
        pipeline.start()
        log.info("watching %s -> %s", pipeline.watcher.reader.describe(),
                 ", ".join(cfg.lang_codes))
        try:
            yield
        finally:
            pipeline.stop()

    app = FastAPI(title="midrash-translate", lifespan=lifespan)
    app.state.pipeline = pipeline

    @app.get("/", response_class=HTMLResponse)
    async def index():
        rows = "".join(
            f'<li><a href="/display/{t.code}">{t.name}</a> '
            f'<span class="code">/display/{t.code}</span></li>'
            for t in cfg.targets
        )
        names = {t.code: t.name for t in cfg.targets} | {"en": "English (source)"}
        downloads = "".join(
            f'<li><span class="lang">{names[c]}</span>'
            f'<a class="dl" href="/download/{c}.docx">.docx</a>'
            f'<a class="dl" href="/print/{c}?auto=1" target="_blank" rel="noopener">PDF</a>'
            f"</li>"
            for c in downloadable
        )
        return _render("index.html", rows=rows, downloads=downloads)

    @app.get("/display/{lang}", response_class=HTMLResponse)
    async def display(lang: str):
        target = targets.get(lang)
        if not target:
            raise HTTPException(404, f"no target language {lang!r}")
        disp = cfg.display
        # the projector page must never come from cache: a stale copy silently
        # keeps whatever bugs the last version had
        headers = {"Cache-Control": "no-store, max-age=0"}
        return HTMLResponse(_render(
            "display.html",
            lang=target.code,
            name=target.name,
            dir="rtl" if target.rtl else "ltr",
            font_size=str(disp.get("font_size_px", 44)),
            max_lines=str(disp.get("max_lines", 400)),
            show_source="true" if disp.get("show_source") else "false",
        ), headers=headers)

    @app.websocket("/ws/{lang}")
    async def ws_endpoint(ws: WebSocket, lang: str):
        if lang not in targets:
            await ws.close(code=4004)
            return
        await ws.accept()
        pipeline.hub.add(lang, ws)
        log.info("viewer connected to %s (%d now)", lang, pipeline.hub.count(lang))
        try:
            await ws.send_text(json.dumps(
                {"type": "snapshot", "lines": pipeline.state.snapshot(lang)},
                ensure_ascii=False))
            while True:
                # the display never sends anything; this just detects disconnects
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            pipeline.hub.remove(lang, ws)
            log.info("viewer disconnected from %s (%d left)", lang,
                     pipeline.hub.count(lang))

    @app.get("/api/status")
    async def status():
        return JSONResponse(pipeline.status())

    @app.get("/api/lines/{lang}")
    async def lines(lang: str):
        if lang not in targets:
            raise HTTPException(404)
        return JSONResponse(pipeline.state.snapshot(lang))

    @app.post("/api/export")
    async def do_export(label: str | None = None):
        written = export_mod.export_session(
            pipeline.state, cfg.targets, cfg.export_dir, label, include_source
        )
        log.info("exported %d files", len(written))
        return JSONResponse({"files": written})

    # ---- downloads. export_session writes to the classroom machine's disk;
    # these hand the same documents to whichever browser asked, which is what
    # you want when the control panel is open on a phone or another laptop.

    def _check(code: str) -> None:
        if code not in downloadable:
            raise HTTPException(404, f"no language {code!r}")

    @app.get("/download/{code}.docx")
    async def download_docx(code: str):
        _check(code)
        stamp = export_mod.stamp_now()
        data = export_mod.docx_bytes(
            export_mod.title_for(cfg.targets, code, stamp),
            export_mod.lines_for(pipeline.state, cfg.targets, code),
            rtl=export_mod.is_rtl(cfg.targets, code),
            include_source=include_source and code != "en",
        )
        log.info("download %s.docx (%d bytes)", code, len(data))
        return Response(data, media_type=DOCX_MIME, headers={
            "Content-Disposition": f'attachment; filename="{stamp}_{code}.docx"'})

    @app.get("/download/all.zip")
    async def download_zip():
        stamp = export_mod.stamp_now()
        data = export_mod.session_zip(pipeline.state, cfg.targets, stamp, include_source)
        log.info("download all.zip (%d bytes)", len(data))
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{stamp}.zip"'})

    @app.get("/print/{code}", response_class=HTMLResponse)
    async def print_page(code: str):
        """Printable transcript. The browser's own Save as PDF turns it into the
        PDF -- see the note at the top of export.py for why that beats making
        one server-side."""
        stamp = export_mod.stamp_now().replace("_", " ")
        if code == "all":
            codes = downloadable
        else:
            _check(code)
            codes = [code]
        body = "".join(
            _print_section(
                next((t.name for t in cfg.targets if t.code == c), "English"),
                stamp,
                export_mod.lines_for(pipeline.state, cfg.targets, c),
                export_mod.is_rtl(cfg.targets, c),
                include_source and c != "en",
            )
            for c in codes
        )
        title = "Transcript" if code == "all" else f"{code.upper()} transcript"
        return HTMLResponse(_render("print.html", title=f"{title} - {stamp}", body=body),
                            headers={"Cache-Control": "no-store, max-age=0"})

    return app
