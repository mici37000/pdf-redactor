"""PDF redaction with PyMuPDF.

We add `redact` annotations filled with white (1, 1, 1), then apply them —
this removes the underlying text from the content stream, not just covers it.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import fitz

from .detectors import BIDI_CONTROLS, Detection, detect_page


WHITE = (1.0, 1.0, 1.0)

# Stamped into the Keywords metadata of every file we write, so a later run can
# recognise its own output and leave it alone.
REDACTED_MARKER = "pdf-redactor:redacted"

# Plausible range for a report year: old enough for historical statements,
# one year ahead to tolerate documents issued for the coming year.
YEAR_MIN = 1990
_FOUR_DIGITS = re.compile(r"\d{4}")
# "שנת" / "לשנת" / "שנה" — the year label in these Hebrew reports. Extraction
# may reverse the word, so accept the reversed spellings too.
_YEAR_LABEL = re.compile(r"ל?שנ[תה]|[הת]נשל?")

# Words that carry no information of their own — a trailing page whose only
# text is "עמוד 4 מתוך 4" / "Page 4 of 4" is blank for our purposes. Hebrew
# extraction may reverse the words, so the reversed spellings are listed too.
_BOILERPLATE_WORDS = re.compile(r"עמוד|מתוך|דומע|ךותמ|page|of", re.IGNORECASE)
# Matches a letter in any script: digits, punctuation and whitespace on their
# own never make a page worth keeping.
_LETTER = re.compile(r"[^\W\d_]")
# A text-free page holding a lot of vector art is a chart, not a blank page;
# only light header/footer decoration (rules, crop marks, a logo) counts as
# blank. IBI's trailing page has ~15 drawings, its content pages ~2200.
MAX_DECORATIVE_DRAWINGS = 100


@dataclass
class PageResult:
    page_index: int
    detections: list[Detection]
    rects_found: int  # total rectangles redacted (a detection can match >1)


@dataclass
class RedactionResult:
    input_path: Path
    output_path: Path
    pages: list[PageResult]
    dropped_pages: list[int] = field(default_factory=list)  # blank, left out

    @property
    def total_detections(self) -> int:
        return sum(len(p.detections) for p in self.pages)

    @property
    def total_rects(self) -> int:
        return sum(p.rects_found for p in self.pages)


def page_is_empty(page: fitz.Page) -> bool:
    """True if `page` carries nothing worth keeping.

    "Nothing" means: no images, no meaningful text once page-number boilerplate
    (and digits/punctuation) is discarded, and no more vector art than the
    header/footer decoration these reports put on every page.
    """
    if page.get_images():
        return False
    text = page.get_text("text").translate(BIDI_CONTROLS)
    if _LETTER.search(_BOILERPLATE_WORDS.sub("", text)):
        return False
    return len(page.get_drawings()) <= MAX_DECORATIVE_DRAWINGS


def _empty_page_indices(doc: fitz.Document) -> list[int]:
    return [i for i, page in enumerate(doc) if page_is_empty(page)]


def _stamp_marker(doc: fitz.Document) -> None:
    """Append `REDACTED_MARKER` to the document's Keywords metadata."""
    meta = {k: v for k, v in (doc.metadata or {}).items() if isinstance(v, str)}
    keywords = (meta.get("keywords") or "").strip()
    if REDACTED_MARKER not in keywords:
        meta["keywords"] = f"{keywords}; {REDACTED_MARKER}" if keywords else REDACTED_MARKER
        doc.set_metadata(meta)


def is_already_redacted(path: Path, password: str | None = None) -> bool:
    """True if `path` looks like it has already been through redaction.

    Two signals: our own `REDACTED_MARKER` in the metadata, or a document that
    has extractable text yet yields no detections at all. The text requirement
    keeps scanned/image-only PDFs — where detection cannot see anything in the
    first place — out of the "already clean" bucket.
    """
    path = Path(path)
    doc = fitz.open(path)
    try:
        if doc.needs_pass and not (password and doc.authenticate(password)):
            # Encrypted and unreadable, and our outputs are never encrypted.
            return False
        if REDACTED_MARKER in ((doc.metadata or {}).get("keywords") or ""):
            return True
        has_text = False
        for page in doc:
            if detect_page(page):
                return False
            if not has_text and page.get_text("text").strip():
                has_text = True
        return has_text
    finally:
        doc.close()


def _year_candidates(text: str) -> tuple[list[int], list[int]]:
    """Return (labelled, all) year candidates found in `text`.

    `labelled` holds only the years on a line that also carries a year label
    ("שנת" and friends), which is a much stronger signal than a bare number.
    """
    year_max = date.today().year + 1
    labelled: list[int] = []
    every: list[int] = []
    for line in text.splitlines():
        line_years: list[int] = []
        for match in _FOUR_DIGITS.finditer(line):
            digits = match.group()
            year = int(digits)
            if not YEAR_MIN <= year <= year_max:
                # Hebrew extraction sometimes emits digit runs right-to-left.
                year = int(digits[::-1])
                if not YEAR_MIN <= year <= year_max:
                    continue
            line_years.append(year)
        every.extend(line_years)
        if line_years and _YEAR_LABEL.search(line):
            labelled.extend(line_years)
    return labelled, every


