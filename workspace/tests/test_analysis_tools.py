from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageFont


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analyze_font_sheet import analyze
from analyze_full_hangul_code_pool import (
    code_index_or_none,
    compact_ranges,
    parsed_code_indices,
    window_code_indices,
)
from analyze_game_font import index_to_sjis, sjis_to_index
from analyze_translation_relocation import (
    apply_alias_candidates,
    merge_zero_aligned_slots,
    pack_multiple_records,
    pack_records_into_regions,
    sorted_capacity_assignment,
)
from audit_translation_csv import (
    directive_sequence,
    encoded_length_and_unsupported,
    functional_directives_match,
    literal_text as audit_literal_text,
    malformed_percent_offsets,
)
from analyze_text_population import group_nearby, is_candidate_table, is_table_string
from analyze_text_references import find_all
from analyze_pack_resources import (
    classify_wrapper,
    compress_type1,
    decompress_type1,
    nul_cp932_candidates,
    parse_directory,
    validate_nonoverlap,
)
from build_dialogue_e2e import encode_literal, validate_selected_font
from build_translation_batch import validate_entry
from build_translation_relocation_poc import (
    compose_cluster_payloads,
    validate_nonoverlapping_writes,
)
from build_full_translation_qa import validate_mapping as validate_full_mapping
from build_graphics_pack_qa import import_canvas, nds_crc16
from build_graphics_review import priority, score_candidate
from build_galmuri7_system_labels_v23 import restore_selected_indices
from build_graphics_edit_canvases import (
    pack_indices,
    roll_tile_sequence,
    roll_x,
    unarrange_indices,
)
from build_font_poc import pack_16x16_2bpp, scale_2x, source_cell_size
from extract_script_candidates import (
    extract_component,
    extract_pointer_targeted_records,
    trim_binary_prefix,
)
from generate_hangul_mapping import allocate_mappings
from generate_full_hangul_mapping import allocate_full_mapping
from export_translation_csv import preview_text, scene_fields, speaker_fields
from extract_pack_graphics import (
    arrange_indices,
    classify_indexed_bitmap,
    decode_bgr555,
    indexed_bitmap_offsets,
    parse_head_texture_records,
    resolve_layout,
    unpack_indices,
)
from render_pack_index_previews import render_index_preview
from nds_inventory import NdsRom, diff_intervals
from normalize_translated_graphics import (
    downsample_uniform_blocks,
    map_greys_to_indices,
    rank_levels,
)
from refine_translated_graphics import (
    compose_delta,
    constrain_box_indices,
    draw_horizontal_lines,
    extract_delta,
    fill_box,
    fill_rows_from_reference,
    load_config,
    reference_rank_greys,
)
from build_opening_1948_font_poc import compose_text_mask, glyph_mask
from build_opening_1948_ttf_poc import compose_ttf_text_mask
from build_opening_haeong_graphics import (
    dilate_binary_mask,
    inside,
    validate_declared_indices,
)
from build_pfstardust_graphics import (
    changed_inside_boxes,
    remove_connected_components,
    restore_rows_from_reference,
)
from build_pfstardust_graphics_v20 import mask_origin_floor
from build_pfstardust_graphics_v21 import threshold_binary
from build_dunggeunmo_graphics_v22 import compose_mono_hinted_text_mask
from build_photo_command_p02_preview import pack_4bpp, unpack_4bpp
from dump_melonds_nds_memory import (
    GPU2D_STATE_SIZE,
    GPU3D_STATE_SIZE,
    parse_gpu2d_state,
    parse_gpu3d_render_state,
)


