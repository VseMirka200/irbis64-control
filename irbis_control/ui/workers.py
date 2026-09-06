from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from threading import Event
from time import perf_counter

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from irbis_control.application.updater import (
    ReleaseAsset,
    UpdateError,
    download_asset,
    fetch_latest_release,
)
from irbis_control.core.matcher import (
    DEFAULT_AGE_MARKER,
    DEFAULT_AGE_MARKER_FIELD,
    DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    DEFAULT_SUBSTANCE_MARKER,
    DEFAULT_SUBSTANCE_MARKER_FIELD,
    ComparisonCancelled,
    apply_markers_to_tag_values,
    build_markers_by_record,
    compare_and_export,
    compare_database_records,
    database_record_from_tag_values,
    export_results,
    remove_markers_from_tag_values,
)
from irbis_control.core.models import MarkerApplicationStats
from irbis_control.infrastructure.atomic_io import atomic_write_text
from irbis_control.infrastructure.irbis_bridge import (
    IrbisClient,
    apply_modified_snapshot,
    create_irbis_snapshot,
)
from irbis_control.infrastructure.irbis_models import IrbisError, IrbisField, IrbisRecord
from irbis_control.ui.storage_paths import app_data_dir


# Выполняет проверку и загрузку обновления вне потока интерфейса.
class UpdateWorker(QObject):
    progress = pyqtSignal(int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, mode: str, asset: ReleaseAsset | None = None) -> None:
        super().__init__()
        self.mode = mode
        self.asset = asset

    @pyqtSlot()
    def run(self) -> None:
        try:
            if self.mode == "check":
                self.finished.emit(fetch_latest_release())
                return
            if self.mode == "download" and self.asset is not None:
                target = download_asset(
                    self.asset,
                    app_data_dir() / "updates",
                    progress_cb=self.progress.emit,
                )
                self.finished.emit(target)
                return
            raise UpdateError("Неизвестная операция обновления.")
        except Exception as exc:
            self.failed.emit(str(exc))


# Запускает сравнение локальных файлов и передаёт прогресс через сигналы Qt.
class ComparisonWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal(str)

    def __init__(
        self,
        database_path: list[str],
        foreign_agents_path: str,
        excel_paths: list[str],
        output_path: str,
        modified_database_path: str,
        use_isbn_matching: bool,
        use_title_fallback: bool,
        use_fuzzy: bool,
        fuzzy_threshold: int,
        report_options: dict[str, object],
        substance_marker: str,
        foreign_agent_marker_template: str,
        age_marker: str,
        substance_marker_field: int,
        foreign_agent_marker_field: int,
        age_marker_field: int,
        match_rules: dict[str, bool] | None = None,
    ) -> None:
        super().__init__()
        self.database_path = database_path
        self.foreign_agents_path = foreign_agents_path
        self.excel_paths = excel_paths
        self.output_path = output_path
        self.modified_database_path = modified_database_path
        self.use_isbn_matching = use_isbn_matching
        self.use_title_fallback = use_title_fallback
        self.match_rules = dict(match_rules or {})
        self.use_fuzzy = use_fuzzy
        self.fuzzy_threshold = fuzzy_threshold
        self.report_options = dict(report_options)
        self.substance_marker = substance_marker
        self.foreign_agent_marker_template = foreign_agent_marker_template
        self.age_marker = age_marker
        self.substance_marker_field = substance_marker_field
        self.foreign_agent_marker_field = foreign_agent_marker_field
        self.age_marker_field = age_marker_field
        self.cancel_event = Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            results, summary = compare_and_export(
                self.database_path,
                self.excel_paths,
                self.output_path,
                self.modified_database_path,
                foreign_agents_path=self.foreign_agents_path or None,
                use_isbn_matching=self.use_isbn_matching,
                use_title_fallback=self.use_title_fallback,
                match_rules=self.match_rules,
                use_fuzzy=self.use_fuzzy,
                fuzzy_threshold=self.fuzzy_threshold,
                report_options=self.report_options,
                substance_marker=self.substance_marker,
                foreign_agent_marker_template=self.foreign_agent_marker_template,
                age_marker=self.age_marker,
                substance_marker_field=self.substance_marker_field,
                foreign_agent_marker_field=self.foreign_agent_marker_field,
                age_marker_field=self.age_marker_field,
                progress_cb=lambda percent, text: self.progress.emit(percent, text),
                cancel_cb=self.cancel_event.is_set,
            )
            self.finished.emit(results, summary)
        except ComparisonCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc())


