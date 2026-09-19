from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class IrbisConnectionDialog(QDialog):
    """Редактор постоянных параметров подключения к серверу ИРБИС."""

    connection_requested = pyqtSignal(object)
    reading_test_requested = pyqtSignal(object)

    def __init__(
        self,
        settings: dict[str, object],
        databases: list[tuple[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.connect_requested = False
        self.setWindowTitle("Подключение к ИРБИС")
        self.setModal(True)
        self.resize(520, 500)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)

        title = QLabel("Настройки подключения")
        title.setObjectName("cardTitle")
        layout.addWidget(title)

        hint = QLabel("Параметры сохраняются на этом компьютере и используются при следующих запусках программы.")
        hint.setObjectName("cardDescription")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        connection_card = QFrame()
        connection_card.setObjectName("sectionCard")
        connection_layout = QFormLayout(connection_card)
        connection_layout.setContentsMargins(4, 4, 4, 4)
        connection_layout.setHorizontalSpacing(7)
        connection_layout.setVerticalSpacing(4)

        self.host_edit = QLineEdit(str(settings.get("host", "127.0.0.1")))
        self.host_edit.setPlaceholderText("127.0.0.1")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(settings.get("port", 6666)))
        self.port_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.login_edit = QLineEdit(str(settings.get("login", "")))
        self.password_edit = QLineEdit(str(settings.get("password", "")))
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password_check = QCheckBox("Показать пароль")
        self.show_password_check.toggled.connect(
            lambda visible: self.password_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
        )
        password_box = QWidget()
        password_layout = QVBoxLayout(password_box)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.setSpacing(4)
        password_layout.addWidget(self.password_edit)
        password_actions = QHBoxLayout()
        password_actions.setSpacing(7)
        password_actions.addWidget(self.show_password_check)
        password_actions.addStretch()
        self.connect_button = QPushButton("Подключиться")
        self.connect_button.setObjectName("primaryButton")
        self.connect_button.clicked.connect(self._save_and_connect)
        password_actions.addWidget(self.connect_button)
        password_layout.addLayout(password_actions)

        self.database_combo = QComboBox()
        self.database_combo.setEditable(True)
        selected_database = str(settings.get("database", "IBIS")).strip() or "IBIS"
        for title_text, database_name in databases:
            if database_name:
                self.database_combo.addItem(title_text, database_name)
        selected_index = self.database_combo.findData(selected_database)
        if selected_index < 0:
            self.database_combo.addItem(selected_database, selected_database)
            selected_index = self.database_combo.count() - 1
        self.database_combo.setCurrentIndex(selected_index)

        self.query_edit = QLineEdit(str(settings.get("query", "I=$")))
        self.query_edit.setToolTip("Поисковое выражение ИРБИС. По умолчанию: I=$")
        self.page_size_spin = QSpinBox()
        self.page_size_spin.setRange(100, 2000)
        self.page_size_spin.setSingleStep(100)
        self.page_size_spin.setValue(int(settings.get("page_size", 500)))
        self.page_size_spin.setSuffix(" зап.")
        self.page_size_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        page_size_box = QWidget()
        page_size_layout = QHBoxLayout(page_size_box)
        page_size_layout.setContentsMargins(0, 0, 0, 0)
        page_size_layout.setSpacing(7)
        page_size_layout.addWidget(self.page_size_spin, 1)
        self.tune_button = QPushButton("Тест")
        self.tune_button.setObjectName("mutedButton")
        self.tune_button.setToolTip("Подобрать максимальный стабильный размер пакета чтения")
        self.tune_button.clicked.connect(self._test_reading)
        page_size_layout.addWidget(self.tune_button)

        for field in (self.host_edit, self.login_edit, self.password_edit, self.query_edit):
            field.setObjectName("settingsField")
        connection_layout.addRow("Сервер", self.host_edit)
        connection_layout.addRow("Порт", self.port_spin)
        connection_layout.addRow("Логин", self.login_edit)
        connection_layout.addRow("Пароль", password_box)
        connection_layout.addRow("База данных", self.database_combo)
        connection_layout.addRow("Запрос", self.query_edit)
        connection_layout.addRow("Пакет чтения", page_size_box)
        layout.addWidget(connection_card)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = QPushButton("Сохранить")
        self.save_button.setObjectName("mutedButton")
        button_box.addButton(self.save_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.rejected.connect(self.reject)
        self.save_button.clicked.connect(self._save)
        layout.addWidget(button_box)

        self.host_edit.setFocus()
        control_height = max(
            self.host_edit.sizeHint().height(),
            self.port_spin.sizeHint().height(),
            self.database_combo.sizeHint().height(),
        )
        for field in (
            self.host_edit,
            self.port_spin,
            self.login_edit,
            self.password_edit,
            self.database_combo,
            self.query_edit,
            self.page_size_spin,
        ):
            field.setFixedHeight(control_height)
        for button in self.findChildren(QPushButton):
            button.setFixedHeight(control_height)
        layout.activate()
        self.adjustSize()
        self.resize(max(520, self.width()), max(self.minimumSizeHint().height(), self.height()))

    def values(self) -> dict[str, object]:
        database_data = self.database_combo.currentData()
        database = str(database_data if database_data is not None else self.database_combo.currentText()).strip()
        if self.database_combo.currentText().strip() != self.database_combo.itemText(
            self.database_combo.currentIndex()
        ):
            database = self.database_combo.currentText().strip()
        return {
            "host": self.host_edit.text().strip() or "127.0.0.1",
            "port": self.port_spin.value(),
            "login": self.login_edit.text().strip(),
            "password": self.password_edit.text(),
            "database": database or "IBIS",
            "query": self.query_edit.text().strip() or "I=$",
            "page_size": self.page_size_spin.value(),
        }

    def _save(self) -> None:
        self.connect_requested = False
        self.accept()

    def _save_and_connect(self) -> None:
        self.connect_requested = False
        self.connection_requested.emit(self.values())

    def _test_reading(self) -> None:
        self.connect_requested = False
        self.reading_test_requested.emit(self.values())