class AnalysisToolTests(unittest.TestCase):
    def test_gpu2d_register_parser_validates_and_reads_master_layout(self) -> None:
        data = bytearray(GPU2D_STATE_SIZE)
        struct.pack_into("<QI", data, 0, 0x12345678, 0)
        data[0x0C] = 1
        struct.pack_into("<I3I", data, 0x10, 0x40013F1D, 1, 2, 3)
        struct.pack_into("<4H", data, 0x24, 0x060F, 0x021D, 0x4112, 0x4007)
        parsed = parse_gpu2d_state(bytes(data), 0)
        self.assertEqual(parsed["disp_cnt"], "0x40013F1D")
        self.assertEqual(parsed["bg_cnt"], ["0x060F", "0x021D", "0x4112", "0x4007"])

    def test_gpu3d_render_parser_resolves_polygon_and_texture(self) -> None:
        base = 0x100000000
        data = bytearray(GPU3D_STATE_SIZE)
        struct.pack_into("<2Q", data, 0, 0x200000000, 0x300000000)
        struct.pack_into("<I", data, 0x1A6080, 1)
        polygon_offset = 0xC2060
        vertex_offset = 0x205C
        struct.pack_into("<Q", data, 0x1A2080, base + polygon_offset)
        struct.pack_into("<Q", data, polygon_offset, base + vertex_offset)
        struct.pack_into("<I", data, polygon_offset + 0x50, 1)
        struct.pack_into("<3I", data, polygon_offset + 0xA8, 0x40, 0x6D434540, 0x6A)
        struct.pack_into("<2I4iI", data, polygon_offset + 0xC0, 0, 0, 38, 70, 120, 248, 9)
        struct.pack_into("<2h", data, vertex_offset + 0x1C, 16, 32)
        struct.pack_into("<2i", data, vertex_offset + 0x24, 120, 38)
        parsed = parse_gpu3d_render_state(bytes(data), base)
        polygon = parsed["render_polygons"][0]
        self.assertEqual(polygon["texture_address"], "0x22A00")
        self.assertEqual(polygon["screen_bounds"], [120, 38, 248, 70])

    def test_photo_command_4bpp_pack_roundtrip(self) -> None:
        values = list(range(16)) * 2
        self.assertEqual(unpack_4bpp(pack_4bpp(values)), values)

    def test_selective_text_restore_preserves_transparency_and_border(self) -> None:
        reference = [0, 12, 1, 12, 0, 7]
        values = list(reference)
        rows, changed = restore_selected_indices(
            values,
            reference,
            6,
            [1, 0, 3, 1],
            [1, 0, 3, 1],
            "mode",
            {1},
            {0},
        )
        self.assertEqual(rows, [12])
        self.assertEqual(changed, {2})
        self.assertEqual(values, [0, 12, 12, 12, 0, 7])

    def test_opening_graphics_label_box_must_stay_inside_cleanup(self) -> None:
        self.assertTrue(inside([10, 10, 20, 10], [0, 0, 40, 30]))
        self.assertFalse(inside([35, 10, 10, 10], [0, 0, 40, 30]))

    def test_opening_graphics_declared_indices_must_exist_in_source(self) -> None:
        validate_declared_indices([0, 17, 239, 255], {239, 255}, "opening_why")
        with self.assertRaises(ValueError):
            validate_declared_indices([0, 17, 239, 255], {150, 255}, "opening_why")

    def test_opening_graphics_outline_is_one_pixel_eight_way_dilation(self) -> None:
        mask = Image.new("1", (1, 1), 1)
        outline = dilate_binary_mask(mask, 1)
        self.assertEqual(outline.size, (3, 3))
        self.assertEqual(set(outline.get_flattened_data()), {255})

    def test_pfstardust_full_icon_restore_clears_entire_registered_rows(self) -> None:
        reference = [
            9, 9, 9, 9,
            8, 1, 8, 8,
            7, 7, 1, 7,
        ]
        values = list(reference)
        restored = restore_rows_from_reference(
            values, reference, 4, [0, 1, 4, 2], [0, 1, 4, 2], "max"
        )
        self.assertEqual(restored, [8, 7])
        self.assertEqual(values[4:], [8, 8, 8, 8, 7, 7, 7, 7])

    def test_pfstardust_changes_must_stay_in_registered_boxes(self) -> None:
        self.assertTrue(changed_inside_boxes([5, 6, 9], 4, [[1, 1, 2, 2]]))
        self.assertFalse(changed_inside_boxes([5, 6, 12], 4, [[1, 1, 2, 2]]))

    def test_pfstardust_component_cleanup_preserves_connected_frame(self) -> None:
        # A three-pixel glyph sits inside the search box while the frame enters
        # it from outside. Only the isolated glyph may be erased.
        values = [0] * 35
        for position in (8, 9, 15, 19, 26, 33):
            values[position] = 255
        removed = remove_connected_components(
            values,
            7,
            5,
            [1, 1, 5, 3],
            0,
            8,
        )
        self.assertEqual(removed, [{"box": [1, 1, 2, 2], "pixel_count": 3}])
        self.assertEqual([values[position] for position in (8, 9, 15)], [0, 0, 0])
        self.assertEqual([values[position] for position in (19, 26, 33)], [255, 255, 255])

    def test_pfstardust_outline_origin_is_exactly_one_pixel_around_glyph(self) -> None:
        glyph = Image.new("1", (115, 21), 1)
        outline = Image.new("1", (117, 23), 1)
        glyph_origin = mask_origin_floor(glyph, (138.0, 116.0))
        outline_origin = mask_origin_floor(outline, (138.0, 116.0))
        self.assertEqual(glyph_origin, (80, 105))
        self.assertEqual(outline_origin, (79, 104))
        self.assertEqual(
            outline_origin,
            (glyph_origin[0] - 1, glyph_origin[1] - 1),
        )

    def test_small_text_lower_threshold_preserves_mid_coverage_strokes(self) -> None:
        greyscale = Image.new("L", (3, 1))
        greyscale.putdata([32, 64, 128])
        threshold_64 = threshold_binary(greyscale, 64)
        threshold_128 = threshold_binary(greyscale, 128)
        self.assertEqual(list(threshold_64.get_flattened_data()), [0, 255, 255])
        self.assertEqual(list(threshold_128.get_flattened_data()), [0, 0, 255])

    def test_dunggeunmo_mono_hinted_mask_is_binary_and_tight(self) -> None:
        mask = compose_mono_hinted_text_mask(
            ImageFont.load_default(), "DGM", 64
        )
        self.assertEqual(mask.mode, "1")
        self.assertEqual(mask.getbbox(), (0, 0, mask.width, mask.height))
        # Pillow exposes mode ``1`` samples as logical 0/1 values here.
        self.assertEqual(set(mask.get_flattened_data()), {0, 1})

    def test_opening_ttf_mask_is_binary_and_tight(self) -> None:
        mask = compose_ttf_text_mask(ImageFont.load_default(), "1948", 128)
        self.assertEqual(mask.mode, "1")
        self.assertIsNotNone(mask.getbbox())
        self.assertEqual(set(mask.get_flattened_data()), {0, 255})

    def test_opening_bitmap_atlas_glyph_and_scaled_text_mask(self) -> None:
        atlas = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
        atlas.putpixel((1, 1), (99, 207, 99, 255))
        atlas.putpixel((2, 1), (99, 207, 99, 255))
        atlas.putpixel((1, 2), (99, 207, 99, 255))
        mask = glyph_mask(
            atlas,
            "가",
            4,
            4,
            1,
            ord("가"),
            ord("가"),
            (99, 207, 99, 255),
        )
        self.assertEqual(mask.size, (2, 2))
        text = compose_text_mask(
            atlas,
            "가 가",
            (4, 4),
            1,
            ord("가"),
            ord("가"),
            (99, 207, 99, 255),
            2,
            space_width=1,
        )
        self.assertEqual(text.size, (10, 4))

    def test_translated_graphics_uniform_integer_downsample(self) -> None:
        source = Image.new("L", (4, 4))
        source.putdata(
            [10, 10, 20, 20, 10, 10, 20, 20, 30, 30, 40, 40, 30, 30, 40, 40]
        )
        values, scale = downsample_uniform_blocks(source, (2, 2))
        self.assertEqual(scale, 2)
        self.assertEqual(values, [10, 20, 30, 40])

    def test_translated_graphics_rank_mapping_quantizes_to_existing_indices(self) -> None:
        levels = rank_levels([3, 3, 9, 15])
        self.assertEqual(levels, {3: 0, 9: 128, 15: 255})
        mapped, errors = map_greys_to_indices([0, 120, 250], levels)
        self.assertEqual(mapped, [3, 9, 15])
        self.assertEqual(errors, [0, 8, 5])

    def test_translated_graphics_index_constraint_blocks_unrelated_palette_roles(self) -> None:
        levels = {0: 0, 14: 64, 235: 160, 255: 255}
        greys = [0, 70, 170, 250]
        mapped = [0, 14, 235, 255]
        errors = [0, 6, 10, 5]
        result = constrain_box_indices(
            mapped, errors, greys, 4, 1, [0, 0, 4, 1], levels, [0, 235, 255]
        )
        self.assertEqual(mapped, [0, 0, 235, 255])
        self.assertNotIn(14, result["indices_after"])
        self.assertEqual(result["allowed_indices"], [0, 235, 255])

    def test_translated_graphics_refine_fills_background_and_scales_text(self) -> None:
        values = [10] * 64
        values[3 * 8 + 3] = 250
        values[3 * 8 + 4] = 250
        delta, tight = extract_delta(values, 8, [2, 2, 4, 3], 10, "bright", 10)
        self.assertEqual(tight, [3, 3, 2, 1])
        fill_box(values, 8, [2, 2, 4, 3], 10)
        output_box = compose_delta(values, 8, 8, delta, [4, 4], 2, 10, "bright", 10)
        self.assertEqual(output_box, [2, 3, 4, 2])
        self.assertGreater(max(values), 10)

    def test_translated_graphics_refine_config_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "base.json").write_text(
                '{"schema_version":1,"canvases":{"a":{"value":1},"b":{"value":2,"keep":4}}}',
                encoding="utf-8",
            )
            (root / "child.json").write_text(
                '{"schema_version":1,"extends":"base.json","canvases":{"b":{"value":3}}}',
                encoding="utf-8",
            )
            config, chain = load_config(root / "child.json")
            self.assertEqual(config["canvases"]["a"]["value"], 1)
            self.assertEqual(config["canvases"]["b"]["value"], 3)
            self.assertEqual(config["canvases"]["b"]["keep"], 4)
            self.assertEqual(len(chain), 2)

    def test_translated_graphics_restores_each_row_from_reference_background(self) -> None:
        values = [10] * 24
        reference = [
            1, 2, 3, 4, 5, 6,
            7, 20, 8, 9, 10, 11,
            12, 13, 30, 14, 15, 16,
            17, 18, 19, 20, 21, 22,
        ]
        row_values = fill_rows_from_reference(
            values, 6, reference, [1, 1, 4, 2], [0, 1, 6, 2]
        )
        self.assertEqual(row_values, [20, 30])
        self.assertEqual(values[7:11], [20, 20, 20, 20])
        self.assertEqual(values[13:17], [30, 30, 30, 30])

    def test_translated_graphics_can_restore_rows_from_reference_median(self) -> None:
        values = [10] * 18
        reference = [
            1, 2, 3, 4, 5, 6,
            7, 18, 18, 182, 182, 164,
            12, 109, 109, 182, 182, 164,
        ]
        row_values = fill_rows_from_reference(
            values, 6, reference, [0, 1, 3, 2], [3, 1, 3, 2], "median"
        )
        self.assertEqual(row_values, [182, 182])
        self.assertEqual(values[6:9], [182, 182, 182])
        self.assertEqual(values[12:15], [182, 182, 182])

    def test_translated_graphics_reference_indices_are_rank_normalized(self) -> None:
        levels, greys = reference_rank_greys([9, 9, 12, 27])
        self.assertEqual(levels, {9: 0, 12: 128, 27: 255})
        self.assertEqual(greys, [0, 0, 128, 255])

    def test_translated_graphics_logo_lines_respect_text_halo(self) -> None:
        values = [255] * 15
        avoid = [0] * 15
        avoid[7] = 255
        written = draw_horizontal_lines(
            values,
            5,
            [{"box": [0, 1, 5, 1], "value": 180}],
            avoid,
        )
        self.assertEqual(written, 4)
        self.assertEqual(values[5:10], [180, 180, 255, 180, 180])

    def test_graphics_edit_canvas_horizontal_roll_is_reversible(self) -> None:
        source = list(range(16))
        normalized = roll_x(source, 8, 2, -3)
        self.assertEqual(normalized[:8], [3, 4, 5, 6, 7, 0, 1, 2])
        self.assertEqual(roll_x(normalized, 8, 2, 3), source)

    def test_graphics_edit_canvas_tiled8_roundtrip(self) -> None:
        storage = list(range(128))
        raster = arrange_indices(storage, 16, 8, "tiled8")
        self.assertEqual(unarrange_indices(raster, 16, 8, "tiled8"), storage)
        self.assertEqual(pack_indices(storage, 8), bytes(storage))

    def test_graphics_edit_canvas_tile_sequence_roll_is_reversible(self) -> None:
        storage = [value % 256 for value in range(64 * 12)]
        normalized = roll_tile_sequence(storage, -3)
        self.assertEqual(normalized[:64], storage[64 * 3 : 64 * 4])
        self.assertEqual(roll_tile_sequence(normalized, 3), storage)

    def test_palette_independent_index_preview_preserves_shape(self) -> None:
        with render_index_preview([7, 7, 19, 31], 2, 2) as image:
            self.assertEqual(image.mode, "L")
            self.assertEqual(list(image.get_flattened_data()), [0, 0, 128, 255])

    def test_graphics_review_candidate_scoring_is_priority_only(self) -> None:
        score, reasons = score_candidate(
            {"archive": "sys_bg", "resource": "push_start", "width": 128, "height": 32, "bpp": 4},
            {"unique_colors": 16, "dominant_fraction": 0.8},
        )
        self.assertEqual(priority(score), "high")
        self.assertIn("text_like_name+4", reasons)
        blank_score, _ = score_candidate(
            {"archive": "sys_bg", "resource": "logo", "width": 256, "height": 192, "bpp": 8},
            {"unique_colors": 1, "dominant_fraction": 1.0},
        )
        self.assertEqual(priority(blank_score), "low")

    def test_pack_directory_records_and_end_sentinel(self) -> None:
        record = bytearray(0x40)
        record[0:5] = b"asset"
        struct.pack_into("<II", record, 0x10, 0x20, 0x40)
        record[0x20:0x23] = b"END"
        struct.pack_into("<II", record, 0x30, 0xFFFFFFFF, 0xFFFFFFFF)
        rows, consumed = parse_directory(bytes(record))
        self.assertEqual(consumed, 0x40)
        self.assertEqual(rows[0].name, "asset")
        self.assertEqual((rows[0].offset, rows[0].size), (0x20, 0x40))

    def test_pack_range_validation(self) -> None:
        record = bytearray(0x40)
        record[0:5] = b"asset"
        struct.pack_into("<II", record, 0x10, 0x20, 0x40)
        record[0x20:0x23] = b"END"
        struct.pack_into("<II", record, 0x30, 0xFFFFFFFF, 0xFFFFFFFF)
        rows, _ = parse_directory(bytes(record))
        validate_nonoverlap(rows, 0x60)
        with self.assertRaises(ValueError):
            validate_nonoverlap(rows, 0x5F)

    def test_pack_wrapper_classification(self) -> None:
        stored = b"\x12\x3D\xDA\x00" + struct.pack("<III", 4, 0xFFFFFFFF, 0) + b"DATA"
        compressed = b"\x12\x3D\xDA\x01" + struct.pack("<III", 20, 3, 0) + b"ZIP"
        self.assertEqual(classify_wrapper(stored)["kind"], "stored")
        self.assertTrue(classify_wrapper(stored)["payload_size_matches_entry"])
        self.assertEqual(classify_wrapper(compressed)["kind"], "compressed")
        self.assertTrue(classify_wrapper(compressed)["payload_size_matches_entry"])
        self.assertEqual(classify_wrapper(b"raw")["kind"], "raw_or_unknown")

    def test_pack_type1_lzss_decoder(self) -> None:
        # Initial ring write position is FEFD, matching ARM9 0x02026B9C.
        payload = b"\x03AB" + struct.pack("<HB", 0xFEFD, 0)
        self.assertEqual(decompress_type1(payload, 6), b"ABABAB")
        with self.assertRaises(ValueError):
            decompress_type1(payload + b"X", 6)

    def test_pack_type1_lzss_encoder_uses_compatible_stream(self) -> None:
        source = b"ABABAB"
        payload = compress_type1(source)
        self.assertEqual(payload, b"\x03AB" + struct.pack("<HB", 0xFEFD, 0))
        self.assertEqual(decompress_type1(payload, len(source)), source)

    def test_pack_type1_lzss_encoder_roundtrips_long_window_data(self) -> None:
        source = (bytes(range(256)) * 260) + b"TAIL" * 80
        payload = compress_type1(source)
        self.assertEqual(decompress_type1(payload, len(source)), source)

    def test_nds_header_crc16_known_vector(self) -> None:
        self.assertEqual(nds_crc16(b"123456789"), 0x4B37)

    def test_pack_indexed_bitmap_contract(self) -> None:
        pixels = bytes([0x10, 0x32])
        palette = b"\0\0" * 16
        data = b"FLAG" + struct.pack("<HHII", 2, 2, len(pixels), len(palette)) + pixels + palette
        info = classify_indexed_bitmap(data)
        self.assertIsNotNone(info)
        self.assertEqual(info["bpp"], 4)
        self.assertEqual(unpack_indices(pixels, 4, 4), [0, 1, 2, 3])

    def test_pack_tiled8_arrangement(self) -> None:
        indices = list(range(128))
        arranged = arrange_indices(indices, 16, 8, "tiled8")
        self.assertEqual(arranged[:8], list(range(8)))
        self.assertEqual(arranged[8:16], list(range(64, 72)))
        self.assertEqual(arranged[16:24], list(range(8, 16)))

    def test_auto_graphics_layout_selects_tiled8_with_linear_fallback(self) -> None:
        self.assertEqual(resolve_layout("auto", 256, 192), "tiled8")
        self.assertEqual(resolve_layout("auto", 13, 7), "linear")
        self.assertEqual(resolve_layout("linear", 256, 192), "linear")

    def test_head_model_texture_record_contract(self) -> None:
        data = bytearray(0x120)
        data[:4] = b"HEAD"
        struct.pack_into("<H", data, 0xA6, 1)
        data[0xB0:0xB4] = b"TEX\0"
        record = 0xD0
        data[record : record + 4] = b"test"
        struct.pack_into("<IHH", data, record + 20, 4, 32, 16)
        struct.pack_into("<I", data, record + 32, 512)
        struct.pack_into("<I", data, record + 40, 512)
        rows = parse_head_texture_records(bytes(data))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["texture_name"], "test")
        self.assertEqual((rows[0]["width"], rows[0]["height"], rows[0]["bpp"]), (32, 16, 8))

    def test_bgr555_palette_decode(self) -> None:
        colors = decode_bgr555(struct.pack("<HHH", 0x001F, 0x03E0, 0x7C00))
        self.assertEqual(colors, [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)])

    def test_decoded_pack_cp932_candidate_classification(self) -> None:
        strong = "会話%w(1)%e".encode("cp932")
        weak = "画像".encode("cp932")
        rows = nul_cp932_candidates(strong + b"\0" + weak + b"\0")
        self.assertEqual([row[3] for row in rows], [
            "strong_terminal_script",
            "weak_nul_cp932",
        ])

    def test_diff_intervals(self) -> None:
        changed, intervals = diff_intervals(b"abcdef", b"abXdeY")
        self.assertEqual(changed, 2)
        self.assertEqual(intervals, [
            {"start": 2, "end": 3, "size": 1},
            {"start": 5, "end": 6, "size": 1},
        ])

    def test_minimal_fnt_and_fat(self) -> None:
        rom = bytearray(0x300)
        rom[0:12] = b"TESTROM\0\0\0\0\0"
        rom[12:16] = b"TSTJ"
        struct.pack_into("<II", rom, 0x40, 0x200, 14)
        struct.pack_into("<II", rom, 0x48, 0x220, 8)
        struct.pack_into("<II", rom, 0x200, 8, 0)
        struct.pack_into("<H", rom, 0x204, 0)
        struct.pack_into("<H", rom, 0x206, 1)
        rom[0x208:0x20E] = b"\x04test\x00"
        struct.pack_into("<II", rom, 0x220, 0x240, 0x244)
        rom[0x240:0x244] = b"DATA"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.nds"
            path.write_bytes(rom)
            parsed = NdsRom(path)
            self.assertEqual(parsed.paths, {0: "test"})
            self.assertEqual(parsed.fat, [(0x240, 0x244)])
            parsed_memory = NdsRom.from_bytes(bytes(rom), "test-memory.nds")
            self.assertEqual(parsed_memory.header, parsed.header)
            self.assertEqual(parsed_memory.paths, parsed.paths)
            self.assertEqual(parsed_memory.fat, parsed.fat)

    def test_font_grid(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "font.png"
            image = Image.new("RGBA", (16, 8), (0, 0, 0, 255))
            image.putpixel((0, 0), (255, 255, 255, 255))
            image.save(path)
            report = analyze(path, 8, 8)
            self.assertEqual(report["grid"]["cell_count"], 2)
            self.assertEqual(report["grid"]["empty_cell_indices"], [1])

    def test_sjis_font_index_roundtrip(self) -> None:
        for code in (0x8140, 0x82A0, 0x9547, 0x96B7, 0x98FC):
            self.assertEqual(index_to_sjis(sjis_to_index(code)), code)

    def test_full_font_code_domain_boundaries(self) -> None:
        self.assertEqual(code_index_or_none(0x81, 0x40, 4375), 0)
        self.assertEqual(code_index_or_none(0x81, 0x7F, 4375), None)
        self.assertEqual(code_index_or_none(0x98, 0x72, 4375), 4374)
        self.assertEqual(code_index_or_none(0x98, 0x73, 4375), None)

    def test_parsed_pairs_are_distinct_from_unaligned_windows(self) -> None:
        raw = bytes.fromhex("81408141")
        self.assertEqual(parsed_code_indices(raw, 4375), [0, 1])
        self.assertIn(code_index_or_none(0x40, 0x81, 4375), (None,))
        self.assertEqual(window_code_indices(raw, 4375), {0, 1})

    def test_compact_code_ranges(self) -> None:
        self.assertEqual(
            compact_ranges([0, 1, 3]),
            [
                {
                    "start_glyph_index": 0,
                    "end_glyph_index": 1,
                    "count": 2,
                    "start_code": "8140",
                    "end_code": "8141",
                },
                {
                    "start_glyph_index": 3,
                    "end_glyph_index": 3,
                    "count": 1,
                    "start_code": "8143",
                    "end_code": "8143",
                },
            ],
        )

    def test_script_candidate_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "overlay.bin"
            raw = "%f(0)テスト%h%e".encode("cp932")
            path.write_bytes(b"\0" + raw + b"\0")
            rows = extract_component("overlay", path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["offset"], 1)
            self.assertEqual(bytes.fromhex(rows[0]["raw_hex"]), raw)
            self.assertTrue(rows[0]["roundtrip_equal"])

    def test_script_binary_prefix_trim(self) -> None:
        script = "%v(1)テスト%e".encode("cp932")
        raw = b"g\x16\x02" + script
        offset, trimmed, text = trim_binary_prefix(0x100, raw, raw.decode("cp932"))
        self.assertEqual(offset, 0x103)
        self.assertEqual(trimmed, script)
        self.assertEqual(text, "%v(1)テスト%e")

    def test_pointer_targeted_script_recovery_without_leading_nul(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "overlay.bin"
            load = 0x02100000
            target = 8
            script = "その朝%w(60)%e".encode("cp932")
            path.write_bytes(struct.pack("<I", load + target) + b"ABCD" + script + b"\0")
            rows = extract_pointer_targeted_records("overlay", path, load, [])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["offset"], target)
            self.assertEqual(rows[0]["cp932"], "その朝%w(60)%e")
            self.assertEqual(rows[0]["discovery_pointer_offsets"], [0])

    def test_mapping_growth_preserves_existing_codes(self) -> None:
        candidates = [(0x9871, 4373), (0x9870, 4372), (0x986F, 4371)]
        existing = {
            "mappings": [
                {"character": "가", "code": "9871", "glyph_index": 4373},
                {"character": "나", "code": "9870", "glyph_index": 4372},
            ]
        }
        rows = allocate_mappings(["가", "다", "나"], candidates, existing)
        self.assertEqual([(r["character"], r["code"]) for r in rows], [
            ("가", "9871"), ("다", "986F"), ("나", "9870")
        ])

    def test_full_mapping_preserves_existing_pairs(self) -> None:
        selected = [
            {
                "code": "8142",
                "glyph_index": 2,
                "original_glyph": "all_zero",
                "cp932_status": "defined_in_cp932",
            },
            {
                "code": "9871",
                "glyph_index": 4373,
                "original_glyph": "nonzero",
                "cp932_status": "defined_in_cp932",
            },
            {
                "code": "8540",
                "glyph_index": 752,
                "original_glyph": "all_zero",
                "cp932_status": "undefined_in_cp932",
            },
        ]
        existing = {
            "mappings": [
                {"character": "가", "code": "9871", "glyph_index": 4373}
            ]
        }
        rows = allocate_full_mapping(["가", "나", "다"], selected, existing)
        self.assertEqual(
            [(row["character"], row["code"]) for row in rows],
            [("가", "9871"), ("나", "8142"), ("다", "8540")],
        )

    def test_translation_csv_context_fields(self) -> None:
        self.assertEqual(scene_fields("arm9_overlay_003_file_003")[:2], ("overlay_003", "3"))
        text = "%v(8)%C(0_002)パパは…%h%C(2_000)科学者だったわ%e"
        self.assertEqual(speaker_fields(text), (
            "0_002;2_000",
            "0_002>2_000",
            "multiple_unresolved_speaker_codes_in_record",
        ))
        self.assertEqual(preview_text(text), "パパは… ⏎ 科学者だったわ")

    def test_translation_csv_audit_helpers(self) -> None:
        text = "한글%w(60)%e"
        self.assertEqual(directive_sequence(text), ["%w(60)", "%e"])
        self.assertEqual(audit_literal_text(text), "한글")
        self.assertEqual(malformed_percent_offsets(text), [])
        self.assertEqual(malformed_percent_offsets("한글%"), [2])
        self.assertEqual(encoded_length_and_unsupported(text), (12, []))
        self.assertEqual(encoded_length_and_unsupported("한😀")[0], None)
        source = "%w(200)%f(0)%f(2)%f(0)%h%e"
        self.assertEqual(
            functional_directives_match(source, "%w(200)%f(0)%h%w(200)%f(2)%f(0)%h%e"),
            (True, True),
        )
        self.assertEqual(
            functional_directives_match(source, "%w(210)%f(0)%h%w(210)%f(2)%f(0)%h%e"),
            (False, True),
        )

    def test_existing_slot_capacity_assignment(self) -> None:
        rows = [
            {"text_id": "small", "source_capacity": 3, "translation_length": 4, "offset": 0},
            {"text_id": "large", "source_capacity": 6, "translation_length": 3, "offset": 8},
        ]
        assignment, failures = sorted_capacity_assignment(rows)
        self.assertEqual(failures, [])
        self.assertEqual([row["remaining_bytes"] for row in assignment], [0, 2])
        self.assertEqual(apply_alias_candidates("A·B"), "A・B")

    def test_multi_record_slot_packing(self) -> None:
        rows = [
            {"text_id": "a", "source_capacity": 2, "translation_length": 4, "offset": 0},
            {"text_id": "b", "source_capacity": 6, "translation_length": 2, "offset": 8},
        ]
        placements, failures = pack_multiple_records(rows)
        self.assertEqual(failures, [])
        self.assertEqual(len(placements), 2)

    def test_zero_aligned_slot_clusters(self) -> None:
        data = b"AAAA\0\0\0BBBB\0"
        intervals = [(0, 5, "a"), (7, 12, "b")]
        regions, borrowed = merge_zero_aligned_slots(data, intervals, [])
        self.assertEqual(len(regions), 1)
        self.assertEqual(borrowed, 2)
        rows = [
            {"text_id": "a", "translation_length": 7},
            {"text_id": "b", "translation_length": 3},
        ]
        placements, failures = pack_records_into_regions(rows, regions)
        self.assertEqual(failures, [])
        self.assertEqual(len(placements), 2)

    def test_compose_cluster_payloads(self) -> None:
        regions = [
            {"cluster_id": 1, "start": 10, "end": 20, "capacity": 10, "member_text_ids": ["a", "b"]}
        ]
        placements = [
            {"cluster_id": 1, "translation_text_id": "a", "target_offset": 10},
            {"cluster_id": 1, "translation_text_id": "b", "target_offset": 14},
        ]
        result = compose_cluster_payloads(regions, placements, {"a": b"ABC", "b": b"XY"})
        self.assertEqual(result[0]["payload"], b"ABC\0XY\0\0\0\0")

    def test_expected_writes_must_not_overlap(self) -> None:
        validate_nonoverlapping_writes(
            [
                {"owner": "a", "rom_offset": 10, "size": 4},
                {"owner": "b", "rom_offset": 14, "size": 2},
            ]
        )
        with self.assertRaises(ValueError):
            validate_nonoverlapping_writes(
                [
                    {"owner": "a", "rom_offset": 10, "size": 4},
                    {"owner": "b", "rom_offset": 13, "size": 2},
                ]
            )

    def test_poc_font_pack(self) -> None:
        source = [[3 for _x in range(8)] for _y in range(8)]
        target = scale_2x(source)
        packed = pack_16x16_2bpp(target)
        self.assertEqual(len(packed), 64)
        self.assertEqual(packed, b"\xFF" * 64)

    def test_supported_font_sheet_cell_sizes(self) -> None:
        self.assertEqual(source_cell_size((512, 1400)), 8)
        self.assertEqual(source_cell_size((1024, 2800)), 16)
        with self.assertRaises(ValueError):
            source_cell_size((100, 100))

    def test_auxiliary_table_classification(self) -> None:
        rows = []
        for index, text in enumerate(("船室", "甲板", "ジェシカ")):
            raw = text.encode("cp932")
            rows.append(
                {
                    "text_id": f"test:{index}",
                    "offset": index * 12,
                    "length": len(raw),
                    "kind": "nul_text",
                    "cp932": text,
                }
            )
        self.assertTrue(all(is_table_string(row) for row in rows))
        groups = group_nearby(rows)
        self.assertEqual(len(groups), 1)
        self.assertTrue(is_candidate_table(groups[0]))

    def test_auxiliary_table_rejects_code_like_text(self) -> None:
        row = {
            "text_id": "test:bad",
            "offset": 0,
            "length": 4,
            "kind": "nul_text",
            "cp932": "ﾀ羃",
        }
        self.assertFalse(is_table_string(row))

    def test_find_all_reference_offsets(self) -> None:
        self.assertEqual(find_all(b"ABABABA", b"ABA"), [0, 2, 4])

    def test_dialogue_e2e_literal_encoding(self) -> None:
        mapping = {
            char: code
            for char, code in zip("왜그러니애슐리야", range(0x9871, 0x9869, -1))
        }
        encoded = encode_literal("왜그러니、애슐리야", mapping)
        self.assertEqual(len(encoded), len("なあに、アシュレイ".encode("cp932")))
        self.assertEqual(encoded.hex().upper(), "98719870986F986E8141986D986C986B986A")

    def test_selected_font_hash_gate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            font = root / "font.png"
            config = root / "font.json"
            font.write_bytes(b"selected-font")
            import hashlib
            digest = hashlib.sha256(font.read_bytes()).hexdigest().upper()
            config.write_text(
                '{"selection_status":"approved_by_user_runtime_qa",'
                f'"source":{{"sha256":"{digest}"}}}}',
                encoding="utf-8",
            )
            self.assertEqual(validate_selected_font(font, config)["source"]["sha256"], digest)
            font.write_bytes(b"wrong-font")
            with self.assertRaises(ValueError):
                validate_selected_font(font, config)

    def test_full_qa_mapping_gate_uses_current_translation_demand(self) -> None:
        mapping = {
            "source_sha256": "6C8CB19DE7B86DC5F1147E4927D6916571968A06B5C2E9B88F62C22C271DD0A1",
            "translation_ssot_sha256": "CSV",
            "font_source_sha256": "FONT",
            "character_count": 1,
            "mappings": [{"character": "가", "code": "8140", "glyph_index": 0}],
        }
        with self.assertRaises(ValueError):
            validate_full_mapping(mapping, "CSV", "FONT", 825)
        self.assertEqual(
            validate_full_mapping(mapping, "CSV", "FONT", 1),
            {"가": 0x8140},
        )

    def test_batch_entry_protects_directives_and_length(self) -> None:
        source = {
            "text_id": "test:1",
            "raw_hex": "82A02577283630292565",
            "cp932": "あ%w(60)%e",
        }
        entry = {
            "text_id": "test:1",
            "source_raw_hex": source["raw_hex"],
            "source_text": source["cp932"],
            "translation_text": "한%w(60)%e",
        }
        self.assertEqual(validate_entry(source, entry, {"한": 0x9871}).hex().upper(), "98712577283630292565")
        entry["translation_text"] = "한%e%w(60)"
        with self.assertRaises(ValueError):
            validate_entry(source, entry, {"한": 0x9871})

    def test_palette_first_bitmap_offsets(self) -> None:
        header = struct.pack("<IHHII", 0x00100000, 8, 8, 32, 32)
        palette = bytes(range(32))
        pixels = bytes([0x11] * 32)
        decoded = header + palette + pixels
        info = classify_indexed_bitmap(decoded)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(indexed_bitmap_offsets(decoded, info), (48, 16))

    def test_palette_first_canvas_import_preserves_header_and_palette(self) -> None:
        header = struct.pack("<IHHII", 0x00100000, 8, 8, 32, 32)
        palette = bytes(range(32))
        pixels = bytes([0x11] * 32)
        decoded = header + palette + pixels
        config = {
            "canvas_id": "palette_first_test",
            "shift_x": "0",
            "shift_mode": "raster_rows",
            "crop_x": "0",
            "crop_y": "0",
            "crop_w": "8",
            "crop_h": "8",
        }
        catalog = {
            "resource_id": "test/palette_first",
            "width": "8",
            "height": "8",
            "bpp": "4",
            "selected_layout": "linear",
        }
        with tempfile.TemporaryDirectory() as folder:
            translated_path = Path(folder) / "translated.png"
            translated = Image.new("P", (8, 8))
            translated.putpalette([channel for value in range(256) for channel in (value, value, value)])
            translated.putdata([2] * 64)
            translated.save(translated_path)
            rebuilt, report = import_canvas(
                decoded, translated_path, config, catalog, allowed_indices={1, 2}
            )
        self.assertEqual(rebuilt[:16], header)
        self.assertEqual(rebuilt[16:48], palette)
        self.assertEqual(rebuilt[48:], bytes([0x22] * 32))
        self.assertEqual(report["payload_order"], "palette_first")
        self.assertEqual(report["pixel_range"], [48, 80])
        self.assertTrue(report["palette_preserved"])


if __name__ == "__main__":
    unittest.main()
