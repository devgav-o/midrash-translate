"""Write the session out as Word documents -- one per language, plus the English
source. Run at the end of class (or any time) for the archive."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt


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


def write_docx(path: Path, title: str, lines: list[tuple[str, str]],
               rtl: bool = False, include_source: bool = False,
               font_size_pt: int = 14) -> Path:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.size = Pt(font_size_pt)

    heading = doc.add_heading(title, level=1)
    if rtl:
        _set_rtl(heading)

    for translated, source in lines:
        text = translated.strip()
        if not text and not source.strip():
            continue
        para = doc.add_paragraph(text or source)
        if rtl:
            _set_rtl(para)
        if include_source and source.strip():
            note = doc.add_paragraph(source)
            note.runs[0].italic = True
            note.runs[0].font.size = Pt(max(8, font_size_pt - 4))

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def export_session(state, targets, out_dir: Path, label: str | None = None,
                   include_source: bool = False) -> list[str]:
    stamp = label or _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    session_dir = Path(out_dir) / stamp
    written: list[str] = []

    for target in targets:
        lines = state.all_lines(target.code)
        path = session_dir / f"{stamp}_{target.code}.docx"
        write_docx(path, f"{target.name} - {stamp}", lines,
                   rtl=target.rtl, include_source=include_source)
        written.append(str(path))

    # the English source, so the archive is self-contained
    src_lines = [(src, "") for _, src in state.all_lines(targets[0].code)] if targets else []
    src_path = session_dir / f"{stamp}_en.docx"
    write_docx(src_path, f"English - {stamp}", src_lines)
    written.append(str(src_path))
    return written
