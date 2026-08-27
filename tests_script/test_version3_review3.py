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
from xlsx2verilog import diffuse_variable_value, generate, list_diffusible_variables


class Version3TechReview3Tests(unittest.TestCase):
    def test_macro_category_preserves_case_and_commented_template_row_is_disabled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "macro-category.xlsx"
            rows = module_sheet(
                "MACRO_CASE",
                [
                    ("kept", "`MixedWidth", None, "o"),
                    ("drop_{{i}}", 1, None, "i"),
                ],
            )
            # A disabled template row must not require a domain merely to be skipped.
            set_cell(rows, 4, 6, "待后续确认 *注释*")
            set_cell(rows, 5, 1, "宏定义")
            set_cell(rows, 5, 2, "MixedWidth")
            set_cell(rows, 5, 4, 8)
            set_cell(rows, 6, 1, "宏定义")
            set_cell(rows, 6, 2, "lane_{{i}}")
            set_cell(rows, 6, 4, "范围是{2,4}")
            set_cell(rows, 6, 6, "i={a,b}")
            set_cell(rows, 7, 1, "parameter")
            set_cell(rows, 7, 2, "CALC_W")
            set_cell(rows, 7, 3, "`log2(MixedWidth)")
            set_cell(rows, 7, 4, 3)
            write_xlsx(workbook, [("MACRO_CASE", rows)])

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertTrue(
                any(item.code == "I_ROW_COMMENTED" for item in reporter.items)
            )
            text = paths[0].read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^// `define MixedWidth\s+8$")
            self.assertRegex(text, r"(?m)^// `define LANE_A\s+2$")
            self.assertRegex(text, r"(?m)^// `define LANE_B\s+4$")
            self.assertIn("[`MixedWidth -1:0] kept", text)
            self.assertRegex(text, r"localparam\s+CALC_W\s+= `log2\(MIXEDWIDTH\)")
            self.assertNotIn("drop_a", text)
            self.assertNotIn("drop_b", text)
            self.assertNotRegex(text, r"(?:localparam|parameter)\s+MixedWidth")

            targets, discovery = list_diffusible_variables(workbook)
            self.assertFalse(discovery.has_errors)
            self.assertIn("`MixedWidth", [item.expression for item in targets])
            self.assertIn("`LANE_A", [item.expression for item in targets])
            diffusion = diffuse_variable_value(
                workbook,
                "`MixedWidth",
                16,
                confirm=lambda _: "y",
                timestamp="20260826_000000_000000",
            )
            self.assertGreaterEqual(diffusion.edited_cells, 1)
            updated_paths, updated_reporter = generate(
                workbook, root / "updated-generated"
            )
            self.assertFalse(updated_reporter.has_errors)
            updated = updated_paths[0].read_text(encoding="utf-8")
            self.assertRegex(updated, r"(?m)^// `define MixedWidth\s+16$")

    def test_parameter_generate_count_and_na_wire_keep_symbolic_dimensions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-generate.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [[("clk", "i"), ("clk", "i")]],
                    ),
                    (["CHILD"], [[("lane_out", "o")]]),
                    (
                        ["TOP", "CHILD"],
                        [[("LANE_NUM", ""), ("LANE_NUM", "")]],
                    ),
                ]
            )
            set_cell(integration, 3, 10, "NA[i]")
            set_cell(integration, 3, 11, "parameter")
            set_cell(integration, 1, 18, "模块名")
            set_cell(integration, 1, 19, "例化名")
            set_cell(integration, 1, 20, "例化次数")
            set_cell(integration, 2, 18, "CHILD")
            set_cell(integration, 2, 19, "U_CHILD_ARRAY")
            set_cell(integration, 2, 20, "LANE_NUM")

            top_rows = module_sheet("TOP", [("clk", 1, None, "i")])
            set_cell(top_rows, 4, 1, "parameter")
            set_cell(top_rows, 4, 2, "LANE_NUM")
            set_cell(top_rows, 4, 4, 4)
            child_rows = module_sheet(
                "CHILD",
                [("clk", 1, None, "i"), ("lane_out", "DATA_W", 8, "o")],
            )
            set_cell(child_rows, 5, 1, "parameter")
            set_cell(child_rows, 5, 2, "LANE_NUM")
            set_cell(child_rows, 5, 4, 4)
            set_cell(child_rows, 6, 1, "parameter")
            set_cell(child_rows, 6, 2, "DATA_W")
            set_cell(child_rows, 6, 4, 8)
            write_xlsx(
                workbook,
                [("集成", integration), ("TOP", top_rows), ("CHILD", child_rows)],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            warnings = [
                item.message
                for item in reporter.items
                if item.code == "W_PARAMETER_AUTO_LOCAL"
            ]
            self.assertTrue(any("CHILD.DATA_W" in message for message in warnings))
            top_path = next(path for path in paths if path.name == "top.v")
            top = top_path.read_text(encoding="utf-8")
            child = next(path for path in paths if path.name == "child.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(
                top,
                r"wire\s+\[LANE_NUM\s+-1:0\]\[DATA_W\s+-1:0\]\s+lane_out;",
            )
            self.assertRegex(top, r"(?m)^localparam DATA_W\s+= 8;$")
            self.assertIn("i_gen_u_child_array < LANE_NUM", top)
            self.assertRegex(top, r"\.LANE_NUM\s+\(LANE_NUM\)")
            self.assertLess(
                top.index("/*USER CODE BEGIN before module*/"),
                top.index("module TOP"),
            )
            self.assertNotIn("before module", child)
            top_path.write_text(
                top.replace(
                    "/*USER CODE BEGIN before module*/",
                    "/*USER CODE BEGIN before module*/\n`include \"project_defs.vh\"",
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
            _, regenerated_reporter = generate(workbook, root / "generated")
            self.assertFalse(regenerated_reporter.has_errors)
            self.assertIn(
                '`include "project_defs.vh"',
                top_path.read_text(encoding="utf-8"),
            )

    def test_macro_generate_count_and_width_keep_original_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "macro-generate.xlsx"
            integration = integration_sheet(
                [
                    (["TOP", "CHILD"], [[("clk", "i"), ("clk", "i")]]),
                    (["CHILD"], [[("data_out", "o")]]),
                ]
            )
            set_cell(integration, 3, 10, "NA[i]")
            set_cell(integration, 1, 12, "模块名")
            set_cell(integration, 1, 13, "例化名")
            set_cell(integration, 1, 14, "例化次数")
            set_cell(integration, 2, 12, "CHILD")
            set_cell(integration, 2, 13, "U_MACRO_CHILD")
            set_cell(integration, 2, 14, "`laneCount")

            top_rows = module_sheet("TOP", [("clk", 1, None, "i")])
            set_cell(top_rows, 4, 1, "宏定义")
            set_cell(top_rows, 4, 2, "laneCount")
            set_cell(top_rows, 4, 4, 3)
            child_rows = module_sheet(
                "CHILD",
                [("clk", 1, None, "i"), ("data_out", "`dataWidth", 8, "o")],
            )
            write_xlsx(
                workbook,
                [("集成", integration), ("TOP", top_rows), ("CHILD", child_rows)],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(
                top,
                r"wire\s+\[`laneCount\s+-1:0\]\[`dataWidth\s+-1:0\]\s+data_out;",
            )
            self.assertIn("i_gen_u_macro_child < `laneCount", top)
            self.assertIn("// `define laneCount", top)
            self.assertIn("// `define dataWidth", top)

    def test_parameter_direction_number_is_a_direct_instance_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "direct-parameter.xlsx"
            integration = integration_sheet(
                [
                    (["TOP", "CHILD"], [[("clk", "i"), ("clk", "i")]]),
                    (
                        ["CHILD"],
                        [[("LANE_NUM", "3")], [("OPTION", "0")]],
                    ),
                ]
            )
            set_cell(integration, 3, 7, "parameter")
            child_rows = module_sheet("CHILD", [("clk", 1, None, "i")])
            set_cell(child_rows, 4, 1, "parameter")
            set_cell(child_rows, 4, 2, "LANE_NUM")
            set_cell(child_rows, 4, 4, 8)
            set_cell(child_rows, 5, 2, "OPTION")
            set_cell(child_rows, 5, 4, 1)
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("CHILD", child_rows),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            child = next(path for path in paths if path.name == "child.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(top, r"\.LANE_NUM\s+\(3\)")
            self.assertRegex(top, r"\.OPTION\s+\(0\)")
            self.assertRegex(child, r"parameter\s+LANE_NUM\s+= 8")
            self.assertRegex(child, r"parameter\s+OPTION\s+= 1")

    def test_integration_row_comment_disables_the_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "commented-connection.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [[("done", "o"), ("done", "o")]],
                    )
                ]
            )
            set_cell(integration, 3, 8, "review *注释*")
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    ("TOP", module_sheet("TOP", [("done", 1, None, "o")])),
                    ("CHILD", module_sheet("CHILD", [("done", 1, None, "o")])),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertTrue(
                any(item.code == "I_ROW_COMMENTED" for item in reporter.items)
            )
            top = next(path for path in paths if path.name == "top.v").read_text(
                encoding="utf-8"
            )
            self.assertNotRegex(top, r"\.done\s+\(done\)")
            self.assertRegex(top, r"\.done\s+\(\s*\)")
            self.assertRegex(top, r"assign\s+done\s+= \{1\{1'b0\}\};")


if __name__ == "__main__":
    unittest.main()
