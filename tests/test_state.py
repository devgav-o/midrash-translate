"""Tests for the paragraph diff, the debounce rules and glossary masking.

Run with `pytest`, or directly: `python tests/test_state.py`
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from midrash_translate.state import DocState  # noqa: E402
from midrash_translate.translators.glossary import Glossary  # noqa: E402
from midrash_translate.watcher import split_word_text  # noqa: E402

LANGS = ["fr", "he"]


def flush(state: DocState):
    """Force everything past the debounce window."""
    time.sleep(0.02)
    return state.collect_ready()


def new_state(debounce=0.0) -> DocState:
    return DocState(LANGS, debounce_s=debounce)


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
    assert masked == "what %0% say", masked
    # what the model gives back, with the marker carried through untouched
    assert g.unmask("ce que %0% disent", restores) == "ce que les Sages disent"

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
    assert "%" not in g.strip_markers("le %0% dit", restores)
    assert "%" not in g.strip_markers("le %7% dit", restores), "unknown index dropped"


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
