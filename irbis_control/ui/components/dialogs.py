from __future__ import annotations

import base64
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QSize, QStandardPaths, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from irbis_control import APP_TITLE as APP_TITLE
from irbis_control.core.manual_review_memory import (
    clear_review_decision_memory,
    export_review_decision_memory,
    import_review_decision_memory,
    load_review_decision_rows,
    remove_review_decision_keys,
)
from irbis_control.infrastructure.atomic_io import atomic_write_text
from irbis_control.paths import icon_path
from irbis_control.reporting.models import ResultDiffRow
from irbis_control.reporting.result_diff import (
    compare_result_files,
    compare_text_files,
)
from irbis_control.ui.context_menu import COPY_TEXT, new_context_menu
from irbis_control.ui.message_box import AppMessageBox as QMessageBox
from irbis_control.ui.theme import (
    result_diff_fill_colors,
    useful_link_foreground,
)


class CopyableTableWidget(QTableWidget):
    """QTableWidget с копированием выделенных ячеек через Ctrl+C и контекстное меню."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._copy_menu = None
        self.context_menu_preparer = None
        self.context_menu_builder = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_copy_menu)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection_to_clipboard()
            event.accept()
            return
        super().keyPressEvent(event)

    def copy_selection_to_clipboard(self) -> None:
        indexes = sorted(self.selectedIndexes(), key=lambda index: (index.row(), index.column()))
        if not indexes:
            current = self.currentIndex()
            if current.isValid():
                indexes = [current]
        if not indexes:
            return

        selected = {(index.row(), index.column()) for index in indexes}
        rows = sorted({row for row, _column in selected})
        columns = sorted({column for _row, column in selected})
        lines: list[str] = []
        for row in rows:
            values: list[str] = []
            for column in columns:
                if (row, column) not in selected:
                    values.append("")
                    continue
                item = self.item(row, column)
                values.append(item.text() if item is not None else "")
            lines.append("\t".join(values))
        QApplication.clipboard().setText("\n".join(lines))

    def _show_copy_menu(self, position) -> None:
        if self._copy_menu is not None:
            self._copy_menu.close()
        if self.context_menu_preparer is not None:
            self.context_menu_preparer(position)
        menu = new_context_menu(self)
        menu.add_app_action(
            COPY_TEXT,
            self.copy_selection_to_clipboard,
            enabled=bool(self.selectedIndexes()) or self.currentIndex().isValid(),
        )
        if self.context_menu_builder is not None:
            self.context_menu_builder(menu, position)
        menu.ensure_action_width()
        self._copy_menu = menu

        def cleanup() -> None:
            if self._copy_menu is menu:
                self._copy_menu = None
            menu.deleteLater()

        menu.aboutToHide.connect(cleanup)
        # popup() не запускает вложенный event loop. Это исключает падение
        # Windows/Qt при закрытии стилизованного меню таблицы по ПКМ.
        menu.popup(self.viewport().mapToGlobal(position))


def _powershell_literal(value: str) -> str:
    """Экранирует строку для одинарных литералов PowerShell."""
    return "'" + value.replace("'", "''") + "'"


def _open_excel_at_source(
    source_file: str,
    sheet_name: str,
    row_number: int,
    parent: QWidget | None = None,
) -> None:
    """Открывает исходную таблицу и, когда возможно, переводит Excel на нужную строку."""
    path = Path(source_file).expanduser()
    try:
        path = path.resolve()
    except OSError:
        path = path.absolute()

    if not path.exists():
        QMessageBox.warning(
            parent,
            APP_TITLE,
            f"Исходный файл не найден:\n{path}",
        )
        return

    row_number = max(1, int(row_number or 1))

    # На Windows используем COM через встроенный PowerShell: это позволяет не только
    # открыть книгу, но и активировать исходный лист и выделить нужную строку.
    if sys.platform.startswith("win"):
        path_ps = _powershell_literal(str(path))
        sheet_ps = _powershell_literal(str(sheet_name or ""))
        script = f"""
