from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process

from irbis_control.core import matcher as _matcher
from irbis_control.core.matcher import (
    EXTRA_MATCH_RULES,
    SOURCE_FOREIGN_AGENTS,
    SOURCE_SUBSTANCES,
    CancelCallback,
    ProgressCallback,
    _author_identity,
    _author_order_variants,
    _cancelled,
    _extract_subfield,
    author_surnames,
    extract_isbns,
    isbn_match_keys,
    match_rule_needs_review,
    normalize_author,
    normalize_publication_year,
    normalize_publisher,
    normalize_title,
    primary_author_contributor,
    publisher_variants,
)
from irbis_control.core.models import (
    ComparisonOptions,
    ComparisonSummary,
    DatabaseRecord,
    ExcelEntry,
    ForeignAgentEntry,
    MatchResult,
)
from irbis_control.core.review_groups import count_review_record_groups
from irbis_control.core.text import safe_text


def _registry_plain_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", safe_text(value)).replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r'[«»„“”"]', " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^0-9A-Za-zА-Яа-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _name_order_variants(value: str) -> list[str]:
    clean = _registry_plain_name(value)
    tokens = clean.split()
    variants = [clean] if clean else []
    if len(tokens) == 2:
        variants.append(f"{tokens[1]} {tokens[0]}")
    return list(dict.fromkeys(item for item in variants if item))


def _registry_name_variants(value: str, *, is_person: bool) -> list[str]:
    raw = safe_text(value)
    variants: list[str] = []
    quoted_values = re.findall(r"[«\"]([^»\"]+)[»\"]", raw)
    pseudonym_values = re.findall(r"\(\s*псевдоним\s*:\s*([^)]+)\)", raw, flags=re.IGNORECASE)

    if is_person:
        # Для ФИО кавычки обычно содержат псевдоним или прежнее имя.
        main_value = _registry_plain_name(re.sub(r"[«\"]([^»\"]+)[»\"]", " ", raw))
        main_value = _registry_plain_name(re.sub(r"\(\s*псевдоним\s*:\s*[^)]+\)", " ", main_value, flags=re.IGNORECASE))
    else:
        # Для организаций сохраняем полное официальное наименование. Не добавляем
        # отдельно общую юридическую форму вроде «общество с ограниченной ответственностью».
        main_value = _registry_plain_name(raw)
    if main_value:
        variants.append(main_value)

    for quoted in quoted_values:
        for part in re.split(r"\s*,\s*", quoted):
            variants.extend(clean for clean in _name_order_variants(part) if clean and len(clean) >= 4)
    for pseudonym in pseudonym_values:
        for part in re.split(r"\s*,\s*", pseudonym):
            variants.extend(clean for clean in _name_order_variants(part) if clean and len(clean) >= 4)
    return list(dict.fromkeys(variants))


_ORGANIZATION_HINT_RE = re.compile(
    r"\b(?:ооо|ао|пао|нко|ано|общество|организация|фонд|ассоциация|союз|движение|"
    r"проект|издание|газета|журнал|редакция|компания|агентство|центр|институт|"
    r"телеканал|радио|издательский\s+дом|автономная\s+некоммерческая)\b",
    flags=re.IGNORECASE,
)


def _participant_is_person(value: str) -> bool:
    """Отличает ФИО участника от организации/проекта в смешанном столбце реестра."""
    return _ORGANIZATION_HINT_RE.search(_registry_plain_name(value)) is None


def _foreign_agent_search_terms(entry: ForeignAgentEntry) -> list[tuple[str, str, bool]]:
    """Преобразует строку реестра в обычные значения для общего индекса базы."""
    main_is_person = "физичес" in normalize_author(entry.agent_type)
    raw_terms = [
        (variant, "ФИО/наименование", main_is_person)
        for variant in _registry_name_variants(entry.name, is_person=main_is_person)
    ]
    for participant in entry.participants:
        is_person = _participant_is_person(participant)
        raw_terms.extend(
            (variant, "Участник", is_person) for variant in _registry_name_variants(participant, is_person=is_person)
        )

    seen: set[tuple[str, str, bool]] = set()
    terms: list[tuple[str, str, bool]] = []
    for value, kind, is_person in raw_terms:
        key = (normalize_author(value), kind, is_person)
        if key[0] and key not in seen:
            seen.add(key)
            terms.append((value, kind, is_person))
    return terms


def _foreign_agent_identity_key(entry: ForeignAgentEntry) -> tuple[str, str]:
    """Стабильный ключ агента для книжной выгрузки с тысячами повторов."""
    return normalize_author(entry.name), entry.registry_number.strip().casefold()


def _foreign_publication_role_is_author(value: str) -> bool:
    role = normalize_author(value)
    return role in {"авт", "автор", "соавт", "авт текста"}


def _publication_year_from_text(value: str) -> str:
    years = re.findall(r"(?<!\d)(?:1[5-9]\d{2}|20\d{2})(?!\d)", safe_text(value))
    return years[-1] if years else ""


def _raw_title_words(value: str) -> set[str]:
    """Слова из исходной строки заглавия без отбрасывания части после «/».

    Нужны только как дополнительное подтверждение для автоматического fuzzy-решения.
    Например, строка «Эпоха мёртвых / Андрей Круз Начало» после обычной
    нормализации превращается в «эпоха мертвых». Слово «Начало» всё ещё есть
    в исходной строке и позволяет не подтвердить по ошибке тома «Москва» и
    «Прорыв».
    """
    raw = unicodedata.normalize("NFKC", safe_text(value)).lower().replace("ё", "е")
    return set(re.findall(r"[0-9a-zа-я]+", raw))


