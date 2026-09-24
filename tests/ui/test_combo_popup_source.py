from pathlib import Path


def _project_source(relative_path: str) -> str:
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / relative_path).read_text(encoding="utf-8")


def test_all_combo_behavior_comes_from_one_component() -> None:
    source = _project_source("irbis_control/ui/components/widgets.py")

    assert "class _AppComboPopup(QFrame)" in source
    assert "class AppComboBox(QComboBox)" in source
    assert "class DatabaseComboBox(AppComboBox)" in source
    assert "class MatchFieldsComboBox(AppComboBox)" in source
    assert "def show_for_owner(self)" in source
    assert "view.setRowHidden(owner.currentIndex(), True)" in source
    assert 'owner.setProperty("popupOpen", True)' in source
    assert "self.setGeometry(x, y, width, popup_height)" in source
    assert "def paintEvent(self, event)" in source
    assert "combo_popup_fill_color()" in source
    assert "WA_NoMouseReplay" in source


def test_windows_use_the_shared_combo_component() -> None:
    sources = [
        _project_source("irbis_control/ui/windows/main.py"),
        _project_source("irbis_control/ui/windows/main_build.py"),
        _project_source("irbis_control/ui/windows/main_layout.py"),
        _project_source("irbis_control/ui/windows/connection_dialog.py"),
        _project_source("irbis_control/ui/windows/database_connector.py"),
    ]

    assert all("= QComboBox()" not in source for source in sources)
    assert all("AppComboBox" in source or "DatabaseComboBox" in source for source in sources)


def test_combo_popup_style_targets_the_shared_popup() -> None:
    source = _project_source("irbis_control/ui/theme.py")

    assert "QFrame#appComboPopup" in source
    assert 'QFrame#appComboPopup[popupEdge="below"]' in source
    assert 'QFrame#appComboPopup[popupEdge="above"]' in source
    assert "_install_combo_popup_filter(app)" not in source
