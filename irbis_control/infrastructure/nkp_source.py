from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from irbis_control.infrastructure.excel_io import load_workbook_quiet
from irbis_control.paths import resource_path

NKP_DRUG_PAGE_URL = "https://nkp.rsl.ru/drug-literature-recommendations"
NKP_FOREIGN_AGENTS_PAGE_URL = "https://nkp.rsl.ru/foreign-agents-registry"

DRUG_CACHE_FILENAME = "publication-drugs-nkp.xlsx"
DRUG_META_FILENAME = "publication-drugs-nkp.json"
FOREIGN_AGENTS_CACHE_FILENAME = "publication-foreign-agent-nkp.xlsx"
FOREIGN_AGENTS_META_FILENAME = "publication-foreign-agent-nkp.json"

# Обратная совместимость с тестами и внешними импортами прошлых версий.
CACHE_FILENAME = DRUG_CACHE_FILENAME
META_FILENAME = DRUG_META_FILENAME

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "IRBIS64-Control/0.0.26"
)


@dataclass(slots=True)
class NkpRegistryResult:
    path: Path
    source_url: str
    used_cache: bool = False
    detail: str = ""


def _looks_like_excel(payload: bytes, content_type: str = "") -> bool:
    content_type = content_type.casefold()
    if payload.startswith(b"PK\x03\x04"):
        return True
    return "spreadsheet" in content_type or "excel" in content_type


def _validate_excel(path: Path) -> int:
    workbook = load_workbook_quiet(path, read_only=True, data_only=True)
    try:
        total_rows = 0
        has_expected_header = False
        for worksheet in workbook.worksheets:
            preview = list(worksheet.iter_rows(min_row=1, max_row=100, values_only=True))
            for row in preview:
                values = [str(value or "").strip().casefold() for value in row]
                if any("заглав" in value or "назван" in value for value in values) and any(
                    "автор" in value or "isbn" in value for value in values
                ):
                    has_expected_header = True
                    break
            total_rows += max(int(worksheet.max_row or 0), len(preview))
        if not has_expected_header:
            raise ValueError("В загруженном файле НКП не найдены ожидаемые столбцы Автор/Заглавие/ISBN.")
        if total_rows < 2:
            raise ValueError("Загруженный файл НКП не содержит записей.")
        return total_rows
    finally:
        workbook.close()