def _fuzzy_title_has_specific_evidence(entry_title: str, normalized_entry_title: str, candidate_title: str) -> bool:
    """Проверяет специфические слова кандидата перед автоподтверждением fuzzy-match.

    token_set_ratio даёт 100%, когда одно заглавие является подмножеством другого.
    Это полезно для составных заглавий, но опасно для серий с разными томами.
    Если у кандидата есть слова, которых нет в нормализованном заглавии реестра,
    они должны присутствовать хотя бы в полной исходной строке.
    """
    candidate_words = set(candidate_title.split())
    normalized_words = set(normalized_entry_title.split())
    extra_words = candidate_words - normalized_words
    return not extra_words or extra_words <= _raw_title_words(entry_title)


def _foreign_publication_results(
    index: DatabaseIndex,
    entry: ForeignAgentEntry,
    options: ComparisonOptions,
) -> list[MatchResult]:
    """Ищет конкретное издание из книжной выгрузки иностранных агентов.

    ISBN считается достаточным идентификатором. Для заглавия дополнительно
    используем автора/псевдоним, роль и год выхода, чтобы не ставить метку по
    одному общему заглавию вроде «Рассказы».
    """
    if not entry.isbn.strip() and not entry.title.strip():
        return []

    synthetic = ExcelEntry(
        entry_id=entry.entry_id,
        source_file=entry.source_file,
        sheet_name=entry.sheet_name,
        row_number=entry.row_number,
        author=entry.name,
        title=entry.title,
        isbn=entry.isbn,
        registration_number=entry.registry_number,
        raw_data=entry.raw_data,
    )

    # ISBN: не требуем совпадения роли/автора. В строке списка иноагент может
    # быть переводчиком, редактором или издателем, а не основным автором 700.
    isbn_records: list[DatabaseRecord] = []
    seen_isbn_records: set[int] = set()
    for isbn in isbn_match_keys(entry.isbn):
        for record in index.by_isbn.get(isbn, []):
            if record.record_number not in seen_isbn_records:
                seen_isbn_records.add(record.record_number)
                isbn_records.append(record)
    if isbn_records:
        return [
            MatchResult(
                status="Совпадение",
                method="Список изданий иноагентов: ISBN",
                confidence=100.0,
                excel=synthetic,
                database=record,
                note=f"Роль: {entry.role}" if entry.role else "Точный ISBN из списка изданий",
                source_type=SOURCE_FOREIGN_AGENTS,
                matched_value=entry.name,
                foreign_agent=entry,
            )
            for record in isbn_records
        ]

    normalized_title = normalize_title(entry.title)
    if not normalized_title:
        return []
    title_records = list(index.by_title.get(normalized_title, []))
    is_person = "физичес" in normalize_author(entry.agent_type)
    author_role = is_person and _foreign_publication_role_is_author(entry.role)

    if title_records:
        if author_role:
            exact_author_records: list[tuple[DatabaseRecord, str]] = []
            partial_author_records: list[tuple[DatabaseRecord, str]] = []
            author_variants = _registry_name_variants(entry.name, is_person=True)
            for record in title_records:
                for variant in author_variants:
                    author_match = index._matching_author(variant, record)
                    if author_match is None:
                        continue
                    kind, database_value = author_match
                    target = exact_author_records if kind == "exact" else partial_author_records
                    target.append((record, database_value))
                    break
            if exact_author_records:
                return [
                    MatchResult(
                        status="Совпадение",
                        method="Список изданий иноагентов: Название и автор",
                        confidence=100.0,
                        excel=synthetic,
                        database=record,
                        note=f"Роль: {entry.role}" if entry.role else "Точное название и автор/псевдоним",
                        source_type=SOURCE_FOREIGN_AGENTS,
                        matched_value=entry.name,
                        foreign_agent=entry,
                        database_matched_value=database_value,
                    )
                    for record, database_value in exact_author_records
                ]
            if partial_author_records:
                return [
                    MatchResult(
                        status="Возможное совпадение",
                        method="Список изданий иноагентов: Название и сокращённый автор",
                        confidence=90.0,
                        excel=synthetic,
                        database=record,
                        note="Точное название, но автор в базе указан неполно — проверить вручную",
                        source_type=SOURCE_FOREIGN_AGENTS,
                        matched_value=entry.name,
                        foreign_agent=entry,
                        database_matched_value=database_value,
                    )
                    for record, database_value in partial_author_records
                ]

        # Для редактора, переводчика или издателя имя не обязано совпадать с 700/701.
        # В этом случае подтверждаем только однозначное заглавие и совместимый год.
        candidate_records = title_records
        publication_year = _publication_year_from_text(entry.publication)
        if publication_year:
            same_year = [
                record
                for record in candidate_records
                if any(year == publication_year for _publisher, year in index.publications[record.record_number])
            ]
            if same_year:
                candidate_records = same_year
        if len(candidate_records) == 1 and (not author_role or not candidate_records[0].authors):
            return [
                MatchResult(
                    status="Совпадение",
                    method="Список изданий иноагентов: Точное название",
                    confidence=100.0,
                    excel=synthetic,
                    database=candidate_records[0],
                    note=(
                        f"Однозначное заглавие; роль: {entry.role}"
                        if entry.role
                        else "Однозначное точное заглавие из списка изданий"
                    ),
                    source_type=SOURCE_FOREIGN_AGENTS,
                    matched_value=entry.name,
                    foreign_agent=entry,
                )
            ]

    # Нечёткое заглавие подтверждаем только при точном совпадении автора или псевдонима.
    if options.use_fuzzy and author_role:
        converted: dict[int, MatchResult] = {}
        for author_variant in _registry_name_variants(entry.name, is_person=True):
            fuzzy_entry = ExcelEntry(
                entry_id=entry.entry_id,
                source_file=entry.source_file,
                sheet_name=entry.sheet_name,
                row_number=entry.row_number,
                author=author_variant,
                title=entry.title,
                isbn="",
                registration_number=entry.registry_number,
                raw_data=entry.raw_data,
            )
            for result in index._match_fuzzy(fuzzy_entry, normalized_title, options.fuzzy_threshold):
                if result.database is None:
                    continue
                converted_result = MatchResult(
                    status=result.status,
                    method="Список изданий иноагентов: Похожее название и автор",
                    confidence=result.confidence,
                    excel=synthetic,
                    database=result.database,
                    note=result.note,
                    source_type=SOURCE_FOREIGN_AGENTS,
                    matched_value=entry.name,
                    foreign_agent=entry,
                    database_matched_value=result.database_matched_value,
                )
                previous = converted.get(result.database.record_number)
                if previous is None or converted_result.confidence > previous.confidence:
                    converted[result.database.record_number] = converted_result
        if converted:
            return list(converted.values())
    return []


