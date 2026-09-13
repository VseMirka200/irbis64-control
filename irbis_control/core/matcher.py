from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict
from itertools import combinations
from pathlib import Path
from typing import Any

from irbis_control.core.models import (
    ComparisonSummary,
    DatabaseRecord,
    ExcelEntry,
    ForeignAgentEntry,
    MarkerApplicationStats,  # noqa: F401 - прежний публичный импорт
    MatchResult,
)
from irbis_control.core.text import safe_text as safe_text
from irbis_control.infrastructure.atomic_io import atomic_write_via_path  # noqa: F401 - используется модулем экспорта
from irbis_control.infrastructure.excel_io import load_workbook_quiet as _load_workbook_quiet

try:
    from rapidfuzz import fuzz, process
except ImportError:  # Программа продолжит работать без приблизительного поиска.
    fuzz = None
    process = None


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


HEADER_SYNONYMS = {
    "publisher": {
        "издательство",
        "издатель",
        "изд-во",
        "изд во",
        "наименование издательства",
        "publisher",
        "publishing house",
    },
    "year": {"год", "год издания", "год выпуска", "год публикации", "year", "publication year"},
    "author": {
        "автор",
        "авторы",
        "фио автора",
        "author",
        "authors",
    },
    "title": {
        "заглавие",
        "название",
        "наименование",
        "название книги",
        "title",
        "book title",
    },
    "isbn": {
        "isbn",
        "isbn 10",
        "isbn 13",
        "исбн",
        "международный стандартный номер книги",
    },
    "registration_number": {
        "рег №",
        "рег номер",
        "регистрационный номер",
        "регистрационный №",
        "инв №",
        "инвентарный номер",
        "номер",
        "рег. №",
    },
}


FOREIGN_AGENT_HEADER_SYNONYMS = {
    "registry_number": {"№ п/п", "номер", "номер п/п"},
    "name": {
        "полное наименование прежнее наименование в случае его изменения фио псевдоним при наличии прежние фио в случае их изменения",
        "полное наименование фио псевдоним",
        "полное наименование фио",
        "наименование фио",
    },
    "participants": {"полное наименование или фио участников", "участники"},
    "agent_type": {"тип иностранного агента", "тип иноагента"},
    "inclusion_date": {
        "дата принятия минюстом россии решения о включении в реестр",
        "дата включения в реестр",
    },
    "exclusion_date": {
        "дата принятия минюстом россии решения об исключении из реестра при наличии",
        "дата исключения из реестра",
    },
}

SOURCE_SUBSTANCES = "Вещества"
MATCH_FIELDS = {
    "isbn": "ISBN",
    "title": "Название",
    "author": "автор",
    "publisher": "издательство",
    "year": "год",
}
EXTRA_MATCH_RULES = {
    "_".join(fields): (" + ".join(MATCH_FIELDS[key] for key in fields), fields)
    for size in range(2, len(MATCH_FIELDS) + 1)
    for fields in combinations(MATCH_FIELDS, size)
    if {"isbn", "title", "author"}.intersection(fields) and fields != ("title", "author")
}
MATCH_RULE_LABELS = {
    "use_isbn_matching": "ISBN",
    "use_title_fallback": "Название + автор",
    **{key: label for key, (label, _fields) in EXTRA_MATCH_RULES.items()},
}


def match_rule_needs_review(fields: tuple[str, ...]) -> bool:
    return not (
        "isbn" in fields or "title" in fields and ("author" in fields or {"publisher", "year"}.issubset(fields))
    )


