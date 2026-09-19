from __future__ import annotations

import html
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from irbis_control.core.matcher import (
    DEFAULT_AGE_MARKER,
    DEFAULT_AGE_MARKER_FIELD,
    DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD,
    DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE,
    DEFAULT_SUBSTANCE_MARKER,
    DEFAULT_SUBSTANCE_MARKER_FIELD,
    SOURCE_FOREIGN_AGENTS,
    CancelCallback,
    ProgressCallback,
    _cancelled,
    _extract_subfield,
    _read_text_file_with_encoding,
    _registry_name_variants,
    _registry_plain_name,
    normalize_author,
)
from irbis_control.core.models import ComparisonSummary, MarkerApplicationStats, MatchResult
from irbis_control.core.report_export import _unique_join
from irbis_control.core.text import safe_text


def _detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\n" in text:
        return "\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _field_number(line: str) -> int | None:
    """Возвращает номер поля строки вида ``#333: ...``."""
    match = re.match(r"^\s*#(\d+):", line)
    return int(match.group(1)) if match else None


def _ordered_field_insert_index(lines: list[str], target_field: int) -> int:
    """Находит вертикальную позицию поля в числовом порядке записи.

    Новое поле ставится после всех полей с номером не больше целевого и
    перед первым полем с большим номером. Благодаря этому #333 не уезжает
    к #900, а отсутствующее #900 появляется перед #910 и последующими полями.
    """
    last_field_index: int | None = None
    for index, line in enumerate(lines):
        field_number = _field_number(line)
        if field_number is None:
            continue
        if field_number > target_field:
            return index
        last_field_index = index

    # Не помещаем новое поле после служебных пустых строк в конце записи.
    return last_field_index + 1 if last_field_index is not None else len(lines)


def _record_author_marker(raw_record: str) -> str:
    authors: list[str] = []
    for line in re.split(r"\r\n|\n|\r", raw_record):
        match = re.match(r"^\s*#(?:700|701|702):\s*(.*)$", line)
        if not match:
            continue
        value = match.group(1)
        parts = [
            _extract_subfield(value, "A"),
            _extract_subfield(value, "B"),
            _extract_subfield(value, "G"),
        ]
        author = " ".join(part for part in parts if part).strip()
        if author:
            authors.append(author)
    if not authors:
        return ""
    return _unique_join(authors, "; ").upper()


def _registry_person_marker_name(value: str) -> str:
    """Формирует человекочитаемое ФИО для метки иностранного агента.

    Реестр может хранить псевдоним либо как ``(псевдоним: ...)``, либо в
    кавычках после ФИО. В библиотечную запись записываем только персону, а
    псевдоним явно подписываем, чтобы он не выглядел второй фамилией/частью ФИО.
    """
    raw = unicodedata.normalize("NFKC", safe_text(value)).strip()
    if not raw:
        return ""

    explicit_aliases = re.findall(r"\(\s*псевдоним\s*:\s*([^)]+)\)", raw, flags=re.IGNORECASE)
    quoted_aliases = re.findall(r'[«"]([^»"]+)[»"]', raw)

    # Для персональных записей текст в кавычках в реестре используется как
    # псевдоним. Убираем его из основного ФИО и выводим отдельно.
    main_raw = re.sub(r"\(\s*псевдоним\s*:\s*[^)]+\)", " ", raw, flags=re.IGNORECASE)
    main_raw = re.sub(r'[«"]([^»"]+)[»"]', " ", main_raw)
    main_name = _registry_plain_name(main_raw)
    if not main_name:
        main_name = _registry_plain_name(raw)

    aliases: list[str] = []
    for alias_source in [*explicit_aliases, *quoted_aliases]:
        for part in re.split(r"\s*[,;]\s*", alias_source):
            clean = _registry_plain_name(part)
            if clean and normalize_author(clean) != normalize_author(main_name):
                aliases.append(clean)
    aliases = list(dict.fromkeys(aliases))

    main_display = re.sub(r"\s+", " ", main_name).strip().upper()
    alias_display = [re.sub(r"\s+", " ", item).strip().upper() for item in aliases]
    if main_display and alias_display:
        return f"{main_display} (ПСЕВДОНИМ: {'; '.join(alias_display)})"
    return main_display


