"""PDF redaction with PyMuPDF.

We add `redact` annotations filled with white (1, 1, 1), then apply them —
this removes the underlying text from the content stream, not just covers it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

from .detectors import Detection, detect


WHITE = (1.0, 1.0, 1.0)


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

    @property
    def total_detections(self) -> int:
        return sum(len(p.detections) for p in self.pages)

    @property
    def total_rects(self) -> int:
        return sum(p.rects_found for p in self.pages)


def redact_pdf(input_path: Path, output_path: Path) -> RedactionResult:
    """Redact PII in the given PDF and write to `output_path`.

    Refuses to overwrite the source file — the caller must provide an
    `output_path` that resolves to a different location than `input_path`.
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
    page_results: list[PageResult] = []

    try:
        for i, page in enumerate(doc):
            text = page.get_text()
            detections = detect(text)
            rects_found = 0
            for d in detections:
                rects = page.search_for(d.text)
                for rect in rects:
                    page.add_redact_annot(rect, fill=WHITE)
                    rects_found += 1
            if detections:
                page.apply_redactions()
            page_results.append(
                PageResult(page_index=i, detections=detections, rects_found=rects_found)
            )

        doc.save(output_path, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    return RedactionResult(
        input_path=input_path, output_path=output_path, pages=page_results
    )