def _request(url: str, *, timeout: int = 25) -> tuple[bytes, str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.6",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        content_type = response.headers.get("Content-Type", "")
        final_url = response.geturl()
    return payload, content_type, final_url


def _extract_urls(text: str, base_url: str, *, keywords: Iterable[str] = ()) -> list[str]:
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()

    # href/src/data-* and URLs embedded in JS/JSON.
    raw_values = re.findall(
        r"(?i)(?:href|src|data-url|data-href|download-url)\s*=\s*['\"]([^'\"]+)['\"]",
        text,
    )
    raw_values.extend(re.findall(r"https?://[^\s'\"<>]+", text))
    raw_values.extend(re.findall(r"['\"]([^'\"]*(?:\.xlsx|\.xlsm)(?:\?[^'\"]*)?)['\"]", text, flags=re.I))

    for raw in raw_values:
        raw = unescape(raw).replace("\\/", "/").strip()
        if not raw or raw.startswith(("javascript:", "mailto:", "#")):
            continue
        url = urllib.parse.urljoin(base_url, raw)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        lowered = url.casefold()
        score = 0
        if ".xlsx" in lowered or ".xlsm" in lowered:
            score += 100
        if "drug" in lowered or "наркот" in lowered or "literature" in lowered:
            score += 30
        if "foreign" in lowered or "agent" in lowered or "иноагент" in lowered:
            score += 30
        if any(keyword.casefold() in lowered for keyword in keywords if keyword):
            score += 35
        if "download" in lowered or "export" in lowered:
            score += 20
        if "/api/" in lowered or "api." in lowered:
            score += 10
        if score:
            candidates.append((score, url))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [url for _score, url in candidates]


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._rows = []
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []
        elif self._cell_parts is not None and tag in {"br", "p", "div"}:
            self._cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._table_depth == 1 and tag in {"td", "th"} and self._cell_parts is not None:
            value = re.sub(r"\s+", " ", "".join(self._cell_parts)).strip()
            if self._row is not None:
                self._row.append(value)
            self._cell_parts = None
        elif self._table_depth == 1 and tag == "tr" and self._row is not None:
            if any(cell for cell in self._row) and self._rows is not None:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            if self._table_depth == 1 and self._rows:
                self.tables.append(self._rows)
                self._rows = None
            self._table_depth -= 1


def _best_registry_table(html: str) -> list[list[str]] | None:
    parser = _TableParser()
    try:
        parser.feed(html)
    except Exception:
        return None
    best: tuple[int, list[list[str]]] | None = None
    for table in parser.tables:
        score = 0
        for row in table[:10]:
            normalized = [re.sub(r"\s+", " ", cell.casefold()).strip() for cell in row]
            if any("заглав" in cell or "назван" in cell for cell in normalized):
                score += 4
            if any("автор" in cell for cell in normalized):
                score += 3
            if any("isbn" in cell for cell in normalized):
                score += 3
            if any("изд" in cell for cell in normalized):
                score += 1
        score += min(len(table), 1000) // 10
        if score >= 7 and (best is None or score > best[0]):
            best = (score, table)
    return best[1] if best else None


def _write_table_xlsx(rows: list[list[str]], target: Path) -> int:
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet("Список Книг")
    for row in rows:
        worksheet.append(row)
    workbook.save(target)
    return len(rows)


def _iter_json_lists(value: Any) -> Iterable[list[dict[str, Any]]]:
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            yield value
        for item in value:
            yield from _iter_json_lists(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_json_lists(item)


def _json_registry_rows(payload: bytes) -> list[list[str]] | None:
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except Exception:
        return None

    aliases = {
        "№ в реестре": {"registrynumber", "registryid", "agentregistrynumber", "numberinregistry"},
        "Автор": {"author", "authors", "creator", "name", "fio", "agentname"},
        "Роль относительно произведения": {"role", "authorrole", "publicationrole"},
        "Рег. №": {"registrationnumber", "regnumber", "regnum", "registration"},
        "ISBN": {"isbn", "isbns"},
        "Заглавие": {"title", "namebook", "booktitle", "publicationtitle"},
        "Выходные данные": {"publication", "publicationdata", "imprint", "outputdata"},
        "Сист.№": {"systemnumber", "systemid", "sysnumber"},
        "Город": {"city", "place", "publicationplace"},
        "Изд-во": {"publisher", "publishinghouse", "publishing"},
        "Год выпуска": {"year", "publicationyear", "publishyear"},
        "Язык": {"language", "lang"},
        "SEARCH": {"search", "url", "link"},
    }
    normalized_aliases = {
        header: {re.sub(r"[^a-zа-я0-9]+", "", alias.casefold()) for alias in names}
        for header, names in aliases.items()
    }

    best: tuple[int, list[dict[str, Any]], dict[str, str]] | None = None
    for items in _iter_json_lists(value):
        if len(items) < 2:
            continue
        keys = {str(key) for item in items[:20] for key in item.keys()}
        mapping: dict[str, str] = {}
        for key in keys:
            normalized = re.sub(r"[^a-zа-я0-9]+", "", key.casefold())
            for header, names in normalized_aliases.items():
                if normalized in names and header not in mapping:
                    mapping[header] = key
                    break
        score = len(mapping) + min(len(items), 10000) // 100
        if "Заглавие" in mapping and ("Автор" in mapping or "ISBN" in mapping):
            if best is None or score > best[0]:
                best = (score, items, mapping)

    if best is None:
        return None
    _score, items, mapping = best
    headers = [header for header in aliases if header in mapping]
    rows = [headers]
    for item in items:
        row: list[str] = []
        for header in headers:
            raw = item.get(mapping[header], "")
            if isinstance(raw, (dict, list)):
                raw = json.dumps(raw, ensure_ascii=False)
            row.append(str(raw or "").strip())
        if any(row):
            rows.append(row)
    return rows if len(rows) > 1 else None


def _browser_executable() -> str | None:
    names = ["msedge", "msedge.exe", "chrome", "chrome.exe", "chromium", "chromium.exe"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relative = [
            Path("Microsoft/Edge/Application/msedge.exe"),
            Path("Google/Chrome/Application/chrome.exe"),
        ]
        for root in roots:
            if not root:
                continue
            for suffix in relative:
                candidate = Path(root) / suffix
                if candidate.is_file():
                    return str(candidate)
    return None


def _render_dom(url: str, *, timeout: int = 35) -> str | None:
    browser = _browser_executable()
    if not browser:
        return None
    with tempfile.TemporaryDirectory(prefix="irbis64-nkp-browser-") as profile:
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            "--virtual-time-budget=12000",
            "--dump-dom",
            url,
        ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
    if not result.stdout:
        return None
    for encoding in ("utf-8", "cp1251"):
        try:
            return result.stdout.decode(encoding)
        except UnicodeDecodeError:
            pass
    return result.stdout.decode("utf-8", errors="replace")


def _save_payload(payload: bytes, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=target.stem + "-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    temp = Path(temp_name)
    temp.write_bytes(payload)
    try:
        rows = _validate_excel(temp)
        os.replace(temp, target)
        return rows
    finally:
        temp.unlink(missing_ok=True)


def _visible_data_row_count(rows: int) -> int:
    # HTML/JSON-таблицы обычно содержат одну строку заголовка. Для определения
    # типичного размера страницы нам важнее приблизительное число записей.
    return max(0, int(rows) - 1)


def _looks_like_single_page(rows: int) -> bool:
    """Определяет типичный неполный результат пагинированной таблицы НКП.

    SPA НКП в интерфейсе показывает ограниченную страницу записей. В прошлых
    версиях dump-dom принимал первые 50 строк за полный реестр и сохранял их
    в кэш. Не считаем маленький прямой XLSX подозрительным: проверка применяется
    только к HTML/JSON-таблицам.
    """

    data_rows = _visible_data_row_count(rows)
    if data_rows < 20:
        return False
    common_page_sizes = (20, 25, 30, 40, 50, 75, 100, 150, 200)
    return any(abs(data_rows - size) <= 2 for size in common_page_sizes)


def _partial_page_error(rows: int) -> str:
    data_rows = _visible_data_row_count(rows)
    return (
        f"НКП вернул только {data_rows:,} записей в HTML/JSON — это похоже на одну страницу "
        "пагинированного реестра. Неполный результат не будет использоваться."
    )


def _safe_write_table_xlsx(rows: list[list[str]], target: Path) -> int:
    """Записывает таблицу во временный XLSX и заменяет кэш только после проверки."""

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=target.stem + "-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        _write_table_xlsx(rows, temp)
        count = _validate_excel(temp)
        if _looks_like_single_page(count):
            raise ValueError(_partial_page_error(count))
        os.replace(temp, target)
        return count
    finally:
        temp.unlink(missing_ok=True)


def _cache_is_suspicious_partial(path: Path) -> tuple[bool, int]:
    rows = _validate_excel(path)
    return _looks_like_single_page(rows), rows


def _try_candidate_urls(urls: Iterable[str], target: Path) -> tuple[str, int] | None:
    for url in urls:
        try:
            payload, content_type, final_url = _request(url, timeout=20)
        except Exception:
            continue
        if _looks_like_excel(payload, content_type):
            try:
                rows = _save_payload(payload, target)
            except Exception:
                continue
            return final_url, rows
        rows = _json_registry_rows(payload)
        if rows:
            try:
                count = _safe_write_table_xlsx(rows, target)
            except Exception:
                continue
            return final_url, count
    return None


def _fetch_nkp_registry(
    cache_dir: str | Path,
    *,
    page_url: str,
    cache_filename: str,
    meta_filename: str,
    bundled_filename: str,
    registry_label: str,
    url_keywords: tuple[str, ...] = (),
) -> NkpRegistryResult:
    """Получает актуальную книжную выгрузку НКП и хранит проверенный XLSX-кэш."""

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / cache_filename
    meta_path = cache_dir / meta_filename
    errors: list[str] = []

    try:
        payload, content_type, final_url = _request(page_url)
        if _looks_like_excel(payload, content_type):
            rows = _save_payload(payload, target)
            _write_meta(meta_path, page_url, final_url, rows)
            return NkpRegistryResult(target, final_url, False, f"получено строк: {rows:,}")

        text = payload.decode("utf-8", errors="replace")
        candidate_result = _try_candidate_urls(
            _extract_urls(text, final_url, keywords=url_keywords),
            target,
        )
        if candidate_result:
            source_url, rows = candidate_result
            _write_meta(meta_path, page_url, source_url, rows)
            return NkpRegistryResult(target, source_url, False, f"получено строк: {rows:,}")

        json_rows = _json_registry_rows(payload)
        if json_rows:
            try:
                rows = _safe_write_table_xlsx(json_rows, target)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                _write_meta(meta_path, page_url, final_url, rows)
                return NkpRegistryResult(target, final_url, False, f"получено строк: {rows:,}")

        table = _best_registry_table(text)
        if table:
            try:
                rows = _safe_write_table_xlsx(table, target)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                _write_meta(meta_path, page_url, final_url, rows)
                return NkpRegistryResult(target, final_url, False, f"получено строк: {rows:,}")
    except Exception as exc:
        errors.append(str(exc))

    rendered = _render_dom(page_url)
    if rendered:
        try:
            candidate_result = _try_candidate_urls(
                _extract_urls(rendered, page_url, keywords=url_keywords),
                target,
            )
            if candidate_result:
                source_url, rows = candidate_result
                _write_meta(meta_path, page_url, source_url, rows)
                return NkpRegistryResult(target, source_url, False, f"получено строк: {rows:,}")
            table = _best_registry_table(rendered)
            if table:
                try:
                    rows = _safe_write_table_xlsx(table, target)
                except ValueError as exc:
                    errors.append(str(exc))
                else:
                    _write_meta(meta_path, page_url, page_url, rows)
                    return NkpRegistryResult(target, page_url, False, f"получено строк: {rows:,}")
        except Exception as exc:
            errors.append(str(exc))

    if target.is_file():
        try:
            suspicious, rows = _cache_is_suspicious_partial(target)
            if suspicious:
                errors.append(
                    f"Сохранённый кэш содержит только {_visible_data_row_count(rows):,} записей и похож на одну страницу НКП; "
                    "кэш отброшен."
                )
                target.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
            else:
                source_url = page_url
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    source_url = str(meta.get("source_url") or page_url)
                except Exception:
                    pass
                detail = "не удалось получить полную актуальную выгрузку НКП; используется последняя сохранённая копия"
                if errors:
                    detail += f" ({errors[-1]})"
                return NkpRegistryResult(target, source_url, True, f"{detail}; строк: {rows:,}")
        except Exception:
            target.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

    bundled = Path(resource_path("assets", "registries", bundled_filename))
    if bundled.is_file():
        try:
            rows = _validate_excel(bundled)
            shutil.copy2(bundled, target)
            detail = "не удалось получить полную актуальную выгрузку НКП; используется встроенная резервная копия"
            if errors:
                detail += f" ({errors[-1]})"
            return NkpRegistryResult(target, page_url, True, f"{detail}; строк: {rows:,}")
        except Exception as exc:
            errors.append(str(exc))

    suffix = f" Последняя ошибка: {errors[-1]}" if errors else ""
    raise RuntimeError(
        f"Не удалось получить актуальный {registry_label} с НКП РГБ. "
        f"Проверьте доступ к {page_url} и повторите запуск." + suffix
    )


def fetch_nkp_drug_registry(
    cache_dir: str | Path,
    *,
    page_url: str = NKP_DRUG_PAGE_URL,
) -> NkpRegistryResult:
    """Загружает актуальный перечень литературы по наркотическим веществам НКП РГБ."""

    return _fetch_nkp_registry(
        cache_dir,
        page_url=page_url,
        cache_filename=DRUG_CACHE_FILENAME,
        meta_filename=DRUG_META_FILENAME,
        bundled_filename="publication-drugs.xlsx",
        registry_label="реестр литературы",
        url_keywords=("drug", "literature", "наркот"),
    )


def fetch_nkp_foreign_agents_registry(
    cache_dir: str | Path,
    *,
    page_url: str = NKP_FOREIGN_AGENTS_PAGE_URL,
) -> NkpRegistryResult:
    """Загружает актуальный реестр изданий, выпущенных иностранными агентами, с НКП РГБ."""

    return _fetch_nkp_registry(
        cache_dir,
        page_url=page_url,
        cache_filename=FOREIGN_AGENTS_CACHE_FILENAME,
        meta_filename=FOREIGN_AGENTS_META_FILENAME,
        bundled_filename="publication-foreign-agent.xlsx",
        registry_label="реестр изданий иностранных агентов",
        url_keywords=("foreign", "agent", "иноагент"),
    )


def _write_meta(path: Path, source_page: str, source_url: str, rows: int) -> None:
    from datetime import datetime

    payload = {
        "source_page": source_page,
        "source_url": source_url,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rows": int(rows),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

