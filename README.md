# PDF Redactor

Drag-and-drop desktop app that redacts PII from PDF reports.

Detects and removes participant name, Israeli ID (with checksum validation),
account number, employer name — plus their label keywords, so a reader
can't tell that redaction happened.

## Requirements

- Python 3.10+
- Linux, macOS, or Windows (desktop launcher auto-installed on Linux)

## Install

```bash
./setup.sh
```

This creates `.venv/`, installs dependencies, and — on Linux — installs a
desktop launcher under `~/.local/share/applications/`.

Manual alternative:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Run

Launch "PDF Redactor" from your application menu, or from a terminal:

```bash
.venv/bin/python -m redactor                      # GUI
.venv/bin/python -m redactor <input.pdf> <out>    # CLI, redact one file
```

Drop one or more PDFs into the window, choose an output folder with
**Browse…**, then click **Redact**. Output files keep the original filename
and are written to the chosen folder (never the source folder, to avoid
overwriting originals).

## Uninstall

```bash
rm -rf .venv ~/.local/share/applications/pdf-redactor.desktop
```
