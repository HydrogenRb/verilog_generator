from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests_script.run_review_matrix import (
    integration_sheet,
    module_sheet,
    write_xlsx,
)
from xlsx2verilog import generate


ROOT = Path(__file__).resolve().parents[1]
WIDTH_BOUNDARY_SAMPLE = (
    ROOT
    / "review_test_cases"
    / "17_v3_techreview2_width_boundary"
    / "width_boundary.xlsx"
)


class Version3TechReview2Tests(unittest.TestCase):
    def test_zero_width_dimensions_generate_with_explicit_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "zero-width.xlsx"
            write_xlsx(
                workbook,
                [
                    (
                        "ZERO_WIDTH",
                        module_sheet(
                            "ZERO_WIDTH",
                            [
                                ("zero_literal", 0, None, "o"),
                                ("zero_expression", "(1-1)", None, "i"),
                                ("zero_macro", "`off_width", 0, "i"),
                                ("zero_parameter", "off_param", 0, "i"),
                                ("zero_array", 1, None, "o", 0, None),
                            ],
                        ),
                    )
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual([path.name for path in paths], ["zero_width.v"])
            zero_warnings = [
                item for item in reporter.items if item.code == "W_ZERO_WIDTH"
            ]
            self.assertEqual(len(zero_warnings), 5)

            text = paths[0].read_text(encoding="utf-8")
            self.assertRegex(text, r"output wire\s+\[0\s+-1:0\]\s+zero_literal")
            self.assertRegex(text, r"input\s+wire\s+\[0\s+-1:0\]\s+zero_expression")
            self.assertRegex(text, r"input\s+wire\s+\[`off_width\s+-1:0\]\s+zero_macro")
            self.assertRegex(text, r"input\s+wire\s+\[OFF_PARAM\s+-1:0\]\s+zero_parameter")
            self.assertRegex(text, r"output wire\s+\[0\s+-1:0\]\s+zero_array")
            self.assertRegex(text, r"parameter\s+OFF_PARAM\s+= 0")
            self.assertRegex(text, r"(?m)^// `define off_width\s+0$")
            self.assertRegex(text, r"(?m)^assign zero_literal\s+= '0;$")

    def test_na_constants_expand_pad_and_warn_when_oversized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "na-constants.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [
                            [("wide", "o"), ("NA->8'hFF", "")],
                            [("narrow", "o"), ("NA->8'hFF", "")],
                            [("ones", "o"), ("NA->1", "")],
                            [("zeros", "o"), ("NA->0", "")],
                        ],
                    )
                ]
            )
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    (
                        "TOP",
                        module_sheet(
                            "TOP",
                            [
                                ("wide", 16, None, "o"),
                                ("narrow", 4, None, "o"),
                                ("ones", "B", 4, "o", "A", 3),
                                ("zeros", 8, None, "o"),
                            ],
                        ),
                    ),
                    ("CHILD", module_sheet("CHILD", [("unused", 1, None, "i")])),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual({path.name for path in paths}, {"top.v", "child.v"})
            width_warnings = [
                item
                for item in reporter.items
                if item.code == "W_NA_CONSTANT_WIDTH"
            ]
            self.assertEqual(len(width_warnings), 1)
            self.assertIn("TOP.narrow", width_warnings[0].message)
            self.assertIn("声明位宽 8 大于目标总位宽 4", width_warnings[0].message)

            text = (root / "generated" / "top.v").read_text(encoding="utf-8")
            self.assertRegex(
                text,
                r"(?m)^assign wide\s+= \{\{8\{1'b0\}\}, 8'hFF\};$",
            )
            self.assertRegex(text, r"(?m)^assign narrow\s+= 8'hFF;$")
            self.assertRegex(text, r"(?m)^assign ones\s+= \{A\*B\{1'b1\}\};$")
            self.assertRegex(text, r"(?m)^assign zeros\s+= \{8\{1'b0\}\};$")

    def test_real_width_boundary_sample_covers_all_resize_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "generated"
            paths, reporter = generate(WIDTH_BOUNDARY_SAMPLE, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual(len(paths), 4)
            counts = {
                code: sum(item.code == code for item in reporter.items)
                for code in (
                    "W_ZERO_WIDTH",
                    "W_NA_CONSTANT_WIDTH",
                    "W_WIDTH_MISMATCH",
                )
            }
            self.assertEqual(
                counts,
                {
                    "W_ZERO_WIDTH": 1,
                    "W_NA_CONSTANT_WIDTH": 1,
                    "W_WIDTH_MISMATCH": 8,
                },
            )

            text = (output / "width_review.v").read_text(encoding="utf-8")
            self.assertIn(".in4      (to_narrow[4 -1:0]", text)
            self.assertIn(".in8      ({{4{1'b0}}, to_wide}", text)
            self.assertIn("assign from_narrow[8-1:4] = '0;", text)
            self.assertIn(
                "assign from_wide          = "
                "w_out8_adapter[4 -1:0];",
                text,
            )
            self.assertIn("assign w_src3[5-1:3]      = '0;", text)
            self.assertIn(".sink2      (w_src9[2 -1:0]", text)
            self.assertIn(".param_sink (w_param_src", text)
            self.assertIn(".multi_sink (w_multi_src", text)


if __name__ == "__main__":
    unittest.main()