# Сверяет серверные записи и ждёт подтверждения перед записью проверенных изменений.
class DirectIrbisComparisonWorker(QObject):
    """Сверяет и изменяет записи прямо на сервере ИРБИС без TXT-снимка."""

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal(str)
    preview_requested = pyqtSignal(object)

    def __init__(
        self,
        *,
        host: str,
        port: int,
        login: str,
        password: str,
        database: str,
        query: str,
        page_size: int,
        foreign_agents_path: str,
        excel_paths: list[str],
        output_path: str,
        use_isbn_matching: bool,
        use_title_fallback: bool,
        use_fuzzy: bool,
        fuzzy_threshold: int,
        report_options: dict[str, object],
        substance_marker: str,
        foreign_agent_marker_template: str,
        age_marker: str,
        substance_marker_field: int,
        foreign_agent_marker_field: int,
        age_marker_field: int,
        backup_dir: str,
        create_backup: bool = True,
        match_rules: dict[str, bool] | None = None,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = int(port)
        self.login = login
        self.password = password
        self.database = database
        self.query = query or "I=$"
        self.page_size = max(100, min(int(page_size or 500), 2000))
        self.foreign_agents_path = foreign_agents_path
        self.excel_paths = list(excel_paths)
        self.output_path = output_path
        self.use_isbn_matching = use_isbn_matching
        self.use_title_fallback = use_title_fallback
        self.match_rules = dict(match_rules or {})
        self.use_fuzzy = use_fuzzy
        self.fuzzy_threshold = fuzzy_threshold
        self.report_options = dict(report_options)
        self.substance_marker = substance_marker
        self.foreign_agent_marker_template = foreign_agent_marker_template
        self.age_marker = age_marker
        self.substance_marker_field = int(substance_marker_field)
        self.foreign_agent_marker_field = int(foreign_agent_marker_field)
        self.age_marker_field = int(age_marker_field)
        self.backup_dir = Path(backup_dir)
        self.create_backup = bool(create_backup)
        self.cancel_event = Event()
        self.preview_event = Event()
        self.preview_approved = False

    def request_cancel(self) -> None:
        self.cancel_event.set()
        self.preview_event.set()

    def confirm_preview(self, approved: bool) -> None:
        self.preview_approved = bool(approved)
        self.preview_event.set()

    def _cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def _save_rollback(self, records: list[IrbisRecord]) -> Path | None:
        if not records or not self.create_backup:
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = self.backup_dir / f"direct_irbis_{self.database}_{stamp}.json"
        payload = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "records": [
                {
                    "mfn": record.mfn,
                    "status": record.status,
                    "version": record.version,
                    "fields": [{"tag": field.tag, "value": field.value} for field in record.fields],
                }
                for record in records
            ],
        }
        atomic_write_text(
            target,
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    @pyqtSlot()
    def run(self) -> None:
        try:
            client = IrbisClient(
                self.host,
                self.port,
                self.login,
                self.password,
                "C",
                timeout=30,
            )
            with client as connected:
                self.progress.emit(2, f"Подключение к ИРБИС • база {self.database}")
                irbis_records = connected.search_read_all(
                    self.database,
                    self.query,
                    page_size=self.page_size,
                    progress_cb=lambda percent, message: self.progress.emit(percent, message),
                    cancel_cb=self._cancelled,
                )
                if self._cancelled():
                    raise ComparisonCancelled("Операция отменена пользователем")
                if not irbis_records:
                    raise RuntimeError("По запросу ИРБИС не найдено ни одной записи.")

                self.progress.emit(49, f"Подготовка к сравнению: {len(irbis_records):,} записей")
                scan_versions = {record.mfn: record.version for record in irbis_records}
                source_label = f"ИРБИС://{self.host}:{self.port}/{self.database}"
                records = [
                    database_record_from_tag_values(
                        record.mfn,
                        ((field.tag, field.value) for field in record.fields),
                        source_file=source_label,
                        source_record_number=record.mfn,
                        raw_record="",
                    )
                    for record in irbis_records
                ]
                # Полные серверные записи больше не нужны в памяти: перед записью
                # каждый изменяемый MFN всё равно перечитывается для проверки версии.
                del irbis_records

                results, summary = compare_database_records(
                    records,
                    self.excel_paths,
                    database_label=source_label,
                    foreign_agents_path=self.foreign_agents_path or None,
                    use_isbn_matching=self.use_isbn_matching,
                    use_title_fallback=self.use_title_fallback,
                    match_rules=self.match_rules,
                    use_fuzzy=self.use_fuzzy,
                    fuzzy_threshold=self.fuzzy_threshold,
                    progress_cb=lambda percent, message: self.progress.emit(percent, message),
                    cancel_cb=self._cancelled,
                )
                if self.report_options.get("enabled", True):
                    export_results(
                        self.output_path,
                        results,
                        summary,
                        progress_cb=lambda percent, message: self.progress.emit(percent, message),
                        cancel_cb=self._cancelled,
                        report_options=self.report_options,
                    )

                if self.report_options.get("report_only", False):
                    summary.modified_database_file = ""
                    summary.modified_database_records = 0
                    self.progress.emit(100, "Отчёт создан, записи ИРБИС не изменялись")
                    self.finished.emit(results, summary)
                    return

                markers_by_mfn = build_markers_by_record(
                    results,
                    substance_marker=self.substance_marker,
                    foreign_agent_marker_template=self.foreign_agent_marker_template,
                    substance_marker_field=self.substance_marker_field,
                    foreign_agent_marker_field=self.foreign_agent_marker_field,
                )
                if self._cancelled():
                    raise ComparisonCancelled("Операция отменена пользователем")

                pending: list[IrbisRecord] = []
                rollback_records: list[IrbisRecord] = []
                preview_records: list[dict[str, object]] = []
                marker_stats = MarkerApplicationStats()
                conflicts = 0
                total_candidates = len(markers_by_mfn)
                for index, mfn in enumerate(sorted(markers_by_mfn), start=1):
                    if self._cancelled():
                        raise ComparisonCancelled("Операция отменена пользователем")
                    live = connected.read_record(self.database, int(mfn))
                    scanned_version = scan_versions.get(int(mfn))
                    if scanned_version is not None and live.version != scanned_version:
                        conflicts += 1
                        continue
                    record_stats = MarkerApplicationStats()
                    tag_values, changed = apply_markers_to_tag_values(
                        ((field.tag, field.value) for field in live.fields),
                        markers_by_mfn[mfn],
                        age_marker=self.age_marker,
                        age_marker_field=self.age_marker_field,
                        stats=record_stats,
                    )
                    marker_stats.already_present += record_stats.already_present
                    marker_stats.added += record_stats.added
                    marker_stats.duplicates_repaired += record_stats.duplicates_repaired
                    if changed:
                        rollback_records.append(live)
                        pending.append(
                            IrbisRecord(
                                live.mfn,
                                live.status,
                                live.version,
                                [IrbisField(tag, value) for tag, value in tag_values],
                            )
                        )
                        requested_markers = [
                            f"#{int(tag):03d}: {marker}" for tag, marker in markers_by_mfn[mfn] if str(marker).strip()
                        ]
                        if self.age_marker.strip():
                            requested_markers.append(f"#{self.age_marker_field:03d}: {self.age_marker.strip()}")
                        preview_records.append(
                            {
                                "mfn": live.mfn,
                                "markers": requested_markers,
                                "added": record_stats.added,
                                "already_present": record_stats.already_present,
                                "duplicates_repaired": record_stats.duplicates_repaired,
                            }
                        )
                    if index == total_candidates or index % 25 == 0:
                        self.progress.emit(
                            92 + int(index / max(total_candidates, 1) * 4),
                            f"Проверка найденных MFN перед записью: {index:,} из {total_candidates:,}",
                        )

                if pending:
                    self.progress.emit(96, "Ожидание подтверждения записи в ИРБИС")
                    self.preview_event.clear()
                    self.preview_approved = False
                    self.preview_requested.emit(
                        {
                            "database": self.database,
                            "records": preview_records,
                            "record_count": len(pending),
                            "markers_added": marker_stats.added,
                            "markers_already_present": marker_stats.already_present,
                            "duplicates_repaired": marker_stats.duplicates_repaired,
                            "conflicts": conflicts,
                            "review_rows": summary.review_rows,
                            "create_backup": self.create_backup,
                        }
                    )
                    self.preview_event.wait()
                    if self._cancelled() or not self.preview_approved:
                        suffix = " Excel-отчёт уже сохранён." if self.output_path else ""
                        raise ComparisonCancelled("Запись изменений в ИРБИС отменена пользователем." + suffix)

                    self.progress.emit(96, "Повторная проверка версий MFN перед записью")
                    changed_during_confirmation: list[int] = []
                    for original in rollback_records:
                        latest = connected.read_record(self.database, original.mfn)
                        if latest.version != original.version:
                            changed_during_confirmation.append(original.mfn)
                    if changed_during_confirmation:
                        preview = ", ".join(str(mfn) for mfn in changed_during_confirmation[:20])
                        if len(changed_during_confirmation) > 20:
                            preview += f" и ещё {len(changed_during_confirmation) - 20}"
                        raise IrbisError(
                            "Запись отменена: после показа предварительного просмотра "
                            f"на сервере изменились MFN {preview}. Запустите проверку заново."
                        )

                backup = self._save_rollback(rollback_records)
                written = 0
                readback_repairs = 0
                # После создания rollback-копии запись выполняется до конца: остановка
                # посередине оставила бы базу частично изменённой.
                # Важно: после каждой записи перечитываем MFN с сервера. Это защищает
                # от ситуации, когда визуально одинаковые повторения 333 появились уже
                # на серверной стороне/из старой версии программы.
                for index, record in enumerate(pending, start=1):
                    try:
                        connected.write_record(self.database, record, actualize=1)
                    except Exception as exc:
                        rollback_hint = (
                            f"Rollback-копия: {backup}." if backup else "Rollback-копия отключена в настройках."
                        )
                        raise IrbisError(
                            f"Запись прервана на MFN {record.mfn}; до сбоя записано: {written}. "
                            f"{rollback_hint} Причина: {exc}"
                        ) from exc

                    readback = connected.read_record(self.database, record.mfn)
                    verified_values, needs_repair = apply_markers_to_tag_values(
                        ((field.tag, field.value) for field in readback.fields),
                        markers_by_mfn.get(record.mfn, []),
                        age_marker=self.age_marker,
                        age_marker_field=self.age_marker_field,
                    )
                    if needs_repair:
                        repaired = IrbisRecord(
                            readback.mfn,
                            readback.status,
                            readback.version,
                            [IrbisField(tag, value) for tag, value in verified_values],
                        )
                        connected.write_record(self.database, repaired, actualize=1)
                        readback_repairs += 1

                        # Контрольный read-back: молча оставлять дубль нельзя.
                        final_record = connected.read_record(self.database, record.mfn)
                        _final_values, still_needs_repair = apply_markers_to_tag_values(
                            ((field.tag, field.value) for field in final_record.fields),
                            markers_by_mfn.get(record.mfn, []),
                            age_marker=self.age_marker,
                            age_marker_field=self.age_marker_field,
                        )
                        if still_needs_repair:
                            raise IrbisError(
                                f"MFN {record.mfn}: сервер ИРБИС повторно вернул дублирующую метку после исправления."
                            )

                    written += 1
                    self.progress.emit(
                        96 + int(index / max(len(pending), 1) * 4),
                        f"Запись изменений в ИРБИС: {index:,} из {len(pending):,}",
                    )

                summary.modified_database_file = source_label
                summary.modified_database_records = written
                summary.markers_already_present = marker_stats.already_present
                summary.markers_added = marker_stats.added
                summary.marker_duplicates_repaired = marker_stats.duplicates_repaired + readback_repairs
                if conflicts:
                    summary.warnings.append(
                        f"Пропущено записей, изменённых на сервере во время проверки: {conflicts}. "
                        "Для них запустите проверку ещё раз."
                    )
                if backup:
                    summary.warnings.append(f"Rollback-копия серверных записей: {backup}")
                if readback_repairs:
                    summary.warnings.append(
                        f"После контрольного чтения автоматически исправлено дублей меток: {readback_repairs}."
                    )
                suffix = f"; конфликтов версий: {conflicts}" if conflicts else ""
                self.progress.emit(100, f"Готово: изменено записей ИРБИС — {written:,}{suffix}")
                self.finished.emit(results, summary)
        except ComparisonCancelled as exc:
            self.cancelled.emit(str(exc))
        except IrbisError as exc:
            if self._cancelled():
                self.cancelled.emit("Операция отменена пользователем")
            else:
                self.failed.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc())


