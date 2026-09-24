from __future__ import annotations

from contextlib import suppress

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from irbis_control.application.settings import THEME_DARK, THEME_SYSTEM, VALID_THEMES

_current_dark = False
_system_palette: QPalette | None = None
_system_color_scheme: Qt.ColorScheme | None = None


def _colors() -> dict[str, str]:
    if _current_dark:
        return {
            "window": "#1f2329",
            "page": "#1f2329",
            "card": "#292e35",
            "text": "#e6edf3",
            "title": "#f0f4f8",
            "muted": "#aeb8c2",
            "disabled": "#7f8993",
            "border": "#4a5663",
            "control_border": "#68798a",
            "danger_border": "#765050",
            "danger_button": "#c94b45",
            "danger_button_hover": "#b8403a",
            "danger_button_pressed": "#a43732",
            "danger_button_disabled": "#7a4c4a",
            "button": "#30363d",
            "button_hover": "#39424c",
            "button_pressed": "#242a31",
            "button_disabled": "#2b3036",
            "accent": "#58a6ff",
            "selection": "#173b5f",
            "track": "#4b5560",
            "grip": "#718096",
            "error": "#ff7b72",
        }
    return {
        "window": "#f5f5f5",
        "page": "#f8fafc",
        "card": "#ffffff",
        "text": "#202020",
        "title": "#151515",
        "muted": "#5a5a5a",
        "disabled": "#6b6b6b",
        "border": "#cbd6e2",
        "control_border": "#9bafc4",
        "danger_border": "#dfcaca",
        "danger_button": "#c53b3b",
        "danger_button_hover": "#b52f2f",
        "danger_button_pressed": "#9f2828",
        "danger_button_disabled": "#d8a2a2",
        "button": "#ffffff",
        "button_hover": "#f3f7fb",
        "button_pressed": "#e7eef5",
        "button_disabled": "#eeeeee",
        "accent": "#006bd6",
        "selection": "#eef6ff",
        "track": "#c4c9cf",
        "grip": "#aebdcd",
        "error": "#b52f2f",
    }


def combo_popup_fill_color() -> QColor:
    """Непрозрачный фон общего выпадающего списка."""
    return QColor(_colors()["card"])


def apply_about_title_font(label: QLabel) -> None:
    """Применяет типографику заголовка окна «О программе»."""
    font = label.font()
    font.setPointSize(12)
    font.setBold(True)
    label.setFont(font)


def useful_link_foreground() -> QColor:
    """Цвет текста элемента списка полезных ссылок."""
    return QColor(_colors()["text"])


def result_diff_fill_colors() -> dict[str, QColor]:
    """Цвета строк предварительного просмотра изменений отчёта."""
    if _current_dark:
        return {
            "Добавлено": QColor("#24452d"),
            "Удалено": QColor("#57352d"),
            "Изменено": QColor("#55491f"),
        }
    return {
        "Добавлено": QColor("#E2F0D9"),
        "Удалено": QColor("#FCE4D6"),
        "Изменено": QColor("#FFF2CC"),
    }