def parse_match_rule(value: str) -> str:
    """Разбирает сочетание полей, не выполняя полученную строку как код."""
    parts = re.split(r"[+,]", unicodedata.normalize("NFKC", value).strip())
    if not value.strip() or any(not part.strip() for part in parts):
        raise ValueError("Введите поля через +, например: Название + издательство + год.")
    fields: set[str] = set()
    for part in parts:
        normalized = normalize_header(part)
        key = next(
            (key for key in MATCH_FIELDS if normalized in {normalize_header(alias) for alias in HEADER_SYNONYMS[key]}),
            None,
        )
        if key is None:
            raise ValueError(f"Неизвестное поле «{part.strip()}». Доступны: ISBN, название, автор, издательство, год.")
        if key in fields:
            raise ValueError(f"Поле «{MATCH_FIELDS[key]}» указано дважды.")
        fields.add(key)
    if fields == {"isbn"}:
        return "use_isbn_matching"
    if fields == {"title", "author"}:
        return "use_title_fallback"
    key = "_".join(field for field in MATCH_FIELDS if field in fields)
    if key not in EXTRA_MATCH_RULES:
        raise ValueError(
            "Выберите ISBN либо название или автора с другим полем. Одного автора, года или издательства недостаточно."
        )
    return key


SOURCE_FOREIGN_AGENTS = "Иностранные агенты"
DEFAULT_SUBSTANCE_MARKER = "^AIII"
DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE = "^AI^@{name}"
DEFAULT_AGE_MARKER = "^Z18+"
DEFAULT_SUBSTANCE_MARKER_FIELD = 333
DEFAULT_FOREIGN_AGENT_MARKER_FIELD = 333
DEFAULT_AGE_MARKER_FIELD = 900

TITLE_STOP_WORDS = {
    "роман",
    "романы",
    "повесть",
    "повести",
    "рассказ",
    "рассказы",
    "сборник",
    "издание",
    "изд",
    "учебник",
    "учебное",
    "пособие",
    "текст",
    "перевод",
    "английского",
    "англ",
    "русского",
    "рус",
    "книга",
    "кн",
    "том",
    "часть",
    "16",
    "18",
    "12",
    "6",
    "0",
}


# Позволяет отличить отмену пользователем от ошибки чтения или сравнения.
class ComparisonCancelled(RuntimeError):
    pass


def _cancelled(cancel_cb: CancelCallback | None) -> None:
    if cancel_cb and cancel_cb():
        raise ComparisonCancelled("Операция отменена пользователем")


def normalize_header(value: Any) -> str:
    text = unicodedata.normalize("NFKC", safe_text(value)).lower().replace("ё", "е")
    text = re.sub(r"[\s._-]+", " ", text)
    text = re.sub(r"[^0-9a-zа-я №]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


ISBN_PLACEHOLDERS = {"", "0", "-", "—", "–", "нет", "нет isbn", "без isbn", "б/н", "бн", "n/a", "na"}
ISBN_PATTERN = re.compile(
    r"(?<![0-9X])(?:97[89](?:[\s\-\u2010-\u2015]?[0-9]){10}|(?:[0-9](?:[\s\-\u2010-\u2015]?[0-9]){8}[\s\-\u2010-\u2015]?[0-9X]))(?![0-9X])",
    re.IGNORECASE,
)


def _isbn_checksum_valid(code: str) -> bool:
    if len(code) == 13 and code.isdigit():
        return sum((1 if index % 2 == 0 else 3) * int(char) for index, char in enumerate(code)) % 10 == 0
    if len(code) == 10 and code[:9].isdigit() and (code[9].isdigit() or code[9] == "X"):
        total = sum((10 - index) * int(char) for index, char in enumerate(code[:9]))
        total += 10 if code[9] == "X" else int(code[9])
        return total % 11 == 0
    return False


def extract_isbns(value: Any) -> list[str]:
    """Возвращает все корректные ISBN-10/ISBN-13 из одной ячейки."""
    text = unicodedata.normalize("NFKC", safe_text(value)).upper().replace("Х", "X").strip()
    if text.lower() in ISBN_PLACEHOLDERS:
        return []

    found: list[str] = []
    for match in ISBN_PATTERN.finditer(text):
        code = re.sub(r"[^0-9X]", "", match.group(0).upper())
        if _isbn_checksum_valid(code) and code not in found:
            found.append(code)

    # На случай ячейки, содержащей только ISBN с необычной пунктуацией.
    if not found:
        compact = re.sub(r"[^0-9X]", "", text)
        if _isbn_checksum_valid(compact):
            found.append(compact)
    return found


def normalize_isbn(value: Any) -> str:
    candidates = extract_isbns(value)
    return candidates[0] if candidates else ""


def normalize_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", safe_text(value)).lower().replace("ё", "е")
    # Содержимое скобок может быть частью настоящего названия (например,
    # «Я (не) робот»). Убираем сами скобки, а возрастные пометы удаляем ниже.
    text = re.sub(r"[\[\]()]", " ", text)
    text = re.sub(r"\b\d+\+\b", " ", text)
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    tokens = text.split()
    filtered = [token for token in tokens if token not in TITLE_STOP_WORDS]
    if filtered:
        return " ".join(filtered)

    # Иногда служебным словом является само название произведения: «Текст»,
    # «Роман», «Рассказы». В таком случае сохраняем главный сегмент до
    # первого двоеточия, иначе книга становится принципиально ненахожимой.
    main_part = re.split(r"[:;/]", unicodedata.normalize("NFKC", safe_text(value)), maxsplit=1)[0]
    main_part = main_part.lower().replace("ё", "е")
    main_part = re.sub(r"[\[\]()]|\b\d+\+\b", " ", main_part)
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-zа-я]+", " ", main_part)).strip()


