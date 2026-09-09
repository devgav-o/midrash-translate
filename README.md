# midrash-translate

Live translation of a classroom transcript. One person types the lecture in
English in Word; French, Spanish and Hebrew appear on their own projectors about
a second behind, edits and deletions included.

Runs on one Windows laptop, offline, at no cost.

## How it works

```
Word (English transcriber)
   |  read-only COM poll every 400ms
   v
watcher  -->  paragraph diff (stable ids across edits)
                  |  debounce: a line is sent once it is quiet for 700ms,
                  |  ends in sentence punctuation, or is no longer being typed
                  v
              3 translation workers (one per language, newest-text-wins queue)
                  |  sentence-level LRU cache + glossary masking
                  v
              FastAPI + WebSocket  -->  3 fullscreen browser pages
                  v
              end of class  -->  4 .docx files (fr, es, he, en)
```

Two design choices worth knowing about:

**No Word macro.** Word has no reliable text-changed event in VBA, and
`Application.OnTime` has one-second granularity. Instead a separate Python
process attaches to the already-running Word instance over COM and reads it. It
never writes, so it cannot corrupt or lock the transcriber's document, no macro
needs installing, and it can be restarted mid-class. One COM call per poll
(`Content.Text` for the whole document), not one per paragraph.

**Browser pages, not live Word documents, on the projectors.** Writing into three
open Word documents steals focus, fights the scroll position and renders Hebrew
badly. The `.docx` archive is still written at the end.

## Measured performance

Opus-MT on CPU, Apple M-series, unquantized:

| language | median | p95 |
|---|---|---|
| French | 81 ms | 234 ms |
| Spanish | 73 ms | 146 ms |
| Hebrew | 54 ms | 143 ms |

A lecturer produces roughly one sentence every four seconds, so p95 has about 7x
headroom. Re-run `scripts/benchmark.py` on the classroom machine to confirm.

## Setup

### On this Mac (development)

Everything is already installed in `.venv`. Note that **`pip3 install` will not
work** — Homebrew's Python refuses system-wide installs and points you at pipx.
You do not need pipx. Use the venv:

```bash
./dev.sh                      # watch demo_transcript.txt, echo backend
BACKEND=opusmt ./dev.sh       # real translation
./dev.sh notes.txt            # watch your own file
```

Edit the watched file and save; the browser pages update. No Word required.

### On the classroom Windows machine (once)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
.\.venv\Scripts\python.exe scripts\benchmark.py       # is it fast enough?
.\.venv\Scripts\python.exe scripts\check_markers.py   # is the glossary working?
```

After that, no network is needed for the local models.

## Running a class

1. The transcriber opens Word and types as usual.
2. `.\.venv\Scripts\python.exe run.py` — it prints a LAN address.
3. On each projector open its page and press `F` for fullscreen:
   `/display/fr`, `/display/es`, `/display/he`.
4. At the end, open `/` and click **Export .docx**. Files land in `sessions/<date>/`.

Order does not matter — start the server before or after Word; it retries until
it finds the document.

### Projector page keys

| Key | Effect |
|---|---|
| `+` / `-` | font size |
| `S` | show the English source under each line |
| `F` | fullscreen |

Scroll up to read back; a **↓ live** button returns to following the lecture.
The page requests a screen wake lock so the projector does not blank mid-class.

> The page deliberately does **not** use CSS `scroll-behavior: smooth`. Animated
> scrolling is driven by the browser's frame loop, which is throttled in
> unfocused windows — on a projector that left the view stranded above the newest
> line while the text arrived normally underneath. Auto-scroll assigns
> `scrollTop` directly so it lands regardless of focus.

## Translation backends

| Backend | Cost | Quality | Notes |
|---|---|---|---|
| `opusmt` | free, offline, unlimited | good fr/es, decent he | default. Helsinki-NLP models |
| `gemini` | free tier (~1500 req/day) | best, Hebrew especially | needs `GEMINI_API_KEY` + network |
| `echo` | — | none | passes text through; for testing |

The shipped config is the hybrid: local models everywhere, Gemini for Hebrew,
falling back to the local model automatically if the key is missing, the quota is
spent, or the network drops mid-lecture. A language never leaves the projector
because a backend failed — it degrades and says so in `/api/status`.

```powershell
$env:GEMINI_API_KEY = "..."   # free key from https://aistudio.google.com/apikey
```

To run fully offline, comment out `backend_by_lang` in `config.yaml`.

## Glossary

`glossary.json` handles the terms machine translation mangles. Terms are masked
with a numeric marker before translation and substituted back afterwards, so it
works identically on every backend.

- `protect` — restore the English term verbatim.
- `replace` — force an exact per-language rendering. Takes precedence.

Two things learned the hard way, both worth respecting:

**The marker must survive the model.** Measured over the three Opus-MT models,
`%0%` and `[0]` survive 12/12 probes; `⟦0⟧` survives **0/12** — unusual Unicode
becomes `<unk>` and is dropped. A dropped marker means every protected sentence
silently falls back to unprotected translation, so the glossary looks configured
but does nothing. Run `scripts/check_markers.py` after changing models.

**Prefer proper nouns in `replace`.** The model cannot grammatically agree with a
token it cannot see. Forcing the common-noun phrase `the sages` to `los Sabios`
produced `el los Sabios dice`. `Rashi`, `Midrash` and `Rambam` carry no agreement
and are always safe.

Growing this file is the cheapest quality improvement available.

## Tuning

| Symptom | Fix in `config.yaml` |
|---|---|
| translations lag behind | lower `debounce_ms`, keep `beams: 1` |
| lines flicker / rewrite as you type | raise `debounce_ms` to 1000-1500 |
| text too small at the back | raise `display.font_size_px`, or press `+` |
| one language much slower | point just that language at `gemini` |

`/` shows per-language queue depth, cache hit rate, viewer count and backend
errors. A queue depth that grows steadily means that language is not keeping up.

## Tests

```bash
.venv/bin/python tests/test_state.py     # or: pytest
```

15 tests covering the paragraph diff (append, mid-document insert, in-place edit,
delete), the debounce rules, stale-result rejection, and glossary masking.

## Limitations

- Machine translation is fast, not faithful. That is the trade against human
  writers who are accurate but minutes behind.
- Hebrew from the local model is weaker than French or Spanish. Use the Gemini
  hybrid if there is any network in the room.
- The Word capture is Windows-only and **has not been tested against a real Word
  instance** — it was written on a Mac. Test it before a real class.
- int8 quantization is skipped on Apple Silicon (no quantized engine). It applies
  on the Windows machine, where it should roughly halve the latencies above.
- Word must stay open. If it closes, the watcher reconnects when it reopens, but
  text typed while it was shut is only picked up on reconnect.