def compare_foreign_agents(
    records: DatabaseIndex | list[DatabaseRecord],
    entries: list[ForeignAgentEntry],
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
    *,
    options: ComparisonOptions | None = None,
) -> list[MatchResult]:
    if not entries:
        return []
    index = records if isinstance(records, DatabaseIndex) else DatabaseIndex(records)
    comparison_options = options or ComparisonOptions(use_fuzzy=True, fuzzy_threshold=92)
    publication_results: list[MatchResult] = []

    # Сначала проверяем книги по отдельности, чтобы сохранить их ISBN и заглавия.
    total = max(len(entries), 1)
    for position, entry in enumerate(entries, start=1):
        if position % 250 == 0:
            _cancelled(cancel_cb)
            if progress_cb:
                progress_cb(72 + int(position / total * 4), f"Сверка изданий иноагентов: {position:,} из {total:,}")
        publication_results.extend(_foreign_publication_results(index, entry, comparison_options))

    # Одного и того же агента книжная выгрузка повторяет для каждой книги.
    # Поиск «все записи этого автора» выполняем один раз на агента.
    unique_entries: dict[tuple[str, str], ForeignAgentEntry] = {}
    for entry in entries:
        unique_entries.setdefault(_foreign_agent_identity_key(entry), entry)

    person_identity_counts: dict[tuple[str, str], int] = defaultdict(int)
    for registry_entry in unique_entries.values():
        identity = _author_identity(registry_entry.name)
        if identity and identity[1]:
            person_identity_counts[(identity[0], identity[1][0])] += 1

    # Точное заглавие + сокращённый автор раньше всегда отправлялись оператору.
    # Если фамилия и первый инициал однозначно указывают на одного человека во
    # всём загруженном реестре, применяем ту же проверку однозначности, которая
    # уже используется ниже для поиска по ФИО.
    for result in publication_results:
        if (
            result.status == "Возможное совпадение"
            and result.method == "Список изданий иноагентов: Название и сокращённый автор"
            and result.foreign_agent is not None
        ):
            identity = _author_identity(result.foreign_agent.name)
            if identity and identity[1] and person_identity_counts[(identity[0], identity[1][0])] == 1:
                result.status = "Совпадение"
                result.confidence = 100.0
                result.note = "Точное название; сокращённое имя однозначно в реестре"

    publication_pairs = {
        (_foreign_agent_identity_key(result.foreign_agent), result.database.record_number)
        for result in publication_results
        if result.foreign_agent is not None and result.database is not None and result.status == "Совпадение"
    }

    results: list[MatchResult] = list(publication_results)
    unique_total = max(len(unique_entries), 1)

    for position, entry in enumerate(unique_entries.values(), start=1):
        if position % 50 == 0:
            _cancelled(cancel_cb)
            if progress_cb:
                progress_cb(
                    76 + int(position / unique_total * 4),
                    f"Сверка авторов-иноагентов: {position:,} из {unique_total:,}",
                )
        for value, kind, is_person in _foreign_agent_search_terms(entry):
            matches = index.match_author_value(value) if is_person else index.match_name_value(value)
            for record, database_field, confidence, database_matched_value in matches:
                # Участники-организации не сравниваются с названиями произведений.
                if kind == "Участник" and database_field == "Название":
                    continue
                # Если эта же книга уже надёжно найдена по ISBN/заглавию для
                # того же агента, не создаём вторую одинаковую строку отчёта.
                if (_foreign_agent_identity_key(entry), record.record_number) in publication_pairs:
                    continue
                synthetic_entry = ExcelEntry(
                    entry_id=entry.entry_id,
                    source_file=entry.source_file,
                    sheet_name=entry.sheet_name,
                    row_number=entry.row_number,
                    author=entry.name,
                    title=value,
                    registration_number=entry.registry_number,
                    raw_data=entry.raw_data,
                )
                identity = _author_identity(value) if is_person else None
                unique_abbreviated_person = bool(
                    confidence < 100.0
                    and kind == "ФИО/наименование"
                    and identity
                    and identity[1]
                    and person_identity_counts[(identity[0], identity[1][0])] == 1
                )
                effective_confidence = 100.0 if unique_abbreviated_person else confidence
                results.append(
                    MatchResult(
                        status="Совпадение" if effective_confidence >= 100.0 else "Возможное совпадение",
                        method=f"Реестр иностранных агентов: {database_field}",
                        confidence=effective_confidence,
                        excel=synthetic_entry,
                        database=record,
                        note=(
                            f"{kind}; сокращённое имя однозначно в реестре"
                            if unique_abbreviated_person
                            else kind
                            if confidence >= 100.0
                            else f"{kind}; неполные данные автора"
                        ),
                        source_type=SOURCE_FOREIGN_AGENTS,
                        matched_value=value,
                        foreign_agent=entry,
                        database_matched_value=database_matched_value,
                    )
                )
    return results