def _foreign_agent_marker_name(result: MatchResult) -> str:
    """Возвращает именно совпавшего автора, а не название родительской записи.

    Особенно важно для реестровых записей-организаций: если книга совпала с
    человеком из списка участников, метка должна содержать этого человека, а
    не, например, название проекта/организации «НАСТОЯЩАЯ РОССИЯ».
    """
    entry = result.foreign_agent
    matched = re.sub(r"\s+", " ", result.matched_value).strip() if result.matched_value else ""

    if entry is not None:
        # Совпадение по участнику: находим исходную строку участника, чтобы не
        # потерять псевдоним, и форматируем именно её.
        if result.note.startswith("Участник"):
            matched_normalized = normalize_author(matched)
            for participant in entry.participants:
                variants = _registry_name_variants(participant, is_person=True)
                if any(normalize_author(item) == matched_normalized for item in variants):
                    return _registry_person_marker_name(participant)
            if matched:
                return _registry_person_marker_name(matched)

        # Основная запись физического лица: используем полное ФИО из реестра,
        # включая правильно оформленный псевдоним.
        if "физичес" in normalize_author(entry.agent_type) and entry.name:
            return _registry_person_marker_name(entry.name)

    # Резервный вариант — именно совпавшее значение автора, но не название
    # родительской организации/проекта.
    if matched:
        return _registry_person_marker_name(matched)
    return _record_author_marker(result.database.raw_record) if result.database else ""


def _foreign_organization_marker_name(result: MatchResult) -> str:
    """Возвращает каноническое название совпавшей организации или проекта."""
    # Если совпал именно участник организации/проекта, в метку записываем
    # участника, а не название родительской записи реестра.
    if result.note.startswith("Участник") and result.matched_value:
        return re.sub(r"\s+", " ", result.matched_value).strip().upper()
    if result.foreign_agent is not None and result.foreign_agent.name:
        return re.sub(r"\s+", " ", result.foreign_agent.name).strip().upper()
    return re.sub(r"\s+", " ", result.matched_value).strip().upper()


def _result_is_eligible_for_txt_marker(result: MatchResult) -> bool:
    """Разрешает метку только для надёжно подтверждённого совпадения."""
    if result.status != "Совпадение" or result.confidence < 100.0 or result.database is None:
        return False
    if result.source_type != SOURCE_FOREIGN_AGENTS:
        return True
    return result.method in {
        "Реестр иностранных агентов: Автор",
        "Реестр иностранных агентов: Организация",
        "Реестр иностранных агентов: Название",
    }


def _normalized_marker_text(value: str) -> str:
    # ИРБИС/Excel иногда приносят визуально невидимые Unicode-символы
    # (соединитель слов, символы нулевой ширины, мягкий перенос и т. п.). На экране две метки
    # выглядят одинаково, но простое сравнение строк считает их разными.
    # Для дедупликации убираем все форматирующие/управляющие символы и
    # приводим любые Unicode-разделители к обычному пробелу.
    # Некоторые источники сохраняют пробел как буквальную HTML-сущность
    # ``&#x20;``. Для сравнения меток это тот же обычный пробел.
    normalized = unicodedata.normalize("NFKC", html.unescape(safe_text(value)))
    cleaned: list[str] = []
    for char in normalized:
        category = unicodedata.category(char)
        if category.startswith("C"):
            continue
        if category.startswith("Z"):
            cleaned.append(" ")
        else:
            cleaned.append(char)
    normalized = "".join(cleaned).replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _foreign_marker_identity(value: str) -> str | None:
    """Ключ метки иноагента ^AI/^AO для удаления повторов.

    Если в метке явно указан псевдоним, используем его как идентификатор
    персоны. В реестре встречаются дубли строк с опечаткой в настоящей
    фамилии, но с одним и тем же псевдонимом (например, ЧХАРТИШВИЛИ и
    ЧХАРТИШВИЛЛИ при псевдониме БОРИС АКУНИН). Для библиотечной записи это
    одна и та же отметка, а не два разных иностранных агента.

    В остальных случаях возвращаем нормализованное имя после ^AI^@. Это позволяет
    схлопывать два визуально одинаковых повторения даже при скрытых Unicode
    символах, попавших из реестра/Excel. Другие значения поля 333 не трогаем.
    """
    normalized = _normalized_marker_text(value)
    prefix_match = re.match(r"^\^a([io])\^@", normalized)
    if prefix_match is None:
        return None
    marker_kind = prefix_match.group(1)
    name = normalized[prefix_match.end() :].strip()
    if not name:
        return None
    pseudonym_match = re.search(r"\(\s*псевдоним\s*:\s*([^)]+)\)", name, flags=re.IGNORECASE)
    if pseudonym_match:
        pseudonym = re.sub(
            r"[^0-9a-zа-я]+",
            "",
            pseudonym_match.group(1),
            flags=re.IGNORECASE,
        )
        if pseudonym:
            return f"{marker_kind}:{pseudonym}"
    # Пунктуация и пробелы в ФИО/псевдониме не должны превращать одного
    # автора в две отдельные метки. Буквы и цифры сохраняем.
    identity = re.sub(r"[^0-9a-zа-я]+", "", name, flags=re.IGNORECASE)
    return f"{marker_kind}:{identity}" if identity else None


