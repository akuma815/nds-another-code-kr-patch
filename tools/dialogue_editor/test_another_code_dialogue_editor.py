from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from another_code_dialogue_editor import (
    DialogueDocument,
    LayoutProfile,
    directive_sequence,
    effective_profile_for_frame,
    functional_directives_match,
    layout_issues,
    load_profiles,
    parse_preview_frames,
    scan_control_tokens,
    speaker_placement,
    token_issues,
    visible_list_text,
)


class AnotherCodeDialogueEditorTests(unittest.TestCase):
    def test_balanced_control_argument(self) -> None:
        text = "%w((꿈속)문을 연다)%f(0)대사%h%e"
        tokens, errors = scan_control_tokens(text)
        self.assertEqual(errors, [])
        self.assertEqual([t.raw for t in tokens], ["%w((꿈속)문을 연다)", "%f(0)", "%h", "%e"])

    def test_linebreak_directive_is_translator_owned(self) -> None:
        source = "%w(110)%C(0_002)대사%r둘째%h%e"
        self.assertEqual(directive_sequence(source), ["%w(110)", "%C(0_002)", "%r", "%h", "%e"])
        self.assertFalse(token_issues(source, source.replace("%r", "")))
        self.assertTrue(token_issues(source, source.replace("%h", "")))

    def test_repeated_same_window_page_split_is_allowed(self) -> None:
        source = "%w(200)%f(0)%f(2)%f(0)%h%e"
        translated = "%w(200)%f(0)%h%w(200)%f(2)%f(0)%h%e"
        self.assertTrue(functional_directives_match(source, translated))
        self.assertFalse(functional_directives_match(source, translated.replace("%w(200)", "%w(210)")))
        self.assertFalse(token_issues(source, translated))

    def test_list_preview_shows_text_without_controls(self) -> None:
        text = "%w(110)%C(0_002)첫 줄%r둘째 줄%h%e"
        self.assertEqual(visible_list_text(text), "첫 줄둘째 줄")

    def test_protagonist_is_bottom_and_other_is_top(self) -> None:
        row = {"speaker_codes": "0_002;2_000", "population": "strong_script", "source_text": ""}
        frames = parse_preview_frames("%C(0_002)아래%h%C(2_000)위%h%e", row)
        self.assertEqual([f.placement for f in frames[:2]], ["bottom", "top"])
        self.assertEqual(speaker_placement("0_999"), "bottom")
        self.assertEqual(speaker_placement("1_000"), "top")

    def test_no_speaker_uses_system_window(self) -> None:
        row = {"speaker_codes": "", "population": "strong_script", "source_text": "%w(200)"}
        frame = parse_preview_frames("%w(200)조사 메시지%h%e", row)[0]
        self.assertEqual(frame.placement, "system")
        self.assertEqual(frame.profile_id, "system_message")
        self.assertEqual(frame.window_code, "200")

    def test_mixed_record_is_classified_per_page_without_trailing_blank(self) -> None:
        row = {"speaker_codes": "0_002", "population": "strong_script", "source_text": "%w(200)"}
        frames = parse_preview_frames("%w(200)시스템%h%C(0_002)주인공%h%e", row)
        self.assertEqual(len(frames), 2)
        self.assertEqual((frames[0].placement, frames[0].profile_id), ("system", "system_message"))
        self.assertEqual((frames[1].placement, frames[1].profile_id), ("bottom", "system_message"))

    def test_window_height_profiles_follow_hundreds_digit(self) -> None:
        row = {"speaker_codes": "", "population": "strong_script", "component": "overlay"}
        two_line = parse_preview_frames("%w(300)첫째%r둘째%h%e", row)[0]
        one_line = parse_preview_frames("%w(400)한 줄%h%e", row)[0]
        self.assertEqual(two_line.profile_id, "dialogue")
        self.assertEqual(one_line.profile_id, "system_message")
        profiles, _overrides = load_profiles()
        self.assertEqual(profiles[two_line.profile_id].max_lines, 2)
        self.assertEqual(profiles[one_line.profile_id].max_lines, 1)

    def test_unwindowed_strong_item_text_uses_two_line_panel(self) -> None:
        row = {
            "speaker_codes": "", "population": "strong_script",
            "component": "arm9", "source_text": "%f(0)광석%h입수 위치%r설명%h%e",
        }
        frame = parse_preview_frames("%f(0)광석%h입수 위치%r설명%h%e", row)[1]
        self.assertEqual(frame.profile_id, "system_panel")

    def test_direct_overlay_width_decreases_with_window_unit(self) -> None:
        row = {"speaker_codes": "0_999", "population": "strong_script", "component": "overlay"}
        frame = parse_preview_frames("%w(007)%C(0_999)자막%h%e", row)[0]
        profiles, _overrides = load_profiles()
        profile = effective_profile_for_frame(profiles[frame.profile_id], frame)
        self.assertEqual(frame.profile_id, "direct_overlay")
        self.assertEqual(profile.max_columns, 12)

    def test_overflow_checks_columns_and_lines(self) -> None:
        profile = LayoutProfile("dialogue", "대화", 17, 2, "left", "test")
        row = {"speaker_codes": "0_002", "population": "strong_script", "source_text": ""}
        frame = parse_preview_frames("%C(0_002)" + "가" * 18 + "%r둘%r셋%h%e", row)[0]
        messages = [issue.message for issue in layout_issues(frame, profile)]
        self.assertTrue(any("줄 수" in message for message in messages))
        self.assertTrue(any("자 초과" in message for message in messages))

    def test_csv_roundtrip_preserves_protected_fields(self) -> None:
        headers = ["row_order", "text_id", "population", "scene_id", "component", "speaker_codes", "source_text", "translation_text", "translator_notes"]
        row = {
            "row_order": "1", "text_id": "test:1", "population": "strong_script",
            "scene_id": "overlay_001", "component": "arm9_overlay_001_file_001",
            "speaker_codes": "0_002", "source_text": "%C(0_002)원문%h%e",
            "translation_text": "%C(0_002)번역%h%e", "translator_notes": "",
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
                writer.writerow(row)
            document = DialogueDocument.load(path)
            document.rows[0]["translation_text"] = "%C(0_002)수정%h%e"
            document.save(path, make_backup=False)
            loaded = DialogueDocument.load(path)
            self.assertEqual(loaded.rows[0]["source_text"], row["source_text"])
            self.assertEqual(loaded.rows[0]["translation_text"], "%C(0_002)수정%h%e")


if __name__ == "__main__":
    unittest.main()
