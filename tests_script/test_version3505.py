from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from tests_script.run_review_matrix import integration_sheet, module_sheet, write_xlsx
from xlsx2verilog import SCRIPT_RELEASE_DATE, SCRIPT_VERSION, generate


def slice_workbook(
    path: Path,
    rows: list[list[tuple[str, str] | None]],
    *,
    lc_outer: int = 3,
) -> None:
    internal_modules = [
        "module:LC 例化名:LC_A",
        "module:LC 例化名:LC_B",
        "module:OE",
    ]
    integration = integration_sheet(
        [
            (
                [
                    "TOP",
                    "module:LC 例化名:LC_A",
                    "module:LC 例化名:LC_B",
                    "module:OE",
                ],
                [[("clk", "i"), ("clk", "i"), ("clk", "i"), ("clk", "i")]],
            ),
            (
                internal_modules,
                rows,
            ),
        ]
    )
    write_xlsx(
        path,
        [
            ("集成", integration),
            ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
            (
                "LC",
                module_sheet(
                    "LC",
                    [
                        ("clk", 1, None, "i"),
                        ("tx_fifo", "2*2", "2*2", "i", lc_outer, lc_outer),
                    ],
                ),
            ),
            (
                "OE",
                module_sheet(
                    "OE",
                    [
                        ("clk", 1, None, "i"),
                        ("tx_fifo", "2*2", "2*2", "o", 6, 6),
                    ],
                ),
            ),
        ],
    )