def _marker_only_repeat_count(value: str, marker: str) -> int:
    """Возвращает число повторов, если поле состоит только из одной метки N раз."""
    normalized_value = _normalized_marker_text(value)
    normalized_marker = _normalized_marker_text(marker)
    if not normalized_marker or not normalized_value:
        return 0
    count = 0
    remaining = normalized_value
    while remaining.startswith(normalized_marker):
        count += 1
        remaining = remaining[len(normalized_marker) :].strip()
    return count if count and not remaining else 0


def _field_contains_marker(value: str, marker: str) -> bool:
    """Проверяет наличие метки в поле, включая поле с дополнительными подполями."""
    normalized_value = _normalized_marker_text(value)
    normalized_marker = _normalized_marker_text(marker)
    if not normalized_marker:
        return True
    if normalized_marker.startswith("^"):
        return bool(re.search(re.escape(normalized_marker) + r"(?=\^|$)", normalized_value))
    return bool(
        re.search(
            r"(?<!\w)" + re.escape(normalized_marker) + r"(?!\w)",
            normalized_value,
        )
    )


def _modify_matched_record(
    raw_record: str,
    newline: str,
    markers_333: Iterable[str],
    age_marker: str = DEFAULT_AGE_MARKER,
    *,
    marker_field: int = DEFAULT_SUBSTANCE_MARKER_FIELD,
    age_field: int = DEFAULT_AGE_MARKER_FIELD,
    field_markers: Iterable[tuple[int, str]] | None = None,
) -> tuple[str, bool]:
    """Добавляет метки в правильных вертикальных позициях без дублирования."""
    had_trailing_newline = raw_record.endswith(("\r\n", "\n", "\r"))
    lines = re.split(r"\r\n|\n|\r", raw_record)
    if had_trailing_newline and lines and lines[-1] == "":
        lines = lines[:-1]

    changed = False

    requested_markers = list(
        field_markers if field_markers is not None else ((marker_field, marker) for marker in markers_333)
    )
    markers: list[tuple[int, str]] = []
    marker_keys: set[tuple[int, str]] = set()
    for field_number, marker in requested_markers:
        marker = marker.strip() if marker else ""
        if not marker:
            continue
        identity = _foreign_marker_identity(marker)
        key = (
            int(field_number),
            f"foreign:{identity}" if identity is not None else _normalized_marker_text(marker),
        )
        if key in marker_keys:
            continue
        marker_keys.add(key)
        markers.append((int(field_number), marker))
    requested_foreign_keys = {
        (field_number, identity)
        for field_number, marker in markers
        if (identity := _foreign_marker_identity(marker)) is not None
    }
    if requested_foreign_keys:
        seen_foreign: set[tuple[int, str]] = set()
        deduped_lines: list[str] = []
        for line in lines:
            field_number = _field_number(line)
            match = re.match(r"^\s*#\d{1,3}:\s*(.*?)\s*$", line)
            identity = _foreign_marker_identity(match.group(1)) if match else None
            pair = (field_number, identity) if field_number is not None and identity is not None else None
            if pair is not None and pair in requested_foreign_keys:
                if pair in seen_foreign:
                    changed = True
                    continue
                seen_foreign.add(pair)
            deduped_lines.append(line)
        lines = deduped_lines

    # Если в TXT уже лежит несколько одинаковых повторений нашей метки,
    # исправляем их так же, как в прямом режиме ИРБИС.
    for field_number, marker in markers:
        matching_indices: list[int] = []
        for index, line in enumerate(lines):
            if _field_number(line) != field_number:
                continue
            match = re.match(r"^\s*#\d{1,3}:\s*(.*?)\s*$", line)
            if not match or not _field_contains_marker(match.group(1), marker):
                continue
            value = match.group(1)
            if _marker_only_repeat_count(value, marker) > 1:
                lines[index] = f"#{field_number:03d}: {marker}"
                changed = True
            matching_indices.append(index)

        if len(matching_indices) > 1:
            for index in reversed(matching_indices[1:]):
                match = re.match(r"^\s*#\d{1,3}:\s*(.*?)\s*$", lines[index])
                if match and _marker_only_repeat_count(match.group(1), marker) == 1:
                    del lines[index]
                    changed = True

    existing_field_values: dict[int, list[str]] = defaultdict(list)
    for line in lines:
        field_number = _field_number(line)
        match = re.match(r"^\s*#\d{1,3}:\s*(.*?)\s*$", line)
        if field_number is not None and match:
            existing_field_values[field_number].append(match.group(1))

    # Возрастная метка дописывается в каждое существующее выбранное поле.
    has_age_field = False
    age_pattern = re.compile(rf"^(\s*#{age_field:03d}:\s*)(.*?)(\s*)$")
    for index, line in enumerate(lines):
        match = age_pattern.match(line)
        if not match:
            continue
        has_age_field = True
        prefix, value, trailing = match.groups()
        if age_marker and not _field_contains_marker(value, age_marker):
            lines[index] = f"{prefix}{value}{age_marker}{trailing}"
            changed = True

    for field_number, marker in markers:
        if not any(_field_contains_marker(value, marker) for value in existing_field_values[field_number]):
            lines.insert(
                _ordered_field_insert_index(lines, field_number),
                f"#{field_number:03d}: {marker}",
            )
            existing_field_values[field_number].append(marker)
            changed = True

    if age_marker and not has_age_field:
        lines.insert(
            _ordered_field_insert_index(lines, age_field),
            f"#{age_field:03d}: {age_marker}",
        )
        changed = True

    modified = newline.join(lines)
    if had_trailing_newline:
        modified += newline
    return modified, changed


