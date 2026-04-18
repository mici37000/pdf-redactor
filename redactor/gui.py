"""PyQt6 GUI: drop PDFs, click Redact to process, click Clean to clear list."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QThread, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .redact import RedactionResult, redact_pdf


ICON_PATH = Path(__file__).parent / "assets" / "icon.svg"


def app_icon() -> QIcon:
    return QIcon(str(ICON_PATH))


STATE_ROLE = Qt.ItemDataRole.UserRole + 1

STATE_QUEUED = "queued"
STATE_PROCESSING = "processing"
STATE_DONE = "done"
STATE_ERROR = "error"


@dataclass
class JobOutcome:
    input_path: Path
    ok: bool
    message: str
    result: RedactionResult | None = None


class RedactionWorker(QThread):
    finished_one = pyqtSignal(object)  # JobOutcome
    all_done = pyqtSignal()

    def __init__(self, paths: list[Path], output_dir: Path) -> None:
        super().__init__()
        self._paths = paths
        self._output_dir = output_dir

    def run(self) -> None:
        for p in self._paths:
            try:
                out_path = self._output_dir / p.name
                result = redact_pdf(p, output_path=out_path)
                msg = (
                    f"{result.total_detections} items redacted "
                    f"({result.total_rects} regions) → {result.output_path}"
                )
                self.finished_one.emit(JobOutcome(p, True, msg, result))
            except Exception as e:  # noqa: BLE001
                self.finished_one.emit(JobOutcome(p, False, f"{type(e).__name__}: {e}"))
        self.all_done.emit()


class DropZone(QLabel):
    files_dropped = pyqtSignal(list)  # list[Path]

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setText("Drop PDF files here, then click Redact")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(14)
        self.setFont(font)
        self.setMinimumHeight(160)
        self._set_idle_style()

    def _set_idle_style(self) -> None:
        self.setStyleSheet(
            "QLabel { border: 2px dashed #888; border-radius: 10px; "
            "background: #fafafa; color: #333; padding: 24px; }"
        )

    def _set_hover_style(self) -> None:
        self.setStyleSheet(
            "QLabel { border: 2px dashed #2a7; border-radius: 10px; "
            "background: #eaffea; color: #222; padding: 24px; }"
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(
            u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            self._set_hover_style()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001
        self._set_idle_style()

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_idle_style()
        paths = [
            Path(u.toLocalFile())
            for u in event.mimeData().urls()
            if u.toLocalFile().lower().endswith(".pdf")
        ]
        if paths:
            self.files_dropped.emit(paths)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Redactor")
        self.setWindowIcon(app_icon())
        self.resize(760, 600)

        self._settings = QSettings("PDFRedactor", "PDFRedactor")

        self._drop = DropZone()
        self._drop.files_dropped.connect(self._add_files)

        self._list = QListWidget()

        self._out_dir_edit = QLineEdit()
        self._out_dir_edit.setReadOnly(True)
        self._out_dir_edit.setPlaceholderText("No folder selected — click Browse…")
        saved = self._settings.value("output_dir", "", type=str)
        if saved and Path(saved).is_dir():
            self._out_dir_edit.setText(saved)

        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._browse_output_dir)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Save to:"))
        out_row.addWidget(self._out_dir_edit, stretch=1)
        out_row.addWidget(self._browse_btn)

        self._redact_btn = QPushButton("Redact")
        self._redact_btn.setMinimumHeight(36)
        self._redact_btn.setStyleSheet("QPushButton { font-weight: bold; }")
        self._redact_btn.clicked.connect(self._redact)

        self._clean_btn = QPushButton("Clean")
        self._clean_btn.setMinimumHeight(36)
        self._clean_btn.clicked.connect(self._clean)

        buttons = QHBoxLayout()
        buttons.addWidget(self._redact_btn)
        buttons.addWidget(self._clean_btn)

        self._status = QLabel("Drop PDF files to queue them.")

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._drop)
        layout.addLayout(out_row)
        layout.addWidget(QLabel("Files:"))
        layout.addWidget(self._list, stretch=1)
        layout.addLayout(buttons)
        layout.addWidget(self._status)
        self.setCentralWidget(root)

        self._worker: RedactionWorker | None = None
        self._refresh_status()

    def _output_dir(self) -> Path | None:
        text = self._out_dir_edit.text().strip()
        if not text:
            return None
        p = Path(text)
        return p if p.is_dir() else None

    def _browse_output_dir(self) -> None:
        start = self._out_dir_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose folder to save redacted PDFs", start
        )
        if chosen:
            self._out_dir_edit.setText(chosen)
            self._settings.setValue("output_dir", chosen)

    def _add_files(self, paths: list[Path]) -> None:
        existing = {
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
        }
        for p in paths:
            if str(p) in existing:
                continue
            item = QListWidgetItem(f"⏳ {p.name} — queued")
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            item.setData(STATE_ROLE, STATE_QUEUED)
            self._list.addItem(item)
        self._refresh_status()

    def _queued_items(self) -> list[tuple[int, Path]]:
        result = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(STATE_ROLE) == STATE_QUEUED:
                result.append((i, Path(item.data(Qt.ItemDataRole.UserRole))))
        return result

    def _redact(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        queued = self._queued_items()
        if not queued:
            self._status.setText("Nothing to redact — drop some PDF files first.")
            return

        output_dir = self._output_dir()
        if output_dir is None:
            self._status.setText("Choose an output folder first (click Browse…).")
            return

        out_resolved = output_dir.resolve()
        conflicts = [p.name for _, p in queued if p.parent.resolve() == out_resolved]
        if conflicts:
            listed = ", ".join(conflicts[:3]) + ("…" if len(conflicts) > 3 else "")
            self._status.setText(
                f"Cannot save to the same folder as source file(s): {listed}. "
                "Pick a different output folder."
            )
            return

        for idx, p in queued:
            item = self._list.item(idx)
            item.setText(f"⏳ {p.name} — processing…")
            item.setData(STATE_ROLE, STATE_PROCESSING)

        self._redact_btn.setEnabled(False)
        self._status.setText(f"Redacting {len(queued)} file(s)…")

        self._worker = RedactionWorker([p for _, p in queued], output_dir)
        self._worker.finished_one.connect(self._on_job_done)
        self._worker.all_done.connect(self._on_batch_done)
        self._worker.start()

    def _clean(self) -> None:
        self._list.clear()
        self._refresh_status()

    def _on_job_done(self, outcome: JobOutcome) -> None:
        target = str(outcome.input_path)
        for i in range(self._list.count()):
            item = self._list.item(i)
            if (
                item.data(Qt.ItemDataRole.UserRole) == target
                and item.data(STATE_ROLE) == STATE_PROCESSING
            ):
                if outcome.ok:
                    item.setText(f"✅ {outcome.input_path.name} — {outcome.message}")
                    item.setData(STATE_ROLE, STATE_DONE)
                else:
                    item.setText(f"❌ {outcome.input_path.name} — {outcome.message}")
                    item.setData(STATE_ROLE, STATE_ERROR)
                break

    def _on_batch_done(self) -> None:
        self._redact_btn.setEnabled(True)
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        queued = sum(
            1
            for i in range(self._list.count())
            if self._list.item(i).data(STATE_ROLE) == STATE_QUEUED
        )
        done = sum(
            1
            for i in range(self._list.count())
            if self._list.item(i).data(STATE_ROLE) == STATE_DONE
        )
        if queued and done:
            self._status.setText(f"{queued} queued · {done} done. Click Redact to process queued files.")
        elif queued:
            self._status.setText(f"{queued} file(s) queued. Click Redact to process.")
        elif done:
            self._status.setText(f"{done} file(s) redacted. Drop more or click Clean to reset.")
        else:
            self._status.setText("Drop PDF files to queue them.")


def run() -> int:
    import sys

    app = QApplication(sys.argv)
    app.setApplicationName("PDF Redactor")
    app.setWindowIcon(app_icon())
    win = MainWindow()
    win.show()
    return app.exec()
