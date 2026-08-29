from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autocad_pdf_direct_pipeline import (
    DimensionCalibrationError,
    calc_global_scale,
    collect_dimension_intersection_pairs,
    deduplicate_lines,
    filter_autocad_texts,
    generate_lsp,
    measure_candidates_from_text,
    normalize_ocr_number,
    pick_median_scale,
    scale_lines,
    scale_texts,
)


class DimensionCalibrationTests(unittest.TestCase):
    def test_exact_and_reversed_duplicates_are_removed_only(self) -> None:
        lines = [
            ((0.0, 0.0), (10.0, 0.0)),
            ((10.0, 0.0), (0.0, 0.0)),
            ((0.0, 0.0), (10.0001, 0.0)),
        ]
        self.assertEqual(deduplicate_lines(lines), [lines[0], lines[2]])

    def test_horizontal_measurement_uses_adjacent_perpendicular_lines(self) -> None:
        lines = [
            ((0.0, 10.0), (100.0, 10.0)),
            ((10.0, 0.0), (10.0, 20.0)),
            ((90.0, 0.0), (90.0, 20.0)),
        ]
        candidates = measure_candidates_from_text(lines, 50.0, 20.0, "horizontal")
        self.assertEqual(candidates[0][1], 80.0)
        self.assertEqual(candidates[0][2], "dimension_intersection_horizontal_adjacent")

    def test_three_consistent_intersections_produce_median_scale(self) -> None:
        lines = [
            ((0.0, 10.0), (100.0, 10.0)),
            ((10.0, 0.0), (10.0, 20.0)),
            ((90.0, 0.0), (90.0, 20.0)),
            ((0.0, 40.0), (150.0, 40.0)),
            ((20.0, 30.0), (20.0, 50.0)),
            ((120.0, 30.0), (120.0, 50.0)),
            ((0.0, 70.0), (200.0, 70.0)),
            ((30.0, 60.0), (30.0, 80.0)),
            ((180.0, 60.0), (180.0, 80.0)),
        ]
        texts = [
            (50.0, 20.0, "800", 1.0),
            (70.0, 50.0, "1000", 1.0),
            (105.0, 80.0, "1500", 1.0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            scale, reason, nums = calc_global_scale(
                lines,
                texts,
                Path(tmp) / "measurements.txt",
                require_valid_scale=True,
            )
        self.assertEqual(scale, 10.0)
        self.assertIn("count=3", reason)
        self.assertEqual(len(nums), 3)

    def test_strict_mode_rejects_missing_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DimensionCalibrationError):
                calc_global_scale(
                    [((0.0, 0.0), (10.0, 0.0))],
                    [],
                    Path(tmp) / "measurements.txt",
                    require_valid_scale=True,
                )

    def test_one_geometric_span_cannot_count_as_three_dimensions(self) -> None:
        lines = [
            ((0.0, 10.0), (100.0, 10.0)),
            ((10.0, 0.0), (10.0, 20.0)),
            ((90.0, 0.0), (90.0, 20.0)),
        ]
        nums = [
            (40.0, 20.0, 800.0, "horizontal"),
            (50.0, 20.0, 800.0, "horizontal"),
            (60.0, 20.0, 800.0, "horizontal"),
        ]
        pairs = collect_dimension_intersection_pairs(lines, nums)
        self.assertEqual(len({pair[5] for pair in pairs}), 1)
        self.assertIsNone(pick_median_scale(pairs))

    def test_scale_is_embedded_in_new_coordinates_without_cad_scale_command(self) -> None:
        lines = [((1.0, 2.0), (3.0, 4.0))]
        texts = [(5.0, 6.0, "800", 2.0)]
        self.assertEqual(scale_lines(lines, 10.0), [((10.0, 20.0), (30.0, 40.0))])
        self.assertEqual(scale_texts(texts, 10.0), [(50.0, 60.0, "800", 20.0)])
        lsp = generate_lsp(lines, texts, 10.0, delete_existing=False)
        self.assertIn("(codex-line 10.000000 20.000000 30.000000 40.000000)", lsp)
        self.assertNotIn("_.SCALE", lsp)

    def test_dimension_mode_prefers_ocr_over_font_encoded_pdf_text(self) -> None:
        lines = [
            ((0.0, 10.0), (100.0, 10.0)),
            ((10.0, 0.0), (10.0, 20.0)),
            ((90.0, 0.0), (90.0, 20.0)),
            ((0.0, 40.0), (150.0, 40.0)),
            ((20.0, 30.0), (20.0, 50.0)),
            ((120.0, 30.0), (120.0, 50.0)),
            ((0.0, 70.0), (200.0, 70.0)),
            ((30.0, 60.0), (30.0, 80.0)),
            ((180.0, 60.0), (180.0, 80.0)),
        ]
        encoded_text = [(0.0, 0.0, "39", 1.0)]
        ocr_nums = [
            (50.0, 20.0, 800.0, "horizontal"),
            (70.0, 50.0, 1000.0, "horizontal"),
            (105.0, 80.0, 1500.0, "horizontal"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "autocad_pdf_direct_pipeline.ocr_numeric_texts",
                return_value=(ocr_nums, "ocr_candidates=3"),
            ) as ocr:
                scale, reason, nums = calc_global_scale(
                    lines,
                    encoded_text,
                    Path(tmp) / "measurements.txt",
                    pdf_path=Path(tmp) / "fixture.pdf",
                    use_ocr=True,
                    out_dir=Path(tmp),
                    require_valid_scale=True,
                )
        ocr.assert_called_once()
        self.assertEqual(scale, 10.0)
        self.assertIn("source=ocr", reason)
        self.assertEqual(nums, ocr_nums)

    def test_ocr_rejects_room_metadata_wrapped_around_numbers(self) -> None:
        self.assertEqual(normalize_ocr_number("17225"), "17225")
        self.assertIsNone(normalize_ocr_number("周长: 17225"))
        self.assertIsNone(normalize_ocr_number("3163面积: 17.24m2"))

    def test_autocad_text_filter_drops_font_encoded_control_text(self) -> None:
        texts = [
            (0.0, 0.0, "Room 101", 1.0),
            (0.0, 0.0, "\x14\x13\x13", 1.0),
            (0.0, 0.0, "中文", 1.0),
        ]
        self.assertEqual(filter_autocad_texts(texts), [texts[0]])


if __name__ == "__main__":
    unittest.main()
