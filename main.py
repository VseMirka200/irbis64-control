from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Event

from PyQt6.QtCore import QObject, QRect, QSize, QStandardPaths, QThread, QTimer, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from matcher import (
    DEFAULT_AGE_MARKER,
    DEFAULT_AGE_MARKER_FIELD,
    DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    DEFAULT_SUBSTANCE_MARKER,
    DEFAULT_SUBSTANCE_MARKER_FIELD,
    ComparisonCancelled,
    ComparisonSummary,
    MatchResult,
    apply_markers_to_tag_values,
    build_markers_by_record,
    compare_and_export,
    compare_database_records,
    database_record_from_tag_values,
    export_results,
    remove_database_markers,
    remove_markers_from_tag_values,
)
from result_diff import ResultDiffRow, ResultDiffSummary, compare_result_files, compare_text_files
from irbis_bridge import IrbisClient, IrbisError, IrbisField, IrbisRecord, apply_modified_snapshot, create_irbis_snapshot, load_manifest
from ui_locale import install_russian_ui


def resource_path(*parts: str) -> str:
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, *parts)


APP_TITLE = "ИРБИС64 Контроль"
APP_VERSION = "1.0.0"
GITHUB_REPO_URL = "https://github.com/VseMirka200/irbis64-control"
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/VseMirka200/irbis64-control/releases/latest"


def apply_light_palette(app: QApplication) -> None:
    """Принудительно использует светлую системную палитру независимо от темы Windows."""
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except Exception:
        pass

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f5f5f5"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f2f2f2"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#0067c0"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0078d4"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6b6b6b"))
    palette.setColor(QPalette.ColorRole.Light, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Midlight, QColor("#e4e4e4"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#5f6368"))
    palette.setColor(QPalette.ColorRole.Dark, QColor("#4a4a4a"))
    palette.setColor(QPalette.ColorRole.Shadow, QColor("#000000"))

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#686868"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#686868"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#686868"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor("#ededed"))

    app.setPalette(palette)


def app_data_dir() -> Path:
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def database_connector_config_path() -> Path:
    return app_data_dir() / "database_connector.json"


def window_state_path() -> Path:
    return app_data_dir() / "window_state.json"


def run_journal_path() -> Path:
    return app_data_dir() / "run_journal.log"


def _version_parts(value: str) -> tuple[int, ...]:
    normalized = value.strip().lstrip("vV")
    parts = re.findall(r"\d+", normalized)
    return tuple(int(part) for part in parts) if parts else (0,)


def _is_newer_version(latest: str, current: str) -> bool:
    latest_parts = _version_parts(latest)
    current_parts = _version_parts(current)
    length = max(len(latest_parts), len(current_parts))
    latest_parts += (0,) * (length - len(latest_parts))
    current_parts += (0,) * (length - len(current_parts))
    return latest_parts > current_parts


class ProgressDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(resource_path("assets", "irbis64_control.ico")))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(460, 260)
        self.setMinimumSize(360, 220)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        self.status_label = QLabel("Ожидание...")
        root.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.text_edit = QTextEdit()
        self.text_edit.setObjectName("plainLogEdit")
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText("Здесь будет отображаться ход выполнения.")
        root.addWidget(self.text_edit, 1)

        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.hide)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(self.close_button)
        root.addLayout(bottom)

    def start(self, text: str) -> None:
        self.clear()
        self.set_running(True)
        self.set_progress(0, text)
        self.show()
        self.raise_()
        self.activateWindow()
        QApplication.processEvents()

    def set_running(self, running: bool) -> None:
        self.close_button.setEnabled(not running)

    def set_progress(self, percent: int, text: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.status_label.setText(text)

    def append_line(self, text: str) -> None:
        self.text_edit.append(text)
        QApplication.processEvents()

    def clear(self) -> None:
        self.text_edit.clear()
        self.progress.setValue(0)
        self.status_label.setText("Ожидание...")

    def finish(self, text: str, percent: int = 100) -> None:
        self.set_progress(percent, text)
        self.set_running(False)
        self.append_line(text)

    def closeEvent(self, event) -> None:
        if self.close_button.isEnabled():
            event.accept()
        else:
            event.ignore()


class ComparisonWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal(str)

    def __init__(
        self,
        database_path: list[str],
        foreign_agents_path: str,
        excel_paths: list[str],
        output_path: str,
        modified_database_path: str,
        use_isbn_matching: bool,
        use_title_fallback: bool,
        use_fuzzy: bool,
        fuzzy_threshold: int,
        report_options: dict[str, object],
        substance_marker: str,
        foreign_agent_marker_template: str,
        age_marker: str,
        substance_marker_field: int,
        foreign_agent_marker_field: int,
        age_marker_field: int,
    ) -> None:
        super().__init__()
        self.database_path = database_path
        self.foreign_agents_path = foreign_agents_path
        self.excel_paths = excel_paths
        self.output_path = output_path
        self.modified_database_path = modified_database_path
        self.use_isbn_matching = use_isbn_matching
        self.use_title_fallback = use_title_fallback
        self.use_fuzzy = use_fuzzy
        self.fuzzy_threshold = fuzzy_threshold
        self.report_options = dict(report_options)
        self.substance_marker = substance_marker
        self.foreign_agent_marker_template = foreign_agent_marker_template
        self.age_marker = age_marker
        self.substance_marker_field = substance_marker_field
        self.foreign_agent_marker_field = foreign_agent_marker_field
        self.age_marker_field = age_marker_field
        self.cancel_event = Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            results, summary = compare_and_export(
                self.database_path,
                self.excel_paths,
                self.output_path,
                self.modified_database_path,
                foreign_agents_path=self.foreign_agents_path or None,
                use_isbn_matching=self.use_isbn_matching,
                use_title_fallback=self.use_title_fallback,
                use_fuzzy=self.use_fuzzy,
                fuzzy_threshold=self.fuzzy_threshold,
                report_options=self.report_options,
                substance_marker=self.substance_marker,
                foreign_agent_marker_template=self.foreign_agent_marker_template,
                age_marker=self.age_marker,
                substance_marker_field=self.substance_marker_field,
                foreign_agent_marker_field=self.foreign_agent_marker_field,
                age_marker_field=self.age_marker_field,
                progress_cb=lambda percent, text: self.progress.emit(percent, text),
                cancel_cb=self.cancel_event.is_set,
            )
            self.finished.emit(results, summary)
        except ComparisonCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc())


class DirectIrbisComparisonWorker(QObject):
    """Сверяет и изменяет записи прямо на сервере ИРБИС без TXT-снимка."""

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal(str)

    def __init__(
        self,
        *,
        host: str,
        port: int,
        login: str,
        password: str,
        database: str,
        query: str,
        page_size: int,
        foreign_agents_path: str,
        excel_paths: list[str],
        output_path: str,
        use_isbn_matching: bool,
        use_title_fallback: bool,
        use_fuzzy: bool,
        fuzzy_threshold: int,
        report_options: dict[str, object],
        substance_marker: str,
        foreign_agent_marker_template: str,
        age_marker: str,
        substance_marker_field: int,
        foreign_agent_marker_field: int,
        age_marker_field: int,
        backup_dir: str,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = int(port)
        self.login = login
        self.password = password
        self.database = database
        self.query = query or "I=$"
        self.page_size = max(100, min(int(page_size or 500), 2000))
        self.foreign_agents_path = foreign_agents_path
        self.excel_paths = list(excel_paths)
        self.output_path = output_path
        self.use_isbn_matching = use_isbn_matching
        self.use_title_fallback = use_title_fallback
        self.use_fuzzy = use_fuzzy
        self.fuzzy_threshold = fuzzy_threshold
        self.report_options = dict(report_options)
        self.substance_marker = substance_marker
        self.foreign_agent_marker_template = foreign_agent_marker_template
        self.age_marker = age_marker
        self.substance_marker_field = int(substance_marker_field)
        self.foreign_agent_marker_field = int(foreign_agent_marker_field)
        self.age_marker_field = int(age_marker_field)
        self.backup_dir = Path(backup_dir)
        self.cancel_event = Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def _cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @staticmethod
    def _raw_record(record: IrbisRecord) -> str:
        return "\n".join(f"#{field.tag:03d}: {field.value}" for field in record.fields)

    def _save_rollback(self, records: list[IrbisRecord]) -> Path | None:
        if not records:
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.backup_dir / f"direct_irbis_{self.database}_{stamp}.json"
        payload = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "records": [
                {
                    "mfn": record.mfn,
                    "status": record.status,
                    "version": record.version,
                    "fields": [{"tag": field.tag, "value": field.value} for field in record.fields],
                }
                for record in records
            ],
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @pyqtSlot()
    def run(self) -> None:
        try:
            client = IrbisClient(
                self.host,
                self.port,
                self.login,
                self.password,
                "C",
                timeout=30,
            )
            with client as connected:
                self.progress.emit(2, f"Подключение к ИРБИС • база {self.database}")
                irbis_records = connected.search_read_all(
                    self.database,
                    self.query,
                    page_size=self.page_size,
                    progress_cb=lambda percent, message: self.progress.emit(percent, message),
                    cancel_cb=self._cancelled,
                )
                if self._cancelled():
                    raise ComparisonCancelled("Операция отменена пользователем")
                if not irbis_records:
                    raise RuntimeError("По запросу ИРБИС не найдено ни одной записи.")

                self.progress.emit(49, f"Подготовка к сравнению: {len(irbis_records):,} записей")
                scan_versions = {record.mfn: record.version for record in irbis_records}
                source_label = f"ИРБИС://{self.host}:{self.port}/{self.database}"
                records = [
                    database_record_from_tag_values(
                        record.mfn,
                        ((field.tag, field.value) for field in record.fields),
                        source_file=source_label,
                        source_record_number=record.mfn,
                        raw_record="",
                    )
                    for record in irbis_records
                ]
                # Полные серверные записи больше не нужны в памяти: перед записью
                # каждый изменяемый MFN всё равно перечитывается для проверки версии.
                del irbis_records

                results, summary = compare_database_records(
                    records,
                    self.excel_paths,
                    database_label=source_label,
                    foreign_agents_path=self.foreign_agents_path or None,
                    use_isbn_matching=self.use_isbn_matching,
                    use_title_fallback=self.use_title_fallback,
                    use_fuzzy=self.use_fuzzy,
                    fuzzy_threshold=self.fuzzy_threshold,
                    progress_cb=lambda percent, message: self.progress.emit(percent, message),
                    cancel_cb=self._cancelled,
                )
                if self.report_options.get("enabled", True):
                    export_results(
                        self.output_path,
                        results,
                        summary,
                        progress_cb=lambda percent, message: self.progress.emit(percent, message),
                        cancel_cb=self._cancelled,
                        report_options=self.report_options,
                    )

                if self.report_options.get("report_only", False):
                    summary.modified_database_file = ""
                    summary.modified_database_records = 0
                    self.progress.emit(100, "Отчёт создан, записи ИРБИС не изменялись")
                    self.finished.emit(results, summary)
                    return

                markers_by_mfn = build_markers_by_record(
                    results,
                    substance_marker=self.substance_marker,
                    foreign_agent_marker_template=self.foreign_agent_marker_template,
                    substance_marker_field=self.substance_marker_field,
                    foreign_agent_marker_field=self.foreign_agent_marker_field,
                )
                if self._cancelled():
                    raise ComparisonCancelled("Операция отменена пользователем")

                pending: list[IrbisRecord] = []
                rollback_records: list[IrbisRecord] = []
                conflicts = 0
                total_candidates = len(markers_by_mfn)
                for index, mfn in enumerate(sorted(markers_by_mfn), start=1):
                    if self._cancelled():
                        raise ComparisonCancelled("Операция отменена пользователем")
                    live = connected.read_record(self.database, int(mfn))
                    scanned_version = scan_versions.get(int(mfn))
                    if scanned_version is not None and live.version != scanned_version:
                        conflicts += 1
                        continue
                    tag_values, changed = apply_markers_to_tag_values(
                        ((field.tag, field.value) for field in live.fields),
                        markers_by_mfn[mfn],
                        age_marker=self.age_marker,
                        age_marker_field=self.age_marker_field,
                    )
                    if changed:
                        rollback_records.append(live)
                        pending.append(
                            IrbisRecord(
                                live.mfn,
                                live.status,
                                live.version,
                                [IrbisField(tag, value) for tag, value in tag_values],
                            )
                        )
                    if index == total_candidates or index % 25 == 0:
                        self.progress.emit(
                            92 + int(index / max(total_candidates, 1) * 4),
                            f"Проверка найденных MFN перед записью: {index:,} из {total_candidates:,}",
                        )

                backup = self._save_rollback(rollback_records)
                written = 0
                readback_repairs = 0
                # После создания rollback-копии запись выполняется до конца: остановка
                # посередине оставила бы базу частично изменённой.
                # Важно: после каждой записи перечитываем MFN с сервера. Это защищает
                # от ситуации, когда визуально одинаковые повторения 333 появились уже
                # на серверной стороне/из старой версии программы.
                for index, record in enumerate(pending, start=1):
                    connected.write_record(self.database, record, actualize=1)

                    readback = connected.read_record(self.database, record.mfn)
                    verified_values, needs_repair = apply_markers_to_tag_values(
                        ((field.tag, field.value) for field in readback.fields),
                        markers_by_mfn.get(record.mfn, []),
                        age_marker=self.age_marker,
                        age_marker_field=self.age_marker_field,
                    )
                    if needs_repair:
                        repaired = IrbisRecord(
                            readback.mfn,
                            readback.status,
                            readback.version,
                            [IrbisField(tag, value) for tag, value in verified_values],
                        )
                        connected.write_record(self.database, repaired, actualize=1)
                        readback_repairs += 1

                        # Контрольный read-back: молча оставлять дубль нельзя.
                        final_record = connected.read_record(self.database, record.mfn)
                        _final_values, still_needs_repair = apply_markers_to_tag_values(
                            ((field.tag, field.value) for field in final_record.fields),
                            markers_by_mfn.get(record.mfn, []),
                            age_marker=self.age_marker,
                            age_marker_field=self.age_marker_field,
                        )
                        if still_needs_repair:
                            raise IrbisError(
                                f"MFN {record.mfn}: сервер ИРБИС повторно вернул дублирующую метку после исправления."
                            )

                    written += 1
                    self.progress.emit(
                        96 + int(index / max(len(pending), 1) * 4),
                        f"Запись изменений в ИРБИС: {index:,} из {len(pending):,}",
                    )

                summary.modified_database_file = source_label
                summary.modified_database_records = written
                if conflicts:
                    summary.warnings.append(
                        f"Пропущено записей, изменённых на сервере во время проверки: {conflicts}. "
                        "Для них запустите проверку ещё раз."
                    )
                if backup:
                    summary.warnings.append(f"Rollback-копия серверных записей: {backup}")
                if readback_repairs:
                    summary.warnings.append(
                        f"После контрольного чтения автоматически исправлено дублей меток: {readback_repairs}."
                    )
                suffix = f"; конфликтов версий: {conflicts}" if conflicts else ""
                self.progress.emit(100, f"Готово: изменено записей ИРБИС — {written:,}{suffix}")
                self.finished.emit(results, summary)
        except ComparisonCancelled as exc:
            self.cancelled.emit(str(exc))
        except IrbisError as exc:
            if self._cancelled():
                self.cancelled.emit("Операция отменена пользователем")
            else:
                self.failed.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc())


class IrbisOperationWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(self, mode: str, params: dict[str, object]) -> None:
        super().__init__()
        self.mode = mode
        self.params = dict(params)

    @pyqtSlot()
    def run(self) -> None:
        try:
            client = IrbisClient(
                str(self.params.get("host", "127.0.0.1")),
                int(self.params.get("port", 6666)),
                str(self.params.get("login", "")),
                str(self.params.get("password", "")),
                "C",
                timeout=20,
            )
            if self.mode in {"test", "databases", "tune_read"}:
                self.progress.emit(20, "Подключение к серверу ИРБИС…")
                with client as connected:
                    databases = []
                    if self.mode in {"test", "databases"}:
                        self.progress.emit(45, "Получение списка доступных баз…")
                        databases = connected.list_databases()
                    page_size = int(self.params.get("page_size", 500) or 500)
                    total = 0
                    database = str(self.params.get("database", "")).strip()
                    available_names = [
                        str(item.get("name", "") if isinstance(item, dict) else item).strip()
                        for item in databases
                    ]
                    if available_names and database.casefold() not in {
                        name.casefold() for name in available_names
                    }:
                        database = available_names[0]
                    if self.mode == "tune_read":
                        if not database:
                            raise RuntimeError("Не выбрана база ИРБИС для теста пакета чтения.")
                        page_size, total = connected.tune_read_page_size(
                            database,
                            str(self.params.get("query", "I=$")),
                            progress_cb=lambda percent, message: self.progress.emit(percent, message),
                        )
                    if self.mode == "tune_read":
                        self.progress.emit(100, f"Пакет чтения подобран: {page_size} записей")
                    else:
                        self.progress.emit(100, f"Доступных баз: {len(databases)}")
                self.finished.emit(
                    self.mode,
                    {
                        "ok": True,
                        "databases": databases,
                        "page_size": page_size,
                        "probe_total": total,
                        "probe_database": database,
                    },
                )
                return

            if self.mode == "clean_markers":
                database = str(self.params.get("database", "")).strip()
                if not database:
                    raise RuntimeError("Не выбрана база ИРБИС для очистки меток.")
                page_size = max(100, min(int(self.params.get("page_size", 500) or 500), 2000))
                query = "I=$"
                candidates: list[int] = []

                with client as connected:
                    first = 1
                    total: int | None = None
                    scanned = 0
                    self.progress.emit(2, f"Поиск меток в базе {database}…")
                    while total is None or scanned < total:
                        current_total, page = connected.search_read_page(
                            database, query, number=page_size, first=first
                        )
                        if total is None:
                            total = current_total
                            if total <= 0:
                                break
                        if not page:
                            break
                        for record in page:
                            _cleaned, changed = remove_markers_from_tag_values(
                                ((field.tag, field.value) for field in record.fields),
                                substance_marker=str(self.params.get("substance_marker", DEFAULT_SUBSTANCE_MARKER)),
                                foreign_agent_marker_template=str(self.params.get("foreign_agent_marker_template", DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE)),
                                age_marker=str(self.params.get("age_marker", DEFAULT_AGE_MARKER)),
                                substance_marker_field=int(self.params.get("substance_marker_field", DEFAULT_SUBSTANCE_MARKER_FIELD)),
                                foreign_agent_marker_field=int(self.params.get("foreign_agent_marker_field", DEFAULT_FOREIGN_AGENT_MARKER_FIELD)),
                                age_marker_field=int(self.params.get("age_marker_field", DEFAULT_AGE_MARKER_FIELD)),
                            )
                            if changed:
                                candidates.append(record.mfn)
                        scanned += len(page)
                        first += len(page)
                        self.progress.emit(
                            min(55, 5 + int(scanned / max(total or 1, 1) * 50)),
                            f"Поиск меток: {scanned:,} из {total:,} • найдено записей: {len(candidates):,}",
                        )
                        if len(page) < page_size:
                            break

                    if not candidates:
                        self.progress.emit(100, "Метки для удаления не найдены.")
                        self.finished.emit(
                            self.mode,
                            {"scanned": scanned, "found": 0, "written": 0, "backup": ""},
                        )
                        return

                    rollback_records: list[IrbisRecord] = []
                    pending: list[IrbisRecord] = []
                    for index, mfn in enumerate(candidates, start=1):
                        # Перечитываем только найденные MFN непосредственно перед
                        # изменением, чтобы не затереть правки другого пользователя.
                        live = connected.read_record(database, int(mfn))
                        cleaned_values, changed = remove_markers_from_tag_values(
                            ((field.tag, field.value) for field in live.fields),
                            substance_marker=str(self.params.get("substance_marker", DEFAULT_SUBSTANCE_MARKER)),
                            foreign_agent_marker_template=str(self.params.get("foreign_agent_marker_template", DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE)),
                            age_marker=str(self.params.get("age_marker", DEFAULT_AGE_MARKER)),
                            substance_marker_field=int(self.params.get("substance_marker_field", DEFAULT_SUBSTANCE_MARKER_FIELD)),
                            foreign_agent_marker_field=int(self.params.get("foreign_agent_marker_field", DEFAULT_FOREIGN_AGENT_MARKER_FIELD)),
                            age_marker_field=int(self.params.get("age_marker_field", DEFAULT_AGE_MARKER_FIELD)),
                        )
                        if changed:
                            rollback_records.append(live)
                            pending.append(
                                IrbisRecord(
                                    live.mfn, live.status, live.version,
                                    [IrbisField(tag, value) for tag, value in cleaned_values],
                                )
                            )
                        if index == len(candidates) or index % 25 == 0:
                            self.progress.emit(
                                55 + int(index / max(len(candidates), 1) * 20),
                                f"Проверка найденных MFN: {index:,} из {len(candidates):,}",
                            )

                    backup_path: Path | None = None
                    if rollback_records:
                        backup_dir = Path(str(self.params.get("backup_dir", app_data_dir() / "backups")))
                        backup_dir.mkdir(parents=True, exist_ok=True)
                        backup_path = backup_dir / f"irbis_cleanup_{database}_{datetime.now():%Y%m%d_%H%M%S}.json"
                        backup_path.write_text(
                            json.dumps(
                                {
                                    "created_at": datetime.now().isoformat(timespec="seconds"),
                                    "host": str(self.params.get("host", "")),
                                    "port": int(self.params.get("port", 6666)),
                                    "database": database,
                                    "operation": "marker_cleanup",
                                    "records": [
                                        {
                                            "mfn": record.mfn,
                                            "status": record.status,
                                            "version": record.version,
                                            "fields": [
                                                {"tag": field.tag, "value": field.value}
                                                for field in record.fields
                                            ],
                                        }
                                        for record in rollback_records
                                    ],
                                },
                                ensure_ascii=False, indent=2,
                            ),
                            encoding="utf-8",
                        )

                    written = 0
                    # Сначала создана rollback-копия, затем меняем живую базу.
                    for index, record in enumerate(pending, start=1):
                        connected.write_record(database, record, actualize=1)
                        written += 1
                        self.progress.emit(
                            75 + int(index / max(len(pending), 1) * 25),
                            f"Очистка меток в ИРБИС: {index:,} из {len(pending):,}",
                        )

                self.finished.emit(
                    self.mode,
                    {
                        "scanned": scanned,
                        "found": len(candidates),
                        "written": written,
                        "backup": str(backup_path) if backup_path else "",
                    },
                )
                return

            if self.mode == "fetch":
                with client as connected:
                    manifest = create_irbis_snapshot(
                        connected,
                        str(self.params["database"]),
                        str(self.params["query"]),
                        str(self.params["snapshot"]),
                        str(self.params["manifest"]),
                        progress_cb=lambda percent, text: self.progress.emit(percent, text),
                        read_workers=int(self.params.get("read_workers", 4)),
                    )
                self.finished.emit(self.mode, manifest)
                return

            if self.mode == "apply":
                with client as connected:
                    written, conflicts, backup = apply_modified_snapshot(
                        connected,
                        Path(str(self.params["manifest"])),
                        Path(str(self.params["modified"])),
                        Path(str(self.params["backup_dir"])),
                        progress_cb=lambda percent, text: self.progress.emit(percent, text),
                    )
                self.finished.emit(
                    self.mode,
                    {"written": written, "conflicts": conflicts, "backup": str(backup) if backup else ""},
                )
                return

            raise RuntimeError(f"Неизвестная операция ИРБИС: {self.mode}")
        except Exception as exc:
            self.failed.emit(self.mode, str(exc))


