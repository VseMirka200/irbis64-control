from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QLabel

from irbis_control.application.settings import THEME_DARK, THEME_LIGHT, THEME_SYSTEM, VALID_THEMES

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
            "danger_border": "#765050",
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
        "danger_border": "#dfcaca",
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


def apply_main_title_font(label: QLabel, compact: bool) -> None:
    """Применяет типографику заголовка главного окна."""
    font = label.font()
    font.setPointSize(10 if compact else 11)
    font.setBold(True)
    label.setFont(font)


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


def common_button_stylesheet() -> str:
    """Единые размеры и состояния кнопок для всех окон приложения."""
    colors = _colors()
    stylesheet = """
        QPushButton {
            min-height: 23px;
            padding: 3px 10px;
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
            color: @error@;
            background: @button@;
            border-color: @danger_border@;
        }
        QPushButton#dangerButton:hover { background: @button_hover@; border-color: @error@; }
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
        QProgressBar { min-height: 12px; max-height: 12px; }
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
    return common_button_stylesheet() + component_stylesheet() + stylesheet


def database_connector_stylesheet() -> str:
    """Стиль отдельного окна работы с базой."""
    return (
        common_button_stylesheet()
        + component_stylesheet()
        + """
        QMainWindow, QWidget#root, QWidget#tabPage { background: palette(window); color: palette(window-text); }
        QFrame#card { border: none; background: transparent; }
        QLabel#title, QLabel#cardTitle { font-weight: 600; }
        QLabel#subtitle, QLabel#hint { color: palette(mid); }
        QTextEdit#log { font-family: Consolas, monospace; }
        QProgressBar { min-height: 10px; max-height: 10px; }
    """
    )


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


def apply_application_theme(app: QApplication, theme: str) -> None:
    """Применяет выбранную цветовую схему, не меняя геометрию и стиль элементов."""
    global _current_dark, _system_palette, _system_color_scheme
    if _system_palette is None:
        _system_palette = QPalette(app.palette())
        _system_color_scheme = app.styleHints().colorScheme()
    if theme not in VALID_THEMES:
        theme = THEME_SYSTEM

    system_theme = theme == THEME_SYSTEM
    if system_theme:
        system_scheme = _system_color_scheme or Qt.ColorScheme.Unknown
        try:
            app.styleHints().setColorScheme(Qt.ColorScheme.Unknown)
        except (AttributeError, RuntimeError):
            # Не все платформенные плагины Qt позволяют менять системную схему.
            pass
        dark = system_scheme == Qt.ColorScheme.Dark or (
            system_scheme != Qt.ColorScheme.Light and _system_palette.color(QPalette.ColorRole.Window).lightness() < 128
        )
        if not dark:
            _current_dark = False
            app.setPalette(QPalette(_system_palette))
            return
    else:
        dark = theme == THEME_DARK
    _current_dark = dark
    if not system_theme:
        try:
            app.styleHints().setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
        except (AttributeError, RuntimeError):
            # При отсутствии поддержки setColorScheme достаточно собственной палитры.
            pass

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


def apply_light_palette(app: QApplication) -> None:
    """Совместимый вызов для окон, которые пока всегда запускаются в светлой теме."""
    apply_application_theme(app, THEME_LIGHT)
