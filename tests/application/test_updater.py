import hashlib
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from irbis_control.application.updater import (
    GitHubRelease,
    ReleaseAsset,
    UpdateError,
    download_asset,
    fetch_latest_release,
    is_newer_version,
    schedule_install,
    select_windows_asset,
)


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload)
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    @staticmethod
    def geturl() -> str:
        return "https://release-assets.githubusercontent.com/update.zip"


class UpdaterTests(unittest.TestCase):
    def test_missing_release_returns_none_without_checking_main_branch(self) -> None:
        error = urllib.error.HTTPError("https://api.github.com", 404, "Not Found", {}, None)
        with patch("irbis_control.application.updater.urllib.request.urlopen", side_effect=error) as urlopen:
            release = fetch_latest_release()

        self.assertIsNone(release)
        urlopen.assert_called_once()

    def test_other_http_errors_are_not_reported_as_missing_release(self) -> None:
        error = urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, None)
        with (
            patch("irbis_control.application.updater.urllib.request.urlopen", side_effect=error),
            self.assertRaises(urllib.error.HTTPError) as raised,
        ):
            fetch_latest_release()
        self.assertEqual(403, raised.exception.code)

    def test_version_comparison_ignores_missing_zero_parts(self) -> None:
        self.assertFalse(is_newer_version("v1.0", "1.0.0"))
        self.assertTrue(is_newer_version("v1.0.1", "1.0.0"))

    def test_installer_asset_has_priority(self) -> None:
        release = GitHubRelease(
            version="v2.0.0",
            page_url="https://github.com/example/release",
            notes="",
            assets=(
                ReleaseAsset("IRBIS64Control-win64.zip", "https://github.com/a.zip"),
                ReleaseAsset("IRBIS64Control-Setup.exe", "https://github.com/a.exe"),
            ),
        )

        self.assertEqual("IRBIS64Control-Setup.exe", select_windows_asset(release).name)

    def test_download_checks_size_and_sha256(self) -> None:
        payload = b"release archive"
        asset = ReleaseAsset(
            "IRBIS64Control-win64.zip",
            "https://github.com/example/update.zip",
            size=len(payload),
            digest="sha256:" + hashlib.sha256(payload).hexdigest(),
        )
        progress: list[int] = []

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch(
                "irbis_control.application.updater.urllib.request.urlopen",
                return_value=FakeResponse(payload),
            ),
        ):
            downloaded = download_asset(asset, temp_dir, progress_cb=progress.append)
            self.assertEqual(payload, downloaded.read_bytes())

        self.assertEqual(100, progress[-1])

    def test_source_mode_does_not_replace_python_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "update.exe"
            executable = Path(temp_dir) / "python.exe"
            package.write_bytes(b"update")
            executable.write_bytes(b"python")

            with self.assertRaisesRegex(UpdateError, "EXE-версии"):
                schedule_install(package, executable)

    def test_installer_waits_for_application_exit_and_runs_silently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "IRBIS64Control-Setup-1.0.0.exe"
            executable = Path(temp_dir) / "ИРБИС64 Контроль.exe"
            package.write_bytes(b"installer")
            executable.write_bytes(b"application")

            with (
                patch("irbis_control.application.updater.sys.frozen", True, create=True),
                patch("irbis_control.application.updater.subprocess.Popen") as popen,
            ):
                script = schedule_install(package, executable)

            payload = script.read_text(encoding="utf-8")
            self.assertIn(f"Get-Process -Id {os.getpid()}", payload)
            self.assertIn("'/VERYSILENT'", payload)
            self.assertIn("-Wait -PassThru", payload)
            self.assertIn(str(executable.resolve()), payload)
            popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
