"""PII detectors for financial reports.

`detect_page(page)` walks each extracted line in Hebrew logical order
(reassembled from word positions) and returns Detection records with the
underlying word rectangles attached — the redaction pipeline uses those
rectangles directly instead of re-searching the page. This handles both
normal extractions (Analyst / Phoenix) and visual-LTR extractions
(Menora) uniformly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz


# Unicode bidi directional-formatting marks. Some Hebrew PDFs embed them in
# their text streams, which breaks plain-text regex matching.
BIDI_CONTROLS = str.maketrans(
    "", "", "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


@dataclass(frozen=True)
class Detection:
    text: str                    # human-readable form (used for logging)
    kind: str                    # "title" | "name" | "id" | "account" | "employer" | "tax_file"
    rects: tuple = field(default_factory=tuple)  # tuple[fitz.Rect, ...]


# Name/employer bodies stop at digits, colons, or newlines so the Phoenix-style
# layout "שם העמית: מרגוליס מיכאל317435956" doesn't swallow the ID digits.
_NAME_RE = re.compile(
    r"(שם\s*העמית\s*:?)\s*([^\n\r\d:]+?)(?=\s*(?:מספר|ת\s*\.?\s*ז|\d|:|$))"
)
# Employer label has two variants: "שם המעסיק" (Analyst/Phoenix) and "שם מעסיק"
# (Menora). The `ה?` accepts both.
_EMPLOYER_RE = re.compile(
    r"(שם\s*ה?מעסיק\s*:?)\s*([^\n\r\d:]+?)(?=\s*(?:\d|:|$))"
)
_ACCOUNT_RE = re.compile(r"(מספר\s*חשבון\s*:?)\s*(\d{4,12})")

# ID label variants: "ת.ז.", "מספר ת.ז.", "תעודת זהות", "מספר תעודת זהות",
# and "מס' זהות" (Menora). Whitespace between dots and letters is tolerated
# because the logical-reorder step can introduce spaces around punctuation.
_ID_LABEL_RE = re.compile(
    r"(?:"
    r"(?:מספר\s+)?ת\s*\.?\s*ז\s*\.?"
    r"|(?:מספר\s+)?תעודת\s*זהות"
    r"|מס\s*['׳]\s*זהות"
    r")"
)
_ID_NUM_RE = re.compile(r"\d{8}\s*/\s*\d|\d{9}")

_TAX_FILE_LABEL_RE = re.compile(r"מספר\s*תיק\s*ניכויים")
_TAX_FILE_NUM_RE = re.compile(r"\d{6,12}")

# IBI Trade monthly statement: page header "דוח לחשבון מס' XXXXXX".
# The separator between label and number is the token ":'" so we tolerate any
# punctuation/whitespace between מס and the digits.
_IBI_HEADER_LABEL_RE = re.compile(r"דוח\s+לחשבון\s+מס")
_IBI_HEADER_NUM_RE = re.compile(r"\d{5,9}")


def _valid_israeli_id(digits: str) -> bool:
    """Teudat Zehut checksum: weights 1,2,1,2,... summed with digit-of-sum per term."""
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (1 if i % 2 == 0 else 2)
        total += n if n < 10 else n - 9
    return total % 10 == 0


def _reorder_lines(page: fitz.Page) -> list[list[tuple]]:
    """Group words by PyMuPDF's (block, line) key and sort each row by
    descending x0 — Hebrew logical right-to-left order. Rebuilding lines
    this way lets the string regexes match even when the PDF extracts text
    in visual (left-to-right) order, as Menora does."""
    lines: dict[tuple[int, int], list[tuple]] = {}
    for w in page.get_text("words"):
        cleaned = w[4].translate(BIDI_CONTROLS)
        if cleaned:
            key = (w[5], w[6])
            lines.setdefault(key, []).append(
                (w[0], w[1], w[2], w[3], cleaned, w[5], w[6], w[7])
            )
    ordered: list[list[tuple]] = []
    for key in sorted(lines.keys()):
        ordered.append(sorted(lines[key], key=lambda x: -x[0]))
    return ordered


def _line_text(row: list[tuple]) -> tuple[str, list]:
    """Join word texts with single spaces; return (text, idx_to_word) where
    idx_to_word[i] = index of the word containing character i (or None for
    the space separators)."""
    parts: list[str] = []
    idx_to_word: list = []
    for i, w in enumerate(row):
        if parts:
            parts.append(" ")
            idx_to_word.append(None)
        parts.append(w[4])
        idx_to_word.extend([i] * len(w[4]))
    return "".join(parts), idx_to_word


def _words_in_span(row, idx_to_word, start: int, end: int) -> list[tuple]:
    seen = set()
    result = []
    for i in range(start, end):
        if i < len(idx_to_word) and idx_to_word[i] is not None:
            wi = idx_to_word[i]
            if wi not in seen:
                seen.add(wi)
                result.append(row[wi])
    return result


def _rects_of(words: list[tuple]) -> tuple:
    return tuple(fitz.Rect(w[0], w[1], w[2], w[3]) for w in words)


def detect_page(page: fitz.Page) -> list[Detection]:
    """Find PII on `page` and return Detections with word rectangles attached."""
    lines = _reorder_lines(page)
    out: list[Detection] = []

    for line_idx, row in enumerate(lines):
        text, idx_to_word = _line_text(row)

        for m in _NAME_RE.finditer(text):
            title_ws = _words_in_span(row, idx_to_word, *m.span(1))
            name_ws = _words_in_span(row, idx_to_word, *m.span(2))
            if name_ws:
                out.append(Detection(m.group(1).strip(), "title", _rects_of(title_ws)))
                out.append(Detection(m.group(2).strip(), "name", _rects_of(name_ws)))

        for m in _EMPLOYER_RE.finditer(text):
            title_ws = _words_in_span(row, idx_to_word, *m.span(1))
            emp_ws = _words_in_span(row, idx_to_word, *m.span(2))
            if emp_ws:
                out.append(Detection(m.group(1).strip(), "title", _rects_of(title_ws)))
                out.append(Detection(m.group(2).strip(), "employer", _rects_of(emp_ws)))

        for m in _ACCOUNT_RE.finditer(text):
            title_ws = _words_in_span(row, idx_to_word, *m.span(1))
            num_ws = _words_in_span(row, idx_to_word, *m.span(2))
            if num_ws:
                out.append(Detection(m.group(1).strip(), "title", _rects_of(title_ws)))
                out.append(Detection(m.group(2), "account", _rects_of(num_ws)))

        _scan_labeled_number(
            out, lines, line_idx, row, text, idx_to_word,
            _ID_LABEL_RE, _ID_NUM_RE, "id",
            validator=lambda raw: _valid_israeli_id(raw.replace("/", "").replace(" ", "")),
        )
        _scan_labeled_number(
            out, lines, line_idx, row, text, idx_to_word,
            _TAX_FILE_LABEL_RE, _TAX_FILE_NUM_RE, "tax_file",
        )

        # IBI Trade: account number in page header "דוח לחשבון מס' XXXXXX"
        _scan_labeled_number(
            out, lines, line_idx, row, text, idx_to_word,
            _IBI_HEADER_LABEL_RE, _IBI_HEADER_NUM_RE, "account",
        )

        # IBI Trade: "לכבוד" salutation block — label + name + street + city
        if text.strip() == "לכבוד":
            out.append(Detection("לכבוד", "title", _rects_of(row)))
            for offset, kind in [(1, "name"), (2, "street"), (3, "city")]:
                if line_idx + offset < len(lines):
                    addr_row = lines[line_idx + offset]
                    addr_text, _ = _line_text(addr_row)
                    if addr_text.strip():
                        out.append(Detection(addr_text.strip(), kind, _rects_of(addr_row)))

    return out


def _scan_labeled_number(out, lines, line_idx, row, text, idx_to_word,
                         label_re, num_re, kind, validator=None) -> None:
    """If `text` contains a matching label, emit title+value detections.
    The value is searched on the same line first, then falls back to the
    next line (some PDFs put label and value on separate rows)."""
    label_m = label_re.search(text)
    if not label_m:
        return

    def try_line(target_row, target_text, target_map) -> bool:
        for num in num_re.finditer(target_text):
            raw = num.group(0)
            if validator and not validator(raw):
                continue
            label_ws = _words_in_span(row, idx_to_word, *label_m.span())
            num_ws = _words_in_span(target_row, target_map, *num.span())
            out.append(Detection(label_m.group(0).strip(), "title", _rects_of(label_ws)))
            out.append(Detection(raw, kind, _rects_of(num_ws)))
            return True
        return False

    if try_line(row, text, idx_to_word):
        return
    if line_idx + 1 < len(lines):
        next_row = lines[line_idx + 1]
        next_text, next_map = _line_text(next_row)
        try_line(next_row, next_text, next_map)


def detect(text: str) -> list[Detection]:
    """Plain-text detection — kept for unit tests. Returns Detections without
    rectangles; the GUI/CLI pipeline uses `detect_page` instead."""
    text = text.translate(BIDI_CONTROLS)
    out: list[Detection] = []

    for m in _NAME_RE.finditer(text):
        title = m.group(1).strip()
        name = m.group(2).strip()
        if name:
            out.append(Detection(text=title, kind="title"))
            out.append(Detection(text=name, kind="name"))

    for m in _EMPLOYER_RE.finditer(text):
        title = m.group(1).strip()
        employer = m.group(2).strip()
        if employer:
            out.append(Detection(text=title, kind="title"))
            out.append(Detection(text=employer, kind="employer"))

    for m in _ACCOUNT_RE.finditer(text):
        title = m.group(1).strip()
        out.append(Detection(text=title, kind="title"))
        out.append(Detection(text=m.group(2), kind="account"))

    for line in text.splitlines():
        label = _ID_LABEL_RE.search(line)
        if not label:
            continue
        for num in _ID_NUM_RE.finditer(line):
            raw = num.group(0)
            digits = raw.replace("/", "").replace(" ", "")
            if _valid_israeli_id(digits):
                out.append(Detection(text=label.group(0).strip(), kind="title"))
                out.append(Detection(text=raw, kind="id"))
                break

    for line in text.splitlines():
        label = _TAX_FILE_LABEL_RE.search(line)
        if not label:
            continue
        for num in _TAX_FILE_NUM_RE.finditer(line):
            out.append(Detection(text=label.group(0).strip(), kind="title"))
            out.append(Detection(text=num.group(0), kind="tax_file"))
            break

    # IBI Trade: account number in page header
    for line in text.splitlines():
        label = _IBI_HEADER_LABEL_RE.search(line)
        if not label:
            continue
        for num in _IBI_HEADER_NUM_RE.finditer(line):
            out.append(Detection(text=label.group(0).strip(), kind="title"))
            out.append(Detection(text=num.group(0), kind="account"))
            break

    # IBI Trade: "לכבוד" salutation block
    text_lines = text.splitlines()
    for i, line in enumerate(text_lines):
        if line.strip() == "לכבוד":
            out.append(Detection(text="לכבוד", kind="title"))
            for offset, kind in [(1, "name"), (2, "street"), (3, "city")]:
                if i + offset < len(text_lines):
                    addr = text_lines[i + offset].strip()
                    if addr:
                        out.append(Detection(text=addr, kind=kind))
            break

    seen: set[tuple[str, str]] = set()
    result: list[Detection] = []
    for d in out:
        key = (d.kind, d.text)
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result
