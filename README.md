# midrash-translate

Live translation of a classroom transcript. One person types the lecture in
English in Word; French, Spanish and Hebrew appear on their own projectors about
a second behind, edits and deletions included.

Runs on one Windows laptop, offline, at no cost.

**Setting up the classroom machine?** Open [`WINDOWS-SETUP.html`](WINDOWS-SETUP.html)
in a browser -- step-by-step install, the routine for each class, and what to do
when something goes wrong. The rest of this file is the technical detail behind it.

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

Edit the watched file **and save**; the browser pages update on save. The file
source only ever sees saved bytes -- TextEdit in particular autosaves when it
loses focus, which makes updates look like they depend on clicking the browser.
The Word source does not behave this way: COM reads the live in-memory document,
so it sees keystrokes as they are typed, with no save.

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
4. At the end, open `/` and take the transcript:
   - **All languages (.zip)** or a single **.docx** -- downloads to whichever
     machine the control panel is open on, phone included.
   - **PDF** -- opens `/print/<lang>` and the browser's print dialog; choose
     *Save as PDF*. The browser lays Hebrew out right-to-left correctly and
     needs no network, which is why there is no PDF library here.
   - **Save .docx to sessions folder** -- the four files onto the classroom
     machine's disk at `sessions/<date>/`, as before.

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

## Speaker labels

The transcriber prefixes lines with who is talking:

```
RY: today we will be learning רשי.
T: can you repeat that please?
```

Labels listed under `speakers.labels` in `config.yaml` are split off before
translation -- a model otherwise mangles `RY:` or absorbs it into the first
word -- and shown as a chip on the projector and in bold in the export. Matching
is case-insensitive and tolerant of spacing (`ry :` works), and a label never
matches inside a word (`TRY:` is not `T`).

`speakers.names` optionally renders a label differently per language (the shipped
config writes `RY` as `ר"י` on the Hebrew screen); it defaults to the label
itself so it stays recognisable across all four screens.

## Mixed-script lines

The transcriber switches script mid-sentence, and those terms are quotations,
not words to translate:

```
the רשי says that the גמרא asks a question.
   fr -> le רשי dit que le גמרא pose une question.
   he -> רשי אומר כי גמרא שואל שאלה.
```

Runs in any script listed under `translation.preserve_scripts` pass through
verbatim to every target, Hebrew included -- it is already the script the
audience reads. Multi-word phrases stay one unit, a line that is entirely a
quotation skips the model, and a preserved right-to-left run inside a
left-to-right line is wrapped in bidi isolates so the punctuation stays put.

Without this, `en->fr` returned a row of 250 dashes and `en->he` silently
dropped the terms.

## Glossary

`glossary.json` handles the terms machine translation mangles. Terms are masked
with a numeric marker before translation and substituted back afterwards, so it
works identically on every backend.

- `protect` — restore the English term verbatim.
- `replace` — force an exact per-language rendering. Takes precedence.

Two things learned the hard way, both worth respecting:

**The marker must survive the model — with several in one sentence.** A
single-marker probe is not enough, and neither is testing only French. Measured
over the three models on 5 multi-marker sentences each:

| marker | fr | es | he | verdict |
|---|---|---|---|---|
| `Z0` | 5/5 | 5/5 | 5/5 | in use |
| `TERM0` | 5/5 | 5/5 | 4/5 | risks being translated to `TERME0` |
| `X0X` | 5/5 | 5/5 | 4/5 | Hebrew transliterates it to `אקס-או-אקס` |
| `%0%` | 2/5 | 4/5 | **0/5** | |
| `⟦0⟧` | 0/5 | 0/5 | 0/5 | becomes `<unk>` and is dropped |

A dropped marker means the sentence silently falls back, so the glossary looks
configured but does nothing. Run `scripts/check_markers.py` after changing
models.

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

30 tests covering the paragraph diff (append, mid-document insert, in-place
edit, delete), the debounce rules, stale-result rejection, glossary masking,
preserved-script passthrough, speaker-label parsing, and the export/download
routes (every language served, RTL marked in the .docx, transcript text escaped
before it reaches the print page).

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