def _marker_removal_pattern(marker: str, *, name_template: bool = False) -> re.Pattern[str] | None:
    marker = marker.strip()
    if not marker:
        return None
    if name_template and "{name}" in marker:
        parts = marker.split("{name}")
        expression = r"[^\^\r\n]*".join(re.escape(part) for part in parts)
    else:
        expression = re.escape(marker)
    if marker.startswith("^"):
        expression += r"(?=\^|$)"
    else:
        expression = r"(?<!\w)" + expression + r"(?!\w)"
    return re.compile(expression, re.IGNORECASE)


def _record_contains_removal_marker(
    raw_record: str,
    removals: dict[int, list[tuple[str, bool]]],
) -> bool:
    for line in re.split(r"\r\n|\n|\r", raw_record):
        field_number = _field_number(line)
        patterns = removals.get(field_number or -1, [])
        match = re.match(r"^\s*#\d{1,3}:\s*(.*?)\s*$", line)
        if not patterns or not match:
            continue
        value = match.group(1)
        for marker, is_template in patterns:
            pattern = _marker_removal_pattern(marker, name_template=is_template)
            if pattern is not None and pattern.search(value):
                return True
    return False


def _remove_markers_from_record(
    raw_record: str,
    newline: str,
    removals: dict[int, list[tuple[str, bool]]],
    *,
    conditional_removals: dict[int, list[tuple[str, bool]]] | None = None,
    conditional_on: dict[int, list[tuple[str, bool]]] | None = None,
) -> tuple[str, bool]:
    """Удаляет только известные пометки, сохраняя прочее содержимое полей."""
    had_trailing_newline = raw_record.endswith(("\r\n", "\n", "\r"))
    lines = re.split(r"\r\n|\n|\r", raw_record)
    if had_trailing_newline and lines and lines[-1] == "":
        lines = lines[:-1]

    effective_removals: dict[int, list[tuple[str, bool]]] = {
        field_number: list(patterns) for field_number, patterns in removals.items()
    }
    if conditional_removals and conditional_on and _record_contains_removal_marker(raw_record, conditional_on):
        for field_number, patterns in conditional_removals.items():
            effective_removals.setdefault(field_number, []).extend(patterns)

    changed = False
    cleaned_lines: list[str] = []
    for line in lines:
        field_number = _field_number(line)
        patterns = effective_removals.get(field_number or -1, [])
        match = re.match(r"^(\s*#\d{1,3}:\s*)(.*?)(\s*)$", line)
        if not patterns or not match:
            cleaned_lines.append(line)
            continue
        prefix, value, trailing = match.groups()
        cleaned_value = value
        for marker, is_template in patterns:
            pattern = _marker_removal_pattern(marker, name_template=is_template)
            if pattern is not None:
                cleaned_value = pattern.sub("", cleaned_value)
        if cleaned_value == value:
            cleaned_lines.append(line)
            continue
        changed = True
        if cleaned_value.strip():
            cleaned_lines.append(f"{prefix}{cleaned_value}{trailing}")

    modified = newline.join(cleaned_lines)
    if had_trailing_newline:
        modified += newline
    return modified, changed