def find_report_year(path: Path, password: str | None = None) -> int | None:
    """Best-effort year of the report in `path`, or None if none is found.

    Prefers years sitting next to a "שנת" label; otherwise falls back to the
    most frequently mentioned plausible year (ties go to the latest), which
    picks out the reporting year over incidental dates.
    """
    path = Path(path)
    doc = fitz.open(path)
    try:
        if doc.needs_pass and not (password and doc.authenticate(password)):
            return None
        text = "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()

    labelled, every = _year_candidates(text)
    for candidates in (labelled, every):
        if candidates:
            counts = Counter(candidates)
            best = max(counts, key=lambda y: (counts[y], y))
            return best
    return None


@dataclass
class MergeResult:
    output_path: Path
    source_count: int
    pages_kept: int
    pages_dropped: int  # blank pages left out of the merged file


def merge_pdfs(
    input_paths: list[Path], output_path: Path, drop_empty_pages: bool = True
) -> MergeResult:
    """Concatenate the given PDFs, in order, into a single file.

    Blank pages (see `page_is_empty`) are left out unless `drop_empty_pages` is
    False. Intended for already-redacted (and therefore unencrypted) outputs,
    so no password handling here.
    """
    paths = [Path(p) for p in input_paths]
    if not paths:
        raise ValueError("No files to merge")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    merged = fitz.open()
    try:
        for p in paths:
            src = fitz.open(p)
            try:
                merged.insert_pdf(src)
            finally:
                src.close()

        # Done on the merged document so blanks are caught in every source,
        # including files that arrived already redacted.
        dropped = _empty_page_indices(merged) if drop_empty_pages else []
        if dropped:
            merged.delete_pages(dropped)
        _stamp_marker(merged)
        pages_kept = merged.page_count
        merged.save(output_path, garbage=4, deflate=True)
    finally:
        merged.close()

    return MergeResult(
        output_path=output_path,
        source_count=len(paths),
        pages_kept=pages_kept,
        pages_dropped=len(dropped),
    )


def redact_pdf(
    input_path: Path,
    output_path: Path,
    password: str | None = None,
    drop_empty_pages: bool = True,
) -> RedactionResult:
    """Redact PII in the given PDF and write to `output_path`.

    Refuses to overwrite the source file — the caller must provide an
    `output_path` that resolves to a different location than `input_path`.
    For encrypted PDFs, pass `password`; the saved output is unencrypted.
    Blank pages (see `page_is_empty`) are left out of the output unless
    `drop_empty_pages` is False; `PageResult.page_index` keeps referring to the
    page's position in the *input*.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    try:
        same = input_path.resolve() == output_path.resolve()
    except OSError:
        same = str(input_path) == str(output_path)
    if same:
        raise ValueError(
            f"Refusing to overwrite original file: {input_path}. "
            "Choose a different output folder."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(input_path)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise ValueError(
                f"Encrypted PDF requires a valid password: {input_path.name}"
            )
    page_results: list[PageResult] = []

    try:
        # Pass 1: detect on every page.
        per_page_detections: dict[int, list[Detection]] = {}
        for i, page in enumerate(doc):
            per_page_detections[i] = detect_page(page)

        # Repeated values (name, employer, IDs, account) may appear elsewhere
        # in the document — for example the Menora transaction table repeats
        # the employer on every row. Collect every detected value string so
        # a second `search_for` pass can catch those occurrences too.
        value_strs: set[str] = set()
        for dets in per_page_detections.values():
            for d in dets:
                if d.kind != "title" and d.text:
                    value_strs.add(d.text)

        # Pass 2: apply rects from detection + search_for for values.
        for i, page in enumerate(doc):
            detections = per_page_detections[i]
            rects_found = 0
            for d in detections:
                for rect in d.rects:
                    page.add_redact_annot(rect, fill=WHITE)
                    rects_found += 1
            for value in value_strs:
                for rect in page.search_for(value):
                    page.add_redact_annot(rect, fill=WHITE)
                    rects_found += 1
            if rects_found:
                page.apply_redactions()
            page_results.append(
                PageResult(page_index=i, detections=detections, rects_found=rects_found)
            )

        # After redaction: a page whose only content was the PII we just removed
        # can now be blank, so this runs last.
        dropped = _empty_page_indices(doc) if drop_empty_pages else []
        if dropped:
            doc.delete_pages(dropped)

        _stamp_marker(doc)
        doc.save(output_path, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    return RedactionResult(
        input_path=input_path,
        output_path=output_path,
        pages=page_results,
        dropped_pages=dropped,
    )
