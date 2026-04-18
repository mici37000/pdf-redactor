"""PII detectors for financial reports.

Each detector returns a list of strings to be located and redacted on the page.
The strings must match the *rendered* PDF text exactly so PyMuPDF's search_for
can find their rectangles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Detection:
    text: str           # exact substring to search for in the PDF
    kind: str           # "title" | "name" | "id" | "account" | "employer"


# Each pattern has group(1) = title keyword (redacted too), group(2) = value.
_NAME_RE = re.compile(r"(שם\s*העמית\s*:?)\s*([^\n\r]+?)(?=\s*(?:מספר|ת\.?\s*ז|$))")
_EMPLOYER_RE = re.compile(r"(שם\s*המעסיק\s*:?)\s*([^\n\r]+?)(?=\s*$)", re.MULTILINE)
_ACCOUNT_RE = re.compile(r"(מספר\s*חשבון\s*:?)\s*(\d{4,12})")
# Matches "NNNNNNNN/N" or "NNNNNNNNN" after a ת.ז. anchor.
_ID_RE = re.compile(r"((?:מספר\s*)?ת\.?\s*ז\.?)\s*[:\s]*?(\d{8}\s*/\s*\d|\d{9})")


def _valid_israeli_id(digits: str) -> bool:
    """Teudat Zehut checksum: weights 1,2,1,2,... summed with digit-of-sum per term."""
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (1 if i % 2 == 0 else 2)
        total += n if n < 10 else n - 9
    return total % 10 == 0


def detect(text: str) -> list[Detection]:
    """Extract PII strings (and their title labels) from a PDF page's text."""
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

    for m in _ID_RE.finditer(text):
        title = m.group(1).strip()
        raw = m.group(2)
        digits = raw.replace("/", "").replace(" ", "")
        if _valid_israeli_id(digits):
            out.append(Detection(text=title, kind="title"))
            out.append(Detection(text=raw, kind="id"))

    return _dedupe(out)


def _dedupe(items: list[Detection]) -> list[Detection]:
    seen: set[tuple[str, str]] = set()
    result: list[Detection] = []
    for d in items:
        key = (d.kind, d.text)
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result