def remove_markers_from_tag_values(
    tag_values: Iterable[tuple[int, str]],
    *,
    substance_marker: str = DEFAULT_SUBSTANCE_MARKER,
    foreign_agent_marker_template: str = DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    foreign_organization_marker_template: str = DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE,
    age_marker: str = DEFAULT_AGE_MARKER,
    substance_marker_field: int = DEFAULT_SUBSTANCE_MARKER_FIELD,
    foreign_agent_marker_field: int = DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    foreign_organization_marker_field: int = DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD,
    age_marker_field: int = DEFAULT_AGE_MARKER_FIELD,
) -> tuple[list[tuple[int, str]], bool]:
    """Удаляет настроенные метки прямо из набора полей записи ИРБИС.

    В отличие от ``remove_database_markers`` эта функция не создаёт TXT-файл,
    поэтому подходит для прямой серверной очистки. Остальное содержимое полей
    и порядок повторений сохраняются.
    """
    values = [(int(tag), str(value)) for tag, value in tag_values]

    removals: dict[int, list[tuple[str, bool]]] = defaultdict(list)
    classification_removals: dict[int, list[tuple[str, bool]]] = defaultdict(list)
    substance_fields = {DEFAULT_SUBSTANCE_MARKER_FIELD, int(substance_marker_field)}
    foreign_fields = {DEFAULT_FOREIGN_AGENT_MARKER_FIELD, int(foreign_agent_marker_field)}
    organization_fields = {
        DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD,
        int(foreign_organization_marker_field),
    }
    age_fields = {DEFAULT_AGE_MARKER_FIELD, int(age_marker_field)}

    for field_number in substance_fields:
        patterns = [(DEFAULT_SUBSTANCE_MARKER, False), (substance_marker, False)]
        removals[field_number].extend(patterns)
        classification_removals[field_number].extend(patterns)
    for field_number in foreign_fields:
        patterns = [
            (DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE, True),
            (foreign_agent_marker_template, True),
        ]
        removals[field_number].extend(patterns)
        classification_removals[field_number].extend(patterns)
    for field_number in organization_fields:
        patterns = [
            (DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE, True),
            (foreign_organization_marker_template, True),
        ]
        removals[field_number].extend(patterns)
        classification_removals[field_number].extend(patterns)
    for field_number in age_fields:
        removals[field_number].extend([(DEFAULT_AGE_MARKER, False), (age_marker, False)])

    # Совместимость со старой схемой: ^A18+ удаляется только из записи,
    # где присутствует наша классификационная метка.
    has_classification_marker = False
    for tag, value in values:
        for marker, is_template in classification_removals.get(tag, []):
            pattern = _marker_removal_pattern(marker, name_template=is_template)
            if pattern is not None and pattern.search(value):
                has_classification_marker = True
                break
        if has_classification_marker:
            break

    conditional_age_removals: dict[int, list[tuple[str, bool]]] = defaultdict(list)
    if has_classification_marker:
        for field_number in substance_fields | foreign_fields | organization_fields:
            conditional_age_removals[field_number].append(("^A18+", False))

    changed = False
    cleaned: list[tuple[int, str]] = []
    for tag, value in values:
        cleaned_value = value
        patterns = [*removals.get(tag, []), *conditional_age_removals.get(tag, [])]
        for marker, is_template in patterns:
            pattern = _marker_removal_pattern(marker, name_template=is_template)
            if pattern is not None:
                cleaned_value = pattern.sub("", cleaned_value)
        if cleaned_value != value:
            changed = True
        if cleaned_value.strip():
            cleaned.append((tag, cleaned_value))
        elif cleaned_value == value:
            # Пустое исходное поле не относится к очистке и должно сохраниться.
            cleaned.append((tag, value))

    return cleaned, changed


def remove_database_markers(
    source_path: str | Path,
    output_path: str | Path,
    *,
    substance_marker: str = DEFAULT_SUBSTANCE_MARKER,
    foreign_agent_marker_template: str = DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    foreign_organization_marker_template: str = DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE,
    age_marker: str = DEFAULT_AGE_MARKER,
    substance_marker_field: int = DEFAULT_SUBSTANCE_MARKER_FIELD,
    foreign_agent_marker_field: int = DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    foreign_organization_marker_field: int = DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD,
    age_marker_field: int = DEFAULT_AGE_MARKER_FIELD,
) -> tuple[Path, int]:
    """Создаёт TXT-копию без стандартных и текущих настроенных пометок."""
    source = Path(source_path)
    output = Path(output_path)
    if source.resolve() == output.resolve():
        raise ValueError("Очищенная TXT-база не должна перезаписывать исходный файл.")

    removals: dict[int, list[tuple[str, bool]]] = defaultdict(list)
    classification_removals: dict[int, list[tuple[str, bool]]] = defaultdict(list)
    substance_fields = {DEFAULT_SUBSTANCE_MARKER_FIELD, substance_marker_field}
    foreign_fields = {DEFAULT_FOREIGN_AGENT_MARKER_FIELD, foreign_agent_marker_field}
    organization_fields = {
        DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD,
        foreign_organization_marker_field,
    }
    age_fields = {DEFAULT_AGE_MARKER_FIELD, age_marker_field}
    for field_number in substance_fields:
        patterns = [
            (DEFAULT_SUBSTANCE_MARKER, False),
            (substance_marker, False),
        ]
        removals[field_number].extend(patterns)
        classification_removals[field_number].extend(patterns)
    for field_number in foreign_fields:
        patterns = [
            (DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE, True),
            (foreign_agent_marker_template, True),
        ]
        removals[field_number].extend(patterns)
        classification_removals[field_number].extend(patterns)
    for field_number in organization_fields:
        patterns = [
            (DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE, True),
            (foreign_organization_marker_template, True),
        ]
        removals[field_number].extend(patterns)
        classification_removals[field_number].extend(patterns)
    for field_number in age_fields:
        removals[field_number].extend(
            [
                (DEFAULT_AGE_MARKER, False),
                (age_marker, False),
            ]
        )

    # Старая пометка ^A18+ удаляется только из записей, где действительно была
    # пометка вещества или иноагента. Самостоятельные ^A18+ в базе сохраняются.
    conditional_age_removals: dict[int, list[tuple[str, bool]]] = defaultdict(list)
    for field_number in substance_fields | foreign_fields | organization_fields:
        conditional_age_removals[field_number].append(("^A18+", False))

    text, encoding = _read_text_file_with_encoding(source)
    newline = _detect_newline(text)
    parts = re.split(r"(\r?\n\*{5}\s*(?:\r?\n|$))", text)
    cleaned_records = 0
    for part_index in range(0, len(parts), 2):
        raw_record = parts[part_index]
        if not raw_record.strip():
            continue
        cleaned, changed = _remove_markers_from_record(
            raw_record,
            newline,
            removals,
            conditional_removals=conditional_age_removals,
            conditional_on=classification_removals,
        )
        parts[part_index] = cleaned
        if changed:
            cleaned_records += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding=encoding, newline="") as output_file:
        output_file.write("".join(parts))
    return output, cleaned_records


