from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


def apply_light_palette(app: QApplication) -> None:
    """Apply the application's single supported light color scheme."""
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