@dataclass(frozen=True, slots=True)
class SubstanceComparison:
    results: list[MatchResult]
    matched_entry_ids: set[int]
    exact_isbn_ids: set[int]
    exact_title_ids: set[int]
    probable_ids: set[int]

    def __iter__(self):
        """Сохраняет распаковку прежнего результата для внешних интеграций."""
        yield self.results
        yield self.matched_entry_ids
        yield self.exact_isbn_ids
        yield self.exact_title_ids
        yield self.probable_ids


def compare_substance_entries(
    index: DatabaseIndex,
    entries: list[ExcelEntry],
    options: ComparisonOptions,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> SubstanceComparison:
    results: list[MatchResult] = []
    matched_entry_ids: set[int] = set()
    exact_isbn_ids: set[int] = set()
    exact_title_ids: set[int] = set()
    probable_ids: set[int] = set()
    total = max(len(entries), 1)

    for position, entry in enumerate(entries, start=1):
        if position % 100 == 0:
            _cancelled(cancel_cb)
            if progress_cb:
                progress_cb(53 + int(position / total * 18), f"Сравнение по веществам: {position:,} из {total:,}")

        entry_results = index.match_with_options(entry, options)
        results.extend(entry_results)
        if any(item.status == "Совпадение" for item in entry_results):
            matched_entry_ids.add(entry.entry_id)
        if any(item.status == "Совпадение" and item.method.startswith("ISBN") for item in entry_results):
            exact_isbn_ids.add(entry.entry_id)
        if any(item.status == "Совпадение" and not item.method.startswith("ISBN") for item in entry_results):
            exact_title_ids.add(entry.entry_id)
        if any(item.status == "Возможное совпадение" for item in entry_results):
            probable_ids.add(entry.entry_id)

    return SubstanceComparison(
        results=results,
        matched_entry_ids=matched_entry_ids,
        exact_isbn_ids=exact_isbn_ids,
        exact_title_ids=exact_title_ids,
        probable_ids=probable_ids,
    )


class DatabaseIndex:
    def __init__(self, records: list[DatabaseRecord]) -> None:
        self.records = records
        self.by_isbn: dict[str, list[DatabaseRecord]] = defaultdict(list)
        self.by_title: dict[str, list[DatabaseRecord]] = defaultdict(list)
        self.by_author_surname: dict[str, list[DatabaseRecord]] = defaultdict(list)
        self.by_organization: dict[str, list[tuple[DatabaseRecord, str]]] = defaultdict(list)
        self.by_plain_title: dict[str, list[tuple[DatabaseRecord, str]]] = defaultdict(list)
        self.publications: dict[int, list[tuple[str, str]]] = {}

        for record in records:
            for surname in {surname for author in record.authors for surname in author_surnames(author)}:
                self.by_author_surname[surname].append(record)
            for organization in record.organizations:
                normalized = normalize_author(organization)
                if normalized:
                    self.by_organization[normalized].append((record, organization))
            self.publications[record.record_number] = [
                (
                    normalize_publisher(_extract_subfield(item, "C")),
                    normalize_publication_year(_extract_subfield(item, "D")),
                )
                for item in record.publication
            ]
            for isbn in record.isbns:
                for normalized in isbn_match_keys(isbn):
                    self.by_isbn[normalized].append(record)
            for title in record.titles:
                plain_title = normalize_author(title)
                if plain_title:
                    self.by_plain_title[plain_title].append((record, title))
                normalized = normalize_title(title)
                if normalized:
                    self.by_title[normalized].append(record)

        self.unique_titles = list(self.by_title.keys())

    @staticmethod
    def _author_match_kind(entry_author: str, record: DatabaseRecord) -> str | None:
        match = DatabaseIndex._matching_author(entry_author, record)
        return match[0] if match is not None else None

    @staticmethod
    def _matched_author_is_primary(record: DatabaseRecord, matched_author: str) -> bool:
        if not record.primary_authors:
            return True
        matched_variants = _author_order_variants(matched_author)
        return any(matched_variants & _author_order_variants(author) for author in record.primary_authors)

    @staticmethod
    def _matching_author(entry_author: str, record: DatabaseRecord) -> tuple[str, str] | None:
        comparison_author = primary_author_contributor(entry_author)
        excel_identity = _author_identity(comparison_author)
        if not excel_identity or not record.authors:
            return None
        excel_surname, excel_initials = excel_identity
        entry_normalized = normalize_author(comparison_author)
        entry_order_variants = _author_order_variants(comparison_author)

        for author in record.authors:
            record_identity = _author_identity(author)
            if not record_identity:
                continue
            record_surname, record_initials = record_identity
            if entry_order_variants & _author_order_variants(author):
                return "exact", author
            if record_surname != excel_surname:
                continue
            if normalize_author(author) == entry_normalized:
                return "exact", author
            if excel_initials and record_initials:
                compare_count = min(len(excel_initials), len(record_initials))
                if excel_initials[:compare_count] != record_initials[:compare_count]:
                    continue
                if len(excel_initials) >= 2 and len(record_initials) >= 2:
                    return "exact", author
            return "partial", author
        return None

    def match_author_value(self, value: str) -> list[tuple[DatabaseRecord, str, float, str]]:
        """Сопоставляет автора по тем же правилам, что используются для строк веществ."""
        surnames = author_surnames(value)
        if not surnames:
            return []
        matches: list[tuple[DatabaseRecord, str, float, str]] = []
        candidates: list[DatabaseRecord] = []
        seen: set[int] = set()
        for surname in surnames:
            for record in self.by_author_surname.get(surname, []):
                if record.record_number not in seen:
                    seen.add(record.record_number)
                    candidates.append(record)
        for record in candidates:
            match = self._matching_author(value, record)
            if match is None:
                continue
            kind, database_value = match
            matches.append((record, "Автор", 100.0 if kind == "exact" else 90.0, database_value))
        return matches

    def match_name_value(self, value: str) -> list[tuple[DatabaseRecord, str, float, str]]:
        """Ищет наименование в проиндексированных организациях и названиях."""
        normalized = normalize_author(value)
        if not normalized:
            return []
        matches = [
            (record, "Организация", 100.0, database_value)
            for record, database_value in self.by_organization.get(normalized, [])
        ]
        if len(normalized.split()) >= 2:
            matches.extend(
                (record, "Название", 100.0, database_value)
                for record, database_value in self.by_plain_title.get(normalized, [])
            )
        return matches

    @staticmethod
    def _author_matches(entry_author: str, record: DatabaseRecord) -> bool:
        return DatabaseIndex._author_match_kind(entry_author, record) == "exact"

    @staticmethod
    def _author_similarity(entry_author: str, record: DatabaseRecord) -> float:
        if not entry_author or not record.authors:
            return 100.0
        entry_variants = _author_order_variants(primary_author_contributor(entry_author))
        return max(
            fuzz.token_set_ratio(entry_variant, record_variant)
            for entry_variant in entry_variants
            for author in record.authors
            for record_variant in _author_order_variants(author)
        )

    def _match_isbn(self, entry: ExcelEntry, normalized_isbns: list[str]) -> list[MatchResult]:
        records: list[DatabaseRecord] = []
        seen: set[int] = set()
        for isbn in normalized_isbns:
            for record in self.by_isbn.get(isbn, []):
                if record.record_number not in seen:
                    seen.add(record.record_number)
                    records.append(record)
        results: list[MatchResult] = []
        entry_title = normalize_title(entry.title)
        for record in records:
            title_conflict = bool(
                entry_title and record.titles and entry_title not in {normalize_title(title) for title in record.titles}
            )
            author_conflict = bool(
                entry.author and record.authors and self._matching_author(entry.author, record) is None
            )
            conflicting_metadata = title_conflict and author_conflict
            results.append(
                MatchResult(
                    status="Возможное совпадение" if conflicting_metadata else "Совпадение",
                    method="ISBN с противоречием в названии и авторе" if conflicting_metadata else "ISBN",
                    confidence=95.0 if conflicting_metadata else 100.0,
                    excel=entry,
                    database=record,
                    note=(
                        "ISBN совпал, но название и автор отличаются — требуется ручная проверка"
                        if conflicting_metadata
                        else ""
                    ),
                    source_type=SOURCE_SUBSTANCES,
                    matched_value=entry.isbn,
                )
            )
        return results

    def _match_fuzzy(
        self,
        entry: ExcelEntry,
        normalized_title: str,
        threshold: int,
    ) -> list[MatchResult]:
        if not self.unique_titles or not entry.author:
            return []
        candidate_records: list[DatabaseRecord] = []
        seen_records: set[int] = set()
        for surname in author_surnames(primary_author_contributor(entry.author)):
            for record in self.by_author_surname.get(surname, []):
                if record.record_number not in seen_records:
                    seen_records.add(record.record_number)
                    candidate_records.append(record)
        candidate_title_keys = sorted(
            {
                normalize_title(title)
                for record in candidate_records
                for title in record.titles
                if normalize_title(title)
            }
        )
        if not candidate_title_keys:
            return []
        candidate_titles = process.extract(
            normalized_title,
            candidate_title_keys,
            scorer=fuzz.token_set_ratio,
            score_cutoff=threshold,
            limit=5,
        )
        best: list[tuple[float, DatabaseRecord, float, float, str]] = []
        for title_key, title_score, _ in candidate_titles:
            for record in self.by_title[title_key]:
                author_score = self._author_similarity(entry.author, record)
                if entry.author and record.authors and author_score < 60:
                    continue
                combined = title_score * 0.85 + author_score * 0.15
                if combined >= threshold:
                    best.append((combined, record, float(title_score), float(author_score), title_key))
        if not best:
            return []
        # Одна запись может попасть в выдачу несколько раз из-за вариантов
        # заглавия. Для проверки однозначности считаем записи, а не заглавия.
        best_by_record: dict[int, tuple[float, DatabaseRecord, float, float, str]] = {}
        for candidate in best:
            record_number = candidate[1].record_number
            previous = best_by_record.get(record_number)
            if previous is None or candidate[0] > previous[0]:
                best_by_record[record_number] = candidate
        best = sorted(best_by_record.values(), key=lambda item: item[0], reverse=True)
        best_score = best[0][0]
        selected = [item for item in best if item[0] >= best_score - 1.0][:3]
        author_match_by_record: dict[int, tuple[str, str] | None] = {}
        primary_author_by_record: dict[int, bool] = {}
        exact_primary_by_record: dict[int, bool] = {}
        for _score, record, _title_score, _author_score, _title_key in selected:
            author_match = self._matching_author(entry.author, record)
            author_match_by_record[record.record_number] = author_match
            primary = bool(author_match is not None and self._matched_author_is_primary(record, author_match[1]))
            primary_author_by_record[record.record_number] = primary
            exact_primary_by_record[record.record_number] = bool(
                primary and author_match is not None and author_match[0] == "exact"
            )
        automatic_group = bool(
            selected and len({item[4] for item in selected}) == 1 and all(exact_primary_by_record.values())
        )
        results: list[MatchResult] = []
        for score, record, title_score, author_score, title_key in selected:
            author_match = author_match_by_record[record.record_number]
            exact_primary = exact_primary_by_record[record.record_number]
            title_evidence = _fuzzy_title_has_specific_evidence(entry.title, normalized_title, title_key)
            # Для составных заглавий token_set_ratio закономерно находит несколько
            # отдельных произведений одного автора. Раньше вся такая группа уходила
            # на ручную проверку только из-за разных ключей заглавия. Точный основной
            # автор является достаточным ограничителем, поэтому такие попадания можно
            # подтверждать автоматически.
            exact_author_automatic = exact_primary and (automatic_group or (title_score >= 98.0 and title_evidence))
            # В ИРБИС имя часто хранится как «Фамилия И. О. Полное Имя», а в списке
            # как «Фамилия, Имя». При практически полном совпадении и названия, и ФИО
            # это не должно создавать сотни ручных подтверждений. Слабые сокращения
            # (например только одна буква) сюда не проходят.
            strong_partial_automatic = bool(
                author_match is not None
                and author_match[0] == "partial"
                and primary_author_by_record[record.record_number]
                and title_score >= 98.0
                and author_score >= 98.0
                and title_evidence
            )
            automatic = exact_author_automatic or strong_partial_automatic
            results.append(
                MatchResult(
                    status="Совпадение" if automatic else "Возможное совпадение",
                    method=(
                        "Похожее название и точный автор"
                        if exact_author_automatic
                        else "Похожее название и однозначный сокращённый автор"
                        if strong_partial_automatic
                        else "Приблизительно по названию и автору"
                    ),
                    # 100% здесь означает надёжность принятого решения, а сами
                    # проценты сходства остаются видимыми в примечании.
                    confidence=100.0 if automatic else round(score, 1),
                    excel=entry,
                    database=record,
                    note=f"Название: {title_score:.0f}%, автор: {author_score:.0f}%",
                    source_type=SOURCE_SUBSTANCES,
                    matched_value=entry.title or entry.author,
                )
            )
        return results

    @staticmethod
    def _publication_matches(entry: ExcelEntry, publications: list[tuple[str, str]]) -> bool:
        entry_publishers = publisher_variants(entry.publisher)
        entry_year = normalize_publication_year(entry.year)
        if not entry_publishers or not entry_year:
            return False
        return any(
            entry_year == record_year and bool(entry_publishers & publisher_variants(record_publisher))
            for record_publisher, record_year in publications
        )

    @staticmethod
    def _publication_fields_match(
        entry: ExcelEntry,
        publications: list[tuple[str, str]],
        fields: tuple[str, ...],
    ) -> bool:
        """Проверяет издательство и год в одном повторении поля публикации."""
        needs_publisher = "publisher" in fields
        needs_year = "year" in fields
        if not needs_publisher and not needs_year:
            return True

        entry_publishers = publisher_variants(entry.publisher)
        entry_year = normalize_publication_year(entry.year)
        if needs_publisher and not entry_publishers or needs_year and not entry_year:
            return False

        return any(
            (not needs_publisher or bool(entry_publishers & publisher_variants(record_publisher)))
            and (not needs_year or entry_year == record_year)
            for record_publisher, record_year in publications
        )

    def _record_matches_rule(
        self,
        entry: ExcelEntry,
        record: DatabaseRecord,
        fields: tuple[str, ...],
        normalized_isbns: set[str],
        normalized_title: str,
    ) -> bool:
        if "isbn" in fields and not any(
            normalized_isbns.intersection(isbn_match_keys(value)) for value in record.isbns
        ):
            return False
        if "title" in fields and normalized_title not in {normalize_title(value) for value in record.titles}:
            return False

        author_match = self._author_match_kind(entry.author, record) if entry.author and record.authors else None
        if "author" in fields and author_match != "exact":
            return False
        # Явно указанный несовпадающий автор исключает ложное подтверждение
        # по названию или данным издания, даже если правило не требует автора.
        if entry.author and record.authors and author_match is None:
            return False

        return self._publication_fields_match(entry, self.publications[record.record_number], fields)

    def _rule_candidates(
        self,
        entry: ExcelEntry,
        fields: tuple[str, ...],
        normalized_isbns: list[str],
        normalized_title: str,
    ) -> list[DatabaseRecord]:
        """Выбирает самый узкий готовый индекс для правила сопоставления."""
        pools: list[list[DatabaseRecord]] = []
        if "isbn" in fields:
            pools.append([record for value in normalized_isbns for record in self.by_isbn.get(value, [])])
        if "title" in fields:
            pools.append(list(self.by_title.get(normalized_title, [])))
        if "author" in fields:
            author_records = [
                record
                for surname in author_surnames(entry.author)
                for record in self.by_author_surname.get(surname, [])
            ]
            pools.append(author_records)
        if not pools:
            return []

        candidates: list[DatabaseRecord] = []
        seen: set[int] = set()
        for record in min(pools, key=len):
            if record.record_number not in seen:
                seen.add(record.record_number)
                candidates.append(record)
        return candidates

    def _match_extra_rules(
        self,
        entry: ExcelEntry,
        match_rules: dict[str, bool],
        normalized_isbns: list[str],
        normalized_title: str,
    ) -> list[MatchResult]:
        enabled_rules = [
            (key, *EXTRA_MATCH_RULES[key])
            for key, enabled in match_rules.items()
            if enabled and key in EXTRA_MATCH_RULES
        ]
        if not enabled_rules:
            return []

        entry_values = {
            "isbn": bool(normalized_isbns),
            "title": bool(normalized_title),
            "author": bool(entry.author.strip()),
            "publisher": bool(publisher_variants(entry.publisher)),
            "year": bool(normalize_publication_year(entry.year)),
        }
        normalized_isbn_set = set(normalized_isbns)
        best_by_record: dict[int, tuple[tuple[int, int], MatchResult]] = {}

        for _key, label, fields in enabled_rules:
            if not all(entry_values[field] for field in fields):
                continue
            for record in self._rule_candidates(entry, fields, normalized_isbns, normalized_title):
                if not self._record_matches_rule(
                    entry,
                    record,
                    fields,
                    normalized_isbn_set,
                    normalized_title,
                ):
                    continue
                author_match = self._matching_author(entry.author, record) if "author" in fields else None
                secondary_author = bool(
                    author_match is not None and not self._matched_author_is_primary(record, author_match[1])
                )
                needs_review = match_rule_needs_review(fields) or secondary_author
                confidence = 90.0 if needs_review else 100.0
                result = MatchResult(
                    status="Возможное совпадение" if needs_review else "Совпадение",
                    method=label,
                    confidence=confidence,
                    excel=entry,
                    database=record,
                    note=(
                        "Совпадение найдено по дополнительному автору — требуется ручная проверка"
                        if secondary_author
                        else "Требуется ручная проверка сочетания полей"
                        if needs_review
                        else ""
                    ),
                    source_type=SOURCE_SUBSTANCES,
                    matched_value=entry.title or entry.author or entry.isbn,
                )
                priority = (int(confidence), len(fields))
                previous = best_by_record.get(record.record_number)
                if previous is None or priority > previous[0]:
                    best_by_record[record.record_number] = (priority, result)

        return [item[1] for item in best_by_record.values()]

    def _match_title_rules(self, entry: ExcelEntry, normalized_title: str) -> list[MatchResult]:
        results: list[MatchResult] = []
        for record in self.by_title.get(normalized_title, []):
            record_has_authors = bool(record.authors)
            author_match = self._matching_author(entry.author, record) if entry.author else None
            author_match_kind = author_match[0] if author_match is not None else None
            if entry.author and record_has_authors:
                if author_match_kind is None:
                    continue
                if author_match_kind == "exact":
                    secondary_author = not self._matched_author_is_primary(record, author_match[1])
                    status = "Возможное совпадение" if secondary_author else "Совпадение"
                    method = "Название и дополнительный автор" if secondary_author else "Название и автор"
                    confidence = 90.0 if secondary_author else 100.0
                    note = "Автор найден в дополнительном поле — проверить роль вручную" if secondary_author else ""
                else:
                    status = "Совпадение"
                    method = "Название и автор (сокращённое имя)"
                    confidence = 100.0
                    note = "Название совпало; фамилия и первый инициал автора совпали"
            elif self._publication_matches(entry, self.publications[record.record_number]):
                status = "Совпадение"
                method = "Название + издательство + год"
                confidence = 100.0
                note = ""
            elif not entry.author:
                status = "Возможное совпадение"
                method = "Только название"
                confidence = 75.0
                note = "Точно совпало только название; в перечне не указан автор — проверить вручную"
            else:
                status = "Возможное совпадение"
                method = "Название без проверки автора"
                confidence = 80.0
                note = "Название совпало, но в записи базы нет автора — проверить вручную"
            results.append(
                MatchResult(
                    status=status,
                    method=method,
                    confidence=confidence,
                    excel=entry,
                    database=record,
                    note=note,
                    source_type=SOURCE_SUBSTANCES,
                    matched_value=entry.title,
                )
            )
        return results

    def match(
        self,
        entry: ExcelEntry,
        use_isbn_matching: bool,
        use_title_fallback: bool,
        use_fuzzy: bool,
        fuzzy_threshold: int,
        match_rules: dict[str, bool] | None = None,
    ) -> list[MatchResult]:
        """Совместимый вход для прежних вызовов с отдельными параметрами."""
        return self.match_with_options(
            entry,
            ComparisonOptions(
                use_isbn_matching=use_isbn_matching,
                use_title_fallback=use_title_fallback,
                use_fuzzy=use_fuzzy,
                fuzzy_threshold=fuzzy_threshold,
                match_rules=match_rules or {},
            ),
        )

    def match_with_options(self, entry: ExcelEntry, options: ComparisonOptions) -> list[MatchResult]:
        """Выполняет стратегии сопоставления с единым набором настроек."""
        use_isbn_matching = options.use_isbn_matching
        use_title_fallback = options.use_title_fallback
        use_fuzzy = options.use_fuzzy
        fuzzy_threshold = options.fuzzy_threshold
        normalized_isbns = sorted(isbn_match_keys(entry.isbn))
        if use_isbn_matching:
            isbn_results = self._match_isbn(entry, normalized_isbns)
            if isbn_results:
                return isbn_results

        normalized_title = normalize_title(entry.title)
        extra_results = self._match_extra_rules(
            entry,
            options.match_rules,
            normalized_isbns,
            normalized_title,
        )

        exact_results: list[MatchResult] = []
        if use_title_fallback and normalized_title:
            exact_results = self._match_title_rules(entry, normalized_title)
        combined_results = [*extra_results, *exact_results]
        if combined_results:
            best_by_record: dict[int, MatchResult] = {}
            for result in combined_results:
                if result.database is None:
                    continue
                previous = best_by_record.get(result.database.record_number)
                priority = (result.confidence, result.method.count("+") + 1)
                previous_priority = (
                    (previous.confidence, previous.method.count("+") + 1) if previous is not None else (-1.0, 0)
                )
                if priority > previous_priority:
                    best_by_record[result.database.record_number] = result
            return list(best_by_record.values())

        if not use_title_fallback:
            note = (
                "Поиск по ISBN и названию отключён"
                if not use_isbn_matching
                else (
                    "Корректный ISBN не найден"
                    if entry.isbn and not normalized_isbns
                    else "Совпадение по ISBN отсутствует"
                )
            )
            return [MatchResult("Не найдено", "—", 0.0, entry, note=note)]

        if not normalized_title:
            note = (
                "ISBN имеет неверный формат или контрольную цифру; названия для резервного поиска нет"
                if entry.isbn and not normalized_isbns
                else "Нет названия для поиска"
            )
            return [MatchResult("Не найдено", "—", 0.0, entry, note=note)]

        if use_title_fallback and use_fuzzy:
            fuzzy_results = self._match_fuzzy(entry, normalized_title, fuzzy_threshold)
            if fuzzy_results:
                return fuzzy_results

        note = "Нет совпадений по ISBN, названию и фамилии либо по данным издания"
        return [MatchResult("Не найдено", "—", 0.0, entry, note=note)]


def compare_database_records(
    records: list[DatabaseRecord],
    excel_paths: list[str | Path],
    *,
    database_label: str = "ИРБИС",
    foreign_agents_path: str | Path | None = None,
    use_isbn_matching: bool = True,
    use_title_fallback: bool = True,
    match_rules: dict[str, bool] | None = None,
    use_fuzzy: bool = False,
    fuzzy_threshold: int = 90,
    options: ComparisonOptions | None = None,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[MatchResult], ComparisonSummary]:
    """Сверяет уже загруженные записи, в том числе полученные напрямую с ИРБИС."""
    comparison_options = options or ComparisonOptions(
        use_isbn_matching=use_isbn_matching,
        use_title_fallback=use_title_fallback,
        use_fuzzy=use_fuzzy,
        fuzzy_threshold=fuzzy_threshold,
        match_rules=match_rules or {},
    )
    _cancelled(cancel_cb)
    index = DatabaseIndex(records)
    entries, warnings = _matcher.read_excel_entries(excel_paths, progress_cb, cancel_cb)
    foreign_entries, foreign_warnings = _matcher.read_foreign_agent_entries(
        foreign_agents_path,
        progress_cb,
        cancel_cb,
    )
    warnings.extend(foreign_warnings)

    if entries and foreign_entries and progress_cb:
        progress_cb(53, "Параллельная сверка по веществам и иностранным агентам")

    if entries and foreign_entries:
        with ThreadPoolExecutor(max_workers=2) as executor:
            substance_future = executor.submit(
                compare_substance_entries,
                index,
                entries,
                comparison_options,
                progress_cb,
                cancel_cb,
            )
            foreign_future = executor.submit(
                compare_foreign_agents,
                index,
                foreign_entries,
                progress_cb,
                cancel_cb,
                options=comparison_options,
            )
            substance = substance_future.result()
            foreign_results = foreign_future.result()
    else:
        substance = compare_substance_entries(
            index,
            entries,
            comparison_options,
            progress_cb,
            cancel_cb,
        )
        foreign_results = compare_foreign_agents(
            index,
            foreign_entries,
            progress_cb,
            cancel_cb,
            options=comparison_options,
        )

    results: list[MatchResult] = []
    results.extend(substance.results)
    results.extend(foreign_results)
    matched_foreign_ids = {
        result.foreign_agent.entry_id
        for result in foreign_results
        if result.foreign_agent is not None and result.status == "Совпадение"
    }
    substance_records = {
        result.database.record_number
        for result in results
        if result.status == "Совпадение" and result.database is not None and result.source_type == SOURCE_SUBSTANCES
    }
    foreign_records = {
        result.database.record_number
        for result in foreign_results
        if result.status == "Совпадение" and result.database is not None
    }

    review_records = count_review_record_groups(results)
    summary = ComparisonSummary(
        database_file=database_label,
        excel_files=[str(Path(path)) for path in excel_paths],
        database_records=len(records),
        database_records_with_isbn=sum(1 for record in records if any(extract_isbns(value) for value in record.isbns)),
        excel_rows=len(entries),
        matched_excel_rows=len(substance.matched_entry_ids),
        unmatched_excel_rows=len(entries) - len(substance.matched_entry_ids),
        result_rows=len(results),
        exact_isbn_rows=len(substance.exact_isbn_ids),
        exact_title_rows=len(substance.exact_title_ids),
        probable_rows=len(substance.probable_ids),
        foreign_agents_file=str(Path(foreign_agents_path)) if foreign_agents_path else "",
        foreign_agent_rows=len(foreign_entries),
        matched_foreign_agent_rows=len(matched_foreign_ids),
        foreign_agent_result_rows=len(foreign_results),
        substance_matched_records=len(substance_records),
        foreign_agent_matched_records=len(foreign_records),
        review_rows=sum(1 for result in results if result.status == "Возможное совпадение"),
        review_records=review_records,
        warnings=warnings,
    )

    if progress_cb:
        progress_cb(82, "Сравнение завершено, подготовка отчёта")
    return results, summary


def compare_files(
    database_path: str | Path | list[str | Path],
    excel_paths: list[str | Path],
    *,
    foreign_agents_path: str | Path | None = None,
    use_isbn_matching: bool = True,
    use_title_fallback: bool = True,
    match_rules: dict[str, bool] | None = None,
    use_fuzzy: bool = False,
    fuzzy_threshold: int = 90,
    options: ComparisonOptions | None = None,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[MatchResult], ComparisonSummary]:
    comparison_options = options or ComparisonOptions(
        use_isbn_matching=use_isbn_matching,
        use_title_fallback=use_title_fallback,
        use_fuzzy=use_fuzzy,
        fuzzy_threshold=fuzzy_threshold,
        match_rules=match_rules or {},
    )
    database_paths = database_path if isinstance(database_path, list) else [database_path]
    records: list[DatabaseRecord] = []
    for source in database_paths:
        source_records = _matcher.parse_database(source, progress_cb, cancel_cb)
        for record in source_records:
            record.record_number = len(records) + 1
            records.append(record)
    return compare_database_records(
        records,
        excel_paths,
        database_label="; ".join(str(Path(path)) for path in database_paths),
        foreign_agents_path=foreign_agents_path,
        options=comparison_options,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )
