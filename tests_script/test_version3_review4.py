from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests_script.run_review_matrix import (
    integration_sheet,
    module_sheet,
    set_cell,
    write_xlsx,
)
from xlsx2verilog import SCRIPT_VERSION, generate


def parameter_row(rows: list[list[object]], row: int, name: str, value: object) -> None:
    set_cell(rows, row, 1, "parameter")
    set_cell(rows, row, 2, name)
    set_cell(rows, row, 4, value)


class Version3TechReview4Tests(unittest.TestCase):
    def test_v34_zero_rendering_unifies_blank_and_explicit_na_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "zero-unified.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [[("top_zero", "o"), ("NA->0", "")]],
                    ),
                    (["CHILD"], [[("blank_in", "i")]]),
                ]
            )
            top_rows = module_sheet("TOP", [("top_zero", "TOP_W", 8, "o")])
            parameter_row(top_rows, 4, "TOP_W", 8)
            child_rows = module_sheet(
                "CHILD", [("blank_in", "CHILD_W", 6, "i")]
            )
            parameter_row(child_rows, 4, "CHILD_W", 6)
            write_xlsx(
                workbook,
                [("集成", integration), ("TOP", top_rows), ("CHILD", child_rows)],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(top, r"assign top_zero\s+= \{TOP_W\{1'b0\}\};")
            self.assertRegex(top, r"\.blank_in\s+\(\{CHILD_W\{1'b0\}\}\s*\)")

    def test_parameter_width_sources_are_traced_without_numeric_freezing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-source.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [
                            [("NA->from_macro", ""), ("from_macro", "o")],
                            [("NA->from_parameter", ""), ("from_parameter", "o")],
                            [("NA->from_number", ""), ("from_number", "o")],
                            [("NA->from_top_number", ""), ("from_top_number", "o")],
                            [("TOP_MAC", ""), ("P_MAC", "")],
                            [("TOP_PARAM", ""), ("P_TOP", "")],
                            [("TOP_LITERAL", ""), ("P_TOP_NUM", "")],
                            [None, ("P_NUM", "5")],
                            [None, ("P_DIRECT", "`DirectOverride")],
                        ],
                    )
                ]
            )
            # Parameter classification starts only at the parameter rows.
            set_cell(integration, 7, 1, "parameter")
            top_rows = module_sheet("TOP", [])
            parameter_row(top_rows, 3, "OTHER_PARAM", 6)
            parameter_row(top_rows, 4, "TOP_MAC", 8)
            set_cell(top_rows, 4, 3, "`GlobalWidth")
            parameter_row(top_rows, 5, "TOP_PARAM", 6)
            set_cell(top_rows, 5, 3, "OTHER_PARAM")
            parameter_row(top_rows, 6, "TOP_LITERAL", 4)
            child_rows = module_sheet(
                "CHILD",
                [
                    ("from_macro", "P_MAC", 8, "o"),
                    ("from_parameter", "P_TOP", 6, "o"),
                    ("from_number", "P_NUM", 5, "o"),
                    ("from_top_number", "P_TOP_NUM", 4, "o"),
                ],
            )
            parameter_row(child_rows, 7, "P_MAC", 8)
            parameter_row(child_rows, 8, "P_TOP", 6)
            parameter_row(child_rows, 9, "P_TOP_NUM", 4)
            parameter_row(child_rows, 10, "P_NUM", 5)
            parameter_row(child_rows, 11, "P_DIRECT", 9)
            write_xlsx(
                workbook,
                [("集成", integration), ("TOP", top_rows), ("CHILD", child_rows)],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(top, r"wire\s+\[`GlobalWidth\s+-1:0\]\s+from_macro;")
            self.assertRegex(top, r"wire\s+\[TOP_PARAM\s+-1:0\]\s+from_parameter;")
            self.assertRegex(top, r"wire\s+\[P_NUM\s+-1:0\]\s+from_number;")
            self.assertRegex(
                top,
                r"wire\s+\[P_TOP_NUM\s+-1:0\]\s+from_top_number;",
            )
            self.assertRegex(top, r"(?m)^localparam P_NUM\s+= 5;$")
            self.assertRegex(
                top, r"(?m)^localparam P_TOP_NUM\s+= TOP_LITERAL;$"
            )
            self.assertRegex(top, r"\.P_NUM\s+\(5\s*\)")
            self.assertRegex(
                top, r"\.P_TOP_NUM\s+\(TOP_LITERAL\s*\)"
            )
            self.assertRegex(
                top, r"\.P_DIRECT\s+\(`DirectOverride\s*\)"
            )

    def test_parameter_na_creates_body_localparams_and_accepts_macro_io(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-na.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [
                            [("clk", "i"), ("clk", "i")],
                            [None, None],
                            [None, None],
                            [None, None],
                        ],
                    ),
                    (
                        ["CHILD", ""],
                        [
                            [None, None],
                            [("P_A", ""), ("NA->A", "`LocalDefault")],
                            [("P_DEFAULT", ""), ("NA->DEFAULT_VALUE", "")],
                            [None, None],
                        ],
                    ),
                    (
                        ["CHILD", ""],
                        [
                            [None, None],
                            [None, None],
                            [("P_B", ""), ("NA[i]->B", "")],
                            [None, None],
                        ],
                    ),
                    (
                        ["CHILD", ""],
                        [
                            [None, None],
                            [None, None],
                            [None, None],
                            [("P_C", ""), ("NA->514", "")],
                        ],
                    ),
                ]
            )
            set_cell(integration, 4, 7, "parameter")
            set_cell(integration, 5, 13, "parameter")
            set_cell(integration, 6, 19, "parameter")
            set_cell(integration, 1, 26, "模块名")
            set_cell(integration, 1, 27, "例化名")
            set_cell(integration, 1, 28, "例化次数")
            set_cell(integration, 2, 26, "CHILD")
            set_cell(integration, 2, 27, "U_CHILD_ARRAY")
            set_cell(integration, 2, 28, 3)
            child_rows = module_sheet("CHILD", [("clk", 1, None, "i")])
            parameter_row(child_rows, 4, "P_A", 7)
            parameter_row(child_rows, 5, "P_B", 3)
            parameter_row(child_rows, 6, "P_C", 1)
            parameter_row(child_rows, 7, "P_DEFAULT", 114)
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("CHILD", child_rows),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(top, r"(?m)^localparam A\s+= `LocalDefault;$")
            self.assertRegex(
                top,
                r"(?m)^localparam \[3 -1:0\]\[32 -1:0\] B\s+"
                r"= '\{default: 3\};$",
            )
            self.assertRegex(top, r"(?m)^localparam P_C\s+= 514;$")
            self.assertRegex(top, r"(?m)^localparam DEFAULT_VALUE\s+= 114;$")
            self.assertRegex(top, r"\.P_A\s+\(A\s*\)")
            self.assertRegex(top, r"\.P_B\s+\(B\[i\]\s*\)")
            self.assertRegex(top, r"\.P_C\s+\(P_C\s*\)")
            self.assertRegex(
                top, r"\.P_DEFAULT\s+\(DEFAULT_VALUE\s*\)"
            )

    def test_module_label_comment_wraps_only_the_generated_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "commented-module.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["module:TOP", "module:CHILD *注释*"],
                        [[("clk", "i"), ("clk", "i")]],
                    )
                ]
            )
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("CHILD", module_sheet("CHILD", [("clk", 1, None, "i")])),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertEqual(SCRIPT_VERSION, "Version V3.5.03")
            self.assertFalse(reporter.has_errors)
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            begin = top.index("/* XLSX2VERILOG COMMENTED MODULE BEGIN: CHILD")
            instance = top.index("CHILD U_CHILD (", begin)
            end = top.index("XLSX2VERILOG COMMENTED MODULE END: CHILD */", instance)
            self.assertLess(begin, instance)
            self.assertLess(instance, end)
            self.assertIn("module CHILD", (root / "generated" / "child.v").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
