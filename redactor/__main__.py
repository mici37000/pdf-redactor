"""Entry point: run the GUI, or redact a single file from the CLI.

Usage:
    python -m redactor                              # launch GUI
    python -m redactor <file.pdf> <output_dir>      # redact one file
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) > 1:
        if len(sys.argv) < 3:
            print("Usage: python -m redactor <input.pdf> <output_dir>", file=sys.stderr)
            return 2

        from .redact import redact_pdf

        path = Path(sys.argv[1])
        output_dir = Path(sys.argv[2])
        result = redact_pdf(path, output_path=output_dir / path.name)
        print(f"Input:  {result.input_path}")
        print(f"Output: {result.output_path}")
        print(f"Detections: {result.total_detections} ({result.total_rects} regions)")
        for p in result.pages:
            for d in p.detections:
                print(f"  page {p.page_index} [{d.kind}] {d.text!r}")
        return 0

    from .gui import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
