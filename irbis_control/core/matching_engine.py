from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from irbis_control.core import matcher as _matcher
from irbis_control.core.matcher import (
    EXTRA_MATCH_RULES,
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
    match_rule_needs_review,
    normalize_author,
    normalize_publication_year,
    normalize_publisher,
    normalize_title,
    process,
    publisher_variants,
)
from irbis_control.core.models import ComparisonSummary, DatabaseRecord, ExcelEntry, ForeignAgentEntry, MatchResult
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
    """Возвращает тип подтверждения ФИО или None, если личности не совпадают.

    single_initial — ослабленное, но допустимое совпадение: фамилия + один
    инициал из библиотечной записи должны точно совпасть с фамилией и первым
    именем из реестра. Такой случай выводится в отчёт с предупреждением.
    """
    db_surname, db_full_names, db_initials = database_identity
    reg_surname, reg_full_names, reg_initials = registry_identity
    if db_surname != reg_surname:
        return None

    if db_full_names:
        # Полные имена из библиографической записи имеют приоритет над инициалами:
        # это исключает совпадения вроде «Петров Александр» и «Петров Алексей».
        if len(reg_full_names) < len(db_full_names):
            return None
        compare_count = min(len(db_full_names), 2)
        return "full_name" if db_full_names[:compare_count] == reg_full_names[:compare_count] else None

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
                terms.extend(
                    (variant, "Участник", True) for variant in _registry_name_variants(participant, is_person=True)
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
    ) -> list[tuple[ForeignAgentEntry, ForeignAgentSearchTerm, str, str, float]]:
        matched: dict[
            tuple[int, str, str],
            tuple[ForeignAgentEntry, ForeignAgentSearchTerm, str, str, float],
        ] = {}

        # Проверяем все персональные поля ответственности: #700, #701 и #702.
        # В parse_database они уже собраны в record.authors.
        for author in record.authors:
            author_normalized = normalize_author(author)
            database_identity = _person_identity(author)
            candidates: list[tuple[ForeignAgentSearchTerm, str | None]] = [
                (term, "exact") for term in self.by_exact.get(author_normalized, [])
            ]
            if database_identity is not None:
                for term in self.by_person_surname.get(database_identity[0], []):
                    if term.person_identity is None:
                        continue
                    match_kind = _person_identity_match_kind(database_identity, term.person_identity)
                    if match_kind is not None:
                        candidates.append((term, match_kind))

            for term, match_kind in candidates:
                if not term.is_person:
                    continue
                entry = self.entries[term.entry_id]
                note = term.kind
                confidence = 100.0
                if match_kind == "single_initial":
                    note = f"{term.kind}; фамилия + один инициал — проверить вручную"
                    confidence = 90.0
                key = (entry.entry_id, term.normalized, "Автор")
                previous = matched.get(key)
                candidate = (entry, term, "Автор", note, confidence)
                if previous is None or confidence > previous[4]:
                    matched[key] = candidate

        for organization in record.organizations:
            organization_normalized = normalize_author(organization)
            for term in self.by_exact.get(organization_normalized, []):
                if term.is_person:
                    continue
                entry = self.entries[term.entry_id]
                key = (entry.entry_id, term.normalized, "Организация")
                matched[key] = (entry, term, "Организация", term.kind, 100.0)

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
                matched[key] = (entry, term, "Название", term.kind, 100.0)

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
        for entry, term, database_field, match_note, confidence in index.match_record(record):
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
                )
            )
    return results


def compare_substance_entries(
    index: DatabaseIndex,
    entries: list[ExcelEntry],
    use_isbn_matching: bool,
    use_title_fallback: bool,
    use_fuzzy: bool,
    fuzzy_threshold: int,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
    match_rules: dict[str, bool] | None = None,
) -> tuple[list[MatchResult], set[int], set[int], set[int], set[int]]:
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

        entry_results = index.match(
            entry,
            use_isbn_matching,
            use_title_fallback,
            use_fuzzy,
            fuzzy_threshold,
            match_rules=match_rules,
        )
        results.extend(entry_results)
        if any(item.status == "Совпадение" for item in entry_results):
            matched_entry_ids.add(entry.entry_id)
        if any(item.status == "Совпадение" and item.method.startswith("ISBN") for item in entry_results):
            exact_isbn_ids.add(entry.entry_id)
        if any(item.status == "Совпадение" and not item.method.startswith("ISBN") for item in entry_results):
            exact_title_ids.add(entry.entry_id)
        if any(item.status == "Возможное совпадение" for item in entry_results):
            probable_ids.add(entry.entry_id)

    return results, matched_entry_ids, exact_isbn_ids, exact_title_ids, probable_ids