# Возвращает метки по номерам записей. Пограничные совпадения не должны менять базу.
def build_markers_by_record(
    results: list[MatchResult],
    *,
    substance_marker: str = DEFAULT_SUBSTANCE_MARKER,
    foreign_agent_marker_template: str = DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    foreign_organization_marker_template: str = DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE,
    substance_marker_field: int = DEFAULT_SUBSTANCE_MARKER_FIELD,
    foreign_agent_marker_field: int = DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    foreign_organization_marker_field: int = DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD,
    include_records_without_markers: bool = False,
) -> dict[int, list[tuple[int, str]]]:
    """Готовит метки по MFN/номеру записи для TXT и прямой записи в ИРБИС."""
    markers_by_record: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for result in results:
        if not _result_is_eligible_for_txt_marker(result) or result.database is None:
            continue
        record_number = result.database.source_record_number or result.database.record_number
        if include_records_without_markers:
            markers_by_record.setdefault(record_number, [])
        if result.source_type == SOURCE_FOREIGN_AGENTS:
            if result.method == "Реестр иностранных агентов: Автор":
                marker_name = _foreign_agent_marker_name(result)
                marker = foreign_agent_marker_template.replace("{name}", marker_name)
                marker_field = foreign_agent_marker_field
            else:
                marker_name = _foreign_organization_marker_name(result)
                marker = foreign_organization_marker_template.replace("{name}", marker_name)
                marker_field = foreign_organization_marker_field
        else:
            marker = substance_marker
            marker_field = substance_marker_field
        marker = marker.strip()
        field_marker = (int(marker_field), marker)
        foreign_identity = _foreign_marker_identity(marker) if result.source_type == SOURCE_FOREIGN_AGENTS else None
        marker_key = (
            int(marker_field),
            f"foreign:{foreign_identity}" if foreign_identity is not None else _normalized_marker_text(marker),
        )
        existing_keys = {
            (
                int(existing_field),
                f"foreign:{existing_identity}"
                if (existing_identity := _foreign_marker_identity(existing_marker)) is not None
                else _normalized_marker_text(existing_marker),
            )
            for existing_field, existing_marker in markers_by_record[record_number]
        }
        if marker and marker_key not in existing_keys:
            markers_by_record[record_number].append(field_marker)
    return dict(markers_by_record)