class ComboPopupBorderOverlay(QWidget):
    """Рисует цельную антиалиасную границу поверх popup QComboBox."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("appComboPopupBorderOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(_colors()["accent"]), 1.5))
        # Полпикселя внутрь гарантирует, что маска top-level окна не срежет
        # внешнюю половину линии. Поэтому граница видна целиком и на углах.
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        edge = str(
            self.parentWidget().property("appComboAttachedEdge") or self.parentWidget().property("popupEdge") or ""
        )
        if edge in {"above", "below"}:
            radius = 6.5
            path = QPainterPath()
            if edge == "below":
                path.moveTo(rect.left(), rect.top())
                path.lineTo(rect.right(), rect.top())
                path.lineTo(rect.right(), rect.bottom() - radius)
                path.quadTo(rect.right(), rect.bottom(), rect.right() - radius, rect.bottom())
                path.lineTo(rect.left() + radius, rect.bottom())
                path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - radius)
            else:
                path.moveTo(rect.left(), rect.bottom())
                path.lineTo(rect.right(), rect.bottom())
                path.lineTo(rect.right(), rect.top() + radius)
                path.quadTo(rect.right(), rect.top(), rect.right() - radius, rect.top())
                path.lineTo(rect.left() + radius, rect.top())
                path.quadTo(rect.left(), rect.top(), rect.left(), rect.top() + radius)
            path.closeSubpath()
            painter.drawPath(path)
        else:
            painter.drawRoundedRect(rect, 6.5, 6.5)


class ComboPopupItemDelegate(QStyledItemDelegate):
    """Рисует hover/selection пунктов QComboBox без квадратной системной подложки."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        prepared = QStyleOptionViewItem(option)
        selected = bool(prepared.state & QStyle.StateFlag.State_Selected)
        hovered = bool(prepared.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            colors = _colors()
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["selection"] if selected else colors["button_hover"]))
            rect = QRectF(prepared.rect.adjusted(1, 1, -1, -1))
            painter.drawRoundedRect(rect, 5.0, 5.0)
            painter.restore()
            # Базовый delegate оставляем только для текста, иконок и check-state.
            # Иначе Fusion поверх нашей скруглённой подложки рисует свой квадрат.
            prepared.state &= ~QStyle.StateFlag.State_Selected
            prepared.state &= ~QStyle.StateFlag.State_MouseOver
        super().paint(painter, prepared, index)


def set_transparent_widget_background(widget: QWidget) -> None:
    """Убирает собственную квадратную заливку, оставляя фон родительского popup."""
    widget.setAutoFillBackground(False)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
    palette = QPalette(widget.palette())
    transparent = QColor(0, 0, 0, 0)
    palette.setColor(QPalette.ColorRole.Base, transparent)
    palette.setColor(QPalette.ColorRole.Window, transparent)
    widget.setPalette(palette)


def common_button_stylesheet() -> str:
    """Единые размеры и состояния кнопок для всех окон приложения."""
    colors = _colors()
    stylesheet = """
        QPushButton {
            min-height: 23px;
            padding: 1px 10px;
            color: @text@;
            background: @button@;
            border: 1px solid @border@;
            border-radius: 4px;
        }
        QPushButton:hover {
            background: @button_hover@;
            border-color: @accent@;
        }
        QPushButton:pressed {
            background: @button_pressed@;
            border-color: @accent@;
        }
        QPushButton:disabled {
            color: @disabled@;
            background: @button_disabled@;
            border-color: @border@;
        }
        QPushButton#mutedButton, QPushButton#secondaryButton, QPushButton#secondary {
            color: @text@;
            background: transparent;
            border-color: @border@;
        }
        QPushButton#mutedButton:hover, QPushButton#secondaryButton:hover, QPushButton#secondary:hover {
            background: @button_hover@;
            border-color: @accent@;
        }
        QPushButton#primaryButton, QPushButton#primary {
            color: #ffffff;
            background: #0878e3;
            border-color: #0870d2;
        }
        QPushButton#primaryButton:hover, QPushButton#primary:hover { background: #006fd8; }
        QPushButton#primaryButton:pressed, QPushButton#primary:pressed { background: #0064c4; }
        QPushButton#primaryButton:disabled, QPushButton#primary:disabled {
            color: #e4e4e4;
            background: #8fb9df;
            border-color: #8fb9df;
        }
        QPushButton#dangerButton {
            color: #ffffff;
            background: @danger_button@;
            border-color: @danger_button@;
        }
        QPushButton#dangerButton:hover {
            background: @danger_button_hover@;
            border-color: @danger_button_hover@;
        }
        QPushButton#dangerButton:pressed {
            background: @danger_button_pressed@;
            border-color: @danger_button_pressed@;
        }
        QPushButton#dangerButton:disabled {
            color: #eeeeee;
            background: @danger_button_disabled@;
            border-color: @danger_button_disabled@;
        }
        QPushButton#linkButton {
            min-height: 0;
            padding: 0;
            margin: 0;
            color: @accent@;
            background: transparent;
            border: none;
            border-radius: 0;
        }
        QPushButton#linkButton:hover, QPushButton#linkButton:pressed {
            color: @title@;
            background: transparent;
            border: none;
        }
        QPushButton#disclosureButton {
            color: @accent@;
            background: transparent;
            border: none;
            padding: 3px 0;
            text-align: left;
        }
        QPushButton#disclosureButton:hover { background: transparent; text-decoration: underline; }
    """
    for name, color in colors.items():
        stylesheet = stylesheet.replace(f"@{name}@", color)
    return stylesheet


def component_stylesheet() -> str:
    """Общие стили переиспользуемых составных виджетов."""
    colors = _colors()
    return """
        QToolButton#embeddedToolButton { border: none; padding: 0; background: transparent; }
        QFrame#listResizeGrip { background: @grip@; border-radius: 1px; }
        QLabel#errorLabel { color: @error@; }
    """.replace("@grip@", colors["grip"]).replace("@error@", colors["error"])


def application_stylesheet() -> str:
    """Глобальный QSS приложения: все окна и диалоги получают один визуальный язык."""
    colors = _colors()
    stylesheet = r"""
        QWidget { color: @text@; }
        QMainWindow, QDialog, QMessageBox, QWidget#root, QWidget#centralPage, QWidget#tabPage {
            background: @page@;
            color: @text@;
        }
        QToolTip {
            color: @text@;
            background: @card@;
            border: 1px solid @border@;
            padding: 4px 6px;
        }
        QLabel { color: @text@; }
        QLabel#mainTitle, QLabel#dialogTitle { color: @title@; font-weight: 600; }
        QLabel#cardTitle { font-size: 13px; font-weight: 600; color: @accent@; }
        QLabel#pageSectionTitle { font-size: 14px; font-weight: 600; color: @text@; margin-top: 4px; }
        QLabel#tabIntro, QLabel#cardDescription, QLabel#statusLabel, QLabel#subtitle, QLabel#hint { color: @muted@; }
        QLabel:disabled { color: @disabled@; }

        QFrame#sectionCard, QFrame#actionCard, QFrame#card {
            border: 1px solid @border@;
            border-radius: 6px;
            background: @card@;
        }
        QFrame#dangerCard { border: 1px solid @danger_border@; border-radius: 6px; background: @card@; }
        QWidget#approvedReviewActionCell { background-color: rgba(22, 131, 232, 55); border: none; }
        QFrame#headerCard, QFrame#irbisActions, QFrame#workflowFooter { border: none; background: transparent; }

        QLineEdit, QComboBox, QSpinBox, QTextEdit, QTextBrowser, QListWidget, QTableWidget, QTreeWidget {
            color: @text@;
            background: @card@;
            border: 1px solid @border@;
            border-radius: 6px;
            selection-background-color: @selection@;
            selection-color: @text@;
        }
        QLineEdit, QSpinBox { min-height: 23px; padding: 1px 5px; }
        QComboBox {
            min-height: 23px;
            padding: 1px 30px 1px 7px;
            border: 1px solid @control_border@;
            border-radius: 7px;
        }
        QComboBox:hover { border-color: @accent@; }
        QComboBox[popupOpen="true"] { border-color: @accent@; }
        QComboBox[popupOpen="true"][popupEdge="below"] {
            border-bottom-left-radius: 0px;
            border-bottom-right-radius: 0px;
        }
        QComboBox[popupOpen="true"][popupEdge="above"] {
            border-top-left-radius: 0px;
            border-top-right-radius: 0px;
        }
        /*
         * Fusion/Windows отрисовывает область стрелки QComboBox отдельным
         * прямоугольным sub-control. Если его не стилизовать, правые углы
         * остаются квадратными даже при border-radius самого поля.
         */
        QComboBox::drop-down {
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 27px;
            border: none;
            background: transparent;
            border-top-right-radius: 7px;
            border-bottom-right-radius: 7px;
        }
        QComboBox::drop-down:hover { background: @button_hover@; }
        QComboBox::drop-down:pressed { background: @button_pressed@; }
        QComboBox:disabled::drop-down { background: transparent; }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QListWidget:focus, QTableWidget:focus {
            border-color: @accent@;
        }
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QTextEdit:disabled, QListWidget:disabled {
            color: @disabled@;
            background: @button_disabled@;
        }
        /*
         * Popup QComboBox состоит из отдельного top-level QFrame и
         * QAbstractItemView с собственным viewport. Фон рисует только внешний
         * контейнер: внутренние слои прозрачны, поэтому второго квадратного
         * прямоугольника под скруглённым popup больше нет.
         */
        QFrame#appComboPopup {
            color: @text@;
            background: @card@;
            border: 1px solid @accent@;
            border-radius: 7px;
        }
        QFrame#appComboPopup[popupEdge="below"] {
            border-top-left-radius: 0px;
            border-top-right-radius: 0px;
        }
        QFrame#appComboPopup[popupEdge="above"] {
            border-bottom-left-radius: 0px;
            border-bottom-right-radius: 0px;
        }
        /* Граница рисуется отдельным overlay поверх private-container: так
           бинарная маска Windows не обрезает линию на скруглённых углах. */
        QWidget#appComboPopupBorderOverlay {
            background: transparent;
            border: none;
        }
        QFrame#appComboPopup QAbstractItemView,
        QComboBox QAbstractItemView {
            color: @text@;
            background: transparent;
            border: none;
            border-radius: 0px;
            padding: 0px;
            outline: none;
            selection-background-color: transparent;
            selection-color: @text@;
        }
        QFrame#appComboPopup QAbstractItemView::item,
        QComboBox QAbstractItemView::item {
            min-height: 22px;
            padding: 2px 7px;
            margin: 1px 0px;
            border: 1px solid transparent;
            border-radius: 5px;
            background: transparent;
            color: @text@;
        }
        QFrame#appComboPopup QAbstractItemView::item {
            min-height: 23px;
            padding: 1px 7px;
            margin: 0px;
        }
        QFrame#appComboPopup QAbstractItemView::item:hover,
        QComboBox QAbstractItemView::item:hover {
            background: @button_hover@;
            color: @text@;
        }
        QFrame#appComboPopup QAbstractItemView::item:selected,
        QComboBox QAbstractItemView::item:selected {
            background: @selection@;
            color: @text@;
        }
        QCheckBox, QRadioButton { spacing: 6px; }
        QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }

        QTabWidget::pane { border: 1px solid @border@; border-radius: 5px; background: @card@; }
        QTabBar::tab { padding: 6px 10px; margin-right: 2px; }
        QTabBar::tab:selected { color: @accent@; }

        QHeaderView::section {
            color: @text@;
            background: @button@;
            border: none;
            border-right: 1px solid @border@;
            border-bottom: 1px solid @border@;
            padding: 4px 6px;
        }
        QTableCornerButton::section { background: @button@; border: 1px solid @border@; }

        QMenu {
            color: @text@;
            background: @card@;
            border: 1px solid @control_border@;
            border-radius: 7px;
            padding: 5px;
        }
        QMenu::item {
            min-height: 20px;
            padding: 5px 30px 5px 9px;
            margin: 1px 0px;
            border: 1px solid transparent;
            border-radius: 5px;
        }
        QMenu::item:selected {
            color: @text@;
            background: @selection@;
            border-color: transparent;
        }
        QMenu::item:disabled { color: @disabled@; background: transparent; }
        QMenu::separator {
            height: 1px;
            background: @border@;
            margin: 4px 8px;
        }
        QMenu#appContextMenu {
            background: @card@;
            border: 1px solid @control_border@;
            border-radius: 8px;
            padding: 5px;
        }

        QMessageBox#appMessageBox { background: @page@; }
        QMessageBox#appMessageBox QLabel#qt_msgbox_label { min-width: 320px; }
        QMessageBox#appMessageBox QPushButton, QDialogButtonBox QPushButton { min-width: 88px; }

        QProgressBar {
            border: 1px solid @border@;
            border-radius: 4px;
            background: @track@;
            text-align: center;
        }
        QProgressBar::chunk { background: @accent@; border-radius: 3px; }

        QPushButton#groupActionButton {
            color: @accent@;
            background: @selection@;
            border: 1px solid @accent@;
            font-weight: 600;
        }
        QPushButton#groupActionButton:hover { color: #ffffff; background: @accent@; }
        QPushButton#groupActionButton:pressed { color: #ffffff; background: @button_pressed@; }

        QScrollBar:vertical { background: transparent; width: 12px; margin: 0; }
        QScrollBar::handle:vertical { background: @grip@; min-height: 24px; border-radius: 5px; margin: 2px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar:horizontal { background: transparent; height: 12px; margin: 0; }
        QScrollBar::handle:horizontal { background: @grip@; min-width: 24px; border-radius: 5px; margin: 2px; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

        QTextEdit#logEdit, QTextEdit#plainLogEdit, QTextEdit#log { font-family: Consolas, monospace; }
    """
    for name, color in colors.items():
        stylesheet = stylesheet.replace(f"@{name}@", color)
    return common_button_stylesheet() + component_stylesheet() + stylesheet


