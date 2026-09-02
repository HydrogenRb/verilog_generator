from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests_script.run_review_matrix import (
    integration_sheet,
    module_sheet,
    set_cell,
    write_xlsx,
)
from xlsx2verilog import SCRIPT_VERSION, build_parser, generate


class Version350Tests(unittest.TestCase):
    def test_version_define_sheet_after_statement_and_module_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "define.xlsx"
            define_rows: list[list[object]] = []
            set_cell(define_rows, 1, 1, "宏名")
            set_cell(define_rows, 1, 2, "数值")
            set_cell(define_rows, 2, 1, "CentralWidth")
            set_cell(define_rows, 2, 2, 16)
            module_rows = module_sheet(
                "ONLY", [("data", "`CentralWidth", None, "o")]
            )
            set_cell(module_rows, 4, 1, "parameter")
            set_cell(module_rows, 4, 2, "COUNT")
            set_cell(module_rows, 4, 4, 3)
            write_xlsx(workbook, [("define", define_rows), ("ONLY", module_rows)])

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
            self.assertEqual("Version V3.5.00", SCRIPT_VERSION)
            text = paths[0].read_text(encoding="utf-8")
            self.assertIn("// `define CentralWidth 16", text)
            self.assertRegex(text, r"parameter\s+COUNT\s+= 3")
            self.assertNotRegex(text, r"module ONLY #\([\s\S]*?localparam")
            self.assertLess(
                text.index("/*USER CODE BEGIN after statement*/"),
                text.index("endmodule"),
            )

    def test_only_top_and_fixed_generate_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "top-only.xlsx"
            integration = integration_sheet(
                [(["TOP", "CHILD"], [[("bus[i]", "o"), ("bit_in", "i")]])]
            )
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("bus", 2, None, "o")])),
                    ("CHILD", module_sheet("CHILD", [("bit_in", 1, None, "i")])),
                ],
            )
            with patch("xlsx2verilog.ONLY_TOP", True):
                paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual(["top.v"], [path.name for path in paths])
            text = paths[0].read_text(encoding="utf-8")
            self.assertEqual(1, text.count("genvar i;"))
            self.assertIn("for (i = 0;", text)
            self.assertLess(
                text.index("/*USER CODE END   after statement*/"),
                text.index("/*USER CODE BEGIN before CHILD*/"),
            )

    def test_parameter_shape_warns_and_optional_multidimensional_zero_fills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-width.xlsx"
            integration = integration_sheet(
                [(["TOP", "CHILD"], [[("bus", "i"), ("bus", "i")]])]
            )
            top_rows = module_sheet(
                "TOP", [("bus", "TOP_W", 2, "i", "TOP_N", 2)]
            )
            child_rows = module_sheet(
                "CHILD", [("bus", "CHILD_W", 4, "i", "CHILD_N", 2)]
            )
            write_xlsx(
                workbook,
                [("集成", integration), ("TOP", top_rows), ("CHILD", child_rows)],
            )

            _, default_reporter = generate(workbook, root / "default")
            self.assertTrue(
                any(
                    item.code == "W_PARAMETER_WIDTH_MISMATCH"
                    for item in default_reporter.items
                )
            )
            with patch(
                "xlsx2verilog.AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH", True
            ):
                paths, reporter = generate(workbook, root / "autofill")
            self.assertFalse(reporter.has_errors)
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(top, r"\.bus\s+\(\{4'b0, bus\}\s*\)")
            self.assertNotRegex(top, r"(?m)^assign .* = '0;$")

    def test_parameter_internal_wire_zero_extends_in_destination_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-internal-width.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "SOURCE", "SINK"],
                        [[("clk", "i"), ("clk", "i"), ("clk", "i")]],
                    ),
                    (
                        ["SOURCE", "SINK"],
                        [[("data_out", "o"), ("data_in", "i")]],
                    ),
                ]
            )
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    (
                        "SOURCE",
                        module_sheet(
                            "SOURCE",
                            [("clk", 1, None, "i"), ("data_out", "SRC_W", 4, "o")],
                        ),
                    ),
                    (
                        "SINK",
                        module_sheet(
                            "SINK",
                            [("clk", 1, None, "i"), ("data_in", "DST_W", 8, "i")],
                        ),
                    ),
                ],
            )

            with patch(
                "xlsx2verilog.AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH", True
            ):
                paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(
                reporter.has_errors, [item.message for item in reporter.items]
            )
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(top, r"wire\s+\[SRC_W\s+-1:0\]\s+w_data_out;")
            self.assertRegex(top, r"\.data_out\s+\(w_data_out\s*\)")
            self.assertRegex(top, r"\.data_in\s+\(\{4'b0, w_data_out\}\s*\)")
            self.assertNotIn("assign w_data_out", top)

    def test_parameter_output_adapter_uses_sized_zero_literal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-output-width.xlsx"
            integration = integration_sheet(
                [(["TOP", "CHILD"], [[("data", "o"), ("data", "o")]])]
            )
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("data", "TOP_W", 8, "o")])),
                    (
                        "CHILD",
                        module_sheet("CHILD", [("data", "CHILD_W", 4, "o")]),
                    ),
                ],
            )

            with patch(
                "xlsx2verilog.AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH", True
            ):
                paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(
                reporter.has_errors, [item.message for item in reporter.items]
            )
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertIn("assign data[8-1:4] = 4'b0;", top)
            self.assertNotIn("assign data[8-1:4] = '0;", top)

    def test_duplicate_named_na_reuses_one_wire_without_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "named-na.xlsx"
            integration = integration_sheet(
                [
                    (["TOP", "CHILD"], [[("clk", "i"), ("clk", "i")]]),
                    (
                        ["CHILD", ""],
                        [
                            [("a", "i"), ("NA->shared", "")],
                            [("b", "i"), ("NA->shared", "")],
                        ],
                    ),
                ]
            )
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    (
                        "CHILD",
                        module_sheet(
                            "CHILD",
                            [("clk", 1, None, "i"), ("a", 4, None, "i"), ("b", 4, None, "i")],
                        ),
                    ),
                ],
            )
            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertEqual(1, len([line for line in top.splitlines() if line.rstrip().endswith(" shared;")]))
            self.assertNotIn("shared_2", top)
            self.assertRegex(top, r"\.a\s+\(shared\s*\)")
            self.assertRegex(top, r"\.b\s+\(shared\s*\)")

    def test_spread_value_cli_was_removed(self) -> None:
        self.assertFalse(any(action.dest == "spread_value" for action in build_parser()._actions))


if __name__ == "__main__":
    unittest.main()
