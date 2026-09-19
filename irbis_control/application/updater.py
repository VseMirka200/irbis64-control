from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from irbis_control.infrastructure.atomic_io import atomic_write_text

GITHUB_REPOSITORY_URL = "https://github.com/VseMirka200/irbis64-control"
GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/VseMirka200/irbis64-control/releases/latest"
)
_TRUSTED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0
    digest: str = ""


@dataclass(frozen=True)
class GitHubRelease:
    version: str
    page_url: str
    notes: str
    assets: tuple[ReleaseAsset, ...]


def version_parts(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lower().lstrip("v")
    return tuple(int(part) for part in __import__("re").findall(r"\d+", cleaned)) or (0,)


def is_newer_version(latest: str, current: str) -> bool:
    latest_parts = version_parts(latest)
    current_parts = version_parts(current)
    length = max(len(latest_parts), len(current_parts))
    return latest_parts + (0,) * (length - len(latest_parts)) > current_parts + (
        0,
    ) * (length - len(current_parts))


def fetch_latest_release(*, timeout: float = 15.0) -> GitHubRelease:
    request = urllib.request.Request(
        GITHUB_LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "IRBIS64Control-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "GitHub не нашёл опубликованный релиз приложения. "
                "Возможно, релизы ещё не опубликованы или репозиторий недоступен.\n\n"
                f"Страница релизов: {GITHUB_REPOSITORY_URL}/releases"
            ) from exc
        raise

    version = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not version:
        raise UpdateError("GitHub вернул релиз без номера версии.")
    assets = tuple(
        ReleaseAsset(
            name=str(item.get("name") or "").strip(),
            download_url=str(item.get("browser_download_url") or "").strip(),
            size=max(0, int(item.get("size") or 0)),
            digest=str(item.get("digest") or "").strip().lower(),
        )
        for item in payload.get("assets", [])
        if isinstance(item, dict) and item.get("name") and item.get("browser_download_url")
    )
    return GitHubRelease(
        version=version,
        page_url=str(payload.get("html_url") or GITHUB_REPOSITORY_URL),
        notes=str(payload.get("body") or "").strip(),
        assets=assets,
    )


def select_windows_asset(release: GitHubRelease) -> ReleaseAsset | None:
    def rank(asset: ReleaseAsset) -> tuple[int, str]:
        name = asset.name.casefold()
        if name.endswith(".exe") and ("setup" in name or "installer" in name):
            return 0, name
        if name.endswith(".zip") and ("windows" in name or "win64" in name or "irbis64" in name):
            return 1, name
        if name.endswith(".exe") and "control" in name and "db" not in name:
            return 2, name
        if name.endswith(".zip"):
            return 3, name
        return 99, name

    candidates = sorted((asset for asset in release.assets if rank(asset)[0] < 99), key=rank)
    return candidates[0] if candidates else None


def _validate_download_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _TRUSTED_DOWNLOAD_HOSTS:
        raise UpdateError("GitHub вернул недопустимый адрес файла обновления.")


def download_asset(
    asset: ReleaseAsset,
    destination_dir: str | Path,
    *,
    progress_cb: Callable[[int], None] | None = None,
    timeout: float = 60.0,
) -> Path:
    _validate_download_url(asset.download_url)
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / Path(asset.name).name
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination,
        prefix=f".{target.name}.",
        suffix=".part",
    )
    digest = hashlib.sha256()
    downloaded = 0
    try:
        request = urllib.request.Request(
            asset.download_url,
            headers={"User-Agent": "IRBIS64Control-Updater"},
        )
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(
            request, timeout=timeout
        ) as response:
            _validate_download_url(response.geturl())
            total = asset.size or int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    progress_cb(min(100, int(downloaded / total * 100)))
            output.flush()
            os.fsync(output.fileno())

        if asset.size and downloaded != asset.size:
            raise UpdateError(
                f"Размер обновления не совпал: получено {downloaded}, ожидалось {asset.size}."
            )
        expected = asset.digest.removeprefix("sha256:")
        if expected and digest.hexdigest().lower() != expected:
            raise UpdateError("Контрольная сумма обновления не совпала.")
        os.replace(temporary_name, target)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    if progress_cb:
        progress_cb(100)
    return target


def schedule_install(downloaded: str | Path, current_executable: str | Path) -> Path:
    """Schedule replacement after the frozen application exits."""
    if not getattr(sys, "frozen", False):
        raise UpdateError("Автоустановка доступна только в собранной EXE-версии.")

    package = Path(downloaded).resolve()
    executable = Path(current_executable).resolve()
    if not package.is_file() or not executable.is_file():
        raise UpdateError("Не найден файл обновления или текущий EXE.")

    def ps_quote(value: Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = package.parent / "install_irbis64_update.ps1"
    install_dir = executable.parent
    if package.suffix.casefold() == ".exe" and any(
        marker in package.name.casefold() for marker in ("setup", "installer")
    ):
        subprocess.Popen([str(package)], cwd=str(package.parent))
        return package

    if package.suffix.casefold() == ".zip":
        staging = package.parent / "staging"
        install_commands = f"""
$staging = {ps_quote(staging)}
if (Test-Path -LiteralPath $staging) {{ Remove-Item -LiteralPath $staging -Recurse -Force }}
Expand-Archive -LiteralPath {ps_quote(package)} -DestinationPath $staging -Force
Copy-Item -Path (Join-Path $staging '*') -Destination {ps_quote(install_dir)} -Recurse -Force
"""
    elif package.suffix.casefold() == ".exe":
        install_commands = (
            f"Copy-Item -LiteralPath {ps_quote(package)} "
            f"-Destination {ps_quote(executable)} -Force\n"
        )
    else:
        raise UpdateError("Формат обновления не поддерживается; требуется ZIP или EXE.")

    payload = f"""$ErrorActionPreference = 'Stop'
$process = Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue
if ($process) {{ $process.WaitForExit() }}
Start-Sleep -Milliseconds 700
{install_commands}
Start-Process -FilePath {ps_quote(executable)} -WorkingDirectory {ps_quote(install_dir)}
Remove-Item -LiteralPath {ps_quote(package)} -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
    atomic_write_text(script, payload, encoding="utf-8")
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        cwd=str(package.parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return script
