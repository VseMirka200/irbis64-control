from pathlib import Path


def test_combo_popup_uses_single_rounded_background() -> None:
    """Regression: combo popup has one rounded background and a complete border."""
    project_root = Path(__file__).resolve().parents[2]
    source = (project_root / "irbis_control" / "ui" / "theme.py").read_text(encoding="utf-8")

    assert 'QFrame[appComboPopup="true"]' in source
    assert 'background: transparent;' in source
    assert '_set_transparent_widget_background(viewport)' in source
    assert 'popup.setMask(_rounded_widget_region(popup))' in source
    assert 'popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)' in source
    assert 'popup.setAutoFillBackground(True)' in source
    assert 'popup_palette.setColor(QPalette.ColorRole.Window, popup_background)' in source
    assert 'viewport.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)' in source
    assert 'view.setItemDelegate(_RoundedComboItemDelegate(view))' in source
    assert '_ComboPopupBorderOverlay' in source
    assert 'painter.drawRoundedRect(rect, 6.5, 6.5)' in source
    assert 'self._sync_border_overlay(popup)' in source
    assert '_install_combo_popup_filter(app)' in source


def test_combo_popup_is_prepared_before_first_open() -> None:
    """Regression: first popup opening must use final styled geometry."""
    project_root = Path(__file__).resolve().parents[2]
    source = (project_root / "irbis_control" / "ui" / "theme.py").read_text(encoding="utf-8")

    assert 'def _prepare_combo_popup(self, combo: QComboBox' in source
    assert 'QEvent.Type.MouseButtonPress' in source
    assert 'QEvent.Type.KeyPress' in source
    assert 'QTimer.singleShot(0, lambda combo=watched: self._prepare_combo_popup(combo))' in source
