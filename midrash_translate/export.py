"""Write the session out as Word documents -- one per language, plus the English
source. Run at the end of class (or any time) for the archive.

Two ways out, both driven from the control panel:

* .docx, built here, either saved into `sessions/` on the classroom machine or
  streamed straight to whichever browser asked for it.
* PDF, which is *not* built here. It is the browser printing `/print/<lang>`.
  Generating Hebrew PDFs server-side means bundling an RTL-capable font and
  reimplementing bidi reordering; the browser already does both, correctly and
  offline, and the print stylesheet is a fifth of the code a PDF library would be.
"""
from __future__ import annotations

import datetime as _dt
import io
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

Line = tuple[str, str, "str | None"]


def _set_rtl(paragraph) -> None:
    """python-docx has no RTL setter, so set w:bidi on the paragraph properties
    and w:rtl on each run directly."""
    p_pr = paragraph._p.get_or_add_pPr()
    bidi = p_pr.makeelement(qn("w:bidi"), {})
    p_pr.append(bidi)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for run in paragraph.runs:
        r_pr = run._r.get_or_add_rPr()
        r_pr.append(r_pr.makeelement(qn("w:rtl"), {}))


def build_docx(title: str, lines: list[Line], rtl: bool = False,
               include_source: bool = False, font_size_pt: int = 14) -> Document:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.size = Pt(font_size_pt)

    heading = doc.add_heading(title, level=1)
    if rtl:
        _set_rtl(heading)

    for translated, source, speaker in lines:
        text = translated.strip()
        if not text and not source.strip():
            continue
        para = doc.add_paragraph()
        if speaker:
            run = para.add_run(f"{speaker}: ")
            run.bold = True
        para.add_run(text or source)
        if rtl:
            _set_rtl(para)
        if include_source and source.strip():
            note = doc.add_paragraph(source)
            note.runs[0].italic = True
            note.runs[0].font.size = Pt(max(8, font_size_pt - 4))
    return doc


def write_docx(path: Path, title: str, lines: list[Line], rtl: bool = False,
               include_source: bool = False, font_size_pt: int = 14) -> Path:
    doc = build_docx(title, lines, rtl, include_source, font_size_pt)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def docx_bytes(title: str, lines: list[Line], rtl: bool = False,
               include_source: bool = False, font_size_pt: int = 14) -> bytes:
    """The same document, in memory, for streaming to a browser."""
    doc = build_docx(title, lines, rtl, include_source, font_size_pt)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def stamp_now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d_%H%M")


def lines_for(state, targets, code: str) -> list[Line]:
    """Export rows for one language. `en` is the English source itself, which is
    no target's translation -- it is read off any target's rows, which all carry
    the same source text."""
    if code == "en":
        if not targets:
            return []
        return [(src, "", spk) for _, src, spk in state.all_lines(targets[0].code)]
    return state.all_lines(code)


def title_for(targets, code: str, stamp: str) -> str:
    name = next((t.name for t in targets if t.code == code), None) or "English"
    return f"{name} - {stamp}"


def is_rtl(targets, code: str) -> bool:
    return any(t.code == code and t.rtl for t in targets)


def export_session(state, targets, out_dir: Path, label: str | None = None,
                   include_source: bool = False) -> list[str]:
    stamp = label or stamp_now()
    session_dir = Path(out_dir) / stamp
    written: list[str] = []

    # the English source last, so the archive is self-contained
    for code in [t.code for t in targets] + ["en"]:
        path = session_dir / f"{stamp}_{code}.docx"
        write_docx(path, title_for(targets, code, stamp), lines_for(state, targets, code),
                   rtl=is_rtl(targets, code),
                   include_source=include_source and code != "en")
        written.append(str(path))
    return written


def session_zip(state, targets, stamp: str, include_source: bool = False) -> bytes:
    """Every language in one download, named the way export_session names them
    on disk so the two archives are interchangeable."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for code in [t.code for t in targets] + ["en"]:
            zf.writestr(
                f"{stamp}/{stamp}_{code}.docx",
                docx_bytes(title_for(targets, code, stamp),
                           lines_for(state, targets, code),
                           rtl=is_rtl(targets, code),
                           include_source=include_source and code != "en"),
            )
    return buf.getvalue()