$ErrorActionPreference = 'Stop'
$path = {path_ps}
$sheetName = {sheet_ps}
$rowNumber = {row_number}
try {{
    try {{
        $excel = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application')
    }} catch {{
        $excel = New-Object -ComObject Excel.Application
    }}
    $excel.Visible = $true
    $workbook = $null
    foreach ($wb in @($excel.Workbooks)) {{
        try {{
            if ($wb.FullName -ieq $path) {{
                $workbook = $wb
                break
            }}
        }} catch {{ }}
    }}
    if ($null -eq $workbook) {{
        $workbook = $excel.Workbooks.Open($path)
    }}
    $worksheet = $null
    if ($sheetName) {{
        try {{ $worksheet = $workbook.Worksheets.Item($sheetName) }} catch {{ }}
    }}
    if ($null -eq $worksheet) {{
        $worksheet = $workbook.ActiveSheet
    }}
    $worksheet.Activate() | Out-Null
    $worksheet.Rows.Item($rowNumber).Select() | Out-Null
    try {{
        $excel.ActiveWindow.ScrollRow = [Math]::Max(1, $rowNumber - 4)
    }} catch {{ }}
    try {{ $excel.WindowState = -4137 }} catch {{ }}
    $excel.Visible = $true
}} catch {{
    try {{ Start-Process -FilePath $path }} catch {{ }}
}}
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            return
        except OSError:
            # Если PowerShell недоступен, ниже откроем файл стандартным приложением.
            pass

    # На других ОС (или без PowerShell) открываем исходный файл ассоциированным
    # табличным редактором. Переход на строку зависит от возможностей приложения.
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        QMessageBox.warning(
            parent,
            APP_TITLE,
            f"Не удалось открыть исходный файл:\n{path}\n\nЛист: {sheet_name}\nСтрока: {row_number}",
        )


