from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from irbis_control.core import matcher as _matcher
from irbis_control.core.matcher import (
    SOURCE_FOREIGN_AGENTS,
    SOURCE_SUBSTANCES,
    CancelCallback,
    ProgressCallback,
    _author_identity,
    _cancelled,
    _extract_subfield,
    author_surname,
    extract_isbns,
    fuzz,
    normalize_author,
    normalize_publication_year,
    normalize_publisher,
    normalize_title,
    process,
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
            for clean in _name_order_variants(part):
                if clean and len(clean) >= 4:
                    variants.append(clean)
    for pseudonym in pseudonym_values:
        for part in re.split(r"\s*,\s*", pseudonym):
            for clean in _name_order_variants(part):
                if clean and len(clean) >= 4:
                    variants.append(clean)
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


def _person_identity(value: str) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    tokens = re.findall(r"[A-Za-zА-Яа-я]+", _registry_plain_name(value).lower())
    if len(tokens) < 2:
        return None
    surname = tokens[0]
    full_names = tuple(token for token in tokens[1:] if len(token) > 1)
    if full_names:
        initials = tuple(token[0] for token in full_names[:2])
    else:
        initials = tuple(token[0] for token in tokens[1:3] if token)
    if not surname or not initials:
        return None
    return surname, full_names, initials


def _person_identity_match_kind(
    database_identity: tuple[str, tuple[str, ...], tuple[str, ...]],
    registry_identity: tuple[str, tuple[str, ...], tuple[str, ...]],
) -> str | None:
    """Возвращает тип совпадения ФИО или None, если личности не совпадают.

    Точным считается только полное совпадение фамилии, имени и отчества.
    Сокращённое имя или инициалы дают лишь возможное совпадение для ручной
    проверки и не должны приводить к автоматической установке метки.
    """
    db_surname, db_full_names, db_initials = database_identity
    reg_surname, reg_full_names, reg_initials = registry_identity
    if db_surname != reg_surname:
        return None

    if db_full_names:
        # Полное имя должно совпасть обязательно: это исключает совпадения
        # вроде «Петров Александр» и «Петров Алексей».
        if not reg_full_names or db_full_names[0] != reg_full_names[0]:
            return None

        # Автоматически подтверждаем личность только по полному ФИО.
        if len(db_full_names) >= 2 and len(reg_full_names) >= 2:
            return "full_name" if db_full_names[:2] == reg_full_names[:2] else None

        # Фамилия и имя совпали, но хотя бы с одной стороны нет отчества.
        return "partial_full_name"

    if len(db_initials) >= 2:
        return "two_initials" if db_initials[:2] == reg_initials[:2] else None

    if len(db_initials) == 1 and reg_initials:
        return "single_initial" if db_initials[0] == reg_initials[0] else None

    return None


# Связывает вариант имени с записью реестра и полем, в котором его нужно искать.
@dataclass(frozen=True)
class ForeignAgentSearchTerm:
    entry_id: int
    value: str
    normalized: str
    kind: str
    is_person: bool
    person_identity: tuple[str, tuple[str, ...], tuple[str, ...]] | None


# Подготавливает варианты имён один раз для поиска по всему реестру.
class ForeignAgentIndex:
    def __init__(self, entries: list[ForeignAgentEntry]) -> None:
        self.entries = {entry.entry_id: entry for entry in entries}
        self.by_exact: dict[str, list[ForeignAgentSearchTerm]] = defaultdict(list)
        self.by_person_surname: dict[str, list[ForeignAgentSearchTerm]] = defaultdict(list)

        for entry in entries:
            terms: list[tuple[str, str, bool]] = []
            main_is_person = "физичес" in normalize_author(entry.agent_type)
            terms.extend(
                (variant, "ФИО/наименование", main_is_person)
                for variant in _registry_name_variants(entry.name, is_person=main_is_person)
            )
            for participant in entry.participants:
                participant_is_person = _participant_is_person(participant)
                terms.extend(
                    (variant, "Участник", participant_is_person)
                    for variant in _registry_name_variants(participant, is_person=participant_is_person)
                )

            seen: set[tuple[str, str, bool]] = set()
            for value, kind, is_person in terms:
                normalized = normalize_author(value)
                if not normalized or (normalized, kind, is_person) in seen:
                    continue
                seen.add((normalized, kind, is_person))
                identity = _person_identity(value) if is_person else None
                term = ForeignAgentSearchTerm(
                    entry_id=entry.entry_id,
                    value=value,
                    normalized=normalized,
                    kind=kind,
                    is_person=is_person,
                    person_identity=identity,
                )
                self.by_exact[normalized].append(term)
                if identity is not None:
                    self.by_person_surname[identity[0]].append(term)

    def match_record(
        self, record: DatabaseRecord
    ) -> list[tuple[ForeignAgentEntry, ForeignAgentSearchTerm, str, str, float, str]]:
        matched: dict[
            tuple[int, str, str],
            tuple[ForeignAgentEntry, ForeignAgentSearchTerm, str, str, float, str],
        ] = {}

        # Проверяем все персональные поля ответственности: #700, #701 и #702.
        # В parse_database они уже собраны в record.authors.
        for author in record.authors:
            author_normalized = normalize_author(author)
            database_identity = _person_identity(author)
            candidates: list[tuple[ForeignAgentSearchTerm, str]] = []
            for term in self.by_exact.get(author_normalized, []):
                # Даже одинаковая строка вида «Иванов И. И.» не является
                # полным ФИО и поэтому не должна считаться точной автоматически.
                if term.is_person and database_identity is not None and term.person_identity is not None:
                    match_kind = _person_identity_match_kind(database_identity, term.person_identity)
                    if match_kind is not None:
                        candidates.append((term, match_kind))
            if database_identity is not None:
                for term in self.by_person_surname.get(database_identity[0], []):
                    if term.person_identity is None:
                        continue
                    match_kind = _person_identity_match_kind(database_identity, term.person_identity)
                    if match_kind is not None:
                        candidates.append((term, match_kind))

            # Сокращённые библиотечные формы ФИО (#700/#701/#702) можно
            # подтверждать автоматически, если они однозначно указывают ровно на
            # одну запись реестра. Один инициал остаётся только для ручной проверки.
            candidate_entry_ids = {term.entry_id for term, _match_kind in candidates if term.is_person}
            unique_short_identity = len(candidate_entry_ids) == 1

            for term, match_kind in candidates:
                if not term.is_person:
                    continue
                entry = self.entries[term.entry_id]
                note = term.kind
                confidence = 100.0
                if match_kind == "partial_full_name":
                    if unique_short_identity:
                        note = f"{term.kind}; фамилия и имя однозначно совпали с одной записью реестра"
                    else:
                        note = f"{term.kind}; совпали фамилия и имя, но найдено несколько кандидатов — проверить вручную"
                        confidence = 90.0
                elif match_kind == "two_initials":
                    if unique_short_identity:
                        note = f"{term.kind}; фамилия + два инициала однозначно совпали с одной записью реестра"
                    else:
                        note = f"{term.kind}; фамилия + два инициала, но найдено несколько кандидатов — проверить вручную"
                        confidence = 90.0
                elif match_kind == "single_initial":
                    note = f"{term.kind}; фамилия + один инициал — проверить вручную"
                    confidence = 80.0
                key = (entry.entry_id, term.normalized, "Автор")
                previous = matched.get(key)
                candidate = (entry, term, "Автор", note, confidence, author)
                if previous is None or confidence > previous[4]:
                    matched[key] = candidate

        for organization in record.organizations:
            organization_normalized = normalize_author(organization)
            for term in self.by_exact.get(organization_normalized, []):
                if term.is_person:
                    continue
                entry = self.entries[term.entry_id]
                key = (entry.entry_id, term.normalized, "Организация")
                matched[key] = (entry, term, "Организация", term.kind, 100.0, organization)

        for title in record.titles:
            title_normalized = normalize_author(title)
            for term in self.by_exact.get(title_normalized, []):
                # Названия книг сравниваются только с наименованиями организаций/проектов,
                # а не с ФИО физических лиц или участников.
                entry = self.entries[term.entry_id]
                if term.is_person or term.kind == "Участник":
                    continue
                if len(term.normalized.split()) < 2:
                    continue
                key = (entry.entry_id, term.normalized, "Название")
                matched[key] = (entry, term, "Название", term.kind, 100.0, title)

        return list(matched.values())


def compare_foreign_agents(
    records: list[DatabaseRecord],
    entries: list[ForeignAgentEntry],
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> list[MatchResult]:
    if not entries:
        return []
    index = ForeignAgentIndex(entries)
    results: list[MatchResult] = []
    total = max(len(records), 1)

    for position, record in enumerate(records, start=1):
        if position % 250 == 0:
            _cancelled(cancel_cb)
            if progress_cb:
                progress_cb(72 + int(position / total * 8), f"Сверка с иноагентами: {position:,} из {total:,}")
        for entry, term, database_field, match_note, confidence, database_matched_value in index.match_record(record):
            synthetic_entry = ExcelEntry(
                entry_id=entry.entry_id,
                source_file=entry.source_file,
                sheet_name=entry.sheet_name,
                row_number=entry.row_number,
                author=entry.name,
                title=term.value,
                registration_number=entry.registry_number,
                raw_data=entry.raw_data,
            )
            results.append(
                MatchResult(
                    status="Совпадение" if confidence >= 100.0 else "Возможное совпадение",
                    method=f"Реестр иностранных агентов: {database_field}",
                    confidence=confidence,
                    excel=synthetic_entry,
                    database=record,
                    note=match_note,
                    source_type=SOURCE_FOREIGN_AGENTS,
                    matched_value=term.value,
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


# Индексирует поля базы, чтобы не перебирать все записи для каждой строки Excel.
class DatabaseIndex:
    def __init__(self, records: list[DatabaseRecord]) -> None:
        self.records = records
        self.by_isbn: dict[str, list[DatabaseRecord]] = defaultdict(list)
        self.by_title: dict[str, list[DatabaseRecord]] = defaultdict(list)
        self.publications: dict[int, list[tuple[str, str]]] = {}

        for record in records:
            self.publications[record.record_number] = [
                (
                    normalize_publisher(_extract_subfield(item, "C")),
                    normalize_publication_year(_extract_subfield(item, "D")),
                )
                for item in record.publication
            ]
            for isbn in record.isbns:
                for normalized in extract_isbns(isbn):
                    self.by_isbn[normalized].append(record)
            for title in record.titles:
                normalized = normalize_title(title)
                if normalized:
                    self.by_title[normalized].append(record)

        self.unique_titles = list(self.by_title.keys())

    @staticmethod
    def _author_match_kind(entry_author: str, record: DatabaseRecord) -> str | None:
        excel_identity = _author_identity(entry_author)
        if not excel_identity or not record.authors:
            return None
        excel_surname, excel_initials = excel_identity
        entry_normalized = normalize_author(entry_author)

        for author in record.authors:
            record_identity = _author_identity(author)
            if not record_identity:
                continue
            record_surname, record_initials = record_identity
            if record_surname != excel_surname:
                continue
            if normalize_author(author) == entry_normalized:
                return "exact"
            if excel_initials and record_initials:
                compare_count = min(len(excel_initials), len(record_initials))
                if excel_initials[:compare_count] != record_initials[:compare_count]:
                    continue
                if len(excel_initials) >= 2 and len(record_initials) >= 2:
                    return "exact"
            return "partial"
        return None

    @staticmethod
    def _author_matches(entry_author: str, record: DatabaseRecord) -> bool:
        return DatabaseIndex._author_match_kind(entry_author, record) == "exact"

    @staticmethod
    def _author_similarity(entry_author: str, record: DatabaseRecord) -> float:
        if not entry_author or not record.authors:
            return 100.0
        entry_normalized = normalize_author(entry_author)
        if fuzz is None:
            return 100.0 if DatabaseIndex._author_matches(entry_author, record) else 0.0
        return max(fuzz.token_set_ratio(entry_normalized, normalize_author(author)) for author in record.authors)

    def _match_isbn(self, entry: ExcelEntry, normalized_isbns: list[str]) -> list[MatchResult]:
        records: list[DatabaseRecord] = []
        seen: set[int] = set()
        for isbn in normalized_isbns:
            for record in self.by_isbn.get(isbn, []):
                if record.record_number not in seen:
                    seen.add(record.record_number)
                    records.append(record)
        return [
            MatchResult(
                status="Совпадение",
                method="ISBN",
                confidence=100.0,
                excel=entry,
                database=record,
                source_type=SOURCE_SUBSTANCES,
                matched_value=entry.isbn,
            )
            for record in records
        ]

    def _match_fuzzy(
        self,
        entry: ExcelEntry,
        normalized_title: str,
        threshold: int,
    ) -> list[MatchResult]:
        if process is None or fuzz is None or not self.unique_titles:
            return []
        candidate_titles = process.extract(
            normalized_title,
            self.unique_titles,
            scorer=fuzz.token_set_ratio,
            score_cutoff=threshold,
            limit=5,
        )
        best: list[tuple[float, DatabaseRecord, float, float]] = []
        for title_key, title_score, _ in candidate_titles:
            for record in self.by_title[title_key]:
                author_score = self._author_similarity(entry.author, record)
                if entry.author and record.authors and author_score < 60:
                    continue
                combined = title_score * 0.85 + author_score * 0.15
                if combined >= threshold:
                    best.append((combined, record, float(title_score), float(author_score)))
        if not best:
            return []
        best.sort(key=lambda item: item[0], reverse=True)
        best_score = best[0][0]
        selected = [item for item in best if item[0] >= best_score - 1.0][:3]
        return [
            MatchResult(
                status="Возможное совпадение",
                method="Приблизительно по названию и автору",
                confidence=round(score, 1),
                excel=entry,
                database=record,
                note=f"Название: {title_score:.0f}%, автор: {author_score:.0f}%",
                source_type=SOURCE_SUBSTANCES,
                matched_value=entry.title or entry.author,
            )
            for score, record, title_score, author_score in selected
        ]

    @staticmethod
    def _publication_matches(entry: ExcelEntry, publications: list[tuple[str, str]]) -> bool:
        entry_publishers = publisher_variants(entry.publisher)
        entry_year = normalize_publication_year(entry.year)
        if not entry_publishers or not entry_year:
            return False
        return any(
            entry_year == record_year
            and bool(entry_publishers & publisher_variants(record_publisher))
            for record_publisher, record_year in publications
        )

    def _match_title_rules(self, entry: ExcelEntry, normalized_title: str) -> list[MatchResult]:
        entry_surname = author_surname(entry.author)
        entry_authors = [value.strip() for value in re.split(r"[;\n]", entry.author) if value.strip()]
        single_entry_author = len(entry_authors) == 1
        results: list[MatchResult] = []
        for record in self.by_title.get(normalized_title, []):
            record_authors = [author for author in record.authors if author_surname(author)]
            if entry_surname and single_entry_author and len(record_authors) == 1:
                if author_surname(record_authors[0]) != entry_surname:
                    continue
                method = "Название + фамилия автора"
            else:
                if not self._publication_matches(entry, self.publications[record.record_number]):
                    continue
                method = "Название + издательство + год"
            results.append(
                MatchResult(
                    status="Совпадение",
                    method=method,
                    confidence=100.0,
                    excel=entry,
                    database=record,
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
        normalized_isbns = extract_isbns(entry.isbn)
        if use_isbn_matching:
            isbn_results = self._match_isbn(entry, normalized_isbns)
            if isbn_results:
                return isbn_results

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

        normalized_title = normalize_title(entry.title)
        if not normalized_title:
            note = (
                "ISBN имеет неверный формат или контрольную цифру; названия для резервного поиска нет"
                if entry.isbn and not normalized_isbns
                else "Нет названия для поиска"
            )
            return [MatchResult("Не найдено", "—", 0.0, entry, note=note)]

        exact_results = self._match_title_rules(entry, normalized_title)
        if exact_results:
            return exact_results
        if use_title_fallback and use_fuzzy:
            fuzzy_results = self._match_fuzzy(entry, normalized_title, fuzzy_threshold)
            if fuzzy_results:
                return fuzzy_results

        note = "Нет совпадений по ISBN, названию и фамилии либо по данным издания"
        return [MatchResult("Не найдено", "—", 0.0, entry, note=note)]


# Принимает записи базы и пути перечней, возвращает совпадения и сводку. Проверки отмены позволяют остановить долгий запуск.
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
                records,
                foreign_entries,
                progress_cb,
                cancel_cb,
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
        foreign_results = compare_foreign_agents(records, foreign_entries, progress_cb, cancel_cb)

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


# Экспорт отчёта загружается по требованию, сохраняя прежний публичный API.
