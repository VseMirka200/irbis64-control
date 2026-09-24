import unittest

from irbis_control.core.matcher import (
    SOURCE_FOREIGN_AGENTS,
    SOURCE_SUBSTANCES,
    DatabaseRecord,
    ExcelEntry,
    ForeignAgentEntry,
    MarkerApplicationStats,
    MatchResult,
    _modify_matched_record,
    apply_markers_to_tag_values,
    build_markers_by_record,
)
from irbis_control.infrastructure.irbis_models import IrbisField, IrbisRecord
from irbis_control.ui.services.workers import _marker_neutral_signature


class ForeignAgentMarkerDeduplicationTests(unittest.TestCase):
    def test_marker_neutral_signature_allows_reapplying_after_cleanup(self) -> None:
        marked = IrbisRecord(
            42,
            version=7,
            fields=[
                IrbisField(200, "^AТестовая книга"),
                IrbisField(333, "^AIII"),
                IrbisField(333, "^AI^@ИВАНОВ ИВАН"),
                IrbisField(900, "^Z18+"),
            ],
        )
        cleaned = IrbisRecord(
            42,
            version=8,
            fields=[IrbisField(200, "^AТестовая книга")],
        )

        settings = {
            "substance_marker": "^AIII",
            "foreign_agent_marker_template": "^AI^@{name}",
            "foreign_organization_marker_template": "^AO^@{name}",
            "age_marker": "^Z18+",
            "substance_marker_field": 333,
            "foreign_agent_marker_field": 333,
            "foreign_organization_marker_field": 333,
            "age_marker_field": 900,
        }

        self.assertEqual(
            _marker_neutral_signature(marked, **settings),
            _marker_neutral_signature(cleaned, **settings),
        )

    def test_marker_neutral_signature_keeps_unrelated_edits_visible(self) -> None:
        scanned = IrbisRecord(42, version=7, fields=[IrbisField(200, "^AСтарое название"), IrbisField(333, "^AIII")])
        edited = IrbisRecord(42, version=8, fields=[IrbisField(200, "^AНовое название")])

        settings = {
            "substance_marker": "^AIII",
            "foreign_agent_marker_template": "^AI^@{name}",
            "foreign_organization_marker_template": "^AO^@{name}",
            "age_marker": "^Z18+",
            "substance_marker_field": 333,
            "foreign_agent_marker_field": 333,
            "foreign_organization_marker_field": 333,
            "age_marker_field": 900,
        }

        self.assertNotEqual(
            _marker_neutral_signature(scanned, **settings),
            _marker_neutral_signature(edited, **settings),
        )

    def test_substance_and_foreign_agent_markers_are_both_kept_for_one_record(self) -> None:
        database = DatabaseRecord(record_number=651, authors=["Берсенева Анна"])
        foreign = ForeignAgentEntry(
            entry_id=1,
            source_file="publication-foreign-agent.xlsx",
            sheet_name="Список Книг",
            row_number=4,
            registry_number="651",
            name="Берсенева Анна",
            agent_type="Физическое лицо",
        )
        substance_result = MatchResult(
            status="Совпадение",
            method="Название и автор",
            confidence=100.0,
            excel=ExcelEntry(1, "publication-drugs.xlsx", "Список Книг", 4),
            database=database,
            source_type=SOURCE_SUBSTANCES,
        )
        foreign_result = MatchResult(
            status="Совпадение",
            method="Реестр иностранных агентов: Автор",
            confidence=100.0,
            excel=ExcelEntry(2, "publication-foreign-agent.xlsx", "Список Книг", 4),
            database=database,
            source_type=SOURCE_FOREIGN_AGENTS,
            matched_value=foreign.name,
            foreign_agent=foreign,
        )

        markers = build_markers_by_record([substance_result, foreign_result])
        fields, changed = apply_markers_to_tag_values([], markers[651], age_marker="")

        self.assertTrue(changed)
        self.assertEqual([(333, "^AIII"), (333, "^AI^@БЕРСЕНЕВА АННА")], fields)

    def test_same_pseudonym_with_typo_in_legal_name_is_one_marker(self) -> None:
        correct = "^AI^@ЧХАРТИШВИЛИ ГРИГОРИЙ ШАЛВОВИЧ (ПСЕВДОНИМ: БОРИС АКУНИН)"
        typo = "^AI^@ЧХАРТИШВИЛЛИ ГРИГОРИЙ ШАЛВОВИЧ (ПСЕВДОНИМ: БОРИС АКУНИН)"

        fields, changed = apply_markers_to_tag_values(
            [(333, correct), (333, typo)],
            [(333, correct), (333, typo)],
            age_marker="",
        )

        self.assertTrue(changed)
        self.assertEqual([(333, correct)], fields)

        text, text_changed = _modify_matched_record(
            f"#333: {correct}\n#333: {typo}",
            "\n",
            [],
            age_marker="",
            field_markers=[(333, correct), (333, typo)],
        )
        self.assertTrue(text_changed)
        self.assertEqual(f"#333: {correct}", text)

        database = DatabaseRecord(record_number=11532, authors=["Акунин Б."])
        results = []
        for entry_id, name in enumerate((correct[5:], typo[5:]), start=1):
            registry_entry = ForeignAgentEntry(
                entry_id=entry_id,
                source_file="registry.xlsx",
                sheet_name="Лист1",
                row_number=entry_id,
                name=name,
                agent_type="Физическое лицо",
            )
            results.append(
                MatchResult(
                    status="Совпадение",
                    method="Реестр иностранных агентов: Автор",
                    confidence=100.0,
                    excel=ExcelEntry(entry_id, "registry.xlsx", "Лист1", entry_id),
                    database=database,
                    source_type=SOURCE_FOREIGN_AGENTS,
                    matched_value="Акунин Борис",
                    foreign_agent=registry_entry,
                )
            )

        self.assertEqual({11532: [(333, correct)]}, build_markers_by_record(results))

    def test_different_pseudonyms_remain_separate_markers(self) -> None:
        first = "^AI^@ПЕРВЫЙ АВТОР (ПСЕВДОНИМ: АЛЬФА)"
        second = "^AI^@ВТОРОЙ АВТОР (ПСЕВДОНИМ: БЕТА)"

        fields, changed = apply_markers_to_tag_values(
            [],
            [(333, first), (333, second)],
            age_marker="",
        )

        self.assertTrue(changed)
        self.assertEqual([(333, first), (333, second)], fields)

    def test_repeated_application_does_not_add_marker_again(self) -> None:
        marker = "ПРЕДУПРЕЖДЕНИЕ О ВРЕДЕ ЗДОРОВЬЮ."

        fields, changed = apply_markers_to_tag_values(
            [(333, marker)],
            [(333, marker)],
            age_marker="",
        )

        self.assertFalse(changed)
        self.assertEqual([(333, marker)], fields)

    def test_html_space_entity_is_the_same_marker(self) -> None:
        marker = "ПРЕДУПРЕЖДЕНИЕ О ВРЕДЕ ЗДОРОВЬЮ."
        stats = MarkerApplicationStats()

        fields, changed = apply_markers_to_tag_values(
            [(333, marker + "&#x20;")],
            [(333, marker)],
            age_marker="",
            stats=stats,
        )

        self.assertFalse(changed)
        self.assertEqual([(333, marker + "&#x20;")], fields)
        self.assertEqual(1, stats.already_present)
        self.assertEqual(0, stats.added)
        self.assertEqual(0, stats.duplicates_repaired)

    def test_marker_statistics_count_only_actual_changes(self) -> None:
        marker = "ПРЕДУПРЕЖДЕНИЕ"
        stats = MarkerApplicationStats()

        fields, changed = apply_markers_to_tag_values(
            [(333, marker), (333, marker), (333, "СЛУЖЕБНОЕ ЗНАЧЕНИЕ")],
            [(333, marker), (333, "НОВАЯ МЕТКА")],
            age_marker="",
            stats=stats,
        )

        self.assertTrue(changed)
        self.assertEqual(
            [(333, marker), (333, "СЛУЖЕБНОЕ ЗНАЧЕНИЕ"), (333, "НОВАЯ МЕТКА")],
            fields,
        )
        self.assertEqual(1, stats.already_present)
        self.assertEqual(1, stats.added)
        self.assertEqual(1, stats.duplicates_repaired)


if __name__ == "__main__":
    unittest.main()