# Принимает поля записи и метки, возвращает новые поля и признак изменений; сохраняет постороннее содержимое полей.
def apply_markers_to_tag_values(
    tag_values: Iterable[tuple[int, str]],
    field_markers: Iterable[tuple[int, str]],
    *,
    age_marker: str = DEFAULT_AGE_MARKER,
    age_marker_field: int = DEFAULT_AGE_MARKER_FIELD,
    stats: MarkerApplicationStats | None = None,
) -> tuple[list[tuple[int, str]], bool]:
    """Добавляет метки прямо в набор полей записи ИРБИС без дублей.

    Если более старая версия программы уже успела записать одну и ту же
    длинную метку дважды (двумя повторениями поля или два раза внутри одного
    поля), при следующей обработке записи оставляется ровно один экземпляр.
    """
    fields = [(int(tag), str(value)) for tag, value in tag_values]

    # Дедуплицируем запросы не только побайтово, но и по отображаемому тексту:
    # NBSP/обычный пробел и другие совместимые Unicode-варианты не должны
    # порождать два одинаковых уведомления.
    requested: list[tuple[int, str]] = []
    requested_keys: set[tuple[int, str]] = set()
    for tag, marker in field_markers:
        marker = str(marker).strip()
        if not marker:
            continue
        key = (int(tag), _normalized_marker_text(marker))
        if key in requested_keys:
            continue
        requested_keys.add(key)
        requested.append((int(tag), marker))

    if stats is not None:
        for tag, marker in requested:
            marker_identity = _foreign_marker_identity(marker)
            occurrences = 0
            for existing_tag, value in fields:
                if existing_tag != tag:
                    continue
                same_foreign_marker = marker_identity is not None and _foreign_marker_identity(value) == marker_identity
                if not same_foreign_marker and not _field_contains_marker(value, marker):
                    continue
                repeat_count = _marker_only_repeat_count(value, marker)
                occurrences += max(1, repeat_count)
            if occurrences:
                stats.already_present += 1
            else:
                stats.added += 1

    changed = False

    # Отдельно схлопываем короткие авторские метки ^AI^@... по смысловому
    # ключу. Это закрывает случай из АРМ Каталогизатора, когда два повторения
    # выглядят абсолютно одинаково, но одно содержит невидимый Unicode-символ.
    requested_foreign_keys = {
        (tag, key) for tag, marker in requested if (key := _foreign_marker_identity(marker)) is not None
    }
    if requested_foreign_keys:
        seen_foreign: set[tuple[int, str]] = set()
        deduped_fields: list[tuple[int, str]] = []
        for tag, value in fields:
            foreign_key = _foreign_marker_identity(value)
            pair = (tag, foreign_key) if foreign_key is not None else None
            if pair is not None and pair in requested_foreign_keys:
                if pair in seen_foreign:
                    changed = True
                    if stats is not None:
                        stats.duplicates_repaired += 1
                    continue
                seen_foreign.add(pair)
            deduped_fields.append((tag, value))
        fields = deduped_fields

    # Исправляем уже существующие дубли именно наших запрошенных меток.
    # Чужие/служебные значения в том же поле не трогаем.
    for tag, marker in requested:
        matching_indices: list[int] = []
        for index, (existing_tag, value) in enumerate(fields):
            if existing_tag != tag or not _field_contains_marker(value, marker):
                continue
            repeat_count = _marker_only_repeat_count(value, marker)
            if repeat_count > 1:
                fields[index] = (existing_tag, marker)
                changed = True
                if stats is not None:
                    stats.duplicates_repaired += repeat_count - 1
            matching_indices.append(index)

        # Если одинаковая метка записана отдельными повторениями одного поля,
        # оставляем первое. Удаляем только повторения, состоящие из одной метки, чтобы не
        # потерять дополнительное содержимое служебного поля.
        if len(matching_indices) > 1:
            for index in reversed(matching_indices[1:]):
                if _marker_only_repeat_count(fields[index][1], marker) == 1:
                    del fields[index]
                    changed = True
                    if stats is not None:
                        stats.duplicates_repaired += 1

    age_marker = age_marker.strip()
    age_positions = [index for index, (tag, _value) in enumerate(fields) if tag == int(age_marker_field)]
    if stats is not None and age_marker:
        if not age_positions:
            stats.added += 1
        else:
            for index in age_positions:
                _tag, value = fields[index]
                if _field_contains_marker(value, age_marker):
                    stats.already_present += 1
                else:
                    stats.added += 1
    if age_marker and age_positions:
        for index in age_positions:
            tag, value = fields[index]
            if not _field_contains_marker(value, age_marker):
                fields[index] = (tag, value + age_marker)
                changed = True

    existing_by_tag: dict[int, list[str]] = defaultdict(list)
    for tag, value in fields:
        existing_by_tag[tag].append(value)

    def insert_ordered(tag: int, value: str) -> None:
        position = len(fields)
        for index, (existing_tag, _existing_value) in enumerate(fields):
            if existing_tag > tag:
                position = index
                break
        fields.insert(position, (tag, value))

    for tag, marker in requested:
        requested_foreign_key = _foreign_marker_identity(marker)
        already_present = any(_field_contains_marker(value, marker) for value in existing_by_tag[tag])
        if not already_present and requested_foreign_key is not None:
            already_present = any(
                _foreign_marker_identity(value) == requested_foreign_key for value in existing_by_tag[tag]
            )
        if not already_present:
            insert_ordered(tag, marker)
            existing_by_tag[tag].append(marker)
            changed = True

    if age_marker and not age_positions:
        insert_ordered(int(age_marker_field), age_marker)
        changed = True

    return fields, changed


