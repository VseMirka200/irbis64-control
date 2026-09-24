"""Запускает тесты, изолируя нестабильный жизненный цикл окон Qt."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STANDARD_TEST_DIRECTORIES = (
    "application",
    "core",
    "infrastructure",
    "reporting",
)
LIGHTWEIGHT_UI_MODULES = (
    "tests.ui.test_combo_popup_source",
    "tests.ui.test_ui_internal_calls",
)


def run_standard_tests() -> bool:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for directory in STANDARD_TEST_DIRECTORIES:
        suite.addTests(
            loader.discover(
                str(PROJECT_ROOT / "tests" / directory),
                top_level_dir=str(PROJECT_ROOT),
            )
        )
    suite.addTests(loader.loadTestsFromNames(LIGHTWEIGHT_UI_MODULES))
    return unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


def run_window_tests() -> bool:
    from tests.ui.test_ui import UiTests

    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    test_names = unittest.defaultTestLoader.getTestCaseNames(UiTests)
    for test_name in test_names:
        test_id = f"tests.ui.test_ui.UiTests.{test_name}"
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", test_id, "-v"],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
        )
        if completed.returncode:
            return False
    return True


def main() -> int:
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))
    if not run_standard_tests():
        return 1
    if not run_window_tests():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