def normalize_author(value: Any) -> str:
    text = unicodedata.normalize("NFKC", safe_text(value)).lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _author_identity(value: Any) -> tuple[str, tuple[str, ...]] | None:
    raw = unicodedata.normalize("NFKC", safe_text(value)).strip()
    if not raw:
        return None
    # Редакторские пометы вроде «(гл. ред.)» не являются частью ФИО.
    raw = re.sub(r"\([^)]*\)", " ", raw)
    first_person = re.split(r"[;\n]", raw, maxsplit=1)[0].strip()
    tokens = normalize_author(first_person).split()
    if not tokens:
        return None

    leading_initials: list[str] = []
    index = 0
    while index < len(tokens) - 1 and len(tokens[index]) == 1:
        leading_initials.append(tokens[index])
        index += 1

    if leading_initials and index < len(tokens):
        # «Л. Н. Толстой» / «Р. Фасхутдинов».
        return tokens[index], tuple(leading_initials[:2])

    # «Толстой Л. Н.», «Толстой Лев Николаевич», «Ильина, В. В.».
    surname = tokens[0]
    initials = tuple(token[0] for token in tokens[1:3] if token)
    return surname, initials


def author_surname(value: Any) -> str:
    identity = _author_identity(value)
    return identity[0] if identity else ""


def normalize_publication_year(value: Any) -> str:
    match = re.fullmatch(
        r"(?:cop|сор|печ)\.?\s*\[?(\d{4})\]?|\[?(\d{4})\]?(?:\s*г(?:од)?\.?)?",
        safe_text(value),
        re.IGNORECASE,
    )
    if not match:
        return ""
    return match.group(1) or match.group(2)


def normalize_publisher(value: Any) -> str:
    normalized = normalize_header(value)
    if normalized in {
        "нет",
        "не указано",
        "не указан",
        "неизвестно",
        "б и",
        "без издательства",
        "unknown",
        "na",
        "n a",
    }:
        return ""
    return normalized


def publisher_variants(value: Any) -> set[str]:
    """Возвращает полное название издателя и отдельные элементы списка."""
    raw = safe_text(value)
    variants = {normalize_publisher(raw)}
    variants.update(normalize_publisher(part) for part in re.split(r"[;,/|]+", raw))
    return variants - {""}


def _extract_subfield(value: str, code: str) -> str:
    match = re.search(rf"\^{re.escape(code)}([^\^]*)", value)
    return match.group(1).strip() if match else ""