def export_modified_database(
    source_path: str | Path,
    output_path: str | Path,
    results: list[MatchResult],
    summary: ComparisonSummary,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
    *,
    substance_marker: str = DEFAULT_SUBSTANCE_MARKER,
    foreign_agent_marker_template: str = DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    foreign_organization_marker_template: str = DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE,
    age_marker: str = DEFAULT_AGE_MARKER,
    substance_marker_field: int = DEFAULT_SUBSTANCE_MARKER_FIELD,
    foreign_agent_marker_field: int = DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    foreign_organization_marker_field: int = DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD,
    age_marker_field: int = DEFAULT_AGE_MARKER_FIELD,
) -> Path:
    """Сохраняет копию TXT-базы и помечает только найденные записи."""
    source_path = Path(source_path)
    output_path = Path(output_path)
    if source_path.resolve() == output_path.resolve():
        raise ValueError("Изменённая TXT-база не должна перезаписывать исходный файл.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if progress_cb:
        progress_cb(94, "Добавление меток в найденные записи TXT")

    source_results = [
        result
        for result in results
        if result.database is not None
        and (not result.database.source_file or Path(result.database.source_file).resolve() == source_path.resolve())
    ]
    markers_by_record = build_markers_by_record(
        source_results,
        substance_marker=substance_marker,
        foreign_agent_marker_template=foreign_agent_marker_template,
        foreign_organization_marker_template=foreign_organization_marker_template,
        substance_marker_field=substance_marker_field,
        foreign_agent_marker_field=foreign_agent_marker_field,
        foreign_organization_marker_field=foreign_organization_marker_field,
    )
    matched_numbers = set(markers_by_record)
    if age_marker.strip():
        matched_numbers.update(
            result.database.source_record_number or result.database.record_number
            for result in source_results
            if _result_is_eligible_for_txt_marker(result) and result.database is not None
        )
    text, encoding = _read_text_file_with_encoding(source_path)
    newline = _detect_newline(text)

    # Разделители ***** сохраняются без изменений. Чётные части — записи, нечётные — разделители.
    parts = re.split(r"(\r?\n\*{5}\s*(?:\r?\n|$))", text)
    record_number = 0
    modified_count = 0

    for part_index in range(0, len(parts), 2):
        raw_record = parts[part_index]
        if not raw_record.strip():
            continue
        record_number += 1
        if record_number not in matched_numbers:
            continue
        if modified_count % 250 == 0:
            _cancelled(cancel_cb)
        modified_record, changed = _modify_matched_record(
            raw_record,
            newline,
            [],
            age_marker.strip(),
            age_field=age_marker_field,
            field_markers=markers_by_record.get(record_number, []),
        )
        parts[part_index] = modified_record
        if changed:
            modified_count += 1

    with output_path.open("w", encoding=encoding, newline="") as output_file:
        output_file.write("".join(parts))
    summary.modified_database_file = str(output_path)
    summary.modified_database_records = len(matched_numbers)

    if progress_cb:
        progress_cb(100, f"Готово: помечено записей TXT — {len(matched_numbers):,}")
    return output_path


def _modified_output_for_source(output_path: Path, source_path: Path, multiple: bool) -> Path:
    if not multiple:
        return output_path
    return output_path.with_name(f"{output_path.stem}_{source_path.stem}{output_path.suffix}")


def export_modified_databases(
    source_paths: list[str | Path],
    output_path: str | Path,
    results: list[MatchResult],
    summary: ComparisonSummary,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
    *,
    substance_marker: str = DEFAULT_SUBSTANCE_MARKER,
    foreign_agent_marker_template: str = DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    foreign_organization_marker_template: str = DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE,
    age_marker: str = DEFAULT_AGE_MARKER,
    substance_marker_field: int = DEFAULT_SUBSTANCE_MARKER_FIELD,
    foreign_agent_marker_field: int = DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    foreign_organization_marker_field: int = DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD,
    age_marker_field: int = DEFAULT_AGE_MARKER_FIELD,
) -> list[Path]:
    output = Path(output_path)
    multiple = len(source_paths) > 1
    written: list[Path] = []
    total_marked = 0
    files = []
    for source in source_paths:
        source_path = Path(source)
        target = _modified_output_for_source(output, source_path, multiple)
        export_modified_database(
            source_path,
            target,
            results,
            summary,
            progress_cb,
            cancel_cb,
            substance_marker=substance_marker,
            foreign_agent_marker_template=foreign_agent_marker_template,
            foreign_organization_marker_template=foreign_organization_marker_template,
            age_marker=age_marker,
            substance_marker_field=substance_marker_field,
            foreign_agent_marker_field=foreign_agent_marker_field,
            foreign_organization_marker_field=foreign_organization_marker_field,
            age_marker_field=age_marker_field,
        )
        written.append(target)
        files.append(str(target))
        total_marked += summary.modified_database_records

    summary.modified_database_file = "; ".join(files)
    summary.modified_database_records = total_marked
    return written


# Сверяет файлы по переданным настройкам и возвращает результаты со сводкой после сохранения выбранных выходных файлов.
