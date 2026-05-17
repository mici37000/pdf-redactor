# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & Running

```bash
./setup.sh                                        # create .venv, install deps, add Linux desktop entry
.venv/bin/python -m redactor                      # launch GUI
.venv/bin/python -m redactor <input.pdf> <outdir> # CLI: redact one file
```

There is no test suite and no linter configuration.

## Architecture

The app has three modules in `redactor/`:

- **`detectors.py`** — pure detection logic. `detect_page(page)` is the primary entry point used by the pipeline: it extracts words from a PyMuPDF page, reassembles lines in Hebrew right-to-left order (sorting words by descending x0 within each block/line group), strips Unicode bidi control characters, then runs regex patterns against each line's joined text. Returns `Detection` objects that carry both the matched text and the exact word bounding rectangles from the PDF. `detect()` is a plain-text variant kept for unit tests — it does not return rectangles.

- **`redact.py`** — redaction pipeline. `redact_pdf()` does two passes: (1) runs `detect_page` on every page to collect detections, (2) applies redact annotations via PyMuPDF's `add_redact_annot` + `apply_redactions` using the rectangles from the detections, then also runs a second-pass `search_for` across all detected value strings to catch repeated occurrences elsewhere in the document (e.g. employer name repeated in Menora transaction tables). Output is saved unencrypted; input is never overwritten.

- **`gui.py`** — PyQt6 GUI. `RedactionWorker` (a `QThread`) calls `redact_pdf` in a background thread and emits `finished_one` / `all_done` signals. The main window (`MainWindow`) persists the output directory via `QSettings`. For encrypted PDFs, `_ensure_unlockable` prompts for a password and caches it across the session.

## Key Design Decisions

**Why rectangles from `detect_page` instead of `page.search_for`**: Hebrew PDFs extract text inconsistently across report formats (Analyst, Phoenix, Menora). Menora uses visual/LTR extraction order, so the `_reorder_lines` step normalizes this by sorting words by position. Carrying the word rects directly from detection avoids a fragile re-search. The second-pass `search_for` in `redact.py` handles repeated value occurrences that detection alone won't catch.

**Label redaction (`"title"` kind)**: Both the label keyword (e.g. "שם העמית:") and the value are redacted, so a reader cannot infer what was removed.

**Supported PII kinds**: `name`, `id` (Israeli Teudat Zehut with checksum validation), `account`, `employer`, `tax_file`. The `title` kind marks the accompanying label keyword.
