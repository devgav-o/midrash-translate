"""Tests for the paragraph diff, the debounce rules and glossary masking.

Run with `pytest`, or directly: `python tests/test_state.py`
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from midrash_translate import export as export_mod  # noqa: E402
from midrash_translate.speakers import SpeakerParser  # noqa: E402
from midrash_translate.state import DocState  # noqa: E402
from midrash_translate.translators.glossary import Glossary  # noqa: E402
from midrash_translate.watcher import split_word_text  # noqa: E402

LANGS = ["fr", "he"]


def flush(state: DocState):
    """Force everything past the debounce window."""
    time.sleep(0.02)
    return state.collect_ready()


def new_state(debounce=0.0, speakers=None) -> DocState:
    return DocState(LANGS, debounce_s=debounce, speakers=speakers)


def test_append_keeps_existing_ids():
    s = new_state()
    s.update_source(["one.", "two."])
    ids = s.order()
    s.update_source(["one.", "two.", "three."])
    assert s.order()[:2] == ids, "appending must not renumber earlier paragraphs"
    assert len(s.order()) == 3


def test_insert_in_middle_keeps_trailing_ids():
    s = new_state()
    s.update_source(["alpha.", "beta.", "gamma."])
    first, _, last = s.order()
    s.update_source(["alpha.", "inserted.", "beta.", "gamma."])
    order = s.order()
    assert order[0] == first
    assert order[-1] == last, "inserting must not invalidate paragraphs after it"


def test_edit_in_place_keeps_id_and_bumps_version():
    s = new_state()
    s.update_source(["the sages say."])
    pid = s.order()[0]
    flush(s)
    s.update_source(["the sages said."])
    assert s.order() == [pid]
    ready = flush(s)
    assert [c.para_id for c in ready] == [pid]
    assert ready[0].version == 2


def test_delete_reports_deletion():
    s = new_state()
    s.update_source(["a.", "b.", "c."])
    _, second, _ = s.order()
    deletions = s.update_source(["a.", "c."])
    assert [d.para_id for d in deletions] == [second]
    assert all(d.deleted for d in deletions)
    assert second not in s.order()


def test_backspace_to_empty_removes_line():
    s = new_state()
    s.update_source(["hello world."])
    flush(s)
    s.update_source([""])
    flush(s)
    line = s.snapshot("fr")[0]
    assert line["src"] == ""
    assert line["pending"] is False, "blank lines must not sit pending forever"


def test_debounce_holds_the_line_being_typed():
    s = new_state(debounce=5.0)
    s.update_source(["the teacher is say"])
    assert s.collect_ready() == [], "an unfinished line should wait"

    # a completed sentence flushes immediately, debounce or not
    s.update_source(["the teacher is saying."])
    assert len(s.collect_ready()) == 1

    # so does any line that is no longer the one being typed
    s.update_source(["the teacher is saying.", "and then"])
    assert s.collect_ready() == []
    s.update_source(["the teacher is saying.", "and then", "next"])
    ready = s.collect_ready()
    assert len(ready) == 1 and ready[0].text == "and then"


def test_stale_translation_is_dropped():
    s = new_state()
    s.update_source(["first version."])
    pid = s.order()[0]
    s.update_source(["second version."])
    assert s.apply_translation(pid, "fr", 1, "premiere version.") is False
    assert s.apply_translation(pid, "fr", 2, "deuxieme version.") is True
    assert s.snapshot("fr")[0]["text"] == "deuxieme version."


def test_translation_marks_line_not_pending():
    s = new_state()
    s.update_source(["shalom."])
    pid = s.order()[0]
    assert s.snapshot("he")[0]["pending"] is True
    s.apply_translation(pid, "he", 1, "שלום.")
    assert s.snapshot("he")[0]["pending"] is False
    assert s.snapshot("fr")[0]["pending"] is True, "languages are tracked separately"


def test_hebrew_injection_passes_through_verbatim():
    """The transcriber types English and quotes terms in Hebrew. Those are
    quotations, not words to translate -- fed to an en->fr model they come back
    as rows of dashes, and en->he silently drops them."""
    g = Glossary(preserve_scripts=["hebrew"], rtl_langs={"he"})
    masked, restores = g.mask("the רשי says that the גמרא asks a question.", "fr")
    assert masked == "the Z0 says that the Z1 asks a question."
    assert "רשי" in restores[0] and "גמרא" in restores[1]
    assert g.unmask("le Z0 dit que le Z1 pose une question.", restores) == \
        "le \u2068רשי\u2069 dit que le \u2068גמרא\u2069 pose une question."


def test_hebrew_target_keeps_injection_verbatim_without_isolates():
    """Even into Hebrew: the quoted term is already in the reader's script, and
    an RTL run inside an RTL line needs no bidi isolation."""
    g = Glossary(preserve_scripts=["hebrew"], rtl_langs={"he"})
    _, restores = g.mask("today we learn רשי.", "he")
    assert restores == ["רשי"], "no isolate characters for an RTL target"


def test_multi_word_hebrew_phrase_masked_as_one_run():
    g = Glossary(preserve_scripts=["hebrew"], rtl_langs={"he"})
    masked, restores = g.mask("He quoted בבא מציעא at length.", "he")
    assert masked == "He quoted Z0 at length."
    assert restores == ["בבא מציעא"]


def test_hebrew_run_does_not_swallow_sentence_period():
    g = Glossary(preserve_scripts=["hebrew"], rtl_langs={"he"})
    masked, _ = g.mask("we will learn רשי. Then we continue.", "he")
    assert masked == "we will learn Z0. Then we continue.", masked


def test_all_hebrew_line_skips_the_model():
    g = Glossary(preserve_scripts=["hebrew"], rtl_langs={"he"})
    masked, _ = g.mask('רש"י אומר', "he")
    assert not g.has_translatable_text(masked), \
        "a line that is entirely a quotation must not be sent to the model"
    assert g.has_translatable_text(g.mask("the רשי says", "he")[0])


def test_speaker_label_is_split_off_before_translation():
    """'RY:' is an attribution, not content. A model asked to translate it
    mangles it or absorbs it into the first word."""
    sp = SpeakerParser(["RY", "T"])
    s = new_state(speakers=sp)
    s.update_source(["RY: today we will learn.", "T: can you repeat that?"])
    ready = flush(s)
    assert [c.text for c in ready] == ["today we will learn.", "can you repeat that?"], \
        "the label must not reach the translator"

    for c in ready:
        s.apply_translation(c.para_id, "fr", c.version, "TRANSLATED")
    lines = s.snapshot("fr")
    assert [l["speaker"] for l in lines] == ["RY", "T"]
    assert [l["text"] for l in lines] == ["TRANSLATED", "TRANSLATED"]


def test_speaker_label_renders_per_language():
    sp = SpeakerParser(["RY"], {"RY": {"he": 'ר"י'}})
    s = new_state(speakers=sp)
    s.update_source(["RY: shalom."])
    assert s.snapshot("he")[0]["speaker"] == 'ר"י'
    assert s.snapshot("fr")[0]["speaker"] == "RY", "defaults to the label itself"


def test_unlabelled_line_has_no_speaker():
    sp = SpeakerParser(["RY", "T"])
    s = new_state(speakers=sp)
    s.update_source(["just a plain line."])
    line = s.snapshot("fr")[0]
    assert line["speaker"] is None and line["src"] == "just a plain line."


def test_label_only_line_is_not_left_pending():
    """The transcriber types 'RY:' and pauses before the sentence."""
    sp = SpeakerParser(["RY"])
    s = new_state(speakers=sp)
    s.update_source(["RY:"])
    flush(s)
    line = s.snapshot("fr")[0]
    assert line["speaker"] == "RY"
    assert line["pending"] is False, "an empty body must not spin forever"


def test_speaker_parser_tolerates_spacing_and_case():
    sp = SpeakerParser(["RY", "T"])
    assert sp.split("RY: hello") == ("RY", "hello")
    assert sp.split("RY:hello") == ("RY", "hello")
    assert sp.split("  ry :  hello") == ("RY", "hello"), "canonical label form kept"
    assert sp.split("T: hi") == ("T", "hi")
    assert sp.split("TRY: hi")[0] is None, "must not match inside a word"


def test_split_word_text_handles_word_control_chars():
    raw = "First para.\rSecond\x0bline.\r\x07\rLast.\r"
    assert split_word_text(raw) == ["First para.", "Second", "line.", "", "Last."]


def test_glossary_masks_and_restores():
    g = Glossary(protect=["Midrash", "Rashi"])
    masked, restores = g.mask("Rashi explains the Midrash here.", "fr")
    assert "Rashi" not in masked and "Midrash" not in masked
    assert restores == ["Rashi", "Midrash"]
    assert g.unmask(masked, restores) == "Rashi explains the Midrash here."


def test_glossary_detects_lost_marker():
    g = Glossary(protect=["Midrash"])
    _, restores = g.mask("the Midrash says", "fr")
    assert g.unmask("le dit", restores) is None, "a dropped marker must be detected"


def test_glossary_forces_rendering_the_model_would_have_translated():
    """The whole point: post-translation replacement cannot fix `les sages`,
    because by then the English is gone. Masking can."""
    g = Glossary(replace={"the sages": {"fr": "les Sages", "he": "חז\"ל"}})
    masked, restores = g.mask("what the sages say", "fr")
    assert masked == "what Z0 say", masked
    # what the model gives back, with the marker carried through untouched
    assert g.unmask("ce que Z0 disent", restores) == "ce que les Sages disent"

    _, he_restores = g.mask("what the sages say", "he")
    assert he_restores == ["חז\"ל"], "the same term resolves per language"


def test_glossary_replace_beats_protect():
    g = Glossary(protect=["Rashi"], replace={"Rashi": {"he": "רש\"י"}})
    assert g.mask("Rashi says", "he")[1] == ["רש\"י"]
    assert g.mask("Rashi says", "fr")[1] == ["Rashi"], "no he-style override for fr"


def test_glossary_case_insensitive_match_keeps_forced_form():
    g = Glossary(replace={"the sages": {"fr": "les Sages"}})
    assert g.mask("THE SAGES teach", "fr")[1] == ["les Sages"]


def test_strip_markers_never_leaks_to_the_projector():
    g = Glossary(protect=["Midrash"])
    _, restores = g.mask("the Midrash", "fr")
    assert "Z" not in g.strip_markers("le Z0 dit", restores)
    assert "Z" not in g.strip_markers("le Z7 dit", restores), "unknown index dropped"



# ---- export and download


class _Target:
    def __init__(self, code, name, rtl=False):
        self.code, self.name, self.rtl = code, name, rtl


TARGETS = [_Target("fr", "Francais"), _Target("he", "Hebrew", rtl=True)]


def _translated_state():
    s = new_state(speakers=SpeakerParser(["RY"], {}))
    s.update_source(["RY: the sages spoke.", "he answered."])
    for change in flush(s):
        for lang in LANGS:
            s.apply_translation(change.para_id, lang, change.version,
                                f"[{lang}] {change.text}")
    return s


def test_english_export_is_the_source_not_a_translation():
    s = _translated_state()
    rows = export_mod.lines_for(s, TARGETS, "en")
    assert [r[0] for r in rows] == ["the sages spoke.", "he answered."]
    assert rows[0][2] == "RY", "the speaker label survives into the English export"
    assert all("[fr]" not in r[0] for r in rows), "English must carry no translation"


def test_docx_bytes_is_a_real_document():
    s = _translated_state()
    blob = export_mod.docx_bytes("Hebrew - now", export_mod.lines_for(s, TARGETS, "he"),
                                 rtl=True)
    assert blob[:2] == b"PK", "a .docx is a zip"
    import zipfile, io
    xml = zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml").decode("utf-8")
    assert "[he] the sages spoke." in xml
    assert "w:bidi" in xml, "Hebrew must be marked right-to-left or Word renders it wrong"


def test_session_zip_holds_every_language_plus_english():
    import zipfile, io
    s = _translated_state()
    blob = export_mod.session_zip(s, TARGETS, "2026-01-01_0900")
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert len(names) == 3
    for code in ("fr", "he", "en"):
        assert f"2026-01-01_0900/2026-01-01_0900_{code}.docx" in names


def test_download_endpoints_serve_each_language():
    from fastapi.testclient import TestClient
    from midrash_translate import config as config_mod
    from midrash_translate.server import create_app

    cfg = config_mod.load(Path(__file__).resolve().parents[1] / "config.yaml")
    cfg.raw["translation"]["backend"] = "echo"
    cfg.raw["translation"].pop("backend_by_lang", None)
    app = create_app(cfg, reader_override=str(Path(__file__).parent / "_probe.txt"))

    probe = Path(__file__).parent / "_probe.txt"
    probe.write_text("RY: the sages spoke.\n", encoding="utf-8")
    try:
        with TestClient(app) as client:
            for code in ("fr", "es", "he", "en"):
                r = client.get(f"/download/{code}.docx")
                assert r.status_code == 200, f"{code}: {r.status_code}"
                assert r.content[:2] == b"PK"
                assert f"_{code}.docx" in r.headers["content-disposition"]
            assert client.get("/download/all.zip").content[:2] == b"PK"
            assert client.get("/download/xx.docx").status_code == 404
            assert client.get("/print/he").status_code == 200
            assert client.get("/print/all").status_code == 200
            assert client.get("/print/xx").status_code == 404
    finally:
        probe.unlink(missing_ok=True)


def test_print_page_escapes_the_transcript():
    s = new_state()
    s.update_source(["<script>alert(1)</script> & so on"])
    for change in flush(s):
        for lang in LANGS:
            s.apply_translation(change.para_id, lang, change.version, change.text)
    from midrash_translate.server import _print_section
    out = _print_section("Hebrew", "now", export_mod.lines_for(s, TARGETS, "he"),
                         True, False)
    assert "<script>" not in out, "typed text must never become markup"
    assert "&lt;script&gt;" in out and "&amp; so on" in out


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:
            failures += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