def _read_text_file_with_encoding(path: str | Path) -> tuple[str, str]:
    """Читает файл без изменения исходных переносов строк и BOM."""
    path = Path(path)
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"

    last_error: Exception | None = None
    for encoding in ("utf-8", "cp1251"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"Не удалось определить кодировку файла {path.name}: {last_error}")


def _read_text_file(path: str | Path) -> str:
    text, _ = _read_text_file_with_encoding(path)
    return text


def database_record_from_tag_values(
    record_number: int,
    tag_values: Iterable[tuple[int | str, str]],
    *,
    source_file: str = "",
    source_record_number: int | None = None,
    raw_record: str = "",
) -> DatabaseRecord:
    """Создаёт DatabaseRecord из полей ИРБИС без промежуточного TXT-файла."""
    fields: dict[str, list[str]] = defaultdict(list)
    for tag, value in tag_values:
        try:
            key = str(int(tag))
        except (TypeError, ValueError):
            key = str(tag).strip().lstrip("0") or "0"
        fields[key].append(safe_text(value))

    isbns = [value for value in (_extract_subfield(item, "A") for item in fields.get("10", [])) if value]
    titles = [value for value in (_extract_subfield(item, "A") for item in fields.get("200", [])) if value]

    authors: list[str] = []
    primary_authors: list[str] = []
    for tag in ("700", "701", "702"):
        for item in fields.get(tag, []):
            parts = [
                _extract_subfield(item, "A"),
                _extract_subfield(item, "B"),
                _extract_subfield(item, "G"),
            ]
            author = " ".join(part for part in parts if part).strip()
            if author:
                authors.append(author)
                if tag in {"700", "701"}:
                    primary_authors.append(author)

    organizations: list[str] = []
    for tag in ("710", "711", "712"):
        for item in fields.get(tag, []):
            organization = _extract_subfield(item, "A")
            if organization:
                organizations.append(organization)

    inventory_numbers = [value for value in (_extract_subfield(item, "B") for item in fields.get("910", [])) if value]

    return DatabaseRecord(
        record_number=record_number,
        source_file=source_file,
        source_record_number=source_record_number if source_record_number is not None else record_number,
        isbns=isbns,
        titles=titles,
        authors=authors,
        primary_authors=primary_authors,
        organizations=organizations,
        inventory_numbers=inventory_numbers,
        publication=fields.get("210", []),
        raw_record=raw_record,
    )