class Version3505PortSliceTests(unittest.TestCase):
    def generated_top(
        self,
        rows: list[list[tuple[str, str] | None]],
        *,
        lc_outer: int = 3,
    ) -> tuple[str, object]:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        workbook = root / "slice.xlsx"
        slice_workbook(workbook, rows, lc_outer=lc_outer)
        paths, reporter = generate(workbook, root / "generated")
        text = ""
        if paths:
            text = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
        return text, reporter

    def test_version_is_3505(self) -> None:
        self.assertEqual("Version V3.5.05", SCRIPT_VERSION)
        self.assertEqual("2026.9.4", SCRIPT_RELEASE_DATE)

    def test_one_output_is_partitioned_between_two_named_instances(self) -> None:
        top, reporter = self.generated_top(
            [
                [("tx_fifo", "i"), None, ("tx_fifo[2:0]", "o")],
                [None, ("tx_fifo", "i"), ("tx_fifo[5:3]", "o")],
            ]
        )
        self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
        self.assertEqual(1, len(re.findall(r"\bw_tx_fifo\s*;", top)))
        self.assertRegex(
            top,
            r"LC LC_A \([\s\S]*?\.tx_fifo\s+\(w_tx_fifo\[2:0\]\s*\)",
        )
        self.assertRegex(
            top,
            r"LC LC_B \([\s\S]*?\.tx_fifo\s+\(w_tx_fifo\[5:3\]\s*\)",
        )
        self.assertRegex(
            top,
            r"OE U_OE \([\s\S]*?\.tx_fifo\s+\(w_tx_fifo\s*\)",
        )
        self.assertFalse(
            any(item.code == "W_PORT_SLICE_WIDTH" for item in reporter.items)
        )

    def test_indexed_plus_slice_is_normalized_and_connected(self) -> None:
        top, reporter = self.generated_top(
            [[("tx_fifo", "i"), None, ("tx_fifo[3+:3]", "o")]]
        )
        self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
        self.assertRegex(top, r"\.tx_fifo\s+\(w_tx_fifo\[3 \+: 3\]\s*\)")

    def test_two_sided_slices_and_na_zero_build_one_full_input(self) -> None:
        top, reporter = self.generated_top(
            [
                [("tx_fifo[1:0]", "i"), None, ("tx_fifo[4:3]", "o")],
                [("tx_fifo[2]", "i"), None, ("NA->0", "")],
            ]
        )
        self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
        self.assertRegex(
            top,
            r"LC LC_A \([\s\S]*?\.tx_fifo\s+"
            r"\(\{\{2\*2\{1'b0\}\}, w_tx_fifo\[4:3\]\}\s*\)",
        )
        lc_a = re.search(r"LC LC_A \((?P<body>[\s\S]*?)\n\);", top)
        self.assertIsNotNone(lc_a)
        assert lc_a is not None
        self.assertEqual(1, lc_a.group("body").count(".tx_fifo"))

    def test_source_bit_and_range_build_one_full_input(self) -> None:
        top, reporter = self.generated_top(
            [
                [("tx_fifo[1:0]", "i"), None, ("tx_fifo[4:3]", "o")],
                [("tx_fifo[2]", "i"), None, ("tx_fifo[0]", "o")],
            ]
        )
        self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
        self.assertRegex(
            top,
            r"\.tx_fifo\s+\(\{w_tx_fifo\[0\], w_tx_fifo\[4:3\]\}\s*\)",
        )
        self.assertEqual(1, len(re.findall(r"\bw_tx_fifo\s*;", top)))

    def test_uncovered_input_slice_is_automatically_zero_filled(self) -> None:
        top, reporter = self.generated_top(
            [[("tx_fifo[1:0]", "i"), None, ("tx_fifo[4:3]", "o")]]
        )
        self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
        self.assertRegex(
            top,
            r"\.tx_fifo\s+"
            r"\(\{\{2\*2\{1'b0\}\}, w_tx_fifo\[4:3\]\}\s*\)",
        )
        self.assertTrue(any(item.code == "I_PORT_SLICE" for item in reporter.items))

    def test_narrow_source_slice_is_padded_before_full_input_connection(self) -> None:
        top, reporter = self.generated_top(
            [[("tx_fifo", "i"), None, ("tx_fifo[4:3]", "o")]]
        )
        self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
        self.assertRegex(
            top,
            r"\.tx_fifo\s+\(\{4'b0, w_tx_fifo\[4:3\]\}\s*\)",
        )
        self.assertTrue(
            any(item.code == "W_PORT_SLICE_WIDTH" for item in reporter.items)
        )

    def test_wide_source_slice_is_flattened_before_truncation(self) -> None:
        top, reporter = self.generated_top(
            [[("tx_fifo", "i"), None, ("tx_fifo[4:3]", "o")]],
            lc_outer=1,
        )
        self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
        self.assertRegex(
            top,
            r"wire\s+\[2\*2\*2\s+-1:0\]\s+w_tx_fifo_\d+_slice_adapter;",
        )
        self.assertRegex(
            top,
            r"assign w_tx_fifo_\d+_slice_adapter\s+= w_tx_fifo\[4:3\];",
        )
        self.assertRegex(
            top,
            r"\.tx_fifo\s+\(w_tx_fifo_\d+_slice_adapter\[4 -1:0\]\s*\)",
        )

    def test_out_of_bounds_and_overlapping_destination_slices_are_errors(self) -> None:
        _, out_of_bounds = self.generated_top(
            [[("tx_fifo", "i"), None, ("tx_fifo[6:4]", "o")]]
        )
        self.assertTrue(out_of_bounds.has_errors)
        self.assertTrue(any(item.code == "E_PORT_SLICE" for item in out_of_bounds.items))

        _, overlap = self.generated_top(
            [
                [("tx_fifo[2:1]", "i"), None, ("tx_fifo[4:3]", "o")],
                [("tx_fifo[1:0]", "i"), None, ("tx_fifo[2:1]", "o")],
            ]
        )
        self.assertTrue(overlap.has_errors)
        self.assertTrue(
            any("目标切片" in item.message and "重叠" in item.message for item in overlap.items)
        )

    def test_reversed_range_and_zero_indexed_width_are_errors(self) -> None:
        _, reversed_range = self.generated_top(
            [[("tx_fifo", "i"), None, ("tx_fifo[0:2]", "o")]]
        )
        self.assertTrue(reversed_range.has_errors)
        self.assertTrue(
            any(item.code == "E_PORT_SLICE" for item in reversed_range.items)
        )

        _, zero_width = self.generated_top(
            [[("tx_fifo", "i"), None, ("tx_fifo[3+:0]", "o")]]
        )
        self.assertTrue(zero_width.has_errors)
        self.assertTrue(any(item.code == "E_PORT_SLICE" for item in zero_width.items))


if __name__ == "__main__":
    unittest.main()