class ConfirmationMemoryDialog(QDialog):
    """Просмотр и удаление сохранённых ручных решений."""

    def __init__(self, memory_path: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._memory_path = Path(memory_path)
        self._row_keys: list[str] = []
        self.setWindowTitle("Память решений")
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        self.resize(1050, 560)
        self.setMinimumSize(760, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        title = QLabel("Сохранённые решения")
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        description = QLabel(
            "Здесь хранятся соответствия, которые вы раньше подтвердили или отклонили вручную. "
            "При следующей проверке программа автоматически применяет то же решение к такому же автору "
            "и кандидату реестра. Удалённое решение снова потребует ручной проверки. "
            "Память хранится отдельно от программы и сохраняется при установке обновлений."
        )
        description.setObjectName("cardDescription")
        description.setWordWrap(True)
        root.addWidget(description)

        self.count_label = QLabel()
        self.count_label.setObjectName("statusLabel")
        root.addWidget(self.count_label)

        headers = [
            "Значение в ИРБИС",
            "Подтверждённый кандидат",
            "№ реестра",
            "Способ совпадения",
            "Решение",
            "Дата решения",
        ]
        self.table = CopyableTableWidget(0, len(headers))
        self.table.setObjectName("resultsTable")
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 260)
        header.resizeSection(1, 260)
        header.resizeSection(2, 110)
        header.resizeSection(3, 240)
        header.resizeSection(4, 130)
        header.resizeSection(5, 170)
        self.table.context_menu_preparer = self._prepare_memory_context_menu
        self.table.context_menu_builder = self._populate_memory_context_menu
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.import_button = QPushButton("Импортировать")
        self.import_button.setObjectName("mutedButton")
        self.import_button.clicked.connect(self._import_memory)
        buttons.addWidget(self.import_button)

        self.export_button = QPushButton("Экспортировать")
        self.export_button.setObjectName("mutedButton")
        self.export_button.clicked.connect(self._export_memory)
        buttons.addWidget(self.export_button)

        self.delete_selected_button = QPushButton("Удалить выбранные")
        self.delete_selected_button.setObjectName("dangerButton")
        self.delete_selected_button.clicked.connect(self._delete_selected)
        buttons.addWidget(self.delete_selected_button)

        self.clear_button = QPushButton("Очистить всю память")
        self.clear_button.setObjectName("dangerButton")
        self.clear_button.clicked.connect(self._clear_all)
        buttons.addWidget(self.clear_button)
        buttons.addStretch()

        close_button = QPushButton("Закрыть")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self._refresh()

    @staticmethod
    def _format_confirmed_at(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        return parsed.astimezone().strftime("%d.%m.%Y %H:%M")

    def _refresh(self) -> None:
        rows = load_review_decision_rows(self._memory_path)
        self._row_keys = [row.get("key", "") for row in rows]
        self.table.clearContents()
        self.table.setRowCount(len(rows))

        for table_row, row in enumerate(rows):
            values = [
                row.get("database_value", ""),
                row.get("registry_value", ""),
                row.get("registry_number", ""),
                row.get("method", ""),
                row.get("decision", ""),
                self._format_confirmed_at(row.get("decided_at", "")),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row.get("key", ""))
                self.table.setItem(table_row, column, item)

        self.table.resizeRowsToContents()
        count = len(rows)
        self.count_label.setText(f"Сохранено решений: {count}")
        self.delete_selected_button.setEnabled(count > 0)
        self.clear_button.setEnabled(count > 0)
        self.export_button.setEnabled(count > 0)

    def _export_memory(self) -> None:
        default_path = str(Path.home() / "Память решений ИРБИС64.json")
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Экспорт памяти решений",
            default_path,
            "Файлы памяти решений (*.json)",
        )
        if not destination:
            return
        try:
            count = export_review_decision_memory(self._memory_path, destination)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось экспортировать память решений:\n{exc}")
            return
        QMessageBox.information(self, APP_TITLE, f"Экспортировано решений: {count}\n\n{destination}")

    def _import_memory(self) -> None:
        source, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Импорт памяти решений",
            str(Path.home()),
            "Файлы памяти решений (*.json)",
        )
        if not source:
            return
        answer = QMessageBox.question(
            self,
            APP_TITLE,
            "Добавить решения из выбранного файла к текущей памяти?\n\n"
            "При совпадении записи будет использовано импортированное решение.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            approved, rejected = import_review_decision_memory(source, self._memory_path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось импортировать память решений:\n{exc}")
            return
        self._refresh()
        QMessageBox.information(
            self,
            APP_TITLE,
            f"Импорт завершён.\nПодтверждённых решений: {approved}\nОтклонённых решений: {rejected}",
        )

    def _prepare_memory_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if index.isValid() and index.row() not in {selected.row() for selected in self.table.selectedIndexes()}:
            self.table.clearSelection()
            self.table.selectRow(index.row())
            self.table.setCurrentCell(index.row(), index.column())

    def _populate_memory_context_menu(self, menu, _position) -> None:
        selected_rows = {selected.row() for selected in self.table.selectedIndexes()}
        menu.addSeparator()
        menu.add_app_action(
            "Удалить выбранные",
            lambda: self._delete_selected_from_context_menu(menu),
            enabled=bool(selected_rows),
        )

    def _selected_keys(self) -> set[str]:
        selected_rows = {index.row() for index in self.table.selectedIndexes()}
        return {self._row_keys[row] for row in selected_rows if 0 <= row < len(self._row_keys) and self._row_keys[row]}

    def _delete_selected_from_context_menu(self, menu) -> None:
        keys = self._selected_keys()
        if not keys:
            return

        # Таблица полностью перестраивается после удаления. Дожидаемся закрытия
        # QMenu, чтобы не уничтожать его связанные объекты внутри triggered().
        menu.close()
        QTimer.singleShot(0, lambda keys=keys: self._delete_keys(keys))

    def _delete_keys(self, keys: set[str]) -> None:
        keys = {key for key in keys if key}
        if not keys:
            return
        try:
            remove_review_decision_keys(self._memory_path, keys)
        except OSError as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось изменить память решений:\n{exc}")
            return
        self._refresh()

    def _delete_selected(self) -> None:
        keys = self._selected_keys()
        if not keys:
            QMessageBox.information(self, APP_TITLE, "Выберите одну или несколько строк для удаления.")
            return
        self._delete_keys(keys)

    def _clear_all(self) -> None:
        if not self._row_keys:
            return
        answer = QMessageBox.question(
            self,
            APP_TITLE,
            "Удалить все сохранённые решения?\n\n"
            "При следующей проверке эти совпадения снова потребуют ручного решения.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            clear_review_decision_memory(self._memory_path)
        except OSError as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось очистить память решений:\n{exc}")
            return
        self._refresh()


class ProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(460, 260)
        self.setMinimumSize(360, 220)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        self.status_label = QLabel("Ожидание...")
        root.addWidget(self.status_label)

        self.progress_label = QLabel("Выполнено: 0%")
        self.progress_label.setObjectName("progressValueLabel")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.progress_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("dialogProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Выполнено: %p%")
        # На Windows нативный стиль рисует встроенный текст поверх тонкой
        # полосы индикатора. Процент показывается отдельной строкой выше.
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self.text_edit = QTextEdit()
        self.text_edit.setObjectName("plainLogEdit")
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText("Здесь будет отображаться ход выполнения.")
        root.addWidget(self.text_edit, 1)

        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.setObjectName("mutedButton")
        self.cancel_button.clicked.connect(self._request_cancel)
        self.cancel_button.hide()
        self.close_button = QPushButton("Закрыть")
        self.close_button.setObjectName("primaryButton")
        self.close_button.clicked.connect(self.hide)
        bottom = QHBoxLayout()
        bottom.addWidget(self.cancel_button)
        bottom.addStretch()
        bottom.addWidget(self.close_button)
        root.addLayout(bottom)

    def start(self, text: str, *, cancellable: bool = False) -> None:
        self.clear()
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.setEnabled(cancellable)
        self.set_running(True)
        self.set_progress(0, text)
        self.show()
        self.raise_()
        self.activateWindow()
        QApplication.processEvents()

    def set_running(self, running: bool) -> None:
        self.close_button.setEnabled(not running)
        if not running:
            self.cancel_button.setEnabled(False)

    def _request_cancel(self) -> None:
        if not self.cancel_button.isEnabled():
            return
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Отмена запрошена…")
        self.cancel_requested.emit()

    def set_progress(self, percent: int, text: str) -> None:
        value = max(0, min(100, percent))
        self.progress.setValue(value)
        self.progress_label.setText(f"Выполнено: {value}%")
        self.status_label.setText(text)

    def append_line(self, text: str) -> None:
        self.text_edit.append(text)
        QApplication.processEvents()

    def clear(self) -> None:
        self.text_edit.clear()
        self.progress.setValue(0)
        self.progress_label.setText("Выполнено: 0%")
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


DEFAULT_USEFUL_LINKS = [
    {
        "title": "Рекомендации по выявлению запрещённой литературы — РГБ",
        "url": "https://nkp.rsl.ru/drug-literature-recommendations",
    },
    {
        "title": "Реестр изданий иностранных агентов — НКП РГБ",
        "url": "https://nkp.rsl.ru/foreign-agents-registry",
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
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        self.resize(620, 360)
        self.setMinimumSize(620, 300)
        self.links = self._load_links()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Полезные ссылки")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        description = QLabel("Откройте нужный сайт двойным щелчком. В этот список можно добавлять свои ссылки.")
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
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
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
            atomic_write_text(
                self._storage_path(),
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
            item.setForeground(useful_link_foreground())
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
            atomic_write_text(
                path,
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
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        self.resize(900, 580)
        self.setMinimumSize(680, 440)
        self.last_output_path = ""
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
        files_layout.addWidget(self.old_edit, 0, 1)
        old_button = QPushButton("Выбрать…")
        old_button.setObjectName("secondaryButton")
        old_button.clicked.connect(self.select_old)
        files_layout.addWidget(old_button, 0, 2)

        files_layout.addWidget(QLabel("Новый результат:"), 1, 0)
        self.new_edit = QLineEdit()
        self.new_edit.setObjectName("filePath")
        self.new_edit.setReadOnly(True)
        self.new_edit.setPlaceholderText("Новый Excel-отчёт не выбран")
        files_layout.addWidget(self.new_edit, 1, 1)
        new_button = QPushButton("Выбрать…")
        new_button.setObjectName("secondaryButton")
        new_button.clicked.connect(self.select_new)
        files_layout.addWidget(new_button, 1, 2)

        files_layout.addWidget(QLabel("Файл изменений:"), 2, 0)
        self.output_edit = QLineEdit()
        self.output_edit.setObjectName("filePath")
        self.output_edit.setReadOnly(True)
        self.output_edit.setPlaceholderText("Путь будет выбран автоматически")
        files_layout.addWidget(self.output_edit, 2, 1)
        output_button = QPushButton("Изменить…")
        output_button.setObjectName("mutedButton")
        output_button.clicked.connect(self.select_output)
        files_layout.addWidget(output_button, 2, 2)
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
        self.table.setHorizontalHeaderLabels(["Изменение", "Раздел", "Автор", "Название", "ISBN", "Изменённые поля"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
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
        fills = result_diff_fill_colors()
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
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
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
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
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
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить отчёт", self.output_edit.text() or self._default_output(), "TXT (*.txt)"
        )
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


class UpdateAvailableDialog(QDialog):
    """Единое окно доступного обновления с заметками релиза и явными действиями."""

    def __init__(
        self,
        *,
        current_version: str,
        release_version: str,
        release_notes: str,
        page_url: str,
        asset_name: str = "",
        asset_size: int = 0,
        can_download: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.download_requested = False
        self.page_url = page_url
        self.setWindowTitle(f"Доступно обновление — {APP_TITLE}")
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        self.setModal(True)
        self.resize(620, 500)
        self.setMinimumSize(500, 380)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        title = QLabel(f"Доступна версия {release_version}")
        title.setObjectName("mainTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        details = [f"Установлена версия: {current_version}"]
        if asset_name:
            size_text = f" · {asset_size / 1024 / 1024:.1f} МБ" if asset_size else ""
            details.append(f"Файл обновления: {asset_name}{size_text}")
        info = QLabel("\n".join(details))
        info.setObjectName("cardDescription")
        info.setWordWrap(True)
        root.addWidget(info)

        notes_label = QLabel("Что изменилось")
        notes_label.setObjectName("fieldLabel")
        root.addWidget(notes_label)

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setObjectName("updateReleaseNotes")
        notes.setMarkdown(release_notes.strip() or "Для этого релиза описание изменений не опубликовано.")
        root.addWidget(notes, 1)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        later_button = QPushButton("Позже")
        later_button.setObjectName("mutedButton")
        later_button.clicked.connect(self.reject)
        actions.addWidget(later_button)
        actions.addStretch(1)

        if can_download:
            download_button = QPushButton("Скачать новую версию")
            download_button.setObjectName("primaryButton")
            download_button.setToolTip("Скачать установщик новой версии")
            download_button.clicked.connect(self._request_download)
            download_button.setDefault(True)
            actions.addWidget(download_button)
        else:
            page_button = QPushButton("Открыть страницу релиза")
            page_button.setObjectName("mutedButton")
            page_button.clicked.connect(self._open_release_page)
            actions.addWidget(page_button)
        root.addLayout(actions)

    def _open_release_page(self) -> None:
        if self.page_url:
            QDesktopServices.openUrl(QUrl(self.page_url))

    def _request_download(self) -> None:
        self.download_requested = True
        self.accept()


class UpdateReadyDialog(QDialog):
    """Подтверждение установки уже скачанного обновления."""

    def __init__(
        self,
        *,
        release_version: str,
        package_path: str | Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.install_requested = False
        package = Path(package_path)
        self.setWindowTitle(f"Обновление скачано — {APP_TITLE}")
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        self.setModal(True)
        self.setMinimumWidth(500)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        title = QLabel("Новая версия готова к установке")
        title.setObjectName("mainTitle")
        root.addWidget(title)

        version_text = f"Версия: {release_version}\n" if release_version else ""
        info = QLabel(
            f"{version_text}Файл: {package.name}\n\n"
            "После нажатия «Установить» программа закроется. Установщик заменит старую версию новой, "
            "после чего приложение запустится снова."
        )
        info.setObjectName("cardDescription")
        info.setWordWrap(True)
        root.addWidget(info)

        actions = QHBoxLayout()
        later_button = QPushButton("Позже")
        later_button.setObjectName("mutedButton")
        later_button.clicked.connect(self.reject)
        actions.addWidget(later_button)
        actions.addStretch(1)
        install_button = QPushButton("Установить")
        install_button.setObjectName("primaryButton")
        install_button.setDefault(True)
        install_button.clicked.connect(self._request_install)
        actions.addWidget(install_button)
        root.addLayout(actions)

    def _request_install(self) -> None:
        self.install_requested = True
        self.accept()
