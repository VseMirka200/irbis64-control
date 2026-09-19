import unittest

from matcher import (
    SOURCE_FOREIGN_AGENTS,
    DatabaseRecord,
    ExcelEntry,
    ForeignAgentEntry,
    MarkerApplicationStats,
    MatchResult,
    _modify_matched_record,
    apply_markers_to_tag_values,
    build_markers_by_record,
)


class ForeignAgentMarkerDeduplicationTests(unittest.TestCase):
    def test_same_pseudonym_with_typo_in_legal_name_is_one_marker(self) -> None:
        correct = (
            "^AI^@ЧХАРТИШВИЛИ ГРИГОРИЙ ШАЛВОВИЧ "
            "(ПСЕВДОНИМ: БОРИС АКУНИН)"
        )
        typo = (
            "^AI^@ЧХАРТИШВИЛЛИ ГРИГОРИЙ ШАЛВОВИЧ "
            "(ПСЕВДОНИМ: БОРИС АКУНИН)"
        )

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
