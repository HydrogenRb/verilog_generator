from __future__ import annotations

import io
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests_script.run_review_matrix import integration_sheet, module_sheet, set_cell, write_xlsx
from xlsx2verilog import (
    Reporter,
    Sheet,
    diffuse_variable_value,
    generate,
    parse_workbook,
    template_values_in_row,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (
    ROOT
    / "review_test_cases"
    / "13_test_for_techreview3"
    / "techreview2version3.xlsx"
)
SMALLFIX_SAMPLE = SAMPLE.with_name("techreview2version_smallfix.xlsx")


class Version2TechReview3Tests(unittest.TestCase):
    def test_range_template_and_internal_na_index_generate(self) -> None:
        parsed_reporter = Reporter()
        _, modules, _ = parse_workbook(SAMPLE, parsed_reporter)
        crg = modules["RISCV_CRG"]
        expanded = [
            port.name
            for port in crg.ports
            if port.name.startswith("high_clk_after_pll_")
        ]
        self.assertEqual(len(expanded), 32)
        self.assertEqual(expanded[0], "high_clk_after_pll_0")
        self.assertEqual(expanded[-1], "high_clk_after_pll_31")
        self.assertFalse(
            any("模板变量 z 未找到取值列表" in item.message for item in parsed_reporter.items)
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / SAMPLE.name
            shutil.copy2(SAMPLE, workbook)
            for variable, value in (
                ("`RST_LANE", "5"),
                ("`CLK_LANE", "5"),
                ("`APB_1", "4"),
                ("`LANE_NUM", "5"),
            ):
                diffuse_variable_value(
                    workbook,
                    variable,
                    value,
                    confirm=lambda _prompt: "y",
                )
            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual(
                {path.name for path in paths},
                {
                    "riscv_top.v",
                    "riscv_core_test.v",
                    "mem_phy.v",
                    "mem_dat.v",
                    "riscv_crg.v",
                },
            )
            top = (root / "generated" / "riscv_top.v").read_text(encoding="utf-8")
            self.assertRegex(
                top,
                r"wire\s+\[114\s+-1:0\]\[CLK_BUS_0\s+-1:0\]\s+"
                r"high_clk_after_pll_0;",
            )
            self.assertRegex(
                top,
                r"\.high_clk_after_pll_0\s+"
                r"\(high_clk_after_pll_0\s*\[i\]\),?\s+"
                r"//TODO:本信号期望有逻辑功能，请完成",
            )
            self.assertIn("begin : G_U_RISCV_CRG", top)
            self.assertIn("/*USER CODE BEGIN before RISCV_CRG*/", top)
            self.assertIn("/*USER CODE BEGIN after RISCV_CRG*/", top)

    def test_user_code_regions_survive_regeneration_and_damage_blocks_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "user-code.xlsx"
            output = root / "generated"
            write_xlsx(
                workbook,
                [
                    (
                        "集成",
                        integration_sheet(
                            [
                                (
                                    ["TOP", "CHILD"],
                                    [[("MiXeD", "i"), ("MiXeD", "i")]],
                                )
                            ]
                        ),
                    ),
                    ("TOP", module_sheet("TOP", [("MiXeD", 1, None, "i")])),
                    ("CHILD", module_sheet("CHILD", [("MiXeD", 1, None, "i")])),
                ],
            )
            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            top_path = output / "top.v"
            text = top_path.read_text(encoding="utf-8")
            replacements = {
                "before statement": "logic keep_declaration;",
                "before CHILD": "assign keep_declaration = 1'b0;",
                "after CHILD": "// keep this user note",
            }
            for label, code in replacements.items():
                text = text.replace(
                    f"/*USER CODE BEGIN {label}*/\n\n/*USER CODE END   {label}*/",
                    f"/*USER CODE BEGIN {label}*/\n{code}\n/*USER CODE END   {label}*/",
                )
            top_path.write_text(text, encoding="utf-8", newline="\n")

            second_paths, second_reporter = generate(workbook, output)
            self.assertFalse(second_reporter.has_errors)
            self.assertEqual(second_paths, paths)
            regenerated = top_path.read_text(encoding="utf-8")
            for code in replacements.values():
                self.assertEqual(regenerated.count(code), 1)
            self.assertRegex(regenerated, r"(?m)^\s*\.MiXeD\s+\(MiXeD\s*\)$")

            damaged = regenerated.replace(
                "/*USER CODE END   after CHILD*/",
                "/*USER CODE END   wrong label*/",
            )
            top_path.write_text(damaged, encoding="utf-8", newline="\n")
            before = top_path.read_bytes()
            blocked_paths, blocked_reporter = generate(workbook, output)
            self.assertEqual(blocked_paths, [])
            self.assertTrue(blocked_reporter.has_errors)
            self.assertTrue(
                any("拒绝覆盖文件" in item.message for item in blocked_reporter.items)
            )
            self.assertEqual(top_path.read_bytes(), before)

    def test_signal_case_inout_and_macro_parameter_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "case-and-parameter.xlsx"
            rows = module_sheet(
                "CaseModule",
                [
                    ("LANE_NUM", None, "`glb_parameter", None),
                    ("DataBus", "LANE_NUM", None, "i"),
                    ("BiDir", 8, None, "io"),
                ],
            )
            set_cell(rows, 3, 1, "Parameter")
            write_xlsx(workbook, [("CaseModule", rows)])

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            text = paths[0].read_text(encoding="utf-8")
            self.assertRegex(
                text,
                r"parameter\s+LANE_NUM\s+= `glb_parameter",
            )
            self.assertRegex(text, r"(?m)^\s*input wire \[LANE_NUM\s+-1:0\]\s+DataBus,")
            self.assertRegex(text, r"(?m)^\s*inout\s+\[8\s+-1:0\]\s+BiDir$")
            self.assertNotRegex(text, r"(?m)^\s*inout\s+wire\b")

    def test_reporter_groups_severity_and_can_emit_color(self) -> None:
        reporter = Reporter()
        reporter.info("third")
        reporter.warning("second")
        reporter.error("first")
        output = io.StringIO()
        reporter.print(output, color=True)
        text = output.getvalue()
        self.assertLess(text.index("=== ERROR"), text.index("=== WARNING"))
        self.assertLess(text.index("=== WARNING"), text.index("=== INFO"))
        self.assertIn("\033[31m", text)
        self.assertIn("\033[33m", text)
        self.assertIn("\033[36m", text)
        self.assertEqual(len(re.findall(r"(?:error|warning|info)\[", text)), 3)

        plain = io.StringIO()
        reporter.print(plain, color=False)
        self.assertNotIn("\033[", plain.getvalue())

    def test_numeric_template_range_supports_descending_and_has_a_limit(self) -> None:
        reporter = Reporter()
        sheet = Sheet("RANGE", {(1, 1): "z=3:1"}, 1, 1)
        self.assertEqual(
            template_values_in_row(sheet, 1, "range", reporter),
            {"z": ["3", "2", "1"]},
        )
        self.assertFalse(reporter.has_errors)

        too_large = Reporter()
        sheet = Sheet("RANGE", {(1, 1): "z=0:4096"}, 1, 1)
        self.assertEqual(template_values_in_row(sheet, 1, "range", too_large), {})
        self.assertTrue(too_large.has_errors)

    def test_top_template_generate_reference_may_connect_to_na(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / SMALLFIX_SAMPLE.name
            shutil.copy2(SMALLFIX_SAMPLE, workbook)
            for variable, value in (
                ("`RST_LANE", "5"),
                ("`CLK_LANE", "5"),
                ("`APB_1", "4"),
                ("`LANE_NUM", "5"),
            ):
                try:
                    diffuse_variable_value(
                        workbook,
                        variable,
                        value,
                        confirm=lambda _prompt: "y",
                    )
                except ValueError as exc:
                    if "没有需要修改的数值单元格" not in str(exc):
                        raise

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual(len(paths), 5)
            self.assertFalse(
                any("没有端口 NA" in item.message for item in reporter.items)
            )
            self.assertTrue(
                any("保留为顶层观察端口" in item.message for item in reporter.items)
            )
            top = (root / "generated" / "riscv_top.v").read_text(
                encoding="utf-8"
            )
            for suffix in ("dat", "req", "rsp"):
                self.assertIn(f"dyadic_bus_out_{suffix}", top)
                self.assertRegex(
                    top,
                    rf"(?m)^assign\s+dyadic_bus_out_{suffix}\s*=\s*"
                    rf"\{{BUS_OUT_{suffix.upper()}\*DW\*BUS\{{1'b0\}}\}};",
                )

    def test_na_endpoint_may_omit_module_name_and_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "anonymous-na.xlsx"
            integration_rows = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [[("clk", "i"), ("clk", "i")]],
                    ),
                    (
                        ["CHILD"],
                        [
                            [("sig1", "i")],
                            [("lanes", "i")],
                        ],
                    ),
                ]
            )
            # J is directly after CHILD's H/I port/direction pair.  It has no
            # module name and no 端口名/i/o header, exactly like a production
            # anonymous NA endpoint.
            set_cell(integration_rows, 3, 10, "NA")
            set_cell(integration_rows, 4, 10, "NA[i]")
            # K is a spacer.  A later block at L must start a new connection
            # group even though the anonymous endpoint itself occupies only
            # one column rather than a port/direction pair.
            set_cell(integration_rows, 1, 12, "OTHER")
            set_cell(integration_rows, 2, 12, "端口名")
            set_cell(integration_rows, 2, 13, "i/o")
            set_cell(integration_rows, 3, 12, "spare")
            set_cell(integration_rows, 3, 13, "i")
            write_xlsx(
                workbook,
                [
                    ("集成", integration_rows),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    (
                        "CHILD",
                        module_sheet(
                            "CHILD",
                            [
                                ("clk", 1, None, "i"),
                                ("sig1", 1, None, "i"),
                                ("lanes", 4, None, "i"),
                            ],
                        ),
                    ),
                    (
                        "OTHER",
                        module_sheet("OTHER", [("spare", 1, None, "i")]),
                    ),
                ],
            )

            paths, reporter = generate(
                workbook,
                root / "generated",
                strict=True,
            )
            self.assertFalse(reporter.has_errors)
            self.assertEqual(
                {path.name for path in paths},
                {"top.v", "child.v", "other.v"},
            )
            text = (root / "generated" / "top.v").read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^\s*wire\s+sig1\s*;")
            self.assertRegex(
                text,
                r"(?m)^\s*wire\s+\[4\s+-1:0\]\[4\s+-1:0\]\s+lanes;",
            )
            self.assertRegex(
                text,
                r"\.sig1\s+\(sig1\s*\),?\s+//TODO:本信号期望有逻辑功能，请完成",
            )
            self.assertRegex(
                text,
                r"\.lanes\s+\(lanes\s*\[i\]\),?\s+"
                r"//TODO:本信号期望有逻辑功能，请完成",
            )
            self.assertIn("begin : G_U_CHILD", text)

            # Keeping a repeated header for the NA endpoint is also legal;
            # only its module-name cell must remain optional.
            headed_rows = integration_sheet(
                [
                    (["TOP", "CHILD"], [[("clk", "i"), ("clk", "i")]]),
                    (["CHILD"], [[("sig1", "i")]]),
                ]
            )
            set_cell(headed_rows, 2, 10, "端口名")
            set_cell(headed_rows, 2, 11, "i/o")
            set_cell(headed_rows, 3, 10, "NA")
            headed_workbook = root / "headed-anonymous-na.xlsx"
            write_xlsx(
                headed_workbook,
                [
                    ("集成", headed_rows),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    (
                        "CHILD",
                        module_sheet(
                            "CHILD",
                            [("clk", 1, None, "i"), ("sig1", 1, None, "i")],
                        ),
                    ),
                ],
            )
            headed_paths, headed_reporter = generate(
                headed_workbook,
                root / "headed-generated",
                strict=True,
            )
            self.assertFalse(headed_reporter.has_errors)
            self.assertEqual(
                {path.name for path in headed_paths},
                {"top.v", "child.v"},
            )
            headed_top = (root / "headed-generated" / "top.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(
                headed_top,
                r"\.sig1\s+\(sig1\s*\),?\s+//TODO:本信号期望有逻辑功能，请完成",
            )


if __name__ == "__main__":
    unittest.main()