class CompactTabWidget(QTabWidget):
    """A tab container that does not inherit the widest page as its minimum."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self._current_page_changed)

    def minimumSizeHint(self) -> QSize:
        return QSize(320, 240)

    def sizeHint(self) -> QSize:
        current = self.currentWidget()
        if current is None:
            return super().sizeHint()
        page_hint = current.sizeHint()
        tab_height = self.tabBar().sizeHint().height()
        return QSize(max(320, page_hint.width()), max(240, page_hint.height() + tab_height + 2))

    def _current_page_changed(self, _index: int) -> None:
        self.updateGeometry()
        if self.parentWidget() is not None:
            self.parentWidget().updateGeometry()


class LayoutHintWidget(QWidget):
    """Expose the current layout hint to a resizable scroll area."""

    def sizeHint(self) -> QSize:
        layout = self.layout()
        return layout.sizeHint() if layout is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        layout = self.layout()
        return layout.minimumSize() if layout is not None else super().minimumSizeHint()


class SectionCard(QFrame):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setObjectName("sectionCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(4, 4, 4, 4)
        self.outer_layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.title_row = title_row

        # В компактном стандартном интерфейсе декоративные иконки карточек
        # не показываем: остаётся обычный заголовок секции и системные контролы.
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        title_row.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.outer_layout.addLayout(title_row)

        self.description_label: QLabel | None = None
        if description:
            self.description_label = QLabel(description)
            self.description_label.setObjectName("cardDescription")
            self.description_label.setWordWrap(True)
            self.description_label.hide()
            self.outer_layout.addWidget(self.description_label)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 1, 0, 0)
        self.body.setSpacing(4)
        self.outer_layout.addLayout(self.body)

    def set_compact(self, compact: bool, very_compact: bool = False) -> None:
        horizontal = 3 if very_compact else 4
        vertical = 3 if compact else 4
        self.outer_layout.setContentsMargins(horizontal, vertical, horizontal, vertical)
        self.outer_layout.setSpacing(4)
        self.body.setSpacing(4)


DEFAULT_USEFUL_LINKS = [
    {
        "title": "Рекомендации по выявлению запрещённой литературы — РГБ",
        "url": "https://nkp.rsl.ru/drug-literature-recommendations",
    },
    {
        "title": "Экспертный совет Российского книжного союза",
        "url": "https://bookunion.ru/expert/",
    },
]


class UsefulLinksDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Полезные ссылки")
        self.setWindowIcon(QIcon(resource_path("assets", "irbis64_control.ico")))
        self.resize(620, 360)
        self.setMinimumSize(620, 300)
        self.links = self._load_links()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Полезные ссылки")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        description = QLabel(
            "Откройте нужный сайт двойным щелчком. В этот список можно добавлять свои ссылки."
        )
        description.setObjectName("cardDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("linksList")
        self.list_widget.setAlternatingRowColors(False)
        self.list_widget.setWordWrap(True)
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list_widget.setSpacing(2)
        self.list_widget.setUniformItemSizes(False)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.open_selected())
        layout.addWidget(self.list_widget, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        add_button = QPushButton("Добавить ссылку")
        add_button.setObjectName("secondaryButton")
        add_button.clicked.connect(self.add_link)
        buttons.addWidget(add_button)

        remove_button = QPushButton("Удалить выбранную")
        remove_button.setObjectName("dangerButton")
        remove_button.clicked.connect(self.remove_selected)
        buttons.addWidget(remove_button)

        buttons.addStretch()

        open_button = QPushButton("Открыть сайт")
        open_button.setObjectName("primaryButton")
        open_button.clicked.connect(self.open_selected)
        buttons.addWidget(open_button)
        layout.addLayout(buttons)

        transfer_buttons = QHBoxLayout()
        transfer_buttons.setSpacing(5)
        import_button = QPushButton("Импорт ссылок")
        import_button.setObjectName("secondaryButton")
        import_button.clicked.connect(self.import_links)
        transfer_buttons.addWidget(import_button)

        export_button = QPushButton("Экспорт ссылок")
        export_button.setObjectName("secondaryButton")
        export_button.clicked.connect(self.export_links)
        transfer_buttons.addWidget(export_button)
        transfer_buttons.addStretch()

        close_button = QPushButton("Закрыть")
        close_button.setObjectName("mutedButton")
        close_button.clicked.connect(self.accept)
        transfer_buttons.addWidget(close_button)

        dialog_buttons = (
            add_button,
            remove_button,
            open_button,
            import_button,
            export_button,
            close_button,
        )
        margins = layout.contentsMargins()
        three_button_row_width = (
            self.minimumWidth() - margins.left() - margins.right() - buttons.spacing() * 2
        ) // 3
        common_button_width = min(
            max(button.sizeHint().width() for button in dialog_buttons),
            three_button_row_width,
        )
        for button in dialog_buttons:
            button.setFixedWidth(common_button_width)
        layout.addLayout(transfer_buttons)

        self._refresh()

    @staticmethod
    def _storage_path() -> Path:
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "useful_links.json"

    def _load_links(self) -> list[dict[str, str]]:
        path = self._storage_path()
        if not path.is_file():
            return [dict(item) for item in DEFAULT_USEFUL_LINKS]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            links = []
            for item in data:
                title = str(item.get("title", "")).strip()
                url = str(item.get("url", "")).strip()
                if title and url:
                    links.append({"title": title, "url": url})
            return links or [dict(item) for item in DEFAULT_USEFUL_LINKS]
        except Exception:
            return [dict(item) for item in DEFAULT_USEFUL_LINKS]

    @staticmethod
    def _validated_links(data) -> list[dict[str, str]]:
        if not isinstance(data, list):
            raise ValueError("ожидался список ссылок")
        links: list[dict[str, str]] = []
        for number, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"элемент {number} должен быть объектом")
            title = str(item.get("title", "")).strip()
            address = str(item.get("url", "")).strip()
            url = QUrl.fromUserInput(address)
            if not title or not url.isValid() or url.scheme().lower() not in {"http", "https"} or not url.host():
                raise ValueError(f"у ссылки {number} отсутствует название или неверный адрес")
            normalized_url = url.toString()
            if not any(link["url"].rstrip("/") == normalized_url.rstrip("/") for link in links):
                links.append({"title": title, "url": normalized_url})
        if not links:
            raise ValueError("список ссылок пуст")
        return links

    def _save_links(self) -> bool:
        try:
            self._storage_path().write_text(
                json.dumps(self.links, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось сохранить список ссылок:\n{exc}")
            return False

    def _refresh(self) -> None:
        self.list_widget.clear()
        for link in self.links:
            item = QListWidgetItem(f"{link['title']}\n{link['url']}")
            item.setData(Qt.ItemDataRole.UserRole, link["url"])
            item.setToolTip(link["url"])
            item.setForeground(QColor("#000000"))
            item.setSizeHint(QSize(0, 62))
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def export_links(self) -> None:
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        default_path = str(Path(documents) / "Полезные ссылки ИРБИС64 Контроль.json")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт полезных ссылок",
            default_path,
            "JSON-файлы (*.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            Path(path).write_text(
                json.dumps(self.links, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось экспортировать ссылки:\n{exc}")
            return
        QMessageBox.information(self, APP_TITLE, f"Ссылки экспортированы:\n{path}")

    def import_links(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Импорт полезных ссылок",
            "",
            "JSON-файлы (*.json);;Все файлы (*.*)",
        )
        if not path:
            return
        try:
            imported = self._validated_links(json.loads(Path(path).read_text(encoding="utf-8")))
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось импортировать ссылки:\n{exc}")
            return
        answer = QMessageBox.question(
            self,
            APP_TITLE,
            f"Заменить текущий список импортированными ссылками ({len(imported)})?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        previous = self.links
        self.links = imported
        if self._save_links():
            self._refresh()
        else:
            self.links = previous

    def open_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            QMessageBox.information(self, APP_TITLE, "Выберите ссылку в списке.")
            return
        url = QUrl.fromUserInput(str(item.data(Qt.ItemDataRole.UserRole)))
        if not url.isValid() or url.scheme().lower() not in {"http", "https"}:
            QMessageBox.warning(self, APP_TITLE, "У ссылки неверный адрес.")
            return
        QDesktopServices.openUrl(url)

    def add_link(self) -> None:
        title, accepted = QInputDialog.getText(self, "Добавить ссылку", "Название сайта:")
        if not accepted or not title.strip():
            return
        address, accepted = QInputDialog.getText(
            self,
            "Добавить ссылку",
            "Адрес сайта:",
            text="https://",
        )
        if not accepted or not address.strip():
            return
        url = QUrl.fromUserInput(address.strip())
        if not url.isValid() or url.scheme().lower() not in {"http", "https"} or not url.host():
            QMessageBox.warning(self, APP_TITLE, "Введите полный адрес сайта, например https://example.ru")
            return
        normalized_url = url.toString()
        if any(item["url"].rstrip("/") == normalized_url.rstrip("/") for item in self.links):
            QMessageBox.information(self, APP_TITLE, "Эта ссылка уже есть в списке.")
            return
        self.links.append({"title": title.strip(), "url": normalized_url})
        if self._save_links():
            self._refresh()
            self.list_widget.setCurrentRow(self.list_widget.count() - 1)

    def remove_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            QMessageBox.information(self, APP_TITLE, "Выберите ссылку для удаления.")
            return
        link = self.links[row]
        answer = QMessageBox.question(
            self,
            APP_TITLE,
            f"Удалить ссылку «{link['title']}»?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.links.pop(row)
        if self._save_links():
            self._refresh()


class ResultComparisonDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сравнение старого и нового результата")
        self.setWindowIcon(QIcon(resource_path("assets", "irbis64_control.ico")))
        self.resize(900, 580)
        self.setMinimumSize(680, 440)
        self.last_output_path = ""
        self.last_differences: list[ResultDiffRow] = []
        self.last_summary: ResultDiffSummary | None = None
        self.progress_dialog = ProgressDialog("Ход сравнения", self)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        title = QLabel("Сравнение результатов по книгам")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel(
            "Выберите старый и новый Excel-отчёты «ИРБИС64 Контроль». Сравнение выполняется "
            "отдельно для листов «Вещества» и «Иностранные агенты»."
        )
        description.setObjectName("cardDescription")
        description.setWordWrap(True)
        root.addWidget(description)

        files_card = QFrame()
        files_card.setObjectName("sectionCard")
        files_layout = QGridLayout(files_card)
        files_layout.setContentsMargins(5, 5, 5, 5)
        files_layout.setHorizontalSpacing(6)
        files_layout.setVerticalSpacing(4)

        files_layout.addWidget(QLabel("Старый результат:"), 0, 0)
        self.old_edit = QLineEdit()
        self.old_edit.setObjectName("filePath")
        self.old_edit.setReadOnly(True)
        self.old_edit.setPlaceholderText("Старый Excel-отчёт не выбран")
        files_layout.addWidget(self.old_edit, 0, 1, 1, 2)
        old_button = QPushButton("Выбрать старый…")
        old_button.setObjectName("secondaryButton")
        old_button.clicked.connect(self.select_old)
        files_layout.addWidget(old_button, 1, 1, 1, 2)

        files_layout.addWidget(QLabel("Новый результат:"), 2, 0)
        self.new_edit = QLineEdit()
        self.new_edit.setObjectName("filePath")
        self.new_edit.setReadOnly(True)
        self.new_edit.setPlaceholderText("Новый Excel-отчёт не выбран")
        files_layout.addWidget(self.new_edit, 2, 1, 1, 2)
        new_button = QPushButton("Выбрать новый…")
        new_button.setObjectName("secondaryButton")
        new_button.clicked.connect(self.select_new)
        files_layout.addWidget(new_button, 3, 1, 1, 2)

        files_layout.addWidget(QLabel("Файл изменений:"), 4, 0)
        self.output_edit = QLineEdit()
        self.output_edit.setObjectName("filePath")
        self.output_edit.setReadOnly(True)
        self.output_edit.setPlaceholderText("Путь будет выбран автоматически")
        files_layout.addWidget(self.output_edit, 4, 1, 1, 2)
        output_button = QPushButton("Изменить путь…")
        output_button.setObjectName("mutedButton")
        output_button.clicked.connect(self.select_output)
        files_layout.addWidget(output_button, 5, 1, 1, 2)
        files_layout.setColumnStretch(1, 1)
        root.addWidget(files_card)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(5)
        self.summary_label = QLabel("Сравнение ещё не выполнялось")
        self.summary_label.setObjectName("statusLabel")
        self.summary_label.setWordWrap(True)
        summary_row.addWidget(self.summary_label, 1)
        self.open_button = QPushButton("Открыть файл изменений")
        self.open_button.setObjectName("mutedButton")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        summary_row.addWidget(self.open_button)
        compare_button = QPushButton("Сравнить")
        compare_button.setObjectName("primaryButton")
        compare_button.clicked.connect(self.run_comparison)
        summary_row.addWidget(compare_button)
        root.addLayout(summary_row)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("resultsTable")
        self.table.setHorizontalHeaderLabels(
            ["Изменение", "Раздел", "Автор", "Название", "ISBN", "Изменённые поля"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_button = QPushButton("Закрыть")
        close_button.setObjectName("mutedButton")
        close_button.clicked.connect(self.accept)
        bottom.addWidget(close_button)
        root.addLayout(bottom)

    def _choose_excel(self, title: str) -> str:
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            "",
            "Excel-отчёты (*.xlsx *.xlsm *.xls);;Все файлы (*)",
        )
        return path

    def select_old(self) -> None:
        path = self._choose_excel("Выберите старый результат")
        if path:
            self.old_edit.setText(path)
            self._set_default_output()

    def select_new(self) -> None:
        path = self._choose_excel("Выберите новый результат")
        if path:
            self.new_edit.setText(path)
            self._set_default_output()

    def _default_output(self) -> str:
        source = Path(self.new_edit.text().strip()) if self.new_edit.text().strip() else Path.home() / "Documents"
        folder = source.parent if source.suffix else source
        name = f"Изменения_между_результатами_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        return str(folder / name)

    def _set_default_output(self) -> None:
        self.output_edit.setText(self._default_output())

    def select_output(self) -> None:
        initial = self.output_edit.text().strip() or self._default_output()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить изменения",
            initial,
            "Excel (*.xlsx)",
        )
        if path:
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            self.output_edit.setText(path)

    def run_comparison(self) -> None:
        old_path = self.old_edit.text().strip()
        new_path = self.new_edit.text().strip()
        output_path = self.output_edit.text().strip() or self._default_output()
        if not old_path or not Path(old_path).is_file():
            QMessageBox.warning(self, APP_TITLE, "Выберите существующий старый Excel-отчёт.")
            return
        if not new_path or not Path(new_path).is_file():
            QMessageBox.warning(self, APP_TITLE, "Выберите существующий новый Excel-отчёт.")
            return
        if not output_path.lower().endswith(".xlsx"):
            output_path += ".xlsx"
            self.output_edit.setText(output_path)

        self.progress_dialog.start("Запуск сравнения отчётов...")
        self._append_progress("Старый отчёт: " + old_path)
        self._append_progress("Новый отчёт: " + new_path)
        self._append_progress("Файл изменений: " + output_path)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.progress_dialog.set_progress(40, "Сравнение файлов...")
            QApplication.processEvents()
            differences, summary = compare_result_files(old_path, new_path, output_path)
        except Exception as exc:
            self.progress_dialog.finish(f"Ошибка сравнения: {exc}", 0)
            QMessageBox.critical(self, APP_TITLE, f"Не удалось сравнить отчёты:\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.last_differences = differences
        self.last_summary = summary
        self.last_output_path = output_path
        self.open_button.setEnabled(Path(output_path).is_file())
        self._fill_preview(differences)
        self.summary_label.setText(
            f"Добавлено: {summary.added}   •   Удалено: {summary.removed}   •   "
            f"Изменено: {summary.changed}   •   Без изменений: {summary.unchanged}"
        )
        self._append_progress("Сравнение завершено.")
        self._append_progress(f"Добавлено: {summary.added}")
        self._append_progress(f"Удалено: {summary.removed}")
        self._append_progress(f"Изменено: {summary.changed}")
        self._append_progress(f"Без изменений: {summary.unchanged}")
        if summary.warnings:
            self._append_progress("Предупреждения:")
            for item in summary.warnings:
                self._append_progress("- " + item)
        self.progress_dialog.finish("Готово.", 100)
        warning_text = ""
        if summary.warnings:
            warning_text = "\n\nПредупреждения:\n" + "\n".join(f"• {item}" for item in summary.warnings)
        QMessageBox.information(
            self,
            APP_TITLE,
            "Сравнение завершено. В итоговом Excel находятся только изменения.\n\n"
            f"Добавлено: {summary.added}\n"
            f"Удалено: {summary.removed}\n"
            f"Изменено: {summary.changed}\n"
            f"Без изменений: {summary.unchanged}\n\n"
            f"Файл: {output_path}{warning_text}",
        )

    def _fill_preview(self, differences: list[ResultDiffRow]) -> None:
        preview = differences[:1000]
        self.table.setRowCount(len(preview))
        fills = {
            "Добавлено": QColor("#E2F0D9"),
            "Удалено": QColor("#FCE4D6"),
            "Изменено": QColor("#FFF2CC"),
        }
        for row_index, difference in enumerate(preview):
            values = [
                difference.change_type,
                difference.values.get("Раздел отчёта", ""),
                difference.values.get("Автор", ""),
                difference.values.get("Название", ""),
                difference.values.get("ISBN", ""),
                ", ".join(difference.changed_fields),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if difference.change_type in fills:
                    item.setBackground(fills[difference.change_type])
                self.table.setItem(row_index, column, item)
        if len(differences) > len(preview):
            self.summary_label.setText(
                self.summary_label.text() + f". В окне показаны первые {len(preview)} изменений."
            )

    def open_output(self) -> None:
        if self.last_output_path and Path(self.last_output_path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output_path))
        else:
            QMessageBox.warning(self, APP_TITLE, "Файл изменений не найден.")

    def _append_progress(self, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.progress_dialog.append_line(f"[{timestamp}] {text}")


class TextComparisonDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сравнение TXT-баз")
        self.setWindowIcon(QIcon(resource_path("assets", "irbis64_control.ico")))
        self.resize(780, 520)
        self.setMinimumSize(600, 400)
        self.last_output_path = ""
        self.progress_dialog = ProgressDialog("Ход сравнения TXT", self)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        title = QLabel("Сравнение текстовых баз")
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        files_card = QFrame()
        files_card.setObjectName("sectionCard")
        files_layout = QGridLayout(files_card)
        files_layout.setContentsMargins(5, 5, 5, 5)
        files_layout.setHorizontalSpacing(8)
        files_layout.setVerticalSpacing(8)

        files_layout.addWidget(QLabel("Старая TXT-база:"), 0, 0)
        self.old_edit = QLineEdit()
        self.old_edit.setObjectName("filePath")
        self.old_edit.setReadOnly(True)
        files_layout.addWidget(self.old_edit, 0, 1, 1, 2)
        old_button = QPushButton("Выбрать старую...")
        old_button.clicked.connect(self.select_old)
        files_layout.addWidget(old_button, 1, 1, 1, 2)

        files_layout.addWidget(QLabel("Новая TXT-база:"), 2, 0)
        self.new_edit = QLineEdit()
        self.new_edit.setObjectName("filePath")
        self.new_edit.setReadOnly(True)
        files_layout.addWidget(self.new_edit, 2, 1, 1, 2)
        new_button = QPushButton("Выбрать новую...")
        new_button.clicked.connect(self.select_new)
        files_layout.addWidget(new_button, 3, 1, 1, 2)

        files_layout.addWidget(QLabel("Отчёт:"), 4, 0)
        self.output_edit = QLineEdit()
        self.output_edit.setObjectName("filePath")
        self.output_edit.setReadOnly(True)
        files_layout.addWidget(self.output_edit, 4, 1, 1, 2)
        output_button = QPushButton("Изменить путь...")
        output_button.clicked.connect(self.select_output)
        files_layout.addWidget(output_button, 5, 1, 1, 2)
        files_layout.setColumnStretch(1, 1)
        root.addWidget(files_card)

        row = QHBoxLayout()
        self.summary_label = QLabel("Сравнение ещё не выполнялось")
        self.summary_label.setWordWrap(True)
        row.addWidget(self.summary_label, 1)
        self.open_button = QPushButton("Открыть отчёт")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        row.addWidget(self.open_button)
        compare_button = QPushButton("Сравнить")
        compare_button.setObjectName("primaryButton")
        compare_button.clicked.connect(self.run_comparison)
        row.addWidget(compare_button)
        root.addLayout(row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Изменение", "Запись"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

    def _choose_txt(self, title: str) -> str:
        path, _ = QFileDialog.getOpenFileName(self, title, "", "TXT (*.txt);;Все файлы (*)")
        return path

    def select_old(self) -> None:
        path = self._choose_txt("Выберите старую TXT-базу")
        if path:
            self.old_edit.setText(path)
            self._set_default_output()

    def select_new(self) -> None:
        path = self._choose_txt("Выберите новую TXT-базу")
        if path:
            self.new_edit.setText(path)
            self._set_default_output()

    def _default_output(self) -> str:
        source = Path(self.new_edit.text().strip()) if self.new_edit.text().strip() else Path.home() / "Documents"
        folder = source.parent if source.suffix else source
        return str(folder / f"Изменения_TXT_{datetime.now():%Y%m%d_%H%M%S}.txt")

    def _set_default_output(self) -> None:
        self.output_edit.setText(self._default_output())

    def select_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", self.output_edit.text() or self._default_output(), "TXT (*.txt)")
        if path:
            if not path.lower().endswith(".txt"):
                path += ".txt"
            self.output_edit.setText(path)

    def run_comparison(self) -> None:
        old_path = self.old_edit.text().strip()
        new_path = self.new_edit.text().strip()
        output_path = self.output_edit.text().strip() or self._default_output()
        if not old_path or not Path(old_path).is_file():
            QMessageBox.warning(self, APP_TITLE, "Выберите существующую старую TXT-базу.")
            return
        if not new_path or not Path(new_path).is_file():
            QMessageBox.warning(self, APP_TITLE, "Выберите существующую новую TXT-базу.")
            return
        if not output_path.lower().endswith(".txt"):
            output_path += ".txt"
            self.output_edit.setText(output_path)

        self.progress_dialog.start("Запуск сравнения TXT...")
        try:
            self.progress_dialog.set_progress(40, "Сравнение записей...")
            differences, summary = compare_text_files(old_path, new_path, output_path)
        except Exception as exc:
            self.progress_dialog.finish(f"Ошибка: {exc}", 0)
            QMessageBox.critical(self, APP_TITLE, f"Не удалось сравнить TXT-файлы:\n{exc}")
            return

        self.last_output_path = output_path
        self.open_button.setEnabled(Path(output_path).is_file())
        self.summary_label.setText(
            f"Добавлено: {summary.added}   Удалено: {summary.removed}   "
            f"Изменено: {summary.changed}   Без изменений: {summary.unchanged}"
        )
        self.table.setRowCount(len(differences[:1000]))
        for row_index, diff in enumerate(differences[:1000]):
            self.table.setItem(row_index, 0, QTableWidgetItem(diff.change_type))
            self.table.setItem(row_index, 1, QTableWidgetItem(str(diff.record_number)))
        self.progress_dialog.finish("Готово.", 100)
        QMessageBox.information(self, APP_TITLE, f"Сравнение TXT завершено.\n\nФайл: {output_path}")

    def open_output(self) -> None:
        if self.last_output_path and Path(self.last_output_path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output_path))
        else:
            QMessageBox.warning(self, APP_TITLE, "Файл отчёта не найден.")


DEFAULT_MARKER_SETTINGS = {
    "use_isbn_matching": True,
    "use_title_fallback": True,
    "use_fuzzy": False,
    "fuzzy_threshold": 90,
    "create_excel_report": True,
    "report_substances": True,
    "report_foreign_agents": True,
    "report_combined": False,
    "report_summary": False,
    "report_deduplicate": True,
    "report_sort": "record",
    "report_only": False,
    "substance_marker": DEFAULT_SUBSTANCE_MARKER,
    "foreign_agent_marker_template": DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    "age_marker": DEFAULT_AGE_MARKER,
    "substance_marker_field": DEFAULT_SUBSTANCE_MARKER_FIELD,
    "foreign_agent_marker_field": DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    "age_marker_field": DEFAULT_AGE_MARKER_FIELD,
}


def _marker_settings_path() -> Path:
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    return folder / "marker_settings.json"


def load_marker_settings() -> dict[str, str | int | bool]:
    settings = dict(DEFAULT_MARKER_SETTINGS)
    path = _marker_settings_path()
    if not path.is_file():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, default in settings.items():
            value = data.get(key)
            if isinstance(default, bool) and isinstance(value, bool):
                settings[key] = value
            elif isinstance(default, str) and isinstance(value, str):
                settings[key] = value
            elif isinstance(default, int) and isinstance(value, int):
                if key == "fuzzy_threshold" and 70 <= value <= 100:
                    settings[key] = value
                elif key.endswith("_field") and 1 <= value <= 999:
                    settings[key] = value
    except Exception:
        pass
    # Нечёткий поиск отключён: приложение работает только с точными совпадениями.
    settings["use_fuzzy"] = False
    settings["fuzzy_threshold"] = 90
    return settings


def save_marker_settings(settings: dict[str, str | int | bool]) -> None:
    path = _marker_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class MarkerSettingsDialog(QDialog):
    def __init__(self, settings: dict[str, str | int | bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setWindowIcon(QIcon(resource_path("assets", "irbis64_control.ico")))
        self.resize(620, 440)
        self.setMinimumSize(500, 380)
        if parent is not None:
            # Отдельные окна верхнего уровня не всегда наследуют таблицу стилей
            # главного окна на всех платформах и системных темах.
            self.setStyleSheet(parent.styleSheet())
        self.settings = dict(settings)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Настройки проверки")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        search_title = QLabel("Параметры поиска")
        search_title.setObjectName("cardTitle")
        layout.addWidget(search_title)

        self.isbn_match_check = QCheckBox("Точные совпадения по ISBN")
        self.isbn_match_check.setChecked(bool(settings["use_isbn_matching"]))
        layout.addWidget(self.isbn_match_check)

        self.title_fallback_check = QCheckBox("Точные совпадения по названию и автору")
        self.title_fallback_check.setChecked(bool(settings["use_title_fallback"]))
        layout.addWidget(self.title_fallback_check)

        marker_title = QLabel("Пометки в TXT-копии")
        marker_title.setObjectName("cardTitle")
        layout.addWidget(marker_title)

        description = QLabel(
            "Для каждой пометки выберите номер поля и задайте его содержимое. "
            "Пустое содержимое отключает соответствующую пометку."
        )
        description.setObjectName("cardDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QGridLayout()
        form.setHorizontalSpacing(7)
        form.setVerticalSpacing(5)

        field_header = QLabel("Поле")
        field_header.setObjectName("fieldLabel")
        marker_header = QLabel("Содержимое")
        marker_header.setObjectName("fieldLabel")
        form.addWidget(field_header, 0, 1)
        form.addWidget(marker_header, 0, 2)

        substance_label = QLabel("Вещества:")
        substance_label.setObjectName("fieldLabel")
        self.substance_field_spin = self._field_spin(int(settings["substance_marker_field"]))
        self.substance_edit = QLineEdit(str(settings["substance_marker"]))
        self.substance_edit.setObjectName("settingsField")
        self.substance_edit.setPlaceholderText(DEFAULT_SUBSTANCE_MARKER)
        form.addWidget(substance_label, 1, 0)
        form.addWidget(self.substance_field_spin, 1, 1)
        form.addWidget(self.substance_edit, 1, 2)

        foreign_label = QLabel("Иноагенты:")
        foreign_label.setObjectName("fieldLabel")
        self.foreign_field_spin = self._field_spin(int(settings["foreign_agent_marker_field"]))
        self.foreign_edit = QLineEdit(str(settings["foreign_agent_marker_template"]))
        self.foreign_edit.setObjectName("settingsField")
        self.foreign_edit.setPlaceholderText(DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE)
        self.foreign_edit.setToolTip("{name} будет заменено на совпавшего автора. Организации и проекты в эту метку не записываются")
        form.addWidget(foreign_label, 2, 0)
        form.addWidget(self.foreign_field_spin, 2, 1)
        form.addWidget(self.foreign_edit, 2, 2)

        foreign_hint = QLabel("Используйте {name}, чтобы подставить совпавшего автора. Если у автора есть псевдоним, он будет оформлен как «ФИО (ПСЕВДОНИМ: ...)».")
        foreign_hint.setObjectName("cardDescription")
        foreign_hint.setWordWrap(True)
        form.addWidget(foreign_hint, 3, 2)

        age_label = QLabel("Все совпадения:")
        age_label.setObjectName("fieldLabel")
        self.age_field_spin = self._field_spin(int(settings["age_marker_field"]))
        self.age_edit = QLineEdit(str(settings["age_marker"]))
        self.age_edit.setObjectName("settingsField")
        self.age_edit.setPlaceholderText(DEFAULT_AGE_MARKER)
        form.addWidget(age_label, 4, 0)
        form.addWidget(self.age_field_spin, 4, 1)
        form.addWidget(self.age_edit, 4, 2)
        form.setColumnStretch(2, 1)
        layout.addLayout(form)

        self.preview = QLabel()
        self.preview.setObjectName("cardDescription")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        for edit in (self.substance_edit, self.foreign_edit, self.age_edit):
            edit.textChanged.connect(self._update_preview)
        for spin in (self.substance_field_spin, self.foreign_field_spin, self.age_field_spin):
            spin.valueChanged.connect(self._update_preview)
        self._update_preview()

        layout.addStretch()
        buttons = QHBoxLayout()
        reset_button = QPushButton("По умолчанию")
        reset_button.setObjectName("mutedButton")
        reset_button.clicked.connect(self._reset_defaults)
        buttons.addWidget(reset_button)
        buttons.addStretch()

        cancel_button = QPushButton("Отмена")
        cancel_button.setObjectName("mutedButton")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)

        save_button = QPushButton("Сохранить")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)

    @staticmethod
    def _field_spin(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 999)
        spin.setValue(value)
        spin.setPrefix("#")
        spin.setMinimumWidth(68)
        spin.setMaximumWidth(88)
        return spin

    def _values(self) -> dict[str, str | int | bool]:
        return {
            "use_isbn_matching": self.isbn_match_check.isChecked(),
            "use_title_fallback": self.title_fallback_check.isChecked(),
            "use_fuzzy": False,
            "fuzzy_threshold": 90,
            "create_excel_report": bool(self.settings.get("create_excel_report", True)),
            "report_substances": bool(self.settings.get("report_substances", True)),
            "report_foreign_agents": bool(self.settings.get("report_foreign_agents", True)),
            "report_combined": bool(self.settings.get("report_combined", False)),
            "report_summary": bool(self.settings.get("report_summary", False)),
            "report_deduplicate": bool(self.settings.get("report_deduplicate", True)),
            "report_sort": str(self.settings.get("report_sort", "record")),
            "report_only": bool(self.settings.get("report_only", False)),
            "substance_marker": self.substance_edit.text().strip(),
            "foreign_agent_marker_template": self.foreign_edit.text().strip(),
            "age_marker": self.age_edit.text().strip(),
            "substance_marker_field": self.substance_field_spin.value(),
            "foreign_agent_marker_field": self.foreign_field_spin.value(),
            "age_marker_field": self.age_field_spin.value(),
        }

    def _update_preview(self) -> None:
        values = self._values()
        foreign_preview = str(values["foreign_agent_marker_template"]).replace(
            "{name}", "ИВАНОВ ИВАН ИВАНОВИЧ"
        )
        self.preview.setText(
            f"Пример: #{int(values['substance_marker_field']):03d}: "
            f"{values['substance_marker'] or 'не добавляется'}; "
            f"для иноагента — #{int(values['foreign_agent_marker_field']):03d}: "
            f"{foreign_preview or 'не добавляется'}; "
            f"#{int(values['age_marker_field']):03d}: "
            f"{values['age_marker'] or 'не добавляется'}."
        )

    def _reset_defaults(self) -> None:
        self.isbn_match_check.setChecked(True)
        self.title_fallback_check.setChecked(True)
        self.substance_edit.setText(DEFAULT_SUBSTANCE_MARKER)
        self.foreign_edit.setText(DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE)
        self.age_edit.setText(DEFAULT_AGE_MARKER)
        self.substance_field_spin.setValue(DEFAULT_SUBSTANCE_MARKER_FIELD)
        self.foreign_field_spin.setValue(DEFAULT_FOREIGN_AGENT_MARKER_FIELD)
        self.age_field_spin.setValue(DEFAULT_AGE_MARKER_FIELD)

    def _save(self) -> None:
        values = self._values()
        marker_values = [value for value in values.values() if isinstance(value, str)]
        if any("\n" in value or "\r" in value for value in marker_values):
            QMessageBox.warning(self, APP_TITLE, "Метка должна состоять из одной строки.")
            return
        if any(re.search(r"#\d{1,3}\s*:", value, re.IGNORECASE) for value in marker_values):
            QMessageBox.warning(self, APP_TITLE, "Введите содержимое метки без номера поля.")
            return
        try:
            save_marker_settings(values)
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось сохранить настройки:\n{exc}")
            return
        self.settings = values
        self.accept()


class MainWindow(QMainWindow):
    RUN_JOURNAL_MAX_LINES = 10_000

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(resource_path("assets", "irbis64_control.ico")))
        # Первый запуск: компактный вертикальный макет без лишнего поля снизу.
        # Последующие запуски восстановят пользовательские размер и положение.
        self.resize(560, 430)
        self.setMinimumWidth(560)
        self._has_saved_window_height = False
        self._initial_height_applied = False

        self.thread: QThread | None = None
        self.worker: QObject | None = None
        self.irbis_thread: QThread | None = None
        self.irbis_worker: IrbisOperationWorker | None = None
        self._irbis_operation = ""
        self.last_results: list[MatchResult] = []
        self.last_summary: ComparisonSummary | None = None
        self.last_output_path = ""
        self.last_modified_database_path = ""
        self.last_run_direct = False
        self.last_run_report_only = False
        self.marker_settings = load_marker_settings()
        self.progress_dialog = ProgressDialog("Ход выполнения", self)
        self._journal_lines: deque[str] = deque(maxlen=self.RUN_JOURNAL_MAX_LINES)
        self._journal_save_timer = QTimer(self)
        self._journal_save_timer.setSingleShot(True)
        self._journal_save_timer.timeout.connect(self._save_run_journal)

        self._build_ui()
        self._apply_style()
        self._align_all_control_heights()
        self._restore_window_state()

    def _asset_icon(self, filename: str) -> QIcon:
        return QIcon(resource_path("assets", filename))

    def _align_all_control_heights(self) -> None:
        """Keep the main-window fields and buttons at one compact height."""
        field_height = max(
            self.irbis_host_edit.minimumHeight(),
            self.irbis_host_edit.sizeHint().height(),
        )

        for field in self.findChildren(QLineEdit):
            if field.objectName() != "qt_spinbox_lineedit":
                field.setFixedHeight(field_height)
        for field in self.findChildren(QComboBox):
            field.setFixedHeight(field_height)
        for field in self.findChildren(QSpinBox):
            field.setFixedHeight(field_height)
        for button in self.findChildren(QPushButton):
            button.setFixedHeight(field_height)

        self.direct_irbis_box.setFixedHeight(field_height)
        self.irbis_password_box.setFixedHeight(field_height)
        self.irbis_database_box.setFixedHeight(field_height)

    def _restore_window_state(self) -> None:
        try:
            data = json.loads(window_state_path().read_text(encoding="utf-8"))
        except Exception:
            data = {}

        screens = QApplication.screens()
        primary = QApplication.primaryScreen()
        default_area = primary.availableGeometry() if primary is not None else QRect(0, 0, 1280, 720)

        width = max(self.minimumWidth(), int(data.get("width", self.width())))
        width = min(width, max(self.minimumWidth(), default_area.width()))
        self._has_saved_window_height = "height" in data
        height = max(240, int(data.get("height", self.height())))
        height = min(height, max(240, default_area.height()))
        self.resize(width, height)

        try:
            x = int(data["x"])
            y = int(data["y"])
            saved_rect = QRect(x, y, width, height)
        except (KeyError, TypeError, ValueError):
            saved_rect = QRect()

        visible = any(saved_rect.intersects(screen.availableGeometry()) for screen in screens)
        if visible:
            self.move(saved_rect.topLeft())
        else:
            area = default_area
            self.move(
                area.x() + max(0, (area.width() - width) // 2),
                area.y() + max(0, (area.height() - height) // 2),
            )

        QTimer.singleShot(0, self._fit_scroll_content)

    def _save_window_state(self) -> None:
        frame_position = self.frameGeometry().topLeft()
        normal_size = self.normalGeometry().size() if self.isMaximized() else self.size()
        payload = {
            "x": frame_position.x(),
            "y": frame_position.y(),
            "width": normal_size.width(),
            "height": normal_size.height(),
            "maximized": False,
        }
        path = window_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def open_useful_links(self) -> None:
        UsefulLinksDialog(self).exec()

    def open_marker_settings(self) -> None:
        dialog = MarkerSettingsDialog(self.marker_settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.marker_settings = dict(dialog.settings)
            if hasattr(self, "substance_marker_edit"):
                self._apply_marker_settings_to_ui()

    def open_database_connector(self) -> None:
        """Запускает отдельную утилиту прямого подключения к хранилищу/ИРБИС."""
        database_paths = self._database_paths()
        database_path = database_paths[0] if database_paths else ""
        modified_paths = [path.strip() for path in self.last_modified_database_path.split(";") if path.strip()]
        modified_path = modified_paths[0] if modified_paths else self.modified_database_edit.text().strip()

        try:
            if getattr(sys, "frozen", False):
                base = Path(sys.executable).resolve().parent
                candidates = [
                    base / "IRBIS64ControlDB.exe",
                ]
                connector = next((path for path in candidates if path.is_file()), None)
                if connector is None:
                    raise FileNotFoundError(
                        "Рядом с ИРБИС64 Контроль не найден IRBIS64ControlDB.exe. "
                        "Пересоберите комплект через build_exe.bat."
                    )
                command = [str(connector)]
            else:
                connector_script = Path(resource_path("db_connector.py"))
                if not connector_script.is_file():
                    raise FileNotFoundError(f"Не найден файл {connector_script.name}")
                command = [sys.executable, str(connector_script)]

            if database_path:
                command.extend(["--database", database_path])
            if modified_path and Path(modified_path).is_file():
                command.extend(["--modified", modified_path])
            subprocess.Popen(command, cwd=str(Path(command[0]).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent))
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось запустить подключение к базе:\n{exc}")

    @staticmethod
    def _make_field_spin(value: int = 1) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 999)
        spin.setValue(value)
        spin.setPrefix("#")
        spin.setMinimumWidth(88)
        spin.setMaximumWidth(88)
        return spin

    def _apply_marker_settings_to_ui(self) -> None:
        if not hasattr(self, "substance_marker_edit"):
            return
        self.inline_isbn_match_check.setChecked(bool(self.marker_settings["use_isbn_matching"]))
        self.inline_title_fallback_check.setChecked(bool(self.marker_settings["use_title_fallback"]))
        self.create_excel_report_check.setChecked(bool(self.marker_settings["create_excel_report"]))
        self.report_substances_check.setChecked(bool(self.marker_settings["report_substances"]))
        self.report_foreign_agents_check.setChecked(bool(self.marker_settings["report_foreign_agents"]))
        self.report_combined_check.setChecked(bool(self.marker_settings["report_combined"]))
        self.report_summary_check.setChecked(bool(self.marker_settings["report_summary"]))
        self.report_deduplicate_check.setChecked(bool(self.marker_settings["report_deduplicate"]))
        sort_index = self.report_sort_combo.findData(str(self.marker_settings["report_sort"]))
        self.report_sort_combo.setCurrentIndex(max(0, sort_index))
        self.report_only_check.setChecked(bool(self.marker_settings["report_only"]))
        self._update_report_controls(self.create_excel_report_check.isChecked())
        self.substance_marker_edit.setText(str(self.marker_settings["substance_marker"]))
        self.foreign_marker_edit.setText(str(self.marker_settings["foreign_agent_marker_template"]))
        self.age_marker_edit.setText(str(self.marker_settings["age_marker"]))
        self.substance_field_spin.setValue(int(self.marker_settings["substance_marker_field"]))
        self.foreign_field_spin.setValue(int(self.marker_settings["foreign_agent_marker_field"]))
        self.age_field_spin.setValue(int(self.marker_settings["age_marker_field"]))

    def _marker_values_from_ui(self) -> dict[str, str | int | bool]:
        return {
            "use_isbn_matching": self.inline_isbn_match_check.isChecked(),
            "use_title_fallback": self.inline_title_fallback_check.isChecked(),
            "use_fuzzy": False,
            "fuzzy_threshold": 90,
            "create_excel_report": self.create_excel_report_check.isChecked(),
            "report_substances": self.report_substances_check.isChecked(),
            "report_foreign_agents": self.report_foreign_agents_check.isChecked(),
            "report_combined": self.report_combined_check.isChecked(),
            "report_summary": self.report_summary_check.isChecked(),
            "report_deduplicate": self.report_deduplicate_check.isChecked(),
            "report_sort": str(self.report_sort_combo.currentData()),
            "report_only": self.report_only_check.isChecked(),
            "substance_marker": self.substance_marker_edit.text().strip(),
            "foreign_agent_marker_template": self.foreign_marker_edit.text().strip(),
            "age_marker": self.age_marker_edit.text().strip(),
            "substance_marker_field": self.substance_field_spin.value(),
            "foreign_agent_marker_field": self.foreign_field_spin.value(),
            "age_marker_field": self.age_field_spin.value(),
        }

    def _sync_marker_settings_from_ui(self, save: bool = True, show_message: bool = True) -> bool:
        values = self._marker_values_from_ui()
        if not bool(values["use_isbn_matching"]) and not bool(values["use_title_fallback"]):
            QMessageBox.warning(self, APP_TITLE, "Выберите совпадения по ISBN или по названию и автору.")
            self.workflow_tabs.setCurrentIndex(1)
            return False
        if bool(values["report_only"]) and not bool(values["create_excel_report"]):
            QMessageBox.warning(self, APP_TITLE, "Для режима «Только отчёт» включите создание Excel-отчёта.")
            self.workflow_tabs.setCurrentIndex(2)
            return False
        report_keys = (
            "report_substances",
            "report_foreign_agents",
            "report_combined",
            "report_summary",
        )
        if bool(values["create_excel_report"]) and not any(bool(values[key]) for key in report_keys):
            QMessageBox.warning(self, APP_TITLE, "Выберите хотя бы один лист для Excel-отчёта.")
            self.workflow_tabs.setCurrentIndex(2)
            return False
        text_values = [value for value in values.values() if isinstance(value, str)]
        if any("\n" in value or "\r" in value for value in text_values):
            QMessageBox.warning(self, APP_TITLE, "Метка должна состоять из одной строки.")
            return False
        if any(re.search(r"#\d{1,3}\s*:", value, re.IGNORECASE) for value in text_values):
            QMessageBox.warning(self, APP_TITLE, "Введите только содержимое метки — номер поля задаётся отдельно.")
            return False
        self.marker_settings = values
        if save:
            try:
                save_marker_settings(values)
            except Exception as exc:
                QMessageBox.warning(self, APP_TITLE, f"Не удалось сохранить настройки:\n{exc}")
                return False
        if show_message:
            self._set_status("Настройки меток сохранены", "idle")
        return True

    def _queue_report_settings_autosave(self, *_args) -> None:
        if getattr(self, "_report_autosave_pending", False):
            return
        self._report_autosave_pending = True
        QTimer.singleShot(0, self._autosave_report_settings)

    def _autosave_report_settings(self) -> None:
        self._report_autosave_pending = False
        values = self._marker_values_from_ui()
        report_keys = (
            "report_substances",
            "report_foreign_agents",
            "report_combined",
            "report_summary",
        )
        # Во время переключения между листами кратковременно может не быть
        # выбрано ни одного варианта. Такое промежуточное состояние не сохраняем.
        if bool(values["create_excel_report"]) and not any(bool(values[key]) for key in report_keys):
            return
        self.marker_settings = values
        try:
            save_marker_settings(values)
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось автоматически сохранить настройки списков:\n{exc}")

    def _queue_marker_settings_autosave(self, *_args) -> None:
        self._marker_autosave_timer.start(350)

    def _autosave_marker_settings(self) -> None:
        self._sync_marker_settings_from_ui(save=True, show_message=False)

    def _update_report_controls(self, enabled: bool) -> None:
        if not enabled and self.report_only_check.isChecked():
            self.report_only_check.setChecked(False)
        for widget in (
            self.output_label,
            self.output_edit,
            self.report_path_button,
            *self.report_list_checks,
            self.report_deduplicate_check,
            self.report_sort_label,
            self.report_sort_combo,
        ):
            widget.setEnabled(enabled)

    def _update_report_only(self, enabled: bool) -> None:
        if enabled and not self.create_excel_report_check.isChecked():
            self.create_excel_report_check.setChecked(True)
        direct = self.direct_irbis_checkbox.isChecked() if hasattr(self, "direct_irbis_checkbox") else False
        for name in ("modified_database_label", "modified_database_edit", "txt_path_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(not enabled and not direct)

    def create_matches_excel(self) -> None:
        """Запускает проверку из раздела отчёта без добавления меток."""
        create_report = self.create_excel_report_check.isChecked()
        report_only = self.report_only_check.isChecked()
        self.create_excel_report_check.setChecked(True)
        self.report_only_check.setChecked(True)
        try:
            self.start_comparison()
        finally:
            # Рабочий поток уже получил копию параметров. Возвращаем прежние
            # настройки интерфейса, чтобы обычный запуск не стал отчётным.
            self.create_excel_report_check.setChecked(create_report)
            self.report_only_check.setChecked(report_only)
            self._sync_marker_settings_from_ui(save=True, show_message=False)

    def select_modified_database_output(self) -> None:
        initial = self.modified_database_edit.text().strip() or self._default_modified_database_path()
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить TXT-копию с метками", initial, "TXT (*.txt)")
        if path:
            if not path.lower().endswith(".txt"):
                path += ".txt"
            self.modified_database_edit.setText(path)

    def _pick_irbis_snapshot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Рабочая TXT-копия ИРБИС",
            self.irbis_snapshot_edit.text().strip(),
            "TXT (*.txt)",
        )
        if path:
            if not path.lower().endswith(".txt"):
                path += ".txt"
            self.irbis_snapshot_edit.setText(path)
            self.irbis_manifest_edit.setText(str(Path(path).with_suffix(".map.json")))
            self._save_irbis_config()

    def _current_irbis_database(self) -> str:
        data = self.irbis_db_combo.currentData()
        return str(data if data is not None else self.irbis_db_combo.currentText()).strip()

    def _populate_irbis_databases(self, databases: object, preferred: str = "") -> None:
        current = preferred.strip() or self._current_irbis_database()
        self.irbis_db_combo.blockSignals(True)
        self.irbis_db_combo.clear()
        if isinstance(databases, list):
            for entry in databases:
                if isinstance(entry, dict):
                    name = str(entry.get("name", "")).strip()
                    description = str(entry.get("description", "")).strip()
                else:
                    name = str(entry).strip()
                    description = ""
                if not name:
                    continue
                title = f"{name} — {description}" if description and description.casefold() != name.casefold() else name
                self.irbis_db_combo.addItem(title, name)
        if self.irbis_db_combo.count() == 0:
            if current:
                self.irbis_db_combo.addItem(current, current)
            else:
                self.irbis_db_combo.addItem("Нет доступных баз", "")
        if current:
            index = self.irbis_db_combo.findData(current)
            if index < 0:
                index = self.irbis_db_combo.findText(current)
            if index >= 0:
                self.irbis_db_combo.setCurrentIndex(index)
        self.irbis_db_combo.blockSignals(False)
        self.irbis_db_combo.setEnabled(bool(self._current_irbis_database()))

    def _update_direct_mode_ui(self, checked: bool | None = None) -> None:
        direct = self.direct_irbis_checkbox.isChecked() if checked is None else bool(checked)
        if hasattr(self, "database_list"):
            self.database_list.setVisible(not direct)
            self.database_button.setVisible(not direct)
            self.clear_database_button.setVisible(not direct)
            database = self._current_irbis_database() or "не выбрана"
            self.direct_source_label.setText(
                f"ИРБИС · {database}" if direct else "Локальная TXT-база"
            )
            self.direct_source_detail_label.setText(
                "Прямое подключение, TXT-копия не создаётся"
                if direct else "Резервный режим работы с TXT-файлом"
            )
            self.direct_source_dot.setProperty("state", "direct" if direct else "local")
            self.direct_source_dot.style().unpolish(self.direct_source_dot)
            self.direct_source_dot.style().polish(self.direct_source_dot)
            self.direct_source_dot.update()
        if hasattr(self, "irbis_base_status"):
            self.irbis_base_status.setText(
                "База читается пакетами при запуске"
                if direct else "Выберите локальный TXT на вкладке «Источники»"
            )
        if hasattr(self, "irbis_local_hint"):
            self.irbis_local_hint.setVisible(not direct)
        if hasattr(self, "write_irbis_button"):
            self.write_irbis_button.setVisible(not direct)
        if hasattr(self, "open_modified_database_button"):
            self.open_modified_database_button.setVisible(not direct)
        for name in ("modified_database_label", "modified_database_edit", "txt_path_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(not direct)
                report_only = bool(getattr(self, "report_only_check", None) and self.report_only_check.isChecked())
                widget.setEnabled(not direct and not report_only)
        if hasattr(self, "action_buttons"):
            self._reflow_actions(2)
        if hasattr(self, "marker_card"):
            self.marker_card.title_label.setText("Метки в ИРБИС" if direct else "Метки в TXT-копии")
        if hasattr(self, "cleanup_button"):
            self.cleanup_button.setText(
                "Удалить метки из ИРБИС" if direct else "Удалить все метки из TXT"
            )

    def _restore_irbis_config(self) -> None:
        try:
            config = json.loads(database_connector_config_path().read_text(encoding="utf-8"))
        except Exception:
            config = {}
        self.irbis_host_edit.setText(str(config.get("host", "127.0.0.1")))
        try:
            self.irbis_port_spin.setValue(int(config.get("port", 6666)))
        except Exception:
            self.irbis_port_spin.setValue(6666)
        self.irbis_login_edit.setText(str(config.get("login", "")))
        saved_database = str(config.get("database", "IBIS")).strip()
        self._populate_irbis_databases([], saved_database)
        self.irbis_query_edit.setText(str(config.get("query", "I=$")))
        try:
            self.irbis_read_workers_spin.setValue(max(1, min(8, int(config.get("read_workers", 4)))))
        except Exception:
            self.irbis_read_workers_spin.setValue(4)
        try:
            self.irbis_page_size_spin.setValue(max(100, min(2000, int(config.get("page_size", 500)))))
        except Exception:
            self.irbis_page_size_spin.setValue(500)
        self.direct_irbis_checkbox.setChecked(bool(config.get("direct_mode", True)))
        if config.get("snapshot"):
            self.irbis_snapshot_edit.setText(str(config["snapshot"]))
        if config.get("manifest"):
            self.irbis_manifest_edit.setText(str(config["manifest"]))
        snapshot = Path(self.irbis_snapshot_edit.text().strip())
        if snapshot.is_file() and not self.direct_irbis_checkbox.isChecked():
            self.irbis_base_status.setText(f"Найдена рабочая копия: {snapshot.name}")
        self._update_direct_mode_ui()

    def _save_irbis_config(self) -> None:
        data = {
            "host": self.irbis_host_edit.text().strip(),
            "port": self.irbis_port_spin.value(),
            "login": self.irbis_login_edit.text().strip(),
            "database": self._current_irbis_database(),
            "query": self.irbis_query_edit.text().strip(),
            "read_workers": self.irbis_read_workers_spin.value(),
            "page_size": self.irbis_page_size_spin.value(),
            "direct_mode": self.direct_irbis_checkbox.isChecked(),
            "snapshot": self.irbis_snapshot_edit.text().strip(),
            "manifest": self.irbis_manifest_edit.text().strip(),
            "modified": self.last_modified_database_path or self.modified_database_edit.text().strip(),
        }
        database_connector_config_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _irbis_params(self) -> dict[str, object]:
        return {
            "host": self.irbis_host_edit.text().strip() or "127.0.0.1",
            "port": self.irbis_port_spin.value(),
            "login": self.irbis_login_edit.text().strip(),
            "password": self.irbis_password_edit.text(),
            "database": self._current_irbis_database(),
            "query": self.irbis_query_edit.text().strip() or "I=$",
            "read_workers": self.irbis_read_workers_spin.value(),
            "page_size": self.irbis_page_size_spin.value(),
            "direct_mode": self.direct_irbis_checkbox.isChecked(),
            "snapshot": self.irbis_snapshot_edit.text().strip(),
            "manifest": self.irbis_manifest_edit.text().strip(),
            "modified": self.last_modified_database_path or self.modified_database_edit.text().strip(),
            "backup_dir": str(app_data_dir() / "backups"),
            "substance_marker": self.marker_settings.get("substance_marker", DEFAULT_SUBSTANCE_MARKER),
            "foreign_agent_marker_template": self.marker_settings.get("foreign_agent_marker_template", DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE),
            "age_marker": self.marker_settings.get("age_marker", DEFAULT_AGE_MARKER),
            "substance_marker_field": int(self.marker_settings.get("substance_marker_field", DEFAULT_SUBSTANCE_MARKER_FIELD)),
            "foreign_agent_marker_field": int(self.marker_settings.get("foreign_agent_marker_field", DEFAULT_FOREIGN_AGENT_MARKER_FIELD)),
            "age_marker_field": int(self.marker_settings.get("age_marker_field", DEFAULT_AGE_MARKER_FIELD)),
        }

    def _start_irbis_operation(self, mode: str) -> None:
        if self.irbis_thread and self.irbis_thread.isRunning():
            QMessageBox.information(self, APP_TITLE, "Операция с ИРБИС уже выполняется.")
            return
        params = self._irbis_params()
        if mode in {"fetch", "apply", "clean_markers", "tune_read"} and not str(params["database"]).strip():
            QMessageBox.warning(self, APP_TITLE, "Сначала обновите список и выберите базу ИРБИС.")
            return
        if mode == "fetch" and not str(params["snapshot"]).strip():
            QMessageBox.warning(self, APP_TITLE, "Укажите путь для рабочей TXT-копии.")
            return
        if mode == "apply":
            if not Path(str(params["manifest"])).is_file() or not Path(str(params["modified"])).is_file():
                QMessageBox.warning(self, APP_TITLE, "Нет карты MFN или готовой TXT-копии с изменениями.")
                return

        self._save_irbis_config()
        self._irbis_operation = mode
        self.irbis_progress.setValue(0)
        self.irbis_progress.show()
        QTimer.singleShot(0, self._fit_scroll_content)
        self.irbis_test_button.setEnabled(False)
        self.irbis_tune_read_button.setEnabled(False)
        self.irbis_refresh_databases_button.setEnabled(False)
        self.irbis_fetch_button.setEnabled(False)
        self.write_irbis_button.setEnabled(False)
        if hasattr(self, "cleanup_button"):
            self.cleanup_button.setEnabled(False)
        captions = {
            "test": "Подключение к ИРБИС…",
            "tune_read": "Тест пакета чтения…",
            "databases": "Обновление списка баз ИРБИС…",
            "fetch": "Получение базы ИРБИС…",
            "apply": "Запись изменений в ИРБИС…",
            "clean_markers": "Очистка меток прямо в ИРБИС…",
        }
        self._set_irbis_status(captions.get(mode, "Выполнение операции…"), "running")
        self._append_progress(captions.get(mode, "Операция ИРБИС"))

        self.irbis_thread = QThread(self)
        self.irbis_worker = IrbisOperationWorker(mode, params)
        self.irbis_worker.moveToThread(self.irbis_thread)
        self.irbis_thread.started.connect(self.irbis_worker.run)
        self.irbis_worker.progress.connect(self._on_irbis_progress)
        self.irbis_worker.finished.connect(self.irbis_thread.quit)
        self.irbis_worker.failed.connect(self.irbis_thread.quit)
        self.irbis_worker.finished.connect(self._on_irbis_finished)
        self.irbis_worker.failed.connect(self._on_irbis_failed)
        self.irbis_thread.finished.connect(self.irbis_worker.deleteLater)
        self.irbis_thread.finished.connect(self._cleanup_irbis_worker)
        self.irbis_thread.start()

    @pyqtSlot(int, str)
    def _on_irbis_progress(self, percent: int, text: str) -> None:
        percent = max(0, min(100, percent))
        self.irbis_progress.show()
        self.irbis_progress.setValue(percent)
        self._set_irbis_status(text, "running")
        self._append_progress(text)

    @pyqtSlot(str, object)
    def _on_irbis_finished(self, mode: str, result: object) -> None:
        self.irbis_progress.setValue(100)
        self.irbis_progress.hide()
        QTimer.singleShot(0, self._fit_scroll_content)
        if mode == "tune_read":
            data = result if isinstance(result, dict) else {}
            page_size = max(100, min(2000, int(data.get("page_size", 500))))
            probe_total = int(data.get("probe_total", 0))
            self.irbis_page_size_spin.setValue(page_size)
            self._set_irbis_status(
                f"Пакет чтения: {page_size} записей • найдено по запросу: {probe_total:,}",
                "success",
            )
            self._append_progress(
                f"Тест чтения завершён. Автоматически выбран пакет: {page_size}; "
                f"записей по запросу: {probe_total:,}."
            )
            self._save_irbis_config()
            return
        if mode in {"test", "databases"}:
            data = result if isinstance(result, dict) else {}
            databases = data.get("databases", [])
            previous = self._current_irbis_database()
            self._populate_irbis_databases(databases, previous)
            count = self.irbis_db_combo.count() if self._current_irbis_database() else 0
            if mode == "test":
                self._set_irbis_status(f"Подключено к ИРБИС • доступно баз: {count}", "success")
                self._append_progress(f"Подключение к ИРБИС выполнено. Загружено баз: {count}.")
            else:
                self._set_irbis_status(f"Список баз обновлён • доступно: {count}", "success")
                self._append_progress(f"Список баз ИРБИС обновлён: {count}.")
            self._save_irbis_config()
            return
        if mode == "fetch":
            manifest = result
            snapshot = Path(str(manifest.snapshot_file))
            self.database_list.clear()
            item = QListWidgetItem(str(snapshot))
            item.setData(Qt.ItemDataRole.UserRole, True)
            self.database_list.addItem(item)
            self.database_edit.setText(str(snapshot))
            self.irbis_snapshot_edit.setText(str(snapshot))
            self.irbis_manifest_edit.setText(str(Path(self.irbis_manifest_edit.text().strip())))
            self._update_database_summary()
            self._set_default_outputs(force=True)
            self.irbis_base_status.setText(f"Готово: {len(manifest.records)} записей • {snapshot.name}")
            self._set_irbis_status(f"Рабочая база готова: {len(manifest.records)} записей", "success")
            self._append_progress(f"Рабочая база готова: {len(manifest.records)} записей. Файл автоматически выбран для проверки.")
            self.last_modified_database_path = ""
            self.write_irbis_button.setEnabled(
                snapshot.is_file() and Path(self.irbis_manifest_edit.text().strip()).is_file()
            )
            self._save_irbis_config()
            self.workflow_tabs.setCurrentIndex(2)
            return
        if mode == "clean_markers":
            data = result if isinstance(result, dict) else {}
            scanned = int(data.get("scanned", 0))
            found = int(data.get("found", 0))
            written = int(data.get("written", 0))
            backup = str(data.get("backup", ""))
            self._set_irbis_status(f"Очистка завершена • изменено: {written}", "success")
            self._append_progress(
                f"Очистка меток завершена: просмотрено {scanned:,}, найдено {found:,}, изменено {written:,}."
            )
            if backup:
                self._append_progress(f"Rollback-копия перед очисткой: {backup}")
            QMessageBox.information(
                self, APP_TITLE,
                f"Очистка меток в ИРБИС завершена.\n\n"
                f"Просмотрено записей: {scanned:,}\n"
                f"Записей с метками: {found:,}\n"
                f"Очищено записей: {written:,}"
                + (f"\n\nRollback-копия: {backup}" if backup else ""),
            )
            return
        if mode == "apply":
            data = result if isinstance(result, dict) else {}
            written = int(data.get("written", 0))
            conflicts = int(data.get("conflicts", 0))
            self._set_irbis_status(
                f"Отправлено в ИРБИС: {written}; конфликтов: {conflicts}",
                "warning" if conflicts else "success",
            )
            self._append_progress(f"Отправка в ИРБИС завершена: записано {written}, конфликтов {conflicts}.")
            if conflicts:
                self._append_progress(
                    "Часть записей была изменена на сервере другим пользователем. Перед следующей отправкой получите базу заново."
                )
            else:
                self._append_progress("Локальный снимок и карта MFN обновлены по состоянию сервера.")
            backup = str(data.get("backup", ""))
            if backup:
                self._append_progress(f"Rollback-копия: {backup}")

    @pyqtSlot(str, str)
    def _on_irbis_failed(self, mode: str, error: str) -> None:
        self.irbis_progress.setValue(0)
        self.irbis_progress.hide()
        QTimer.singleShot(0, self._fit_scroll_content)
        self._set_irbis_status("Ошибка подключения/обмена с ИРБИС", "error")
        self._append_progress(f"Ошибка ИРБИС: {error}")
        QMessageBox.critical(self, APP_TITLE, f"Операция ИРБИС не выполнена:\n{error}")

    @pyqtSlot()
    def _cleanup_irbis_worker(self) -> None:
        if self.irbis_thread:
            self.irbis_thread.deleteLater()
        self.irbis_thread = None
        self.irbis_worker = None
        self.irbis_test_button.setEnabled(True)
        self.irbis_tune_read_button.setEnabled(True)
        self.irbis_refresh_databases_button.setEnabled(True)
        self.irbis_fetch_button.setEnabled(True)
        if hasattr(self, "cleanup_button"):
            self.cleanup_button.setEnabled(True)
        modified_candidates = [
            item.strip() for item in self.last_modified_database_path.split(";") if item.strip()
        ]
        if not modified_candidates:
            output_candidate = self.modified_database_edit.text().strip()
            if output_candidate and Path(output_candidate).is_file():
                modified_candidates = [output_candidate]
        if not modified_candidates:
            snapshot_candidate = self.irbis_snapshot_edit.text().strip()
            if snapshot_candidate and Path(snapshot_candidate).is_file():
                modified_candidates = [snapshot_candidate]
        can_write = bool(
            len(modified_candidates) == 1
            and Path(modified_candidates[0]).is_file()
            and Path(self.irbis_manifest_edit.text().strip()).is_file()
        )
        self.write_irbis_button.setEnabled(can_write)

    def apply_results_to_irbis(self) -> None:
        # Можно отправить результат сравнения, очищенную копию или исходный
        # снимок без изменений. Приоритет — последняя созданная копия, затем
        # существующий выходной TXT, затем сам снимок ИРБИС.
        modified_paths = [item.strip() for item in self.last_modified_database_path.split(";") if item.strip()]
        if not modified_paths:
            candidate = self.modified_database_edit.text().strip()
            if candidate and Path(candidate).is_file():
                modified_paths = [candidate]
        if not modified_paths:
            snapshot_candidate = self.irbis_snapshot_edit.text().strip()
            if snapshot_candidate and Path(snapshot_candidate).is_file():
                modified_paths = [snapshot_candidate]
        if len(modified_paths) != 1 or not Path(modified_paths[0]).is_file():
            QMessageBox.warning(
                self, APP_TITLE,
                "Не найдена TXT-копия для отправки в ИРБИС. Сначала получите базу или создайте очищенную/изменённую копию."
            )
            return
        modified = modified_paths[0]
        manifest_path = Path(self.irbis_manifest_edit.text().strip())
        if not manifest_path.is_file():
            QMessageBox.warning(self, APP_TITLE, "Карта MFN отсутствует. Сначала получите базу через вкладку ИРБИС.")
            return
        try:
            manifest = load_manifest(manifest_path)
            selected_databases = [Path(item).resolve() for item in self._database_paths()]
            snapshot = Path(manifest.snapshot_file).resolve()
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось проверить карту MFN:\n{exc}")
            return
        if len(selected_databases) != 1 or selected_databases[0] != snapshot:
            QMessageBox.warning(
                self, APP_TITLE,
                "Текущая выбранная TXT-база не совпадает со снимком, полученным из ИРБИС. Для безопасности запись отменена."
            )
            return
        source_name = Path(modified).name
        answer = QMessageBox.question(
            self,
            APP_TITLE,
            "TXT-копия будет отправлена в живую базу ИРБИС полностью — даже если в ней нет изменений. "
            "Перед записью каждой записи проверяется версия на сервере и создаётся rollback-копия.\n\n"
            f"Файл: {source_name}\n\nПродолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.last_modified_database_path = modified
        self._start_irbis_operation("apply")

    def check_updates(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            request = urllib.request.Request(
                GITHUB_LATEST_RELEASE_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"IRBIS64Control/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            QApplication.restoreOverrideCursor()
            if exc.code == 404:
                answer = QMessageBox.information(
                    self,
                    APP_TITLE,
                    "На GitHub пока не найден опубликованный релиз для проверки обновлений.\n\n"
                    "Открыть страницу репозитория?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    QDesktopServices.openUrl(QUrl(GITHUB_REPO_URL))
                return
            QMessageBox.warning(self, APP_TITLE, f"Не удалось проверить обновление:\nHTTP {exc.code}")
            return
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, APP_TITLE, f"Не удалось проверить обновление:\n{exc}")
            return
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()

        latest_version = str(data.get("tag_name") or data.get("name") or "").strip()
        release_url = str(data.get("html_url") or GITHUB_REPO_URL)
        if not latest_version:
            QMessageBox.information(self, APP_TITLE, "GitHub ответил без номера версии релиза.")
            return

        if _is_newer_version(latest_version, APP_VERSION):
            answer = QMessageBox.information(
                self,
                APP_TITLE,
                f"Доступно обновление: {latest_version}\n"
                f"Текущая версия: {APP_VERSION}\n\n"
                "Открыть страницу загрузки?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(release_url))
        else:
            QMessageBox.information(self, APP_TITLE, f"Обновлений нет.\nТекущая версия: {APP_VERSION}")

    def open_result_comparison(self) -> None:
        dialog = ResultComparisonDialog(self)
        if self.last_output_path and Path(self.last_output_path).is_file():
            dialog.new_edit.setText(self.last_output_path)
            dialog._set_default_output()
        dialog.exec()

    def open_text_comparison(self) -> None:
        dialog = TextComparisonDialog(self)
        modified_paths = [path.strip() for path in self.last_modified_database_path.split(";") if path.strip()]
        if modified_paths and Path(modified_paths[0]).is_file():
            dialog.new_edit.setText(modified_paths[0])
            dialog._set_default_output()
        dialog.exec()

    def clean_markers(self) -> None:
        if not self._sync_marker_settings_from_ui(save=True, show_message=False):
            return
        if self.thread and self.thread.isRunning():
            QMessageBox.information(self, APP_TITLE, "Сначала дождитесь завершения текущей проверки.")
            return
        if not self.direct_irbis_checkbox.isChecked():
            self.clean_txt_markers()
            return

        params = self._irbis_params()
        database = str(params.get("database", "")).strip()
        if not str(params.get("login", "")).strip():
            QMessageBox.warning(self, APP_TITLE, "Введите логин каталогизатора ИРБИС.")
            self.workflow_tabs.setCurrentIndex(0)
            return
        if not database:
            QMessageBox.warning(self, APP_TITLE, "Выберите базу ИРБИС из списка.")
            self.workflow_tabs.setCurrentIndex(0)
            return

        answer = QMessageBox.warning(
            self,
            APP_TITLE,
            "Из выбранной живой базы ИРБИС будут удалены стандартные и текущие "
            "настроенные метки ИРБИС64 Контроль. Другие значения в тех же полях сохраняются.\n\n"
            f"База: {database}\n"
            f"Сервер: {params.get('host')}:{params.get('port')}\n\n"
            "Перед изменениями будет создана rollback-копия найденных записей. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_irbis_operation("clean_markers")

    def clean_txt_markers(self) -> None:
        initial_folder = str(self._default_output_folder())
        source_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите TXT-базу с метками",
            initial_folder,
            "TXT-базы (*.txt);;Все файлы (*.*)",
        )
        if not source_path:
            return
        source = Path(source_path)
        default_output = source.with_name(
            f"{source.stem}_без_меток_{datetime.now():%Y%m%d_%H%M%S}{source.suffix or '.txt'}"
        )
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить очищенную TXT-базу",
            str(default_output),
            "TXT-базы (*.txt)",
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".txt"):
            output_path += ".txt"
        try:
            written, cleaned_records = remove_database_markers(
                source_path,
                output_path,
                substance_marker=str(self.marker_settings["substance_marker"]),
                foreign_agent_marker_template=str(
                    self.marker_settings["foreign_agent_marker_template"]
                ),
                age_marker=str(self.marker_settings["age_marker"]),
                substance_marker_field=int(self.marker_settings["substance_marker_field"]),
                foreign_agent_marker_field=int(
                    self.marker_settings["foreign_agent_marker_field"]
                ),
                age_marker_field=int(self.marker_settings["age_marker_field"]),
            )
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось очистить TXT-базу:\n{exc}")
            return
        cleaned_path = str(Path(written))
        self.last_modified_database_path = cleaned_path
        self.modified_database_edit.setText(cleaned_path)
        self.open_modified_database_button.setEnabled(Path(cleaned_path).is_file())
        self.write_irbis_button.setEnabled(
            Path(cleaned_path).is_file()
            and Path(self.irbis_manifest_edit.text().strip()).is_file()
            and Path(self.irbis_snapshot_edit.text().strip()).is_file()
        )
        self._save_irbis_config()
        self._append_progress(
            f"Очищенная TXT-копия выбрана для отправки в ИРБИС: {cleaned_path}"
        )
        QMessageBox.information(
            self,
            APP_TITLE,
            f"Очищенная копия сохранена и выбрана для отправки в ИРБИС.\n"
            f"Записей с удалёнными метками: {cleaned_records}\n\n"
            f"Файл: {written}",
        )

    def _build_ui(self) -> None:
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("mainScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setCentralWidget(self.scroll_area)

        central = LayoutHintWidget()
        central.setObjectName("centralPage")
        self.scroll_area.setWidget(central)
        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(8, 8, 8, 8)
        self.root_layout.setSpacing(8)

        # Компактная шапка в стиле нового макета.
        self.header_card = QFrame()
        self.header_card.setObjectName("headerCard")
        self.header_layout = QHBoxLayout(self.header_card)
        self.header_layout.setContentsMargins(4, 3, 4, 3)
        self.header_layout.setSpacing(8)

        self.header_logo = QLabel()
        self.header_logo.setObjectName("appLogo")
        self.header_logo.setPixmap(QIcon(resource_path("assets", "irbis64_control_icon.png")).pixmap(28, 28))
        self.header_logo.setFixedSize(30, 30)
        self.header_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_layout.addWidget(self.header_logo)

        header_text = QVBoxLayout()
        header_text.setContentsMargins(2, 0, 0, 0)
        header_text.setSpacing(2)
        self.main_title = QLabel(APP_TITLE)
        self.main_title.setObjectName("mainTitle")
        self.subtitle_primary = QLabel("Проверка и контроль библиотечных баз")
        self.subtitle_primary.setObjectName("subtitle")
        self.subtitle_secondary = QLabel("")
        self.subtitle_secondary.hide()
        self.subtitle_label = self.subtitle_primary
        header_text.addWidget(self.main_title)
        header_text.addWidget(self.subtitle_primary)
        self.header_layout.addLayout(header_text, 1)

        self.header_divider = QFrame(); self.header_divider.hide()
        self.header_watermark = QLabel(); self.header_watermark.hide()

        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(4)

        self.start_button = QPushButton("Запустить проверку")
        self.start_button.setObjectName("headerStartButton")
        self.start_button.setMinimumWidth(0)
        self.start_button.clicked.connect(self.start_comparison)
        self.header_actions.addWidget(self.start_button)

        self.marker_settings_button = QPushButton("Настройки")
        self.marker_settings_button.setObjectName("headerButton")
        self.marker_settings_button.clicked.connect(lambda: self.workflow_tabs.setCurrentIndex(3))
        self.header_actions.addWidget(self.marker_settings_button)

        self.useful_links_button = QPushButton("Полезные ссылки")
        self.useful_links_button.setObjectName("headerButton")
        self.useful_links_button.clicked.connect(self.open_useful_links)
        self.header_actions.addWidget(self.useful_links_button)

        self.update_button = QPushButton("Проверить обновление")
        self.update_button.setObjectName("headerButton")
        self.update_button.clicked.connect(self.check_updates)
        self.header_actions.addWidget(self.update_button)

        self.header_layout.addLayout(self.header_actions)
        self.root_layout.addWidget(self.header_card)
        self.header_card.hide()

        self.workflow_tabs = CompactTabWidget()
        self.workflow_tabs.setObjectName("workflowTabs")
        self.workflow_tabs.setDocumentMode(True)
        self.workflow_tabs.setUsesScrollButtons(True)
        self.workflow_tabs.tabBar().setExpanding(False)
        self.workflow_tabs.tabBar().setDrawBase(False)
        self.workflow_tabs.tabBar().setMovable(False)
        self.workflow_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.workflow_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.root_layout.addWidget(self.workflow_tabs, 1)

        # ------------------------- Вкладка 1: ИРБИС -------------------------
        self.irbis_tab = QWidget()
        self.irbis_tab.setObjectName("tabPage")
        irbis_root = QVBoxLayout(self.irbis_tab)
        irbis_root.setContentsMargins(6, 6, 6, 0)
        irbis_root.setSpacing(7)

        intro = QLabel("Подключитесь к ИРБИС и выберите базу.")
        self.irbis_intro = intro
        intro.setObjectName("tabIntro")
        intro.setWordWrap(True)
        irbis_root.addWidget(intro)

        irbis_columns = QGridLayout()
        irbis_columns.setHorizontalSpacing(6)
        irbis_columns.setVerticalSpacing(5)
        self.irbis_columns = irbis_columns

        connection_card = SectionCard("Параметры подключения", "")
        form = QGridLayout()
        form.setHorizontalSpacing(7)
        form.setVerticalSpacing(4)
        self.irbis_host_edit = QLineEdit()
        self.irbis_port_spin = QSpinBox(); self.irbis_port_spin.setRange(1, 65535); self.irbis_port_spin.setValue(6666)
        self.irbis_port_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.irbis_login_edit = QLineEdit()
        self.irbis_password_edit = QLineEdit(); self.irbis_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.irbis_show_password = self.irbis_password_edit.addAction(
            self._asset_icon("eye.svg"),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.irbis_show_password.setCheckable(True)
        self.irbis_show_password.setText("Показать пароль")
        self.irbis_show_password.setToolTip("Показать пароль")

        def toggle_irbis_password(visible: bool) -> None:
            self.irbis_password_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
            self.irbis_show_password.setIcon(
                self._asset_icon("eye-crossed.svg" if visible else "eye.svg")
            )
            hint = "Скрыть пароль" if visible else "Показать пароль"
            self.irbis_show_password.setText(hint)
            self.irbis_show_password.setToolTip(hint)

        self.irbis_show_password.toggled.connect(toggle_irbis_password)
        self.irbis_db_combo = QComboBox()
        self.irbis_db_combo.setObjectName("settingsField")
        self.irbis_db_combo.setToolTip("Список загружается с сервера ИРБИС из доступных баз АРМ Каталогизатор.")
        self.irbis_db_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.irbis_db_combo.setMinimumContentsLength(18)
        self.irbis_db_combo.currentIndexChanged.connect(lambda _index: self._update_direct_mode_ui() if hasattr(self, "direct_source_label") else None)
        self.irbis_refresh_databases_button = QPushButton()
        self.irbis_refresh_databases_button.setObjectName("mutedButton")
        self.irbis_refresh_databases_button.setIcon(self._asset_icon("refresh.svg"))
        self.irbis_refresh_databases_button.setIconSize(QSize(16, 16))
        self.irbis_refresh_databases_button.setToolTip("Заново получить список существующих баз с сервера ИРБИС")
        self.irbis_refresh_databases_button.clicked.connect(lambda: self._start_irbis_operation("databases"))
        self.irbis_query_edit = QLineEdit("I=$")
        self.irbis_query_edit.setToolTip("Поисковое выражение ИРБИС. По умолчанию используется I=$.")
        self.irbis_read_workers_spin = QSpinBox()
        self.irbis_read_workers_spin.setRange(1, 8)
        self.irbis_read_workers_spin.setValue(4)
        self.irbis_read_workers_spin.setSuffix(" пот.")
        self.irbis_read_workers_spin.setToolTip(
            "Количество параллельных сеансов для чтения записей. "
            "4 — рекомендуется; 1 — режим совместимости; 6–8 — быстрее на мощном сервере."
        )
        self.irbis_read_workers_spin.setMaximumWidth(120)
        for edit in (self.irbis_host_edit, self.irbis_login_edit, self.irbis_password_edit, self.irbis_query_edit):
            edit.setObjectName("settingsField")

        self.irbis_read_workers_spin.hide()  # используется только старым режимом получения TXT-снимка

        # На обычной ширине параметры собраны попарно: это экономит высоту,
        # но не уменьшает сами поля. На узком окне раскладка автоматически
        # возвращается к привычной форме «подпись — поле».
        self.irbis_connection_form = form
        self.irbis_field_labels = {}
        for key, text in (
            ("host", "Сервер"),
            ("port", "Порт"),
            ("login", "Логин"),
            ("password", "Пароль"),
            ("database", "База"),
            ("query", "Запрос"),
        ):
            label = QLabel(text)
            label.setObjectName("fieldLabel")
            self.irbis_field_labels[key] = label

        self.irbis_password_box = QWidget()
        password_row = QHBoxLayout(self.irbis_password_box)
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.addWidget(self.irbis_password_edit, 1)

        self.irbis_database_box = QWidget()
        database_row = QHBoxLayout(self.irbis_database_box)
        database_row.setContentsMargins(0, 0, 0, 0)
        database_row.setSpacing(4)
        database_row.addWidget(self.irbis_db_combo, 1)
        database_row.addWidget(self.irbis_refresh_databases_button)

        self._reflow_irbis_connection_form(False)
        connection_card.body.addLayout(form)
        self.connection_card = connection_card
        irbis_columns.addWidget(connection_card, 0, 0)

        base_card = SectionCard("Режим работы", "")
        self.direct_irbis_checkbox = QCheckBox()
        self.direct_irbis_checkbox.setAccessibleName("Работать напрямую с ИРБИС")
        self.direct_irbis_checkbox.setChecked(True)
        self.direct_irbis_checkbox.setToolTip("Без создания полной TXT-копии: чтение пакетами с сервера и запись найденных меток сразу в ИРБИС.")
        self.direct_irbis_label = QLabel("Работать напрямую с ИРБИС")
        self.direct_irbis_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.direct_irbis_label.setBuddy(self.direct_irbis_checkbox)
        self.direct_irbis_label.setToolTip(self.direct_irbis_checkbox.toolTip())
        self.direct_irbis_box = QWidget()
        direct_mode_row = QHBoxLayout(self.direct_irbis_box)
        direct_mode_row.setContentsMargins(0, 0, 0, 0)
        direct_mode_row.setSpacing(5)
        direct_mode_row.addWidget(
            self.direct_irbis_checkbox,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        direct_mode_row.addWidget(
            self.direct_irbis_label,
            1,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        base_card.body.addWidget(self.direct_irbis_box)

        direct_note = QLabel("Чтение настраивается автоматически; запись выполняется безопасно по одной MFN.")
        direct_note.setObjectName("cardDescription")
        direct_note.setWordWrap(True)
        base_card.body.addWidget(direct_note)

        batch_row = QHBoxLayout(); batch_row.setSpacing(4)
        batch_label = QLabel("Пакет чтения"); batch_label.setObjectName("fieldLabel"); batch_row.addWidget(batch_label)
        self.irbis_page_size_spin = QSpinBox()
        self.irbis_page_size_spin.setRange(100, 2000)
        self.irbis_page_size_spin.setSingleStep(100)
        self.irbis_page_size_spin.setValue(500)
        self.irbis_page_size_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.irbis_page_size_spin.setSuffix(" зап.")
        self.irbis_page_size_spin.setToolTip("Подбирается кнопкой «Тест»; при необходимости значение можно изменить вручную.")
        batch_row.addWidget(self.irbis_page_size_spin)
        self.irbis_tune_read_button = QPushButton("Тест")
        self.irbis_tune_read_button.setObjectName("mutedButton")
        self.irbis_tune_read_button.setToolTip(
            "Проверить доступные размеры пакета чтения и автоматически выбрать максимальный стабильный"
        )
        self.irbis_tune_read_button.clicked.connect(
            lambda: self._start_irbis_operation("tune_read")
        )
        batch_row.addWidget(self.irbis_tune_read_button)
        batch_row.addStretch()
        base_card.body.addLayout(batch_row)

        self.irbis_base_status = QLabel("Прямой режим: TXT-копия не требуется")
        self.irbis_base_status.setObjectName("statusLabel")
        self.irbis_base_status.setWordWrap(True)
        base_card.body.addWidget(self.irbis_base_status)

        self.irbis_local_hint = QLabel("TXT-режим: снимите флажок и выберите файл на вкладке «Источники».")
        self.irbis_local_hint.setObjectName("cardDescription")
        self.irbis_local_hint.setWordWrap(True)
        base_card.body.addWidget(self.irbis_local_hint)

        # Служебные поля старого режима оставлены скрытыми для совместимости с
        # существующими настройками и ручной отправкой старых снимков.
        self.irbis_snapshot_edit = QLineEdit(str(app_data_dir() / "direct_database.txt"))
        self.irbis_snapshot_edit.hide()
        self.irbis_manifest_edit = QLineEdit(str(app_data_dir() / "direct_database.map.json"))
        self.irbis_manifest_edit.hide()
        self.base_card = base_card
        irbis_columns.addWidget(base_card, 0, 1)
        irbis_columns.setColumnStretch(0, 3)
        irbis_columns.setColumnStretch(1, 2)
        irbis_root.addLayout(irbis_columns)

        irbis_actions = QFrame(); irbis_actions.setObjectName("irbisActions")
        ia = QVBoxLayout(irbis_actions); ia.setContentsMargins(0, 0, 0, 0); ia.setSpacing(4)
        action_box = QFrame(); action_box.setObjectName("actionCard")
        action_row = QGridLayout(action_box); action_row.setContentsMargins(5, 5, 5, 5); action_row.setHorizontalSpacing(4); action_row.setVerticalSpacing(4)
        self.irbis_action_layout = action_row
        self.irbis_test_button = QPushButton("Подключиться")
        self.irbis_test_button.setObjectName("primaryButton")
        self.irbis_test_button.setToolTip("Подключиться к ИРБИС и загрузить список доступных баз")
        self.irbis_test_button.clicked.connect(lambda: self._start_irbis_operation("test"))
        self.irbis_fetch_button = QPushButton("Получить TXT-копию")
        self.irbis_fetch_button.setObjectName("mutedButton")
        self.irbis_fetch_button.clicked.connect(lambda: self._start_irbis_operation("fetch"))
        self.irbis_fetch_button.hide()
        self.irbis_next_button = QPushButton("Далее: источники →")
        self.irbis_next_button.setObjectName("primaryButton")
        self.irbis_next_button.clicked.connect(lambda: self.workflow_tabs.setCurrentIndex(1))
        self.irbis_next_button.hide()
        action_row.addWidget(self.irbis_test_button, 0, 0)
        action_row.setColumnStretch(0, 1)
        ia.addWidget(action_box)
        self.irbis_progress = QProgressBar(); self.irbis_progress.setRange(0, 100); self.irbis_progress.setValue(0)
        self.irbis_progress.hide()
        action_row.addWidget(self.irbis_progress, 1, 0)
        self.irbis_status_box = QWidget()
        self.irbis_status_box.setObjectName("irbisStateRow")
        irbis_status_layout = QHBoxLayout(self.irbis_status_box)
        irbis_status_layout.setContentsMargins(0, 0, 0, 0)
        irbis_status_layout.setSpacing(6)
        self.irbis_status_dot = QLabel()
        self.irbis_status_dot.setObjectName("irbisStateDot")
        self.irbis_status_dot.setProperty("state", "error")
        self.irbis_status_dot.setFixedSize(10, 10)
        self.irbis_status = QLabel("Готово к подключению")
        self.irbis_status.setObjectName("cardTitle")
        irbis_status_layout.addWidget(self.irbis_status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        irbis_status_layout.addWidget(self.irbis_status, 1)
        action_row.addWidget(self.irbis_status_box, 2, 0)
        irbis_root.addWidget(irbis_actions)
        # Не растягиваем вкладку пустым spacer-ом: после статуса не должно быть
        # искусственного большого нижнего отступа. Свободная высота остаётся
        # только если пользователь сам увеличит окно.
        irbis_root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.workflow_tabs.addTab(self.irbis_tab, "Подключение")

        # --------------------- Вкладка 2: источник записей ------------------
        self.files_tab = QWidget(); self.files_tab.setObjectName("tabPage")
        files_root = QVBoxLayout(self.files_tab); files_root.setContentsMargins(6, 6, 6, 6); files_root.setSpacing(7)
        files_intro = QLabel("Выберите источник библиографических записей. В прямом режиме TXT-база не требуется.")
        self.files_intro = files_intro
        files_intro.setObjectName("tabIntro"); files_intro.setWordWrap(True); files_root.addWidget(files_intro)

        self.database_card = SectionCard("Источник записей", "")
        self.direct_source_dot = QLabel()
        self.direct_source_dot.setObjectName("directSourceDot")
        self.direct_source_dot.setProperty("state", "direct")
        self.direct_source_dot.setFixedSize(9, 9)
        self.direct_source_label = QLabel("ИРБИС")
        self.direct_source_label.setObjectName("sourceStatusTitle")
        self.direct_source_detail_label = QLabel("Прямое подключение, TXT-копия не создаётся")
        self.direct_source_detail_label.setObjectName("statusLabel")
        source_text = QVBoxLayout()
        source_text.setContentsMargins(0, 0, 0, 0)
        source_text.setSpacing(0)
        source_text.addWidget(self.direct_source_label)
        source_text.addWidget(self.direct_source_detail_label)
        source_status_row = QHBoxLayout()
        source_status_row.setContentsMargins(0, 0, 0, 0)
        source_status_row.setSpacing(7)
        source_status_row.addWidget(
            self.direct_source_dot,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        source_status_row.addLayout(source_text, 1)

        # Заголовок и состояние образуют единую левую колонку. Благодаря этому
        # кнопка справа центрируется относительно всей высоты карточки.
        self.database_card.outer_layout.removeItem(self.database_card.title_row)
        source_left = QVBoxLayout()
        source_left.setContentsMargins(0, 0, 0, 0)
        source_left.setSpacing(4)
        source_left.addLayout(self.database_card.title_row)
        source_left.addLayout(source_status_row)
        source_summary_row = QHBoxLayout()
        source_summary_row.setContentsMargins(0, 0, 0, 0)
        source_summary_row.setSpacing(7)
        source_summary_row.addLayout(source_left, 1)
        self.source_useful_links_button = QPushButton("Справочные сайты")
        self.source_useful_links_button.setObjectName("mutedButton")
        self.source_useful_links_button.setToolTip(
            "Открыть полезные ссылки на справочные и экспертные материалы"
        )
        self.source_useful_links_button.clicked.connect(self.open_useful_links)
        source_summary_row.addWidget(
            self.source_useful_links_button,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        self.database_card.body.addLayout(source_summary_row)
        self.database_edit = QLineEdit(); self.database_edit.hide()
        self.database_list = QListWidget(); self.database_list.setObjectName("compactList"); self.database_list.setMaximumHeight(58); self.database_list.setMinimumHeight(38)
        self.database_button = QPushButton("Добавить TXT-базы"); self.database_button.setObjectName("secondaryButton"); self.database_button.clicked.connect(self.select_database)
        self.clear_database_button = QPushButton("Очистить"); self.clear_database_button.setObjectName("mutedButton"); self.clear_database_button.clicked.connect(self._clear_database_files)
        database_row = QGridLayout(); database_row.setHorizontalSpacing(4); database_row.setVerticalSpacing(4)
        self.database_controls = database_row
        self.database_card.body.addLayout(database_row)
        self.direct_irbis_checkbox.toggled.connect(self._update_direct_mode_ui)
        files_root.addWidget(self.database_card)

        sources_row = QGridLayout(); sources_row.setHorizontalSpacing(6); sources_row.setVerticalSpacing(5); self.sources_grid = sources_row
        self.foreign_agents_card = SectionCard("Реестр иностранных агентов", "")
        self.foreign_agents_edit = QLineEdit(); self.foreign_agents_edit.hide()
        self.foreign_agents_list = QListWidget(); self.foreign_agents_list.setObjectName("compactList"); self.foreign_agents_list.setMaximumHeight(58); self.foreign_agents_list.setMinimumHeight(38)
        self.foreign_agents_button = QPushButton("Добавить Excel"); self.foreign_agents_button.setObjectName("secondaryButton"); self.foreign_agents_button.clicked.connect(self.select_foreign_agents)
        self.clear_foreign_agents_button = QPushButton("Очистить"); self.clear_foreign_agents_button.setObjectName("mutedButton"); self.clear_foreign_agents_button.clicked.connect(self._clear_foreign_agents)
        fa_actions = QGridLayout(); fa_actions.setHorizontalSpacing(4); fa_actions.setVerticalSpacing(4)
        self.foreign_agents_controls = fa_actions
        self.foreign_agents_card.body.addLayout(fa_actions)
        sources_row.addWidget(self.foreign_agents_card, 0, 0)

        self.excel_card = SectionCard("Реестр по наркотическим веществам", "")
        self.excel_list = QListWidget(); self.excel_list.setObjectName("compactList"); self.excel_list.setMaximumHeight(58); self.excel_list.setMinimumHeight(38)
        self.excel_summary_edit = QLineEdit(); self.excel_summary_edit.hide()
        self.add_excel_button = QPushButton("Добавить Excel"); self.add_excel_button.setObjectName("secondaryButton"); self.add_excel_button.clicked.connect(self.add_excel_files)
        self.clear_excel_button = QPushButton("Очистить"); self.clear_excel_button.setObjectName("mutedButton"); self.clear_excel_button.clicked.connect(self._clear_excel_files)
        ex_actions = QGridLayout(); ex_actions.setHorizontalSpacing(4); ex_actions.setVerticalSpacing(4)
        self.excel_controls = ex_actions
        self.excel_card.body.addLayout(ex_actions)
        sources_row.addWidget(self.excel_card, 0, 1)
        sources_row.setColumnStretch(0, 1); sources_row.setColumnStretch(1, 1)
        files_root.addLayout(sources_row)

        match_settings_card = SectionCard("Настройка совпадений", "")
        self.inline_isbn_match_check = QCheckBox("Точные совпадения по ISBN")
        self.inline_title_fallback_check = QCheckBox("Точные совпадения по названию и автору")
        match_settings_card.body.addWidget(self.inline_isbn_match_check)
        match_settings_card.body.addWidget(self.inline_title_fallback_check)
        files_root.addWidget(match_settings_card)

        compare_card = SectionCard("Результаты", "")
        compare_row = QGridLayout(); compare_row.setHorizontalSpacing(7); compare_row.setVerticalSpacing(4)
        self.compare_controls = compare_row
        self.create_excel_report_check = QCheckBox("Создавать Excel-отчёт")
        self.create_excel_report_check.toggled.connect(self._update_report_controls)
        self.report_only_check = QCheckBox("Только отчёт — не добавлять метки")
        self.report_only_check.toggled.connect(self._update_report_only)
        compare_card.body.addLayout(compare_row)

        output_grid = QGridLayout(); output_grid.setHorizontalSpacing(5); output_grid.setVerticalSpacing(4)
        self.output_edit = QLineEdit(); self.output_edit.setObjectName("filePath"); self.output_edit.setReadOnly(True)
        self.modified_database_edit = QLineEdit(); self.modified_database_edit.setObjectName("filePath"); self.modified_database_edit.setReadOnly(True)
        self.output_label = QLabel("Excel-отчёт"); output_grid.addWidget(self.output_label, 0, 0); output_grid.addWidget(self.output_edit, 0, 1)
        self.report_path_button = QPushButton("Изменить…"); self.report_path_button.setObjectName("mutedButton"); self.report_path_button.clicked.connect(self.select_output); output_grid.addWidget(self.report_path_button, 0, 2)
        self.modified_database_label = QLabel("TXT-копия с метками"); output_grid.addWidget(self.modified_database_label, 1, 0); output_grid.addWidget(self.modified_database_edit, 1, 1)
        self.txt_path_button = QPushButton("Изменить…"); self.txt_path_button.setObjectName("mutedButton"); self.txt_path_button.clicked.connect(self.select_modified_database_output); output_grid.addWidget(self.txt_path_button, 1, 2)
        output_grid.setColumnStretch(1, 1); compare_card.body.addLayout(output_grid)

        utility_row = QGridLayout(); utility_row.setHorizontalSpacing(4); utility_row.setVerticalSpacing(4)
        self.utility_controls = utility_row
        self.compare_reports_button = QPushButton("Сравнить Excel-отчёты"); self.compare_reports_button.setObjectName("mutedButton"); self.compare_reports_button.clicked.connect(self.open_result_comparison)
        next_lists = QPushButton("Далее: списки →"); next_lists.setObjectName("primaryButton"); next_lists.clicked.connect(lambda: self.workflow_tabs.setCurrentIndex(2))
        files_root.addWidget(next_lists)
        files_root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.workflow_tabs.addTab(self.files_tab, "Источники")

        # --------------------- Вкладка 3: состав списков ------------------
        self.lists_tab = QWidget(); self.lists_tab.setObjectName("tabPage")
        lists_root = QVBoxLayout(self.lists_tab); lists_root.setContentsMargins(6, 6, 6, 6); lists_root.setSpacing(7)
        self.lists_intro = QLabel("Настройте состав листов в итоговом Excel-отчёте.")
        self.lists_intro.setObjectName("tabIntro"); self.lists_intro.setWordWrap(True); lists_root.addWidget(self.lists_intro)
        lists_root.addWidget(compare_card)

        report_lists_card = SectionCard("Списки точных совпадений", "")
        self.report_lists_card = report_lists_card
        report_lists_hint = QLabel("Выберите, какие листы будут созданы в итоговом Excel-отчёте.")
        report_lists_hint.setObjectName("cardDescription")
        report_lists_hint.setWordWrap(True)
        report_lists_card.body.addWidget(report_lists_hint)
        report_lists_grid = QGridLayout(); report_lists_grid.setHorizontalSpacing(12); report_lists_grid.setVerticalSpacing(5)
        self.report_lists_grid = report_lists_grid
        self.report_substances_check = QCheckBox("Вещества — отдельный список")
        self.report_foreign_agents_check = QCheckBox("Иностранные агенты — отдельный список")
        self.report_combined_check = QCheckBox("Все совпадения — общий список")
        self.report_summary_check = QCheckBox("Сводка по результатам проверки")
        self.report_list_checks = (
            self.report_substances_check,
            self.report_foreign_agents_check,
            self.report_combined_check,
            self.report_summary_check,
        )
        report_lists_grid.addWidget(self.report_substances_check, 0, 0)
        report_lists_grid.addWidget(self.report_foreign_agents_check, 0, 1)
        report_lists_grid.addWidget(self.report_combined_check, 1, 0)
        report_lists_grid.addWidget(self.report_summary_check, 1, 1)
        report_lists_grid.setColumnStretch(0, 1); report_lists_grid.setColumnStretch(1, 1)
        report_lists_card.body.addLayout(report_lists_grid)
        report_format_row = QHBoxLayout(); report_format_row.setSpacing(7)
        self.report_deduplicate_check = QCheckBox("Объединять дубли записей")
        report_format_row.addWidget(self.report_deduplicate_check)
        self.report_sort_label = QLabel("Сортировка:")
        self.report_sort_label.setObjectName("fieldLabel")
        report_format_row.addWidget(self.report_sort_label)
        self.report_sort_combo = QComboBox()
        self.report_sort_combo.addItem("По номеру записи", "record")
        self.report_sort_combo.addItem("По названию", "title")
        self.report_sort_combo.addItem("По автору", "author")
        self.report_sort_combo.addItem("По ISBN", "isbn")
        report_format_row.addWidget(self.report_sort_combo)
        report_format_row.addStretch()
        report_lists_card.body.addLayout(report_format_row)
        lists_root.addWidget(report_lists_card)

        report_create_card = SectionCard("Создание Excel-файла с совпадениями", "")
        self.report_create_card = report_create_card
        report_create_hint = QLabel(
            "Создать выбранные списки совпадений без добавления меток в ИРБИС или TXT-базу."
        )
        report_create_hint.setObjectName("cardDescription")
        report_create_hint.setWordWrap(True)
        report_create_card.body.addWidget(report_create_hint)
        self.create_matches_excel_button = QPushButton("Создать Excel-файл с совпадениями")
        self.create_matches_excel_button.setObjectName("primaryButton")
        self.create_matches_excel_button.clicked.connect(self.create_matches_excel)
        for button in (self.create_matches_excel_button, self.compare_reports_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        report_create_actions = QGridLayout()
        report_create_actions.setHorizontalSpacing(4)
        report_create_actions.setVerticalSpacing(4)
        report_create_actions.addWidget(self.create_matches_excel_button, 0, 0)
        report_create_actions.addWidget(self.compare_reports_button, 0, 1)
        report_create_actions.setColumnStretch(0, 1)
        report_create_actions.setColumnStretch(1, 1)
        report_create_card.body.addLayout(report_create_actions)
        lists_root.addWidget(report_create_card)

        next_marks = QPushButton("Далее: метки →"); next_marks.setObjectName("primaryButton"); next_marks.clicked.connect(lambda: self.workflow_tabs.setCurrentIndex(3))
        self.next_marks_button = next_marks
        lists_root.addLayout(utility_row)
        lists_root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.workflow_tabs.addTab(self.lists_tab, "Списки")

        # ---------------------- Вкладка 4: метки --------------------
        self.run_tab = QWidget(); self.run_tab.setObjectName("tabPage")
        run_root = QVBoxLayout(self.run_tab); run_root.setContentsMargins(6, 6, 6, 6); run_root.setSpacing(7)
        run_intro = QLabel("Настройте поля и содержимое меток.")
        self.markers_intro = run_intro
        run_intro.setObjectName("tabIntro"); run_intro.setWordWrap(True); run_root.addWidget(run_intro)

        marker_card = SectionCard("Метки в ИРБИС", "")
        self.marker_card = marker_card
        marker_grid = QGridLayout(); marker_grid.setHorizontalSpacing(6); marker_grid.setVerticalSpacing(4)
        marker_grid.addWidget(QLabel("Тип совпадения"), 0, 0); marker_grid.addWidget(QLabel("Поле"), 0, 1); marker_grid.addWidget(QLabel("Содержимое метки"), 0, 2)
        self.substance_field_spin = self._make_field_spin(); self.substance_marker_edit = QLineEdit(); self.substance_marker_edit.setObjectName("settingsField")
        self.foreign_field_spin = self._make_field_spin(); self.foreign_marker_edit = QLineEdit(); self.foreign_marker_edit.setObjectName("settingsField"); self.foreign_marker_edit.setToolTip("{name} будет заменено на совпавшего автора; названия организаций и проектов не подставляются")
        self.age_field_spin = self._make_field_spin(); self.age_marker_edit = QLineEdit(); self.age_marker_edit.setObjectName("settingsField")
        rows = [
            ("Вещества", self.substance_field_spin, self.substance_marker_edit),
            ("Иностранные агенты", self.foreign_field_spin, self.foreign_marker_edit),
            ("Все найденные записи", self.age_field_spin, self.age_marker_edit),
        ]
        for row, (name, spin, edit) in enumerate(rows, start=1):
            marker_grid.addWidget(QLabel(name), row, 0); marker_grid.addWidget(spin, row, 1); marker_grid.addWidget(edit, row, 2)
        marker_grid.setColumnStretch(2, 1)
        marker_card.body.addLayout(marker_grid)
        run_root.addWidget(marker_card)

        next_run_tab = QPushButton("Далее: запуск и журнал →")
        next_run_tab.setObjectName("primaryButton")
        next_run_tab.clicked.connect(lambda: self.workflow_tabs.setCurrentIndex(4))
        run_root.addWidget(next_run_tab)
        run_root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.workflow_tabs.addTab(self.run_tab, "Метки")

        # ---------------------- Вкладка 5: запуск и журнал --------------------
        self.results_tab = QWidget(); self.results_tab.setObjectName("tabPage")
        results_root = QVBoxLayout(self.results_tab); results_root.setContentsMargins(6, 6, 6, 6); results_root.setSpacing(7)
        results_intro = QLabel("Запуск, прогресс, результаты и журнал работы.")
        self.results_intro = results_intro
        results_intro.setObjectName("tabIntro"); results_intro.setWordWrap(True); results_root.addWidget(results_intro)

        self.actions_card = QFrame(); self.actions_card.setObjectName("actionCard")
        self.actions_layout = QVBoxLayout(self.actions_card); self.actions_layout.setContentsMargins(4, 3, 4, 4); self.actions_layout.setSpacing(4)
        action_title_row = QHBoxLayout(); action_title_row.setSpacing(6)
        action_title = QLabel("Управление запуском и результатами"); action_title.setObjectName("cardTitle"); action_title_row.addWidget(action_title, 1)
        self.actions_layout.addLayout(action_title_row)
        action_hint = QLabel("Запустите проверку или удалите ранее добавленные метки из выбранной базы.")
        action_hint.setObjectName("cardDescription")
        action_hint.setWordWrap(True)
        self.actions_layout.addWidget(action_hint)
        local_start_row = QHBoxLayout(); local_start_row.setSpacing(4)
        self.run_tab_start_button = QPushButton("Запустить действие")
        self.run_tab_start_button.setObjectName("primaryButton")
        self.run_tab_start_button.clicked.connect(self.start_comparison)
        local_start_row.addWidget(self.run_tab_start_button, 1)
        self.cleanup_button = QPushButton("Удалить метки из ИРБИС")
        self.cleanup_button.setObjectName("dangerButton")
        self.cleanup_button.clicked.connect(self.clean_markers)
        for button in (self.run_tab_start_button, self.cleanup_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        local_start_row.addWidget(self.cleanup_button, 1)
        self.actions_layout.addLayout(local_start_row)
        self.actions_buttons_layout = QGridLayout(); self.actions_buttons_layout.setHorizontalSpacing(4); self.actions_buttons_layout.setVerticalSpacing(4)
        # Служебные объекты остаются для логики состояния, но эти действия
        # больше не выводятся в интерфейсе блока запуска.
        self.cancel_button = QPushButton("Отменить", self.actions_card); self.cancel_button.setEnabled(False); self.cancel_button.hide()
        self.open_button = QPushButton("Открыть Excel-отчёт", self.actions_card); self.open_button.setEnabled(False); self.open_button.hide()
        self.open_modified_database_button = QPushButton("Открыть TXT-копию"); self.open_modified_database_button.setObjectName("mutedButton"); self.open_modified_database_button.setEnabled(False); self.open_modified_database_button.clicked.connect(self.open_modified_database)
        self.write_irbis_button = QPushButton("Отправить TXT в ИРБИС")
        self.write_irbis_button.setObjectName("secondaryButton"); self.write_irbis_button.setEnabled(False); self.write_irbis_button.clicked.connect(self.apply_results_to_irbis)
        self.clear_all_button = QPushButton("Очистить всё", self.actions_card); self.clear_all_button.hide()
        action_buttons = [self.open_modified_database_button, self.write_irbis_button]
        for i, button in enumerate(action_buttons):
            self.actions_buttons_layout.addWidget(button, i // 2, i % 2)
        for column in range(2): self.actions_buttons_layout.setColumnStretch(column, 1)
        self.action_buttons = action_buttons
        self.actions_layout.addLayout(self.actions_buttons_layout)

        self.progress = QProgressBar(); self.progress.setObjectName("mainProgress"); self.progress.setRange(0, 100); self.progress.setValue(0)
        self.progress.setMinimumWidth(0); self.progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.progress.hide()
        self.actions_layout.addWidget(self.progress)
        self.status_row = QHBoxLayout(); self.status_row.setSpacing(4)
        self.status_dot = QLabel(); self.status_dot.setObjectName("statusDot"); self.status_dot.setProperty("state", "idle"); self.status_dot.setFixedSize(6, 6); self.status_dot.hide()
        self.status_label = QLabel("Готово к работе"); self.status_label.setObjectName("statusLabel"); self.status_label.setWordWrap(True)
        self.status_row.addWidget(self.status_dot); self.status_row.addWidget(self.status_label, 1); self.actions_layout.addLayout(self.status_row)
        results_root.addWidget(self.actions_card)

        log_card = QFrame(); log_card.setObjectName("sectionCard")
        log_layout = QVBoxLayout(log_card); log_layout.setContentsMargins(4, 3, 4, 4); log_layout.setSpacing(4)
        log_header = QHBoxLayout(); log_header.setSpacing(6); log_title = QLabel("Журнал"); log_title.setObjectName("cardTitle"); log_header.addWidget(log_title); log_header.addStretch(); log_layout.addLayout(log_header)
        self.run_log = QTextEdit(); self.run_log.setObjectName("logEdit"); self.run_log.setReadOnly(True); self.run_log.setMinimumHeight(100); self.run_log.setPlaceholderText("Здесь будет отображаться ход подключения, загрузки и сравнения…")
        self.run_log.document().setMaximumBlockCount(self.RUN_JOURNAL_MAX_LINES)
        self._load_run_journal()
        log_layout.addWidget(self.run_log, 1); results_root.addWidget(log_card, 1)
        self.workflow_tabs.addTab(self.results_tab, "Запуск")
        self.workflow_tabs.currentChanged.connect(
            lambda _index: QTimer.singleShot(0, self._fit_scroll_content)
        )

        # Служебные элементы для совместимости с прежней логикой.
        self.table = QTableWidget(0, 6, self); self.table.hide()
        self.file_action_buttons = [self.database_button, self.clear_database_button, self.foreign_agents_button, self.clear_foreign_agents_button, self.add_excel_button, self.clear_excel_button]
        self.section_cards = [self.database_card, self.foreign_agents_card, self.excel_card, match_settings_card, compare_card, report_lists_card, report_create_card, marker_card]
        self.tools_card = compare_card
        self.left_panel = self.files_tab; self.right_panel = self.results_tab
        self.content_host = self.workflow_tabs
        self.content_grid = QGridLayout(); self.left_layout = files_root; self.right_layout = run_root
        self.tools_controls = QGridLayout()
        self._responsive_mode = None

        self._restore_irbis_config()
        self._apply_marker_settings_to_ui()
        for checkbox in (
            self.create_excel_report_check,
            self.report_only_check,
            *self.report_list_checks,
            self.report_deduplicate_check,
        ):
            checkbox.toggled.connect(self._queue_report_settings_autosave)
        self.report_sort_combo.currentIndexChanged.connect(self._queue_report_settings_autosave)
        self._marker_autosave_timer = QTimer(self)
        self._marker_autosave_timer.setSingleShot(True)
        self._marker_autosave_timer.timeout.connect(self._autosave_marker_settings)
        for edit in (self.substance_marker_edit, self.foreign_marker_edit, self.age_marker_edit):
            edit.textChanged.connect(self._queue_marker_settings_autosave)
        for spin in (self.substance_field_spin, self.foreign_field_spin, self.age_field_spin):
            spin.valueChanged.connect(self._queue_marker_settings_autosave)
        self._update_database_summary(); self._update_foreign_agents_summary(); self._update_excel_summary()
        self._set_default_outputs(force=False)
        self._apply_responsive_layout(force=True)

    @staticmethod
    def _take_all(layout) -> None:
        while layout.count():
            layout.takeAt(0)

    def _reflow_irbis_connection_form(self, _narrow: bool) -> None:
        if not hasattr(self, "irbis_connection_form"):
            return
        form = self.irbis_connection_form
        self._take_all(form)
        for column in range(4):
            form.setColumnStretch(column, 0)
            form.setColumnMinimumWidth(column, 0)

        labels = self.irbis_field_labels
        fields = {
            "host": self.irbis_host_edit,
            "port": self.irbis_port_spin,
            "login": self.irbis_login_edit,
            "password": self.irbis_password_box,
            "database": self.irbis_database_box,
            "query": self.irbis_query_edit,
        }

        # Макет подключения всегда состоит из двух равных колонок:
        # слева сервер/логин/база, справа порт/пароль/запрос.
        pairs = (("host", "port"), ("login", "password"), ("database", "query"))
        for pair_row, (left, right) in enumerate(pairs):
            label_row = pair_row * 2
            field_row = label_row + 1
            form.addWidget(labels[left], label_row, 0)
            form.addWidget(labels[right], label_row, 1)
            form.addWidget(fields[left], field_row, 0)
            form.addWidget(fields[right], field_row, 1)
        form.setColumnStretch(0, 1)
        form.setColumnStretch(1, 1)
        self.irbis_db_combo.setMinimumContentsLength(6)
        self.irbis_db_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.irbis_refresh_databases_button.setText("")
        self.irbis_refresh_databases_button.setFixedWidth(32)

    def _reflow_actions(self, columns: int) -> None:
        self._take_all(self.actions_buttons_layout)
        for column in range(8):
            self.actions_buttons_layout.setColumnStretch(column, 0)
        visible_buttons = [button for button in self.action_buttons if not button.isHidden()]
        for index, button in enumerate(visible_buttons):
            self.actions_buttons_layout.addWidget(button, index // columns, index % columns)
        for column in range(columns):
            self.actions_buttons_layout.setColumnStretch(column, 1)

    def _reflow_source_controls(self, narrow: bool, very_narrow: bool) -> None:
        groups = (
            (self.database_controls, self.database_list, self.database_button, self.clear_database_button),
            (self.foreign_agents_controls, self.foreign_agents_list, self.foreign_agents_button, self.clear_foreign_agents_button),
            (self.excel_controls, self.excel_list, self.add_excel_button, self.clear_excel_button),
        )
        for layout, list_widget, primary, clear in groups:
            self._take_all(layout)
            for column in range(3):
                layout.setColumnStretch(column, 0)
                layout.setColumnMinimumWidth(column, 0)
            if very_narrow:
                layout.addWidget(list_widget, 0, 0, 1, 2)
                layout.addWidget(primary, 1, 0)
                layout.addWidget(clear, 1, 1)
                layout.setColumnStretch(0, 1)
                layout.setColumnStretch(1, 1)
            else:
                layout.addWidget(list_widget, 0, 0, 2, 1)
                layout.addWidget(primary, 0, 1)
                layout.addWidget(clear, 1, 1)
                layout.setColumnStretch(0, 1)

    def _reflow_file_controls(self, narrow: bool) -> None:
        self._take_all(self.compare_controls)
        self._take_all(self.utility_controls)
        for column in range(4):
            self.compare_controls.setColumnStretch(column, 0)
            self.utility_controls.setColumnStretch(column, 0)

        if narrow:
            self.compare_controls.addWidget(self.create_excel_report_check, 0, 0, 1, 4)
            self.compare_controls.addWidget(self.report_only_check, 1, 0, 1, 4)
            self.compare_controls.setColumnStretch(0, 1)
            self.utility_controls.addWidget(self.next_marks_button, 0, 0, 1, 2)
            self.utility_controls.setColumnStretch(0, 1)
            self.utility_controls.setColumnStretch(1, 1)
        else:
            self.compare_controls.addWidget(self.create_excel_report_check, 0, 0)
            self.compare_controls.addWidget(self.report_only_check, 0, 1)
            self.compare_controls.setColumnStretch(0, 1)
            self.compare_controls.setColumnStretch(1, 1)
            self.utility_controls.addWidget(self.next_marks_button, 0, 0)
            self.utility_controls.setColumnStretch(0, 1)

    def _reflow_report_lists(self, narrow: bool) -> None:
        self._take_all(self.report_lists_grid)
        for column in range(2):
            self.report_lists_grid.setColumnStretch(column, 0)
        columns = 1 if narrow else 2
        for index, checkbox in enumerate(self.report_list_checks):
            self.report_lists_grid.addWidget(
                checkbox,
                index // columns,
                index % columns,
            )
        for column in range(columns):
            self.report_lists_grid.setColumnStretch(column, 1)

    def _reflow_main_grid(self) -> None:
        self._take_all(self.content_grid)
        for column in range(3):
            self.content_grid.setColumnStretch(column, 0)
        for row in range(5):
            self.content_grid.setRowStretch(row, 0)
        self.content_grid.addWidget(self.left_panel, 0, 0)
        self.content_grid.addWidget(self.right_panel, 1, 0)
        self.content_grid.addWidget(self.actions_card, 2, 0)
        self.content_grid.setColumnStretch(0, 1)

    def _apply_responsive_layout(self, force: bool = False) -> None:
        if not hasattr(self, "scroll_area"):
            return
        width = max(1, self.scroll_area.viewport().width())
        mode = tuple(width < breakpoint for breakpoint in (1500, 720, 900, 820, 800, 760, 700))
        if not force and mode == self._responsive_mode:
            QTimer.singleShot(0, self._fit_scroll_content)
            return
        self._responsive_mode = mode
        (
            compact_header,
            stack_irbis,
            compact,
            compact_tabs,
            short_start,
            very_compact,
            hide_logo,
        ) = mode

        if hasattr(self, "irbis_connection_form"):
            self._reflow_irbis_connection_form(very_compact)

        margin = 4 if very_compact else (6 if compact else 8)
        self.root_layout.setContentsMargins(margin, margin, margin, 0)
        self.root_layout.setSpacing(4 if very_compact else (6 if compact else 8))

        logo_size = 22 if compact else 26
        self.header_logo.setVisible(not hide_logo)
        if self.header_logo.isVisible():
            self.header_logo.setPixmap(QIcon(resource_path("assets", "irbis64_control_icon.png")).pixmap(logo_size, logo_size))
            self.header_logo.setFixedSize(logo_size + 2, logo_size + 2)
        title_font = self.main_title.font()
        title_font.setPointSize(10 if compact else 11)
        title_font.setBold(True)
        self.main_title.setFont(title_font)
        self.subtitle_primary.setVisible(width >= 900)

        self.start_button.setText("Запуск" if short_start else "Запустить проверку")
        self.start_button.setMinimumWidth(0)
        header_buttons = (
            (self.marker_settings_button, "Настройки"),
            (self.useful_links_button, "Полезные ссылки"),
            (self.update_button, "Проверить обновление"),
        )
        for button, full_text in header_buttons:
            button.setVisible(True)
            button.setText("" if compact_header else full_text)
            button.setToolTip(full_text)
            button.setMinimumWidth(32 if compact_header else 0)
            button.setMaximumWidth(32 if compact_header else 16777215)

        tab_titles = (
            ("Подключение", "Подключение к ИРБИС"),
            ("Источники", "Источники данных"),
            ("Списки", "Списки для проверки"),
            ("Метки", "Метки"),
            ("Запуск", "Запуск и журнал"),
        )
        for index, (short_title, full_title) in enumerate(tab_titles):
            self.workflow_tabs.setTabText(index, short_title)

        for intro_label in (self.irbis_intro, self.files_intro, self.lists_intro, self.markers_intro, self.results_intro):
            intro_label.hide()

        # Блоки подключения располагаются рядом только когда для обоих хватает
        # места; на меньшей ширине они складываются вертикально без обрезания.
        if hasattr(self, "irbis_columns"):
            self._take_all(self.irbis_columns)
            for column in range(2):
                self.irbis_columns.setColumnStretch(column, 0)
            if stack_irbis:
                self.irbis_columns.addWidget(self.connection_card, 0, 0)
                self.irbis_columns.addWidget(self.base_card, 1, 0)
                self.irbis_columns.setAlignment(self.connection_card, Qt.AlignmentFlag.AlignTop)
                self.irbis_columns.setAlignment(self.base_card, Qt.AlignmentFlag.AlignTop)
                self.irbis_columns.setColumnStretch(0, 1)
                self.irbis_columns.setColumnStretch(1, 0)
            else:
                self.irbis_columns.addWidget(self.connection_card, 0, 0)
                self.irbis_columns.addWidget(self.base_card, 0, 1)
                self.irbis_columns.setAlignment(self.connection_card, Qt.AlignmentFlag.AlignTop)
                self.irbis_columns.setAlignment(self.base_card, Qt.AlignmentFlag.AlignTop)
                self.irbis_columns.setColumnStretch(0, 3)
                self.irbis_columns.setColumnStretch(1, 2)

        if hasattr(self, "irbis_action_layout"):
            self._take_all(self.irbis_action_layout)
            for column in range(3):
                self.irbis_action_layout.setColumnStretch(column, 0)
            if very_compact:
                self.irbis_test_button.setMaximumWidth(16777215)
                self.irbis_action_layout.addWidget(self.irbis_test_button, 0, 0)
                self.irbis_action_layout.setColumnStretch(0, 1)
            else:
                self.irbis_test_button.setMaximumWidth(240)
                self.irbis_action_layout.addWidget(self.irbis_test_button, 0, 1)
                self.irbis_action_layout.setColumnStretch(0, 1)
            self.irbis_action_layout.addWidget(self.irbis_progress, 1, 0, 1, 3)
            self.irbis_action_layout.addWidget(self.irbis_status_box, 2, 0, 1, 3)

        if hasattr(self, "sources_grid"):
            self._take_all(self.sources_grid)
            if compact:
                self.sources_grid.addWidget(self.foreign_agents_card, 0, 0)
                self.sources_grid.addWidget(self.excel_card, 1, 0)
                self.sources_grid.setColumnStretch(0, 1)
                self.sources_grid.setColumnStretch(1, 0)
            else:
                self.sources_grid.addWidget(self.foreign_agents_card, 0, 0)
                self.sources_grid.addWidget(self.excel_card, 0, 1)
                self.sources_grid.setColumnStretch(0, 1)
                self.sources_grid.setColumnStretch(1, 1)

        if hasattr(self, "report_lists_grid"):
            self._reflow_report_lists(compact)

        if hasattr(self, "database_controls"):
            self._reflow_source_controls(compact, very_compact)
            self._reflow_file_controls(compact)

        if hasattr(self, "action_buttons"):
            self._reflow_actions(2)

        for card in self.section_cards:
            card.set_compact(True, very_compact)

        QTimer.singleShot(0, self._fit_scroll_content)

    def _fit_scroll_content(self) -> None:
        if not hasattr(self, "scroll_area") or self.scroll_area.widget() is None:
            return
        content = self.scroll_area.widget()
        desired_height = max(240, content.sizeHint().height())
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        available_height = screen.availableGeometry().height() if screen is not None else desired_height
        minimum_height = min(desired_height, available_height)

        # The content defines only the minimum size. The user can freely enlarge
        # the workspace in both directions, and the chosen size is restored later.
        self.setMaximumHeight(16777215)
        if self.minimumHeight() != minimum_height:
            self.setMinimumHeight(0)
            self.setMinimumHeight(minimum_height)

        if not self._initial_height_applied:
            target_height = self.height() if self._has_saved_window_height else minimum_height
            self.resize(self.width(), max(minimum_height, min(target_height, available_height)))
            self._initial_height_applied = True
        elif self.height() < minimum_height:
            self.resize(self.width(), minimum_height)

        viewport = self.scroll_area.viewport()
        content.setFixedHeight(max(viewport.height(), desired_height))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "section_cards"):
            self._apply_responsive_layout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._apply_responsive_layout(force=True))

    def _apply_style(self) -> None:
        # Основные элементы оставляем системному стилю Qt/Windows.
        # Здесь только небольшие типографические настройки, без карточек,
        # скруглений, цветных рамок и нестандартных кнопок.
        self.setStyleSheet(
            """
            QMainWindow, QDialog, QWidget#centralPage, QWidget#tabPage { background: #f8fafc; color: #202020; }
            QScrollArea#mainScroll { border: none; background: #f8fafc; }
            QFrame#headerCard, QFrame#irbisActions { border: none; background: transparent; }
            QFrame#sectionCard, QFrame#actionCard {
                border: 1px solid #cbd6e2;
                border-radius: 5px;
                background: #ffffff;
            }
            QFrame#dangerCard { border: 1px solid #dfcaca; border-radius: 5px; background: #ffffff; }
            QLabel { color: #202020; }
            QLabel#mainTitle { font-weight: 600; color: #151515; }
            QLabel#cardTitle { font-size: 13px; font-weight: 600; color: #006bd6; }
            QLabel#irbisStateDot { background: #d94a4a; border-radius: 5px; }
            QLabel#irbisStateDot[state="success"] { background: #35b85f; }
            QLabel#irbisStateDot[state="running"], QLabel#irbisStateDot[state="warning"] { background: #e4a11b; }
            QLabel#irbisStateDot[state="error"] { background: #d94a4a; }
            QLabel#directSourceDot { background: #35b85f; border-radius: 4px; }
            QLabel#directSourceDot[state="local"] { background: #6f7f8f; }
            QLabel#sourceStatusTitle { color: #202020; font-weight: 600; }
            QLabel#fieldLabel { color: #202020; }
            QLabel#tabIntro, QLabel#cardDescription, QLabel#statusLabel { color: #5a5a5a; }
            QLabel:disabled { color: #6b6b6b; }
            QTextEdit#logEdit, QTextEdit#plainLogEdit { font-family: Consolas, monospace; }
            QLineEdit, QComboBox, QSpinBox, QPushButton { min-height: 23px; }
            QPushButton#primaryButton {
                color: white;
                background: #0878e3;
                border: 1px solid #0870d2;
                border-radius: 4px;
                padding: 3px 10px;
            }
            QPushButton#primaryButton:hover { background: #006fd8; }
            QPushButton#primaryButton:pressed { background: #0064c4; }
            QPushButton#primaryButton:disabled { color: #e4e4e4; background: #8fb9df; border-color: #8fb9df; }
            QListWidget#compactList::item { min-height: 21px; }
            QProgressBar { min-height: 12px; max-height: 12px; }
            QProgressBar#mainProgress {
                min-height: 4px;
                max-height: 4px;
                border: none;
                border-radius: 2px;
                background: #c4c9cf;
                padding: 0;
                margin: 0;
                text-align: center;
            }
            QProgressBar#mainProgress::chunk {
                border: none;
                border-radius: 2px;
                background: #0078d4;
                margin: 0;
            }

            /* Вкладки и их содержимое образуют одну общую поверхность. */
            QTabWidget#workflowTabs::pane {
                border: none;
                background: palette(window);
                top: 0px;
            }
            QTabWidget#workflowTabs QTabBar {
                background: transparent;
                qproperty-drawBase: 0;
            }
            QTabWidget#workflowTabs QTabBar::tab {
                background: transparent;
                border: none;
                padding: 5px 10px;
                margin: 0px;
            }
            QTabWidget#workflowTabs QTabBar::tab:selected {
                color: palette(highlight);
                border: none;
                border-bottom: 2px solid palette(highlight);
            }
            QTabWidget#workflowTabs QTabBar::tab:hover:!selected {
                background: palette(alternate-base);
                border: none;
            }
            """
        )

    def _set_irbis_status(self, text: str, state: str = "success") -> None:
        self.irbis_status.setText(text)
        visual_state = state if state in {"success", "running", "warning", "error"} else "error"
        self.irbis_status_dot.setProperty("state", visual_state)
        self.irbis_status_dot.style().unpolish(self.irbis_status_dot)
        self.irbis_status_dot.style().polish(self.irbis_status_dot)
        self.irbis_status_dot.update()

    def _set_status(self, text: str, state: str = "idle") -> None:
        self.status_label.setText(text)
        self.status_dot.setProperty("state", state)
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)
        self.status_dot.update()

    def _update_excel_summary(self) -> None:
        paths = self._excel_paths()
        if not paths:
            self.excel_list.clear()
            placeholder = QListWidgetItem("Файлы не выбраны")
            placeholder.setData(Qt.ItemDataRole.UserRole, False)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.excel_list.addItem(placeholder)
            self.excel_summary_edit.clear()
            self.excel_summary_edit.setPlaceholderText("Файлы не выбраны")
            self.excel_summary_edit.setToolTip("Файлы не выбраны")
            return
        names = [Path(path).name for path in paths]
        if len(names) == 1:
            summary = names[0]
        else:
            summary = f"Выбрано файлов: {len(names)} — {names[0]}"
        self.excel_summary_edit.setText(summary)
        self.excel_summary_edit.setToolTip("\n".join(paths))

    def _update_foreign_agents_summary(self) -> None:
        self.foreign_agents_list.clear()
        path = self.foreign_agents_edit.text().strip()
        if path:
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole, True)
            self.foreign_agents_list.addItem(item)
            self.foreign_agents_list.setToolTip(path)
        else:
            placeholder = QListWidgetItem("Файлы не выбраны")
            placeholder.setData(Qt.ItemDataRole.UserRole, False)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.foreign_agents_list.addItem(placeholder)
            self.foreign_agents_list.setToolTip("Файлы не выбраны")

    def _update_database_summary(self) -> None:
        paths = self._database_paths()
        if not paths:
            self.database_list.clear()
            placeholder = QListWidgetItem("Файлы не выбраны")
            placeholder.setData(Qt.ItemDataRole.UserRole, False)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.database_list.addItem(placeholder)
            self.database_list.setToolTip("Файлы не выбраны")
            return
        self.database_list.setToolTip("\n".join(paths))

    def _clear_excel_files(self) -> None:
        self.excel_list.clear()
        self._update_excel_summary()
        self._set_default_outputs(force=True)

    def _clear_foreign_agents(self) -> None:
        self.foreign_agents_edit.clear()
        self._update_foreign_agents_summary()
        self._set_default_outputs(force=True)

    def _clear_database_files(self) -> None:
        self.database_list.clear()
        self.database_edit.clear()
        self._update_database_summary()
        self._set_default_outputs(force=True)

    def select_database(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите TXT-базы данных",
            "",
            "Текстовые файлы (*.txt);;Все файлы (*)",
        )
        if self.database_list.count() == 1 and not self.database_list.item(0).data(Qt.ItemDataRole.UserRole):
            self.database_list.clear()
        existing = set(self._database_paths())
        for path in paths:
            if path not in existing:
                item = QListWidgetItem(path)
                item.setData(Qt.ItemDataRole.UserRole, True)
                self.database_list.addItem(item)
                existing.add(path)
        if paths:
            self.database_edit.setText(paths[0])
            self._update_database_summary()
            self._set_default_outputs(force=True)

    def select_foreign_agents(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите реестр иностранных агентов",
            "",
            "Excel (*.xlsx *.xlsm);;Все файлы (*)",
        )
        if path:
            self.foreign_agents_edit.setText(path)
            self._update_foreign_agents_summary()
            self._set_default_outputs(force=True)

    def add_excel_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите Excel-файлы",
            "",
            "Excel (*.xlsx *.xlsm *.xls);;Все файлы (*)",
        )
        if self.excel_list.count() == 1 and not self.excel_list.item(0).data(Qt.ItemDataRole.UserRole):
            self.excel_list.clear()
        existing = set(self._excel_paths())
        for path in paths:
            if path not in existing:
                item = QListWidgetItem(path)
                item.setData(Qt.ItemDataRole.UserRole, True)
                self.excel_list.addItem(item)
                existing.add(path)
        if paths:
            self._update_excel_summary()
            self._set_default_outputs(force=True)

    def clear_all(self) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, APP_TITLE, "Сначала дождитесь завершения операции или нажмите «Отменить».")
            return
        self.database_edit.clear()
        self.database_list.clear()
        self._update_database_summary()
        self.foreign_agents_edit.clear()
        self._update_foreign_agents_summary()
        self.excel_list.clear()
        self._update_excel_summary()
        self.output_edit.clear()
        self.modified_database_edit.clear()
        self.progress.setValue(0)
        self._set_status("Готово к работе", "idle")
        self.table.setRowCount(0)
        self.progress_dialog.clear()
        self.open_button.setEnabled(False)
        self.open_modified_database_button.setEnabled(False)
        if hasattr(self, "write_irbis_button"):
            self.write_irbis_button.setEnabled(False)
        self.last_results = []
        self.last_summary = None
        self.last_output_path = ""
        self.last_modified_database_path = ""

    def select_output(self) -> None:
        initial = self.output_edit.text() or self._default_output_path()
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", initial, "Excel (*.xlsx)")
        if path:
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            self.output_edit.setText(path)

    def _default_output_folder(self) -> Path:
        database_paths = self._database_paths()
        excel_paths = self._excel_paths()
        if excel_paths:
            return Path(excel_paths[0]).parent
        if self.foreign_agents_edit.text():
            return Path(self.foreign_agents_edit.text()).parent
        if database_paths:
            return Path(database_paths[0]).parent
        return Path.home() / "Documents"

    def _default_output_path(self) -> str:
        name = f"ИРБИС64 Контроль_совпадения_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        return str(self._default_output_folder() / name)

    def _default_modified_database_path(self) -> str:
        database_paths = self._database_paths()
        source = Path(database_paths[0]) if database_paths else Path("База.TXT")
        name = f"{source.stem}_с_метками_18+_{datetime.now():%Y%m%d_%H%M%S}{source.suffix or '.TXT'}"
        return str(self._default_output_folder() / name)

    def _set_default_outputs(self, force: bool = False) -> None:
        if force or not self.output_edit.text().strip():
            self.output_edit.setText(self._default_output_path())
        if force or not self.modified_database_edit.text().strip():
            self.modified_database_edit.setText(self._default_modified_database_path())

    def _excel_paths(self) -> list[str]:
        return [
            self.excel_list.item(index).text()
            for index in range(self.excel_list.count())
            if self.excel_list.item(index).data(Qt.ItemDataRole.UserRole) is not False
        ]

    def _database_paths(self) -> list[str]:
        return [
            self.database_list.item(index).text()
            for index in range(self.database_list.count())
            if self.database_list.item(index).data(Qt.ItemDataRole.UserRole) is not False
        ]

    def _validate_inputs(self) -> tuple[list[str], str, list[str], str, str] | None:
        database_paths = self._database_paths()
        foreign_agents_path = self.foreign_agents_edit.text().strip()
        excel_paths = self._excel_paths()
        create_report = bool(self.marker_settings["create_excel_report"])
        report_only = bool(self.marker_settings["report_only"])
        output_path = (self.output_edit.text().strip() or self._default_output_path()) if create_report else ""
        modified_database_path = "" if report_only else (self.modified_database_edit.text().strip() or self._default_modified_database_path())

        if not database_paths:
            QMessageBox.warning(self, APP_TITLE, "Выберите хотя бы одну TXT-базу данных.")
            self.workflow_tabs.setCurrentIndex(1)
            return None
        missing_databases = [path for path in database_paths if not Path(path).is_file()]
        if missing_databases:
            QMessageBox.warning(self, APP_TITLE, "Некоторые TXT-базы не найдены:\n" + "\n".join(missing_databases))
            return None
        if not excel_paths and not foreign_agents_path:
            QMessageBox.warning(
                self,
                APP_TITLE,
                "Выберите хотя бы один источник проверки: реестр по наркотическим веществам или реестр иностранных агентов.",
            )
            self.workflow_tabs.setCurrentIndex(1)
            return None
        missing = [path for path in excel_paths if not Path(path).is_file()]
        if missing:
            QMessageBox.warning(self, APP_TITLE, "Некоторые Excel-файлы не найдены:\n" + "\n".join(missing))
            return None
        if foreign_agents_path and not Path(foreign_agents_path).is_file():
            QMessageBox.warning(self, APP_TITLE, "Файл реестра иностранных агентов не найден.")
            return None
        if foreign_agents_path and Path(foreign_agents_path).suffix.lower() not in {".xlsx", ".xlsm"}:
            QMessageBox.warning(self, APP_TITLE, "Реестр иностранных агентов должен быть файлом .xlsx или .xlsm.")
            return None
        if create_report:
            if not output_path.lower().endswith(".xlsx"):
                output_path += ".xlsx"
                self.output_edit.setText(output_path)
            source_paths = {Path(path).resolve() for path in excel_paths}
            if foreign_agents_path:
                source_paths.add(Path(foreign_agents_path).resolve())
            if Path(output_path).resolve() in source_paths:
                QMessageBox.warning(self, APP_TITLE, "Файл отчёта не должен совпадать с исходным Excel-файлом.")
                return None
        if not report_only and not modified_database_path.lower().endswith(".txt"):
            modified_database_path += ".txt"
            self.modified_database_edit.setText(modified_database_path)
        if not report_only and Path(modified_database_path).resolve() in {Path(path).resolve() for path in database_paths}:
            QMessageBox.warning(
                self,
                APP_TITLE,
                "TXT-копия должна сохраняться в новый файл, а не поверх исходной базы.",
            )
            return None
        if not report_only and create_report and Path(modified_database_path).resolve() == Path(output_path).resolve():
            QMessageBox.warning(self, APP_TITLE, "Пути Excel-отчёта и TXT-копии должны отличаться.")
            return None
        return database_paths, foreign_agents_path, excel_paths, output_path, modified_database_path

    def _validate_direct_inputs(self) -> tuple[str, list[str], str, dict[str, object]] | None:
        foreign_agents_path = self.foreign_agents_edit.text().strip()
        excel_paths = self._excel_paths()
        create_report = bool(self.marker_settings["create_excel_report"])
        output_path = (self.output_edit.text().strip() or self._default_output_path()) if create_report else ""
        params = self._irbis_params()

        if not str(params.get("login", "")).strip():
            QMessageBox.warning(self, APP_TITLE, "Введите логин каталогизатора ИРБИС.")
            self.workflow_tabs.setCurrentIndex(0)
            return None
        if not str(params.get("database", "")).strip():
            QMessageBox.warning(self, APP_TITLE, "Выберите базу ИРБИС из списка.")
            self.workflow_tabs.setCurrentIndex(0)
            return None
        if not excel_paths and not foreign_agents_path:
            QMessageBox.warning(
                self,
                APP_TITLE,
                "Выберите хотя бы один источник проверки: реестр по наркотическим веществам или реестр иностранных агентов.",
            )
            self.workflow_tabs.setCurrentIndex(1)
            return None
        missing = [path for path in excel_paths if not Path(path).is_file()]
        if missing:
            QMessageBox.warning(self, APP_TITLE, "Некоторые Excel-файлы не найдены:\n" + "\n".join(missing))
            return None
        if foreign_agents_path and not Path(foreign_agents_path).is_file():
            QMessageBox.warning(self, APP_TITLE, "Файл реестра иностранных агентов не найден.")
            return None
        if foreign_agents_path and Path(foreign_agents_path).suffix.lower() not in {".xlsx", ".xlsm"}:
            QMessageBox.warning(self, APP_TITLE, "Реестр иностранных агентов должен быть файлом .xlsx или .xlsm.")
            return None
        if create_report:
            if not output_path.lower().endswith(".xlsx"):
                output_path += ".xlsx"
                self.output_edit.setText(output_path)
            source_paths = {Path(path).resolve() for path in excel_paths}
            if foreign_agents_path:
                source_paths.add(Path(foreign_agents_path).resolve())
            if Path(output_path).resolve() in source_paths:
                QMessageBox.warning(self, APP_TITLE, "Файл отчёта не должен совпадать с исходным Excel-файлом.")
                return None
        return foreign_agents_path, excel_paths, output_path, params

    def start_comparison(self) -> None:
        if not self._sync_marker_settings_from_ui(save=True, show_message=False):
            return

        direct_mode = self.direct_irbis_checkbox.isChecked()
        if direct_mode:
            validated_direct = self._validate_direct_inputs()
            if not validated_direct:
                return
            foreign_agents_path, excel_paths, output_path, irbis_params = validated_direct
            database_paths: list[str] = []
            modified_database_path = ""
        else:
            validated = self._validate_inputs()
            if not validated:
                return
            database_paths, foreign_agents_path, excel_paths, output_path, modified_database_path = validated
            irbis_params = {}

        self.last_run_direct = direct_mode
        self.last_run_report_only = bool(self.marker_settings.get("report_only", False))
        self.table.setRowCount(0)
        selected_sources = []
        if excel_paths:
            selected_sources.append(f"реестр по наркотическим веществам ({len(excel_paths)} файл.)")
        if foreign_agents_path:
            selected_sources.append("реестр иностранных агентов")
        sources_text = ", ".join(selected_sources)

        self.workflow_tabs.setCurrentIndex(4)
        self.progress_dialog.clear()
        self._append_progress("Подготовка к проверке всех выбранных реестров…")
        self.progress.setValue(0)
        self.progress.show()
        self.open_button.setEnabled(False)
        self.open_modified_database_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.run_tab_start_button.setEnabled(False)
        self.create_matches_excel_button.setEnabled(False)
        self.marker_settings_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.write_irbis_button.setEnabled(False)
        if hasattr(self, "cleanup_button"):
            self.cleanup_button.setEnabled(False)
        self._set_status("Подготовка к проверке выбранных реестров…", "running")
        self.last_output_path = output_path
        self.last_modified_database_path = modified_database_path

        if direct_mode:
            self._append_progress(
                f"Прямой режим ИРБИС: {irbis_params['host']}:{irbis_params['port']} / {irbis_params['database']}"
            )
            self._append_progress(
                f"Полная TXT-копия не создаётся • пакет чтения: {irbis_params.get('page_size', 500)} записей"
            )
            self._append_progress(f"Запрос выборки: {irbis_params.get('query', 'I=$')}")
        else:
            self._append_progress(f"TXT-баз: {len(database_paths)}")
            for path in database_paths:
                self._append_progress(f"База: {path}")
            self._append_progress(f"TXT-копия результата: {modified_database_path}")

        self._append_progress(f"Будут проверены источники: {sources_text}")
        self._append_progress(f"Файлов реестра по наркотическим веществам: {len(excel_paths)}")
        self._append_progress(f"Реестр иностранных агентов: {foreign_agents_path or 'не выбран'}")
        self._append_progress(
            f"Excel-отчёт: {output_path}" if output_path else "Excel-отчёт: не создаётся"
        )
        self._append_progress(
            f"Метки: вещества #{int(self.marker_settings['substance_marker_field']):03d} — "
            f"{self.marker_settings['substance_marker'] or 'отключена'}; "
            f"иноагенты #{int(self.marker_settings['foreign_agent_marker_field']):03d} — "
            f"{self.marker_settings['foreign_agent_marker_template'] or 'отключена'}; "
            f"#{int(self.marker_settings['age_marker_field']):03d} — "
            f"{self.marker_settings['age_marker'] or 'отключена'}."
        )

        report_options = {
            "enabled": bool(self.marker_settings["create_excel_report"]),
            "substances": bool(self.marker_settings["report_substances"]),
            "foreign_agents": bool(self.marker_settings["report_foreign_agents"]),
            "combined": bool(self.marker_settings["report_combined"]),
            "summary": bool(self.marker_settings["report_summary"]),
            "deduplicate": bool(self.marker_settings["report_deduplicate"]),
            "sort": str(self.marker_settings["report_sort"]),
            "report_only": bool(self.marker_settings["report_only"]),
        }

        self.thread = QThread(self)
        if direct_mode:
            self.worker = DirectIrbisComparisonWorker(
                host=str(irbis_params["host"]),
                port=int(irbis_params["port"]),
                login=str(irbis_params["login"]),
                password=str(irbis_params["password"]),
                database=str(irbis_params["database"]),
                query=str(irbis_params["query"]),
                page_size=int(irbis_params.get("page_size", 500)),
                foreign_agents_path=foreign_agents_path,
                excel_paths=excel_paths,
                output_path=output_path,
                use_isbn_matching=bool(self.marker_settings["use_isbn_matching"]),
                use_title_fallback=bool(self.marker_settings["use_title_fallback"]),
                use_fuzzy=False,
                fuzzy_threshold=90,
                report_options=report_options,
                substance_marker=self.marker_settings["substance_marker"],
                foreign_agent_marker_template=self.marker_settings["foreign_agent_marker_template"],
                age_marker=self.marker_settings["age_marker"],
                substance_marker_field=int(self.marker_settings["substance_marker_field"]),
                foreign_agent_marker_field=int(self.marker_settings["foreign_agent_marker_field"]),
                age_marker_field=int(self.marker_settings["age_marker_field"]),
                backup_dir=str(app_data_dir() / "backups"),
            )
        else:
            self.worker = ComparisonWorker(
                database_paths,
                foreign_agents_path,
                excel_paths,
                output_path,
                modified_database_path,
                bool(self.marker_settings["use_isbn_matching"]),
                bool(self.marker_settings["use_title_fallback"]),
                False,
                90,
                report_options,
                self.marker_settings["substance_marker"],
                self.marker_settings["foreign_agent_marker_template"],
                self.marker_settings["age_marker"],
                int(self.marker_settings["substance_marker_field"]),
                int(self.marker_settings["foreign_agent_marker_field"]),
                int(self.marker_settings["age_marker_field"]),
            )
        self._save_irbis_config()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.worker.cancelled.connect(self.thread.quit)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.cancelled.connect(self.on_cancelled)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._cleanup_worker)
        self.thread.start()

    def cancel_comparison(self) -> None:
        if self.worker:
            self._set_status("Отмена…", "warning")
            self.worker.request_cancel()
            self.cancel_button.setEnabled(False)

    @pyqtSlot(int, str)
    def on_progress(self, percent: int, text: str) -> None:
        self.progress.show()
        self.progress.setValue(max(0, min(100, percent)))
        self._set_status(text, "running")
        self.progress_dialog.set_progress(percent, text)
        self._append_progress(text)

    @pyqtSlot(object, object)
    def on_finished(self, results: list[MatchResult], summary: ComparisonSummary) -> None:
        self.progress.hide()
        self.last_results = results
        self.last_summary = summary
        self._fill_preview(results)
        self.open_button.setEnabled(Path(self.last_output_path).is_file())
        self._save_irbis_config()

        unique_records = len(
            {
                result.database.record_number
                for result in results
                if result.status == "Совпадение" and result.database is not None
            }
        )

        report_only = self.last_run_report_only
        if report_only:
            self.last_modified_database_path = ""
            self.open_modified_database_button.setEnabled(False)
            self.write_irbis_button.setEnabled(False)
            target_name = "ИРБИС" if self.last_run_direct else "TXT-базы"
            self._set_status(f"Готово. Отчёт создан; {target_name} не изменялись", "idle")
            self._append_progress(
                f"Завершено в режиме только отчёта: найдено уникальных записей {unique_records}; "
                f"{target_name} не изменялись."
            )
        elif self.last_run_direct:
            self.last_modified_database_path = ""
            self.open_modified_database_button.setEnabled(False)
            self.write_irbis_button.setEnabled(False)
            self._set_status(
                f"Готово. ИРБИС изменён: {summary.modified_database_records}; "
                f"вещества: {summary.substance_matched_records}; "
                f"иноагенты: {summary.foreign_agent_matched_records}",
                "idle",
            )
            self._append_progress(
                f"Завершено: прочитано записей ИРБИС {summary.database_records:,}; "
                f"по веществам совпало {summary.matched_excel_rows} из {summary.excel_rows}; "
                f"по иноагентам найдено {summary.matched_foreign_agent_rows} из {summary.foreign_agent_rows}; "
                f"уникальных найденных MFN: {unique_records}; "
                f"изменено записей непосредственно на сервере: {summary.modified_database_records}."
            )
        else:
            self.last_modified_database_path = summary.modified_database_file or self.last_modified_database_path
            modified_paths = [path.strip() for path in self.last_modified_database_path.split(";") if path.strip()]
            self.open_modified_database_button.setEnabled(any(Path(path).is_file() for path in modified_paths))
            self.write_irbis_button.setEnabled(
                bool(len(modified_paths) == 1 and Path(modified_paths[0]).is_file() and Path(self.irbis_manifest_edit.text().strip()).is_file())
            )
            self._set_status(
                f"Готово. Вещества: {summary.substance_matched_records}; "
                f"иноагенты: {summary.foreign_agent_matched_records}; "
                f"помечено TXT: {summary.modified_database_records}",
                "idle",
            )
            self._append_progress(
                f"Завершено: по веществам совпало {summary.matched_excel_rows} из {summary.excel_rows}; "
                f"по иноагентам найдено {summary.matched_foreign_agent_rows} из {summary.foreign_agent_rows}; "
                f"всего уникальных записей TXT: {unique_records}; "
                f"в TXT-копии помечено {summary.modified_database_records}."
            )

        if summary.probable_rows:
            self._append_progress(
                f"Возможных совпадений: {summary.probable_rows}. Они не получили метки."
            )
        if summary.warnings:
            self._append_progress("Предупреждения:")
            for warning in summary.warnings:
                self._append_progress("• " + warning)
        self.progress_dialog.finish("Готово.", 100)

        substance_marker = self.marker_settings["substance_marker"] or "не добавляется"
        foreign_marker = self.marker_settings["foreign_agent_marker_template"].replace("{name}", "АВТОР") or "не добавляется"
        age_marker = self.marker_settings["age_marker"] or "не добавляется"
        substance_field = int(self.marker_settings["substance_marker_field"])
        foreign_field = int(self.marker_settings["foreign_agent_marker_field"])
        age_field = int(self.marker_settings["age_marker_field"])
        report_result = self.last_output_path or "не создавался"

        if report_only:
            QMessageBox.information(
                self,
                APP_TITLE,
                "Сравнение завершено в режиме «Только отчёт».\n"
                "Метки не добавлялись, исходные записи не изменялись.\n\n"
                f"Найдено уникальных записей: {unique_records}\n"
                f"Excel-отчёт: {report_result}",
            )
        elif self.last_run_direct:
            QMessageBox.information(
                self,
                APP_TITLE,
                "Сравнение завершено. Изменения записаны непосредственно в ИРБИС.\n"
                "Полная TXT-копия базы не создавалась.\n\n"
                "Метки для найденных записей:\n"
                f"• по веществам #{substance_field:03d}: {substance_marker};\n"
                f"• по иноагентам #{foreign_field:03d}: {foreign_marker};\n"
                f"• поле #{age_field:03d}: {age_marker}.\n\n"
                f"Прочитано записей ИРБИС: {summary.database_records}\n"
                f"Изменено записей ИРБИС: {summary.modified_database_records}\n"
                f"Совпавших строк по веществам: {summary.matched_excel_rows}\n"
                f"Иноагентов с совпадениями: {summary.matched_foreign_agent_rows}\n\n"
                f"Excel-отчёт: {report_result}",
            )
        else:
            QMessageBox.information(
                self,
                APP_TITLE,
                "Сравнение завершено. Результаты сохранены автоматически.\n\n"
                f"Помечено записей TXT: {summary.modified_database_records}\n"
                f"Excel-отчёт: {report_result}\n"
                f"TXT-копия: {self.last_modified_database_path}",
            )

    @pyqtSlot(str)
    def on_failed(self, error_text: str) -> None:
        self.progress.hide()
        self._set_status("Ошибка", "error")
        self.progress_dialog.finish("Ошибка. Подробности показаны ниже.", 0)
        self._append_progress(error_text)
        self.workflow_tabs.setCurrentIndex(4)
        QMessageBox.critical(
            self,
            APP_TITLE,
            "Во время сравнения произошла ошибка. Подробности показаны в журнале на вкладке «Запуск и журнал».",
        )

    @pyqtSlot(str)
    def on_cancelled(self, message: str) -> None:
        self.progress.hide()
        self._set_status(message, "warning")
        self.progress_dialog.finish(message, self.progress.value())

    @pyqtSlot()
    def _cleanup_worker(self) -> None:
        self.progress.hide()
        if self.thread:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self.start_button.setEnabled(True)
        self.run_tab_start_button.setEnabled(True)
        self.create_matches_excel_button.setEnabled(True)
        self.marker_settings_button.setEnabled(True)
        if hasattr(self, "cleanup_button"):
            self.cleanup_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _fill_preview(self, results: list[MatchResult]) -> None:
        unique_records = {
            result.database.record_number
            for result in results
            if result.status == "Совпадение" and result.database is not None
        }
        self.table.setRowCount(0)
        self._append_progress(f"Подтверждённых записей TXT для отчёта: {len(unique_records)}")

    @staticmethod
    def _publication_text(database) -> str:
        parts = []
        for item in database.publication:
            city_match = re.search(r"\^A([^\^]*)", item)
            publisher_match = re.search(r"\^C([^\^]*)", item)
            year_match = re.search(r"\^D([^\^]*)", item)
            values = [
                match.group(1).strip()
                for match in (city_match, publisher_match, year_match)
                if match and match.group(1).strip()
            ]
            if values:
                parts.append(", ".join(values))
        return " | ".join(dict.fromkeys(parts))

    def _append_progress(self, text: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {text}"
        self.progress_dialog.append_line(line)
        if hasattr(self, "run_log"):
            self.run_log.append(line)
        self._journal_lines.append(line)
        self._journal_save_timer.start(250)

    def _load_run_journal(self) -> None:
        try:
            lines = run_journal_path().read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            lines = []
        self._journal_lines.extend(lines[-self.RUN_JOURNAL_MAX_LINES:])
        if self._journal_lines:
            self.run_log.setPlainText("\n".join(self._journal_lines))
            scrollbar = self.run_log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _save_run_journal(self) -> None:
        try:
            path = run_journal_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = "\n".join(self._journal_lines)
            path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        except OSError:
            # Ошибка журнала не должна прерывать основную проверку базы.
            pass

    def open_report(self) -> None:
        if self.last_output_path and Path(self.last_output_path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output_path))
        else:
            QMessageBox.warning(self, APP_TITLE, "Файл отчёта не найден.")

    def open_modified_database(self) -> None:
        paths = [path.strip() for path in self.last_modified_database_path.split(";") if path.strip()]
        if paths and Path(paths[0]).is_file():
            target = Path(paths[0]).parent if len(paths) > 1 else Path(paths[0])
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        else:
            QMessageBox.warning(self, APP_TITLE, "TXT-копия не найдена.")

    def closeEvent(self, event) -> None:
        if self.worker and self.thread and self.thread.isRunning():
            answer = QMessageBox.question(
                self,
                APP_TITLE,
                "Сравнение ещё выполняется. Отменить его и закрыть программу?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.request_cancel()
            if not self.thread.wait(10000):
                QMessageBox.warning(
                    self,
                    APP_TITLE,
                    "Операция ещё завершается. Закройте программу после появления сообщения об отмене.",
                )
                event.ignore()
                return
        if self.irbis_thread and self.irbis_thread.isRunning():
            QMessageBox.warning(self, APP_TITLE, "Дождитесь завершения операции с ИРБИС перед закрытием программы.")
            event.ignore()
            return
        try:
            self._save_irbis_config()
        except Exception:
            pass
        try:
            self._save_window_state()
        except Exception:
            pass
        self._save_run_journal()
        event.accept()


def main() -> int:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("IRBIS64Control.DesktopApp")
        except Exception:
            pass

    app = QApplication(sys.argv)
    install_russian_ui(app)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    apply_light_palette(app)
    app.setWindowIcon(QIcon(resource_path("assets", "irbis64_control.ico")))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
