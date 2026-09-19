from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


def common_button_stylesheet() -> str:
    """Единые размеры и состояния кнопок для всех окон приложения."""
    return """
        QPushButton {
            min-height: 23px;
            padding: 3px 10px;
            color: #202020;
            background: #ffffff;
            border: 1px solid #b8c4d0;
            border-radius: 4px;
        }
        QPushButton:hover {
            background: #f3f7fb;
            border-color: #8fa7bf;
        }
        QPushButton:pressed {
            background: #e7eef5;
            border-color: #718ba5;
        }
        QPushButton:disabled {
            color: #777777;
            background: #eeeeee;
            border-color: #d2d2d2;
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
            color: #a92828;
            background: #ffffff;
            border-color: #d6aaaa;
        }
        QPushButton#dangerButton:hover { background: #fff2f2; border-color: #c67f7f; }
        QPushButton#disclosureButton {
            color: #006bd6;
            background: transparent;
            border: none;
            padding: 3px 0;
            text-align: left;
        }
        QPushButton#disclosureButton:hover { background: transparent; text-decoration: underline; }
    """


def apply_light_palette(app: QApplication) -> None:
    """Применяет единственную поддерживаемую светлую цветовую схему приложения."""
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except Exception:
        pass

    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#f5f5f5",
        QPalette.ColorRole.WindowText: "#111111",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#f2f2f2",
        QPalette.ColorRole.ToolTipBase: "#ffffff",
        QPalette.ColorRole.ToolTipText: "#111111",
        QPalette.ColorRole.Text: "#111111",
        QPalette.ColorRole.Button: "#f0f0f0",
        QPalette.ColorRole.ButtonText: "#111111",
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Link: "#0067c0",
        QPalette.ColorRole.Highlight: "#0078d4",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: "#6b6b6b",
        QPalette.ColorRole.Light: "#ffffff",
        QPalette.ColorRole.Midlight: "#e4e4e4",
        QPalette.ColorRole.Mid: "#5f6368",
        QPalette.ColorRole.Dark: "#4a4a4a",
        QPalette.ColorRole.Shadow: "#000000",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#686868"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor("#ededed"))
    app.setPalette(palette)