# Индексирует поля базы, чтобы не перебирать все записи для каждой строки Excel.
class DatabaseIndex:
    def __init__(self, records: list[DatabaseRecord]) -> None:
        self.records = records
        self.by_isbn: dict[str, list[DatabaseRecord]] = defaultdict(list)
        self.by_title: dict[str, list[DatabaseRecord]] = defaultdict(list)
        self.by_author_surname: dict[str, list[DatabaseRecord]] = defaultdict(list)
        self.publications: dict[int, list[tuple[str, str]]] = {}

        for record in records:
            for surname in {author_surname(author) for author in record.authors} - {""}:
                self.by_author_surname[surname].append(record)
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

    def match(
        self,
        entry: ExcelEntry,
        use_isbn_matching: bool,
        use_title_fallback: bool,
        use_fuzzy: bool,
        fuzzy_threshold: int,
        match_rules: dict[str, bool] | None = None,
    ) -> list[MatchResult]:
        normalized_isbns = extract_isbns(entry.isbn)
        isbn_records: list[DatabaseRecord] = []
        seen_record_numbers: set[int] = set()
        if use_isbn_matching:
            for normalized_isbn in normalized_isbns:
                for record in self.by_isbn.get(normalized_isbn, []):
                    if record.record_number not in seen_record_numbers:
                        seen_record_numbers.add(record.record_number)
                        isbn_records.append(record)
        if isbn_records:
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
                for record in isbn_records
            ]

        enabled_rules = {key for key in EXTRA_MATCH_RULES if (match_rules or {}).get(key, False)}
        if not use_title_fallback and not enabled_rules:
            if not use_isbn_matching:
                note = "Поиск по ISBN и названию отключён"
            else:
                note = (
                    "Корректный ISBN не найден"
                    if entry.isbn and not normalized_isbns
                    else "Совпадение по ISBN отсутствует"
                )
            return [MatchResult("Не найдено", "—", 0.0, entry, note=note)]

        normalized_title = normalize_title(entry.title)
        author_search = any(not {"isbn", "title"}.intersection(EXTRA_MATCH_RULES[key][1]) for key in enabled_rules)
        if not normalized_title and not (
            enabled_rules and (normalized_isbns or author_search and author_surname(entry.author))
        ):
            if entry.isbn and not normalized_isbns:
                note = "ISBN имеет неверный формат или контрольную цифру; названия для резервного поиска нет"
            else:
                note = "Нет ISBN или названия для поиска"
            return [MatchResult("Не найдено", "—", 0.0, entry, note=note)]

        extra_candidates = self.by_title.get(normalized_title, []) if enabled_rules else []
        extra_candidate_ids = {record.record_number for record in extra_candidates}
        custom_isbn_candidates = (
            [record for isbn in normalized_isbns for record in self.by_isbn.get(isbn, [])] if enabled_rules else []
        )
        custom_isbn_ids = {record.record_number for record in custom_isbn_candidates}
        legacy_candidates = self.by_title.get(normalized_title, []) if use_title_fallback else []
        legacy_ids = {record.record_number for record in legacy_candidates}
        author_candidates = self.by_author_surname.get(author_surname(entry.author), []) if author_search else []
        exact_candidates = list(legacy_candidates) + extra_candidates + custom_isbn_candidates + list(author_candidates)
        if exact_candidates:
            title_results: list[MatchResult] = []
            seen_titles: set[int] = set()
            for record in exact_candidates:
                if record.record_number in seen_titles:
                    continue
                seen_titles.add(record.record_number)
                excel_author = _author_identity(entry.author)
                record_has_authors = bool(record.authors)
                author_match_kind = (
                    self._author_match_kind(entry.author, record) if excel_author and record_has_authors else None
                )
                author_confirmed = author_match_kind == "exact"
                if entry.author and record_has_authors and author_match_kind is None:
                    continue

                extra_method = ""
                extra_confirmed = False
                if enabled_rules:
                    publishers = publisher_variants(entry.publisher)
                    year = normalize_publication_year(entry.year)
                    for key in sorted(
                        enabled_rules,
                        key=lambda key: (
                            match_rule_needs_review(EXTRA_MATCH_RULES[key][1]),
                            -len(EXTRA_MATCH_RULES[key][1]),
                            key,
                        ),
                    ):
                        label, required = EXTRA_MATCH_RULES[key]
                        if "author" in required and not author_confirmed:
                            continue
                        if "title" in required and record.record_number not in extra_candidate_ids:
                            continue
                        if "isbn" in required and record.record_number not in custom_isbn_ids:
                            continue
                        if not {"publisher", "year"}.intersection(required) or any(
                            ("publisher" not in required or bool(publishers & publisher_variants(record_publisher)))
                            and ("year" not in required or bool(year) and year == record_year)
                            for record_publisher, record_year in self.publications[record.record_number]
                        ):
                            extra_method = label
                            extra_confirmed = not match_rule_needs_review(required)
                            break

                legacy_confirmed = use_title_fallback and record.record_number in legacy_ids and author_confirmed
                if extra_method and (extra_confirmed or not legacy_confirmed):
                    status = "Совпадение" if extra_confirmed else "Возможное совпадение"
                    method = extra_method
                    confidence = 100.0 if extra_confirmed else 85.0
                    note = (
                        ""
                        if extra_confirmed
                        else (
                            "Совпали автор и данные издания без проверки названия или ISBN; проверьте вручную"
                            if "title" not in EXTRA_MATCH_RULES[key][1]
                            else "Совпало название и одно поле издания; проверьте вручную"
                        )
                    )
                elif not use_title_fallback or record.record_number not in legacy_ids:
                    continue
                elif author_confirmed:
                    status = "Совпадение"
                    method = "Название и автор"
                    confidence = 100.0
                    note = ""
                elif author_match_kind == "partial":
                    status = "Возможное совпадение"
                    method = "Название и неполные данные автора"
                    confidence = 90.0
                    note = "Название совпало, но для надёжной проверки автора недостаточно инициалов"
                elif not entry.author:
                    status = "Возможное совпадение"
                    method = "Только название"
                    confidence = 75.0
                    note = "Точно совпало только название; в перечне не указан автор — проверить вручную"
                elif not record_has_authors:
                    status = "Возможное совпадение"
                    method = "Название без проверки автора"
                    confidence = 80.0
                    note = "Название совпало, но в записи базы нет автора — проверить вручную"
                else:
                    status = "Возможное совпадение"
                    method = "Название без надёжной проверки автора"
                    confidence = 80.0
                    note = "Название совпало, но автора нельзя надёжно сопоставить — проверить вручную"

                title_results.append(
                    MatchResult(
                        status=status,
                        method=method,
                        confidence=confidence,
                        excel=entry,
                        database=record,
                        note=note,
                        source_type=SOURCE_SUBSTANCES,
                        matched_value=entry.title or entry.author,
                    )
                )
            if title_results:
                return title_results

        if use_title_fallback and use_fuzzy and process is not None and self.unique_titles:
            candidate_titles = process.extract(
                normalized_title,
                self.unique_titles,
                scorer=fuzz.token_set_ratio,
                score_cutoff=fuzzy_threshold,
                limit=5,
            )
            best: list[tuple[float, DatabaseRecord, float, float]] = []
            for title_key, title_score, _ in candidate_titles:
                for record in self.by_title[title_key]:
                    author_score = self._author_similarity(entry.author, record)
                    if entry.author and record.authors and author_score < 60:
                        continue
                    combined = title_score * 0.85 + author_score * 0.15
                    if combined >= fuzzy_threshold:
                        best.append((combined, record, float(title_score), float(author_score)))

            if best:
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

        note = "Нет совпадений по включённым правилам; проверьте наличие необходимых полей"
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
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[MatchResult], ComparisonSummary]:
    """Сверяет уже загруженные записи, в том числе полученные напрямую с ИРБИС."""
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
                use_isbn_matching,
                use_title_fallback,
                use_fuzzy,
                fuzzy_threshold,
                progress_cb,
                cancel_cb,
                match_rules,
            )
            foreign_future = executor.submit(
                compare_foreign_agents,
                records,
                foreign_entries,
                progress_cb,
                cancel_cb,
            )
            substance_results, matched_entry_ids, exact_isbn_ids, exact_title_ids, probable_ids = (
                substance_future.result()
            )
            foreign_results = foreign_future.result()
    else:
        (
            substance_results,
            matched_entry_ids,
            exact_isbn_ids,
            exact_title_ids,
            probable_ids,
        ) = compare_substance_entries(
            index,
            entries,
            use_isbn_matching,
            use_title_fallback,
            use_fuzzy,
            fuzzy_threshold,
            progress_cb,
            cancel_cb,
            match_rules,
        )
        foreign_results = compare_foreign_agents(records, foreign_entries, progress_cb, cancel_cb)

    results: list[MatchResult] = []
    results.extend(substance_results)
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
        matched_excel_rows=len(matched_entry_ids),
        unmatched_excel_rows=len(entries) - len(matched_entry_ids),
        result_rows=len(results),
        exact_isbn_rows=len(exact_isbn_ids),
        exact_title_rows=len(exact_title_ids),
        probable_rows=len(probable_ids),
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
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> tuple[list[MatchResult], ComparisonSummary]:
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
        use_isbn_matching=use_isbn_matching,
        use_title_fallback=use_title_fallback,
        match_rules=match_rules,
        use_fuzzy=use_fuzzy,
        fuzzy_threshold=fuzzy_threshold,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )


# Экспорт отчёта загружается по требованию, сохраняя прежний публичный API.