def main_window_stylesheet() -> str:
    """Единый стиль главного окна и его дочерних диалогов."""
    colors = _colors()
    stylesheet = """
        QMainWindow, QDialog, QWidget#centralPage, QWidget#tabPage { background: @page@; color: @text@; }
        QScrollArea#mainScroll { border: none; background: @page@; }
        QFrame#headerCard, QFrame#irbisActions { border: none; background: transparent; }
        QFrame#sectionCard, QFrame#actionCard {
            border: 1px solid @border@;
            border-radius: 5px;
            background: @card@;
        }
        QFrame#dangerCard { border: 1px solid @danger_border@; border-radius: 5px; background: @card@; }
        QLabel { color: @text@; }
        QLabel#mainTitle { font-weight: 600; color: @title@; }
        QLabel#cardTitle { font-size: 13px; font-weight: 600; color: @accent@; }
        QLabel#pageSectionTitle { font-size: 14px; font-weight: 600; color: @text@; margin-top: 4px; }
        QLabel#irbisStateDot { background: #d94a4a; border-radius: 5px; }
        QLabel#irbisStateDot[state="success"] { background: #35b85f; }
        QLabel#irbisStateDot[state="running"], QLabel#irbisStateDot[state="warning"] { background: #e4a11b; }
        QLabel#irbisStateDot[state="error"] { background: #d94a4a; }
        QLabel#directSourceDot { background: #d94a4a; border-radius: 4px; }
        QLabel#directSourceDot[state="success"] { background: #35b85f; }
        QLabel#directSourceDot[state="running"], QLabel#directSourceDot[state="warning"] { background: #e4a11b; }
        QLabel#directSourceDot[state="error"] { background: #d94a4a; }
        QLabel#directSourceDot[state="local"] { background: #6f7f8f; }
        QLabel#sourceStatusTitle { color: @text@; font-weight: 600; }
        QLabel#sourceStateLabel { color: #d94a4a; }
        QLabel#sourceStateLabel[state="success"] { color: #218b45; }
        QLabel#sourceStateLabel[state="running"], QLabel#sourceStateLabel[state="warning"] { color: #9a6800; }
        QLabel#sourceStateLabel[state="error"] { color: @error@; }
        QLabel#sourceStateLabel[state="local"] { color: @muted@; }
        QLabel#fieldLabel { color: @text@; }
        QLabel#tabIntro, QLabel#cardDescription, QLabel#statusLabel { color: @muted@; }
        QLabel#statusLabel[state="success"] { color: #218b45; }
        QLabel#statusLabel[state="running"], QLabel#statusLabel[state="warning"] { color: #9a6800; }
        QLabel#statusLabel[state="error"] { color: @error@; }
        QLabel:disabled { color: @disabled@; }
        QTextEdit#logEdit, QTextEdit#plainLogEdit { font-family: Consolas, monospace; }
        QLineEdit, QComboBox, QSpinBox { min-height: 23px; }
        QComboBox#databaseCombo::drop-down { width: 0; border: none; }
        QComboBox#databaseCombo::down-arrow { image: none; }
        QPushButton#primaryButton[compact="true"] { margin: 1px; }
        QFrame#workflowFooter { border: none; background: transparent; }
        QListWidget#compactList::item { min-height: 21px; }
        QListWidget#matchRulesList::item {
            padding: 0px 8px;
            border-left: 2px solid transparent;
            color: @text@;
        }
        QListWidget#matchRulesList::item:selected {
            border-left-color: #0878e3;
            background: @selection@;
            color: @text@;
        }
        QTableWidget#manualReviewTable {
            outline: none;
            selection-background-color: @card@;
            selection-color: @text@;
        }
        QTableWidget#manualReviewTable::item:selected {
            background: @card@;
            color: @text@;
            border: none;
        }
        QProgressBar { min-height: 12px; max-height: 12px; }
        QProgressBar#dialogProgress {
            min-height: 22px;
            max-height: 22px;
            text-align: center;
        }
        QProgressBar#mainProgress {
            min-height: 4px;
            max-height: 4px;
            border: none;
            border-radius: 2px;
            background: @track@;
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
            border-bottom: 2px solid transparent;
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
            border-bottom: 2px solid transparent;
        }
        QPushButton#settingsTab {
            background: transparent;
            border: none;
            border-bottom: 2px solid transparent;
            border-radius: 0;
            padding: 5px 10px;
            margin: 0;
            min-height: 0;
        }
        QPushButton#settingsTab:checked {
            color: palette(highlight);
            border-bottom: 2px solid palette(highlight);
        }
        QPushButton#settingsTab:hover:!checked { background: palette(alternate-base); }
    """
    for name, color in colors.items():
        stylesheet = stylesheet.replace(f"@{name}@", color)
    return application_stylesheet() + stylesheet