# Выполняет операции подключения и снимков вне потока интерфейса.
class IrbisOperationWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(self, mode: str, params: dict[str, object]) -> None:
        super().__init__()
        self.mode = mode
        self.params = dict(params)

    @pyqtSlot()
    def run(self) -> None:
        try:
            operation_started = perf_counter()
            client = IrbisClient(
                str(self.params.get("host", "127.0.0.1")),
                int(self.params.get("port", 6666)),
                str(self.params.get("login", "")),
                str(self.params.get("password", "")),
                "C",
                timeout=5 if self.mode == "health" else 20,
            )
            if self.mode == "health":
                with client:
                    pass
                self.finished.emit(
                    self.mode,
                    {
                        "ok": True,
                        "response_ms": max(1, round((perf_counter() - operation_started) * 1000)),
                    },
                )
                return
            if self.mode in {"test", "databases", "tune_read"}:
                self.progress.emit(20, "Подключение к серверу ИРБИС…")
                with client as connected:
                    databases = []
                    if self.mode in {"test", "databases"}:
                        self.progress.emit(45, "Получение списка доступных баз…")
                        databases = connected.list_databases()
                    page_size = int(self.params.get("page_size", 500) or 500)
                    total = 0
                    database = str(self.params.get("database", "")).strip()
                    available_names = [
                        str(item.get("name", "") if isinstance(item, dict) else item).strip() for item in databases
                    ]
                    if available_names and database.casefold() not in {name.casefold() for name in available_names}:
                        database = available_names[0]
                    if self.mode == "tune_read":
                        if not database:
                            raise RuntimeError("Не выбрана база ИРБИС для теста пакета чтения.")
                        page_size, total = connected.tune_read_page_size(
                            database,
                            str(self.params.get("query", "I=$")),
                            progress_cb=lambda percent, message: self.progress.emit(percent, message),
                        )
                    if self.mode == "tune_read":
                        self.progress.emit(100, f"Пакет чтения подобран: {page_size} записей")
                    else:
                        self.progress.emit(100, f"Доступных баз: {len(databases)}")
                self.finished.emit(
                    self.mode,
                    {
                        "ok": True,
                        "databases": databases,
                        "page_size": page_size,
                        "probe_total": total,
                        "probe_database": database,
                        "response_ms": max(1, round((perf_counter() - operation_started) * 1000)),
                    },
                )
                return

            if self.mode == "clean_markers":
                database = str(self.params.get("database", "")).strip()
                if not database:
                    raise RuntimeError("Не выбрана база ИРБИС для очистки меток.")
                page_size = max(100, min(int(self.params.get("page_size", 500) or 500), 2000))
                query = "I=$"
                candidates: list[int] = []

                with client as connected:
                    first = 1
                    total: int | None = None
                    scanned = 0
                    self.progress.emit(2, f"Поиск меток в базе {database}…")
                    while total is None or scanned < total:
                        current_total, page = connected.search_read_page(database, query, number=page_size, first=first)
                        if total is None:
                            total = current_total
                            if total <= 0:
                                break
                        if not page:
                            break
                        for record in page:
                            _cleaned, changed = remove_markers_from_tag_values(
                                ((field.tag, field.value) for field in record.fields),
                                substance_marker=str(self.params.get("substance_marker", DEFAULT_SUBSTANCE_MARKER)),
                                foreign_agent_marker_template=str(
                                    self.params.get(
                                        "foreign_agent_marker_template", DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE
                                    )
                                ),
                                age_marker=str(self.params.get("age_marker", DEFAULT_AGE_MARKER)),
                                substance_marker_field=int(
                                    self.params.get("substance_marker_field", DEFAULT_SUBSTANCE_MARKER_FIELD)
                                ),
                                foreign_agent_marker_field=int(
                                    self.params.get("foreign_agent_marker_field", DEFAULT_FOREIGN_AGENT_MARKER_FIELD)
                                ),
                                age_marker_field=int(self.params.get("age_marker_field", DEFAULT_AGE_MARKER_FIELD)),
                            )
                            if changed:
                                candidates.append(record.mfn)
                        scanned += len(page)
                        first += len(page)
                        self.progress.emit(
                            min(55, 5 + int(scanned / max(total or 1, 1) * 50)),
                            f"Поиск меток: {scanned:,} из {total:,} • найдено записей: {len(candidates):,}",
                        )
                        if len(page) < page_size:
                            break

                    if not candidates:
                        self.progress.emit(100, "Метки для удаления не найдены.")
                        self.finished.emit(
                            self.mode,
                            {"scanned": scanned, "found": 0, "written": 0, "backup": ""},
                        )
                        return

                    rollback_records: list[IrbisRecord] = []
                    pending: list[IrbisRecord] = []
                    for index, mfn in enumerate(candidates, start=1):
                        # Перечитываем только найденные MFN непосредственно перед
                        # изменением, чтобы не затереть правки другого пользователя.
                        live = connected.read_record(database, int(mfn))
                        cleaned_values, changed = remove_markers_from_tag_values(
                            ((field.tag, field.value) for field in live.fields),
                            substance_marker=str(self.params.get("substance_marker", DEFAULT_SUBSTANCE_MARKER)),
                            foreign_agent_marker_template=str(
                                self.params.get("foreign_agent_marker_template", DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE)
                            ),
                            age_marker=str(self.params.get("age_marker", DEFAULT_AGE_MARKER)),
                            substance_marker_field=int(
                                self.params.get("substance_marker_field", DEFAULT_SUBSTANCE_MARKER_FIELD)
                            ),
                            foreign_agent_marker_field=int(
                                self.params.get("foreign_agent_marker_field", DEFAULT_FOREIGN_AGENT_MARKER_FIELD)
                            ),
                            age_marker_field=int(self.params.get("age_marker_field", DEFAULT_AGE_MARKER_FIELD)),
                        )
                        if changed:
                            rollback_records.append(live)
                            pending.append(
                                IrbisRecord(
                                    live.mfn,
                                    live.status,
                                    live.version,
                                    [IrbisField(tag, value) for tag, value in cleaned_values],
                                )
                            )
                        if index == len(candidates) or index % 25 == 0:
                            self.progress.emit(
                                55 + int(index / max(len(candidates), 1) * 20),
                                f"Проверка найденных MFN: {index:,} из {len(candidates):,}",
                            )

                    backup_path: Path | None = None
                    if rollback_records and bool(self.params.get("create_backup", True)):
                        backup_dir = Path(str(self.params.get("backup_dir", app_data_dir() / "backups")))
                        backup_dir.mkdir(parents=True, exist_ok=True)
                        backup_path = backup_dir / f"irbis_cleanup_{database}_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
                        atomic_write_text(
                            backup_path,
                            json.dumps(
                                {
                                    "created_at": datetime.now().isoformat(timespec="seconds"),
                                    "host": str(self.params.get("host", "")),
                                    "port": int(self.params.get("port", 6666)),
                                    "database": database,
                                    "operation": "marker_cleanup",
                                    "records": [
                                        {
                                            "mfn": record.mfn,
                                            "status": record.status,
                                            "version": record.version,
                                            "fields": [
                                                {"tag": field.tag, "value": field.value} for field in record.fields
                                            ],
                                        }
                                        for record in rollback_records
                                    ],
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )

                    written = 0
                    # Сначала создана rollback-копия, затем меняем живую базу.
                    for index, record in enumerate(pending, start=1):
                        connected.write_record(database, record, actualize=1)
                        written += 1
                        self.progress.emit(
                            75 + int(index / max(len(pending), 1) * 25),
                            f"Очистка меток в ИРБИС: {index:,} из {len(pending):,}",
                        )

                self.finished.emit(
                    self.mode,
                    {
                        "scanned": scanned,
                        "found": len(candidates),
                        "written": written,
                        "backup": str(backup_path) if backup_path else "",
                    },
                )
                return

            if self.mode == "fetch":
                with client as connected:
                    manifest = create_irbis_snapshot(
                        connected,
                        str(self.params["database"]),
                        str(self.params["query"]),
                        str(self.params["snapshot"]),
                        str(self.params["manifest"]),
                        progress_cb=lambda percent, text: self.progress.emit(percent, text),
                        read_workers=int(self.params.get("read_workers", 4)),
                    )
                self.finished.emit(self.mode, manifest)
                return

            if self.mode == "apply":
                with client as connected:
                    written, conflicts, backup = apply_modified_snapshot(
                        connected,
                        Path(str(self.params["manifest"])),
                        Path(str(self.params["modified"])),
                        Path(str(self.params["backup_dir"])),
                        progress_cb=lambda percent, text: self.progress.emit(percent, text),
                        create_backup=bool(self.params.get("create_backup", True)),
                    )
                self.finished.emit(
                    self.mode,
                    {"written": written, "conflicts": conflicts, "backup": str(backup) if backup else ""},
                )
                return

            raise RuntimeError(f"Неизвестная операция ИРБИС: {self.mode}")
        except Exception as exc:
            self.failed.emit(self.mode, str(exc))