def parse_database(
    path: str | Path,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> list[DatabaseRecord]:
    path = Path(path)
    if progress_cb:
        progress_cb(2, f"Чтение текстовой базы: {path.name}")
    text = _read_text_file(path)
    raw_records = [part for part in re.split(r"\r?\n\*{5}\s*(?:\r?\n|$)", text) if part.strip()]
    records: list[DatabaseRecord] = []
    total = max(len(raw_records), 1)

    for index, raw_record in enumerate(raw_records, start=1):
        if index % 250 == 0:
            _cancelled(cancel_cb)
            if progress_cb:
                progress_cb(2 + int(index / total * 23), f"Разбор базы: {index:,} из {total:,}")

        tag_values: list[tuple[int, str]] = []
        for line in raw_record.splitlines():
            match = re.match(r"#(\d+):\s?(.*)$", line.strip())
            if match:
                tag_values.append((int(match.group(1)), match.group(2)))

        records.append(
            database_record_from_tag_values(
                index,
                tag_values,
                source_file=str(path),
                source_record_number=index,
                raw_record=raw_record,
            )
        )

    if progress_cb:
        progress_cb(25, f"База загружена: {len(records):,} записей")
    return records


def _detect_header(rows: list[tuple[Any, ...]]) -> tuple[int, dict[str, int], list[str]]:
    best_score = 0
    best_row = -1
    best_map: dict[str, int] = {}
    best_headers: list[str] = []

    for row_index, row in enumerate(rows[:100]):
        mapping: dict[str, int] = {}
        headers = [safe_text(value) or f"Столбец {column + 1}" for column, value in enumerate(row)]
        for column, value in enumerate(row):
            normalized = normalize_header(value)
            for key, synonyms in HEADER_SYNONYMS.items():
                normalized_synonyms = {normalize_header(item) for item in synonyms}
                if normalized in normalized_synonyms and key not in mapping:
                    mapping[key] = column
                    break
        score = len(mapping) if any(key in mapping for key in ("isbn", "title", "author")) else 0
        if score > best_score:
            best_score = score
            best_row = row_index
            best_map = mapping
            best_headers = headers

    if best_score == 0:
        raise ValueError(
            "Не удалось найти строку заголовков. Нужен хотя бы один столбец: ISBN, Заглавие/Название или Автор."
        )
    return best_row, best_map, best_headers


def _make_entry(
    entry_id: int,
    source_file: Path,
    sheet_name: str,
    row_number: int,
    row: Iterable[Any],
    mapping: dict[str, int],
    headers: list[str],
) -> ExcelEntry:
    values = list(row)

    def get(key: str) -> str:
        column = mapping.get(key)
        if column is None or column >= len(values):
            return ""
        return safe_text(values[column])

    raw_data: dict[str, Any] = {}
    for index, value in enumerate(values):
        if value is None or safe_text(value) == "":
            continue
        header = headers[index] if index < len(headers) else f"Столбец {index + 1}"
        raw_data[header] = safe_text(value)

    raw_isbn = get("isbn")
    isbn = "" if raw_isbn.strip().lower() in ISBN_PLACEHOLDERS else raw_isbn

    return ExcelEntry(
        entry_id=entry_id,
        source_file=str(source_file),
        sheet_name=sheet_name,
        row_number=row_number,
        author=get("author"),
        title=get("title"),
        isbn=isbn,
        registration_number=get("registration_number"),
        raw_data=raw_data,
        publisher=get("publisher"),
        year=get("year"),
    )


def _read_xlsx_entries(
    path: Path,
    start_entry_id: int,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[ExcelEntry], list[str]]:
    entries: list[ExcelEntry] = []
    warnings: list[str] = []
    workbook = _load_workbook_quiet(path, read_only=True, data_only=True)
    entry_id = start_entry_id

    try:
        for worksheet in workbook.worksheets:
            _cancelled(cancel_cb)
            if worksheet.max_row == 1 and worksheet.max_column == 1:
                worksheet.reset_dimensions()
            max_preview_row = 100 if worksheet.max_row is None else min(100, worksheet.max_row)
            preview = list(worksheet.iter_rows(min_row=1, max_row=max_preview_row, values_only=True))
            if not preview or not any(any(value is not None for value in row) for row in preview):
                continue
            try:
                header_index, mapping, headers = _detect_header(preview)
            except ValueError as exc:
                warnings.append(f"{path.name}, лист «{worksheet.title}»: {exc}")
                continue

            for row_number, row in enumerate(
                worksheet.iter_rows(min_row=header_index + 2, values_only=True), start=header_index + 2
            ):
                if row_number % 500 == 0:
                    _cancelled(cancel_cb)
                entry = _make_entry(entry_id, path, worksheet.title, row_number, row, mapping, headers)
                if entry.isbn or entry.title or entry.author:
                    entries.append(entry)
                    entry_id += 1
    finally:
        workbook.close()

    return entries, warnings


def _read_xls_entries(
    path: Path,
    start_entry_id: int,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[ExcelEntry], list[str]]:
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError("Для файлов .xls требуется пакет xlrd. Запустите start.bat ещё раз.") from exc

    entries: list[ExcelEntry] = []
    warnings: list[str] = []
    workbook = xlrd.open_workbook(path)
    entry_id = start_entry_id

    for sheet in workbook.sheets():
        _cancelled(cancel_cb)
        preview = [tuple(sheet.row_values(index)) for index in range(min(100, sheet.nrows))]
        if not preview or not any(any(safe_text(value) for value in row) for row in preview):
            continue
        try:
            header_index, mapping, headers = _detect_header(preview)
        except ValueError as exc:
            warnings.append(f"{path.name}, лист «{sheet.name}»: {exc}")
            continue

        for row_index in range(header_index + 1, sheet.nrows):
            if row_index % 500 == 0:
                _cancelled(cancel_cb)
            row = tuple(sheet.row_values(row_index))
            entry = _make_entry(entry_id, path, sheet.name, row_index + 1, row, mapping, headers)
            if entry.isbn or entry.title or entry.author:
                entries.append(entry)
                entry_id += 1

    return entries, warnings


def _deduplicate_cross_sheet_entries(entries: list[ExcelEntry]) -> tuple[list[ExcelEntry], int]:
    """Убирает зеркальные копии одной записи на разных листах, не трогая повторы внутри листа."""
    first_sheet_by_key: dict[tuple[Any, ...], str] = {}
    result: list[ExcelEntry] = []
    skipped = 0

    for entry in entries:
        key = (
            normalize_author(entry.author),
            normalize_title(entry.title),
            tuple(extract_isbns(entry.isbn)),
            normalize_header(entry.registration_number),
            normalize_header(entry.publisher),
            normalize_publication_year(entry.year) or safe_text(entry.year),
        )
        first_sheet = first_sheet_by_key.get(key)
        if first_sheet is None:
            first_sheet_by_key[key] = entry.sheet_name
            result.append(entry)
        elif first_sheet == entry.sheet_name:
            # Повтор на одном и том же листе может означать разные экземпляры книги.
            result.append(entry)
        else:
            skipped += 1

    return result, skipped


def read_excel_entries(
    paths: list[str | Path],
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[ExcelEntry], list[str]]:
    all_entries: list[ExcelEntry] = []
    warnings: list[str] = []
    total = max(len(paths), 1)

    for file_index, source_path in enumerate(paths, start=1):
        _cancelled(cancel_cb)
        path = Path(source_path)
        if progress_cb:
            progress_cb(
                27 + int((file_index - 1) / total * 18),
                f"Чтение Excel {file_index} из {total}: {path.name}",
            )

        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
            entries, file_warnings = _read_xlsx_entries(path, len(all_entries) + 1, cancel_cb)
        elif suffix == ".xls":
            entries, file_warnings = _read_xls_entries(path, len(all_entries) + 1, cancel_cb)
        else:
            warnings.append(f"Файл {path.name} пропущен: неподдерживаемое расширение {suffix}")
            continue

        entries, duplicate_count = _deduplicate_cross_sheet_entries(entries)
        if duplicate_count:
            file_warnings.append(
                f"{path.name}: исключено повторов одних и тех же записей на других листах: {duplicate_count}."
            )

        for entry in entries:
            entry.entry_id = len(all_entries) + 1
            all_entries.append(entry)
        warnings.extend(file_warnings)

    if progress_cb:
        progress_cb(45, f"Excel-строк для проверки: {len(all_entries):,}")
    return all_entries, warnings


def _detect_foreign_agent_header(
    rows: list[tuple[Any, ...]],
) -> tuple[int, dict[str, int], list[str]]:
    normalized_synonyms = {
        key: {normalize_header(item) for item in values} for key, values in FOREIGN_AGENT_HEADER_SYNONYMS.items()
    }
    best_score = 0
    best_row = -1
    best_map: dict[str, int] = {}
    best_headers: list[str] = []

    for row_index, row in enumerate(rows[:100]):
        mapping: dict[str, int] = {}
        headers = [safe_text(value) or f"Столбец {column + 1}" for column, value in enumerate(row)]
        for column, value in enumerate(row):
            normalized = normalize_header(value)
            for key, synonyms in normalized_synonyms.items():
                if normalized in synonyms and key not in mapping:
                    mapping[key] = column
                    break
        score = len(mapping)
        if "name" in mapping:
            score += 5
        if score > best_score:
            best_score = score
            best_row = row_index
            best_map = mapping
            best_headers = headers

    if best_row < 0 or "name" not in best_map:
        raise ValueError("Не удалось найти столбец с полным наименованием/ФИО иностранного агента.")
    return best_row, best_map, best_headers


def _split_registry_participants(value: Any) -> list[str]:
    text = safe_text(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    parts = re.split(r"\s*,\s*(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", text)
    return [part.strip() for part in parts if part.strip()]


def _read_foreign_agents_xlsx(
    path: Path,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[ForeignAgentEntry], list[str]]:
    entries: list[ForeignAgentEntry] = []
    warnings: list[str] = []
    workbook = _load_workbook_quiet(path, read_only=True, data_only=True)
    entry_id = 1

    try:
        for worksheet in workbook.worksheets:
            _cancelled(cancel_cb)
            if worksheet.max_row == 1 and worksheet.max_column == 1:
                worksheet.reset_dimensions()
            max_preview_row = 100 if worksheet.max_row is None else min(100, worksheet.max_row)
            preview = list(
                worksheet.iter_rows(
                    min_row=1,
                    max_row=max_preview_row,
                    values_only=True,
                )
            )
            if not preview or not any(any(value is not None for value in row) for row in preview):
                continue
            try:
                header_index, mapping, headers = _detect_foreign_agent_header(preview)
            except ValueError as exc:
                warnings.append(f"{path.name}, лист «{worksheet.title}»: {exc}")
                continue

            for row_number, row in enumerate(
                worksheet.iter_rows(min_row=header_index + 2, values_only=True),
                start=header_index + 2,
            ):
                if row_number % 500 == 0:
                    _cancelled(cancel_cb)
                values = list(row)

                def get(key: str) -> str:
                    column = mapping.get(key)
                    if column is None or column >= len(values):
                        return ""
                    return safe_text(values[column])

                name = get("name")
                if not name:
                    continue

                raw_data: dict[str, Any] = {}
                for index, value in enumerate(values):
                    if value is None or safe_text(value) == "":
                        continue
                    header = headers[index] if index < len(headers) else f"Столбец {index + 1}"
                    raw_data[header] = safe_text(value)

                entry = ForeignAgentEntry(
                    entry_id=entry_id,
                    source_file=str(path),
                    sheet_name=worksheet.title,
                    row_number=row_number,
                    registry_number=get("registry_number"),
                    name=name,
                    participants=_split_registry_participants(get("participants")),
                    agent_type=get("agent_type"),
                    inclusion_date=get("inclusion_date"),
                    exclusion_date=get("exclusion_date"),
                    raw_data=raw_data,
                )
                # Для проверки используются только действующие записи. Исключённые остаются
                # в исходном файле, но не должны приводить к новым меткам в библиотечной базе.
                if entry.is_active:
                    entries.append(entry)
                    entry_id += 1
    finally:
        workbook.close()

    return entries, warnings


def read_foreign_agent_entries(
    path: str | Path | None,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[ForeignAgentEntry], list[str]]:
    if not path:
        return [], []
    source = Path(path)
    if progress_cb:
        progress_cb(47, f"Чтение реестра иностранных агентов: {source.name}")
    if source.suffix.lower() not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        raise ValueError("Реестр иностранных агентов должен быть файлом Excel формата .xlsx или .xlsm.")
    entries, warnings = _read_foreign_agents_xlsx(source, cancel_cb)
    if progress_cb:
        progress_cb(52, f"Действующих записей в реестре иностранных агентов: {len(entries):,}")
    return entries, warnings


from irbis_control.core.matching_engine import (  # noqa: F401 - совместимый публичный фасад
    DatabaseIndex,
    ForeignAgentIndex,
    ForeignAgentSearchTerm,
    _name_order_variants,
    _person_identity,
    _person_identity_match_kind,
    _registry_name_variants,
    _registry_plain_name,
    compare_database_records,
    compare_files,
    compare_foreign_agents,
    compare_substance_entries,
)


def export_results(*args: Any, **kwargs: Any) -> Path:
    from irbis_control.core.report_export import export_results as implementation

    return implementation(*args, **kwargs)


# Изменение и очистка записей вынесены в отдельный модуль и загружаются по требованию.
def _modify_matched_record(*args: Any, **kwargs: Any):
    from irbis_control.core.marker_updates import _modify_matched_record as implementation

    return implementation(*args, **kwargs)


def remove_markers_from_tag_values(*args: Any, **kwargs: Any):
    from irbis_control.core.marker_updates import remove_markers_from_tag_values as implementation

    return implementation(*args, **kwargs)


def remove_database_markers(*args: Any, **kwargs: Any):
    from irbis_control.core.marker_updates import remove_database_markers as implementation

    return implementation(*args, **kwargs)


def build_markers_by_record(*args: Any, **kwargs: Any):
    from irbis_control.core.marker_updates import build_markers_by_record as implementation

    return implementation(*args, **kwargs)


def apply_markers_to_tag_values(*args: Any, **kwargs: Any):
    from irbis_control.core.marker_updates import apply_markers_to_tag_values as implementation

    return implementation(*args, **kwargs)


def export_modified_database(*args: Any, **kwargs: Any) -> Path:
    from irbis_control.core.marker_updates import export_modified_database as implementation

    return implementation(*args, **kwargs)


def export_modified_databases(*args: Any, **kwargs: Any) -> list[Path]:
    from irbis_control.core.marker_updates import export_modified_databases as implementation

    return implementation(*args, **kwargs)
def compare_and_export(
    database_path: str | Path | list[str | Path],
    excel_paths: list[str | Path],
    output_path: str | Path,
    modified_database_path: str | Path,
    *,
    foreign_agents_path: str | Path | None = None,
    use_isbn_matching: bool = True,
    use_title_fallback: bool = True,
    match_rules: dict[str, bool] | None = None,
    use_fuzzy: bool = False,
    fuzzy_threshold: int = 90,
    report_options: dict[str, Any] | None = None,
    substance_marker: str = DEFAULT_SUBSTANCE_MARKER,
    foreign_agent_marker_template: str = DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    age_marker: str = DEFAULT_AGE_MARKER,
    substance_marker_field: int = DEFAULT_SUBSTANCE_MARKER_FIELD,
    foreign_agent_marker_field: int = DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    age_marker_field: int = DEFAULT_AGE_MARKER_FIELD,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[MatchResult], ComparisonSummary]:
    database_paths = database_path if isinstance(database_path, list) else [database_path]
    results, summary = compare_files(
        database_paths,
        excel_paths,
        foreign_agents_path=foreign_agents_path,
        use_isbn_matching=use_isbn_matching,
        use_title_fallback=use_title_fallback,
        match_rules=match_rules,
        use_fuzzy=use_fuzzy,
        fuzzy_threshold=fuzzy_threshold,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )
    if report_options is None or report_options.get("enabled", True):
        export_results(
            output_path,
            results,
            summary,
            progress_cb,
            cancel_cb,
            report_options=report_options,
        )
    if not (report_options and report_options.get("report_only", False)):
        export_modified_databases(
            database_paths,
            modified_database_path,
            results,
            summary,
            progress_cb,
            cancel_cb,
            substance_marker=substance_marker,
            foreign_agent_marker_template=foreign_agent_marker_template,
            age_marker=age_marker,
            substance_marker_field=substance_marker_field,
            foreign_agent_marker_field=foreign_agent_marker_field,
            age_marker_field=age_marker_field,
        )
    return results, summary


def result_to_dict(result: MatchResult) -> dict[str, Any]:
    return asdict(result)
