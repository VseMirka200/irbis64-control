import unittest

from matcher import (
    SOURCE_FOREIGN_AGENTS,
    DatabaseRecord,
    ExcelEntry,
    ForeignAgentEntry,
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


if __name__ == "__main__":
    unittest.main()