def manual_review_stylesheet() -> str:
    """Оформление окна ручной проверки в стиле карточек и групп."""
    stylesheet = """
        QDialog#manualReviewDialog { background: @page@; }
        QDialog#manualReviewDialog QPushButton { padding: 4px 10px; min-height: 28px; }
        QDialog#manualReviewDialog QPushButton#linkButton {
            padding: 0; min-height: 0; text-align: left; border: none; background: transparent; color: @accent@;
        }

        QLabel#reviewTitle { font-size: 22px; font-weight: 650; color: @title@; }
        QLabel#reviewHeading { font-size: 17px; font-weight: 650; color: @title@; }
        QLabel#reviewCandidateTitle { font-size: 15px; font-weight: 650; color: @title@; }
        QLabel#reviewAccent { color: @accent@; font-size: 15px; font-weight: 650; }
        QLabel#reviewProgressLabel { font-weight: 600; }
        QLabel#reviewMetaCaption { color: @muted@; font-size: 11px; }
        QLabel#reviewMetaValue { color: @text@; font-size: 12px; }
        QLabel#reviewIrbisCaption { color: @muted@; font-size: 11px; font-weight: 600; }
        QLabel#reviewIrbisValue { color: @text@; font-size: 12px; }

        QFrame#reviewSource {
            background: @selection@; border: 1px solid @border@; border-radius: 9px;
        }
        QFrame#reviewCandidate {
            background: @card@; border: 1px solid @border@; border-radius: 9px;
        }
        QFrame#reviewCandidate[active="true"] { border: 1px solid @accent@; }

        QLabel#reviewMatch {
            background: rgba(33, 139, 80, 25);
            border: 1px solid #218b50;
            border-radius: 7px;
            padding: 7px 9px;
            color: @text@;
        }

        QListWidget#reviewGroupList {
            background: @page@; border: none; padding-right: 4px;
        }
        QListWidget#reviewGroupList::item {
            padding: 10px 12px;
            border: 1px solid @border@;
            border-radius: 8px;
            margin: 0 1px 4px 0;
        }
        QListWidget#reviewGroupList::item:hover { background: @button_hover@; }
        QListWidget#reviewGroupList::item:selected {
            background: @selection@; border: 1px solid @accent@; color: @text@;
        }

        QPushButton#reviewFilter {
            min-width: 104px; border-radius: 7px; font-weight: 600;
        }
        QPushButton#reviewFilter:checked {
            background: #0878e3; color: white; border-color: #0878e3;
        }
        QPushButton#reviewDecisionApprove,
        QPushButton#reviewDecisionReject {
            background: transparent; font-weight: 600;
        }
        QPushButton#reviewDecisionApprove {
            color: #2fbf71; border-color: #2fbf71;
        }
        QPushButton#reviewDecisionApprove:hover {
            background: rgba(47, 191, 113, 0.10); border-color: #2fbf71;
        }
        QPushButton#reviewDecisionApprove:pressed {
            background: rgba(47, 191, 113, 0.18); border-color: #2fbf71;
        }
        QPushButton#reviewDecisionApprove[selected="true"] {
            background: #218b50; color: white; border-color: #218b50;
        }
        QPushButton#reviewDecisionApprove[selected="true"]:hover { background: #187341; border-color: #187341; }

        QPushButton#reviewDecisionReject {
            color: @danger_button@; border-color: @danger_button@;
        }
        QPushButton#reviewDecisionReject:hover {
            background: rgba(201, 75, 69, 0.10); border-color: @danger_button@;
        }
        QPushButton#reviewDecisionReject:pressed {
            background: rgba(201, 75, 69, 0.18); border-color: @danger_button@;
        }
        QPushButton#reviewDecisionReject[selected="true"] {
            background: @danger_button@; color: white; border-color: @danger_button@;
        }
        QPushButton#reviewDecisionReject[selected="true"]:hover {
            background: @danger_button_hover@; border-color: @danger_button_hover@;
        }

        QPushButton#groupActionButton { color: @accent@; background: transparent; border-color: @accent@; }
        QPushButton#groupActionButton:hover { background: @selection@; }
        QPushButton#groupRejectButton { color: @danger_button@; background: transparent; border-color: @danger_button@; }
        QPushButton#groupRejectButton:hover { background: rgba(201, 75, 69, 0.10); }
        QPushButton#groupRejectButton:pressed { background: rgba(201, 75, 69, 0.18); }
        QDialog#manualReviewDialog QPushButton#reviewNumber {
            min-width: 28px; min-height: 28px; max-width: 28px; max-height: 28px;
            background: transparent; color: @accent@; border: none; border-radius: 0; padding: 0; font-weight: 700;
        }
        QDialog#manualReviewDialog QPushButton#reviewNumber:hover,
        QDialog#manualReviewDialog QPushButton#reviewNumber:pressed {
            background: transparent; border: none; color: @title@;
        }
        QProgressBar#reviewProgress {
            background: @button@; border: none; border-radius: 4px;
        }
        QProgressBar#reviewProgress::chunk {
            background: @accent@; border-radius: 4px;
        }
        QDialog#manualReviewDialog QLineEdit,
        QDialog#manualReviewDialog QComboBox {
            min-height: 30px; border-radius: 7px;
        }
    """
    for name, color in _colors().items():
        stylesheet = stylesheet.replace(f"@{name}@", color)
    return application_stylesheet() + stylesheet


