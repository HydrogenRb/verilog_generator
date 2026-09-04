from __future__ import annotations

import re
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


class Version345BugfixTests(unittest.TestCase):
    def test_unlinked_child_width_parameter_is_top_body_localparam(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "unlinked-parameter.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [[("clk", "i"), ("clk", "i")]],
                    ),
                    (["CHILD", ""], [[("payload", "o"), ("NA", "")]]),
                ]
            )
            child = module_sheet(
                "CHILD",
                [("clk", 1, None, "i"), ("payload", "WIDTH", 10, "o")],
            )
            parameter_row(child, 5, "WIDTH", 10)
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("CHILD", child),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(top, r"(?m)^localparam\s+WIDTH\s+= 10;$")
            self.assertNotRegex(top, r"module TOP #\(")
            self.assertRegex(top, r"wire\s+\[WIDTH\s+-1:0\]\s+payload;")
            self.assertRegex(top, r"\.WIDTH\s+\(WIDTH\s*\)")

    def test_parameter_na_index_creates_array_initializer_and_indexed_override(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-array.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [[("clk", "i"), ("clk", "i")], [None, None]],
                    ),
                    (
                        ["CHILD", ""],
                        [
                            [None, None],
                            [
                                ("SELECT", "{1,2,,`macro1,4}"),
                                ("NA[i]->loc_param_a", ""),
                            ],
                        ],
                    ),
                ]
            )
            set_cell(integration, 4, 7, "parameter")
            set_cell(integration, 1, 14, "模块名")
            set_cell(integration, 1, 15, "例化名")
            set_cell(integration, 1, 16, "例化次数")
            set_cell(integration, 2, 14, "CHILD")
            set_cell(integration, 2, 16, 4)
            child = module_sheet("CHILD", [("clk", 1, None, "i")])
            parameter_row(child, 4, "SELECT", 7)
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("CHILD", child),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(
                top,
                r"localparam \[4 -1:0\]\[32 -1:0\] LOC_PARAM_A\s+"
                r"= '\{1,2,`macro1,4\};",
            )
            self.assertTrue(
                any(item.code == "W_PARAMETER_NA_REPAIR" for item in reporter.items)
            )
            self.assertIn(
                "for (i = 0; i < 4; ", top
            )
            self.assertRegex(
                top,
                r"\.SELECT\s+\(LOC_PARAM_A\[i\]\s*\)",
            )

    def test_parameter_na_accepts_top_parameter_as_default_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-top-source.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD", ""],
                        [
                            [("clk", "i"), ("clk", "i"), None],
                            [("BASE", ""), ("SELECT", ""), ("NA[i]->loc", "")],
                        ],
                    )
                ]
            )
            set_cell(integration, 4, 1, "parameter")
            set_cell(integration, 1, 10, "模块名")
            set_cell(integration, 1, 11, "例化名")
            set_cell(integration, 1, 12, "例化次数")
            set_cell(integration, 2, 10, "CHILD")
            set_cell(integration, 2, 12, 4)
            top_rows = module_sheet("TOP", [("clk", 1, None, "i")])
            parameter_row(top_rows, 4, "BASE", 3)
            child_rows = module_sheet("CHILD", [("clk", 1, None, "i")])
            parameter_row(child_rows, 4, "SELECT", 7)
            write_xlsx(
                workbook,
                [("集成", integration), ("TOP", top_rows), ("CHILD", child_rows)],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(
                top,
                r"localparam \[4 -1:0\]\[32 -1:0\] LOC\s+"
                r"= '\{default: BASE\};",
            )
            self.assertRegex(top, r"\.SELECT\s+\(LOC\[i\]\s*\)")

    def test_parameter_na_reports_only_genuinely_conflicting_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-conflict.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD", ""],
                        [
                            [("clk", "i"), ("clk", "i"), None],
                            [None, ("SELECT", "1"), ("NA[i]->loc", "2")],
                        ],
                    )
                ]
            )
            set_cell(integration, 4, 1, "parameter")
            child_rows = module_sheet("CHILD", [("clk", 1, None, "i")])
            parameter_row(child_rows, 4, "SELECT", 7)
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("CHILD", child_rows),
                ],
            )
            _, reporter = generate(workbook, root / "generated")
            self.assertTrue(reporter.has_errors)
            self.assertTrue(
                any(
                    item.code == "E_PARAMETER"
                    and "多个不一致的初始化来源" in item.message
                    for item in reporter.items
                )
            )
            self.assertFalse(
                any("不能与 TOP parameter 来源" in item.message for item in reporter.items)
            )

    def test_inline_instance_names_create_independent_instances_and_win_priority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "multi-instance.xlsx"
            integration = integration_sheet(
                [
                    (
                        [
                            "module:TOP",
                            "module:CHILD 例化名:RISC_CORE1",
                            "module:CHILD 例化名:RISC_CORE2",
                        ],
                        [[("clk", "i"), ("clk", "i"), ("clk", "i")]],
                    )
                ]
            )
            # The older side table remains valid, but its instance name has
            # lower priority than each inline label.
            set_cell(integration, 1, 10, "模块名")
            set_cell(integration, 1, 11, "例化名")
            set_cell(integration, 1, 12, "例化次数")
            set_cell(integration, 2, 10, "CHILD")
            set_cell(integration, 2, 11, "LOW_PRIORITY_NAME")
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("CHILD", module_sheet("CHILD", [("clk", 1, None, "i")])),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors, [item.message for item in reporter.items])
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                len(re.findall(r"(?m)^CHILD RISC_CORE[12] \($", top)),
                2,
            )
            self.assertIn("CHILD RISC_CORE1 (", top)
            self.assertIn("CHILD RISC_CORE2 (", top)
            self.assertNotIn("LOW_PRIORITY_NAME", top)
            self.assertEqual(len([path for path in paths if path.name == "child.v"]), 1)

    def test_version_is_345(self) -> None:
        self.assertEqual(SCRIPT_VERSION, "Version V3.5.05")


if __name__ == "__main__":
    unittest.main()