def database_connector_stylesheet() -> str:
    """Совместимый вызов: отдельное окно использует тот же глобальный стиль."""
    return application_stylesheet()


def about_page_stylesheet() -> str:
    """Стиль области с информацией о программе."""
    colors = _colors()
    return "QTextBrowser#aboutPage { border: none; background: @page@; color: @text@; }".replace(
        "@page@", colors["page"]
    ).replace("@text@", colors["text"])


def about_document_stylesheet() -> str:
    """Стиль HTML-содержимого страницы «О программе»."""
    accent = _colors()["accent"]
    return (
        f"h3 {{ font-size: 13px; font-weight: 600; color: {accent}; margin-top: 12px; margin-bottom: 3px; }}"
        "p { margin-top: 0; margin-bottom: 7px; }"
        f"a {{ color: {accent}; }}"
    )


def prepare_application_ui() -> None:
    """Включает единый Qt-интерфейс до создания QApplication."""
    attribute = getattr(Qt.ApplicationAttribute, "AA_DontUseNativeDialogs", None)
    if attribute is not None:
        with suppress(AttributeError, RuntimeError):
            QApplication.setAttribute(attribute, True)


def apply_application_theme(app: QApplication, theme: str) -> None:
    """Применяет выбранную цветовую схему, не меняя геометрию и стиль элементов."""
    global _current_dark, _system_palette, _system_color_scheme
    if _system_palette is None:
        _system_palette = QPalette(app.palette())
        _system_color_scheme = app.styleHints().colorScheme()
    # Fusion даёт одинаковые размеры и состояния контролов на Windows/Linux.
    with suppress(Exception):
        app.setStyle("Fusion")
    if theme not in VALID_THEMES:
        theme = THEME_SYSTEM

    system_theme = theme == THEME_SYSTEM
    if system_theme:
        system_scheme = _system_color_scheme or Qt.ColorScheme.Unknown
        # Не все платформенные плагины Qt позволяют менять системную схему.
        with suppress(AttributeError, RuntimeError):
            app.styleHints().setColorScheme(Qt.ColorScheme.Unknown)
        dark = system_scheme == Qt.ColorScheme.Dark or (
            system_scheme != Qt.ColorScheme.Light and _system_palette.color(QPalette.ColorRole.Window).lightness() < 128
        )
        if not dark:
            _current_dark = False
            app.setPalette(QPalette(_system_palette))
            app.setStyleSheet(application_stylesheet())
            return
    else:
        dark = theme == THEME_DARK
    _current_dark = dark
    if not system_theme:
        # При отсутствии поддержки setColorScheme достаточно собственной палитры.
        with suppress(AttributeError, RuntimeError):
            app.styleHints().setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)

    palette = QPalette()
    colors = _colors()
    palette_colors = {
        QPalette.ColorRole.Window: colors["window"],
        QPalette.ColorRole.WindowText: colors["text"],
        QPalette.ColorRole.Base: colors["card"],
        QPalette.ColorRole.AlternateBase: colors["button_hover"],
        QPalette.ColorRole.ToolTipBase: colors["card"],
        QPalette.ColorRole.ToolTipText: colors["text"],
        QPalette.ColorRole.Text: colors["text"],
        QPalette.ColorRole.Button: colors["button"],
        QPalette.ColorRole.ButtonText: colors["text"],
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Link: colors["accent"],
        QPalette.ColorRole.Highlight: "#2f81f7" if dark else "#0078d4",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: colors["muted"],
        QPalette.ColorRole.Light: "#3d444d" if dark else "#ffffff",
        QPalette.ColorRole.Midlight: "#343b43" if dark else "#e4e4e4",
        QPalette.ColorRole.Mid: colors["muted"],
        QPalette.ColorRole.Dark: "#15191e" if dark else "#4a4a4a",
        QPalette.ColorRole.Shadow: "#000000",
    }
    for role, color in palette_colors.items():
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(colors["button_disabled"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(colors["button_disabled"]))
    app.setPalette(palette)
    app.setStyleSheet(application_stylesheet())
