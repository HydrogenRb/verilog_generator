from __future__ import annotations

import ast
import re
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests_script.run_review_matrix import (
    integration_sheet,
    module_sheet,
    set_cell,
    write_xlsx,
)
from xlsx2verilog import (
    DIAGNOSTIC_VISIBILITY_BY_CODE,
    Reporter,
    SCRIPT_CONTACT,
    SCRIPT_DISPLAY_NAME,
    SCRIPT_RELEASE_DATE,
    SCRIPT_VERSION,
    generate,
    print_startup_banner,
)


ROOT = Path(__file__).resolve().parents[1]
EDGE_SAMPLE = (
    ROOT / "review_test_cases" / "14_edge_case_test_problem" / "eage_case.xlsx"
)


class Version3TechReview1Tests(unittest.TestCase):
    def test_edge_sample_covers_parameter_na_and_instance_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "generated"
            paths, reporter = generate(EDGE_SAMPLE, output)
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

            top = (output / "riscv_top.v").read_text(encoding="utf-8")
            core = (output / "riscv_core_test.v").read_text(encoding="utf-8")
            self.assertRegex(
                top,
                r"localparam\s+RST_LANE\s+= `GLB_RST_LANE,\s+// 1",
            )
            self.assertRegex(core, r"parameter\s+RST_LANE\s+= 1")
            self.assertNotIn("parameter integer", top + core)
            self.assertRegex(top, r"(?m)^// `define GLB_RST_LANE\s+1$")
            self.assertNotRegex(top, r"(?m)^`define\s+")

            self.assertRegex(
                top,
                r"wire\s+\[13\s+-1:0\]\s+ready_test_process;",
            )
            self.assertRegex(
                top,
                r"\.ready_to_process\s+\(ready_test_process\s*\)",
            )
            for name in ("sig1", "sig2", "sig3"):
                self.assertRegex(
                    top,
                    rf"(?m)^assign test_bus2_{name}_valid\s+= "
                    rf"\{{1\{{1'b1\}}\}};$",
                )
            self.assertIn(
                "\n// Internal connections and NA placeholder signals.\n"
                "// 子模块内部连线及 NA 占位信号。\n",
                top,
            )
            self.assertNotIn("\n    // 子模块内部连线及 NA 占位信号。", top)
            self.assertRegex(top, r"(?m)^wire\s+\[13\s+-1:0\]\s+ready_test_process;")

            self.assertIn("MEM_DAT PROJECT_PERSONAL_MEM_DAT (", top)
            self.assertIn("genvar i_gen_u_riscv_crg;", top)
            self.assertIn(
                "for (i_gen_u_riscv_crg = 0; i_gen_u_riscv_crg < 10;",
                top,
            )
            self.assertTrue(
                any("使用显式例化次数 10" in item.message for item in reporter.items)
            )

    def test_child_parameter_link_creates_top_localparam(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-link.xlsx"
            rows = integration_sheet(
                [
                    (
                        ["TOP", "CHILD_A", "CHILD_B"],
                        [[("clk", "i"), ("clk", "i"), ("clk", "i")]],
                    ),
                    (
                        ["CHILD_A", "CHILD_B"],
                        [[("P_A", ""), ("P_B", "")]],
                    ),
                ]
            )
            # The second group's category column is immediately before J/L.
            set_cell(rows, 3, 9, "parameter")

            child_a = module_sheet(
                "CHILD_A", [("P_A", None, 8, None), ("clk", 1, None, "i")]
            )
            child_b = module_sheet(
                "CHILD_B", [("P_B", None, 8, None), ("clk", 1, None, "i")]
            )
            set_cell(child_a, 3, 1, "parameter")
            set_cell(child_b, 3, 1, "parameter")
            write_xlsx(
                workbook,
                [
                    ("集成", rows),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("CHILD_A", child_a),
                    ("CHILD_B", child_b),
                ],
            )

            _, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            top = (root / "generated" / "top.v").read_text(encoding="utf-8")
            child_a_text = (root / "generated" / "child_a.v").read_text(
                encoding="utf-8"
            )
            child_b_text = (root / "generated" / "child_b.v").read_text(
                encoding="utf-8"
            )
            self.assertRegex(top, r"localparam\s+P_A\s+= 8")
            self.assertRegex(top, r"\.P_A\s+\(P_A\)")
            self.assertRegex(top, r"\.P_B\s+\(P_A\)")
            self.assertRegex(child_a_text, r"parameter\s+P_A\s+= 8")
            self.assertRegex(child_b_text, r"parameter\s+P_B\s+= 8")
            self.assertTrue(
                any("自动在 TOP 创建 localparam P_A" in item.message for item in reporter.items)
            )

    def test_parameter_generation_accepts_macro_and_parameter_expressions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-expressions.xlsx"
            rows = module_sheet(
                "PARAMETER_EXPRESSIONS",
                [
                    ("para_a", "`lane_num", 4, None),
                    ("para_b", "para_a+1", 5, None),
                    ("DW", "`log2(para_b)", 3, None),
                    ("feature_off", None, 0, None),
                    ("macro_off", "`feature_enable", 0, None),
                    ("data", "DW", 3, "o"),
                ],
            )
            for row in (3, 4, 5, 6, 7):
                set_cell(rows, row, 1, "parameter")
            write_xlsx(workbook, [("PARAMETER_EXPRESSIONS", rows)])

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual([path.name for path in paths], ["parameter_expressions.v"])
            text = paths[0].read_text(encoding="utf-8")
            self.assertRegex(text, r"localparam\s+PARA_A\s+= `LANE_NUM,\s+// 4")
            self.assertRegex(text, r"localparam\s+PARA_B\s+= PARA_A\+1,\s+// 5")
            self.assertRegex(
                text,
                r"localparam\s+DW\s+= `LOG2\(PARA_B\),?\s+// 3",
            )
            self.assertRegex(text, r"localparam\s+FEATURE_OFF\s+= 0")
            self.assertRegex(
                text,
                r"localparam\s+MACRO_OFF\s+= `FEATURE_ENABLE\s+// 0",
            )
            self.assertRegex(text, r"(?m)^// `define FEATURE_ENABLE\s+0$")
            self.assertRegex(text, r"output wire\s+\[DW\s+-1:0\]\s+data")

    def test_parameter_generation_rejects_statement_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "unsafe-parameter-expression.xlsx"
            rows = module_sheet(
                "UNSAFE_PARAMETER",
                [("DW", "para_a; assign hacked = 1", 3, None)],
            )
            set_cell(rows, 3, 1, "parameter")
            write_xlsx(workbook, [("UNSAFE_PARAMETER", rows)])

            paths, reporter = generate(workbook, root / "generated")
            self.assertEqual(paths, [])
            self.assertTrue(reporter.has_errors)
            self.assertTrue(
                any(
                    item.code == "E_PARAMETER" and "安全的单行" in item.message
                    for item in reporter.items
                )
            )

    def test_na_one_replicates_across_every_packed_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "na-all-ones.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [[("all_bits", "o"), ("NA->1", "")]],
                    ),
                    (
                        ["CHILD"],
                        [[("data_in", "i")]],
                    ),
                ]
            )
            # Headerless anonymous NA endpoint immediately after the second
            # group's CHILD port/direction pair.
            set_cell(integration, 3, 10, "NA->1")
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    (
                        "TOP",
                        module_sheet(
                            "TOP",
                            [("all_bits", "B", 4, "o", "A", 3)],
                        ),
                    ),
                    (
                        "CHILD",
                        module_sheet(
                            "CHILD",
                            [("data_in", "Y", 6, "i", "X", 5)],
                        ),
                    ),
                ],
            )

            _, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            text = (root / "generated" / "top.v").read_text(encoding="utf-8")
            self.assertRegex(
                text,
                r"(?m)^assign all_bits\s+= \{A\*B\{1'b1\}\};$",
            )
            self.assertRegex(text, r"\.data_in\s+\(\{5\*6\{1'b1\}\}\)")

    def test_named_na_works_for_top_and_single_real_module_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "named-na-everywhere.xlsx"
            integration = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [
                            [("probe", "i"), ("NA->probe_alias", "")],
                            [("status", "o"), ("NA->status_alias", "")],
                            [
                                ("NA->top_area_child_wire", ""),
                                ("data_from_child", "o"),
                            ],
                        ],
                    ),
                    (
                        ["CHILD"],
                        [
                            [("data_in", "i")],
                            [("data_out", "o")],
                        ],
                    ),
                ]
            )
            # One real module plus an adjacent anonymous NA endpoint uses the
            # same downstream connection path as every other internal group.
            set_cell(integration, 4, 10, "NA->child_probe")
            write_xlsx(
                workbook,
                [
                    ("集成", integration),
                    (
                        "TOP",
                        module_sheet(
                            "TOP",
                            [("probe", "BUS", 8, "i"), ("status", 2, None, "o")],
                        ),
                    ),
                    (
                        "CHILD",
                        module_sheet(
                            "CHILD",
                            [
                                ("data_in", 4, None, "i"),
                                ("data_out", 3, None, "o"),
                                ("data_from_child", 5, None, "o"),
                            ],
                        ),
                    ),
                ],
            )

            _, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            text = (root / "generated" / "top.v").read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^wire\s+\[BUS\s+-1:0\]\s+probe_alias;")
            self.assertRegex(text, r"(?m)^wire\s+\[2\s+-1:0\]\s+status_alias;")
            self.assertRegex(text, r"(?m)^wire\s+\[3\s+-1:0\]\s+child_probe;")
            self.assertRegex(
                text,
                r"(?m)^wire\s+\[5\s+-1:0\]\s+top_area_child_wire;",
            )
            self.assertRegex(
                text,
                r"(?m)^assign probe_alias\s+= probe;\s+"
                r"//TODO:本信号期望有逻辑功能，请完成$",
            )
            self.assertRegex(
                text,
                r"(?m)^assign status_alias\s+= status;\s+"
                r"//TODO:本信号期望有逻辑功能，请完成$",
            )
            self.assertRegex(text, r"\.data_in\s+\(4'b0\s*\)")
            self.assertRegex(
                text,
                r"\.data_out\s+\(child_probe\s*\),?\s+"
                r"//TODO:本信号期望有逻辑功能，请完成",
            )
            self.assertRegex(
                text,
                r"\.data_from_child\s+\(top_area_child_wire\s*\)\s+"
                r"//TODO:本信号期望有逻辑功能，请完成",
            )

    def test_custom_count_warns_when_index_range_is_too_small(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "instance-count.xlsx"
            rows = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [[("bus[i]", "o"), ("bit_in", "i")]],
                    )
                ]
            )
            set_cell(rows, 1, 10, "模块名")
            set_cell(rows, 1, 11, "例化名")
            set_cell(rows, 1, 12, "例化次数")
            set_cell(rows, 2, 10, "TOP")
            set_cell(rows, 3, 10, "CHILD")
            set_cell(rows, 3, 11, "MyInst")
            set_cell(rows, 3, 12, 3)
            write_xlsx(
                workbook,
                [
                    ("集成", rows),
                    ("TOP", module_sheet("TOP", [("bus", 2, None, "o")])),
                    (
                        "CHILD",
                        module_sheet("CHILD", [("bit_in", 1, None, "i")]),
                    ),
                ],
            )

            _, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertTrue(
                any("存在索引越界风险" in item.message for item in reporter.items)
            )
            text = (root / "generated" / "top.v").read_text(encoding="utf-8")
            self.assertIn("genvar i_gen_myinst;", text)
            self.assertIn("i_gen_myinst < 3", text)
            self.assertIn("CHILD MyInst (", text)
            self.assertIn("begin : G_MyInst", text)

    def test_header_overwrite_switch_banner_and_diagnostic_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "header.xlsx"
            output = root / "generated"
            write_xlsx(
                workbook,
                [("ONLY", module_sheet("ONLY", [("clk", 1, None, "i")]))],
            )
            with patch("xlsx2verilog.VERILOG_FILE_HEADER", "// Header A"):
                generate(workbook, output)
            path = output / "only.v"
            path.write_text(
                path.read_text(encoding="utf-8").replace("// Header A", "// User header"),
                encoding="utf-8",
            )
            with patch("xlsx2verilog.VERILOG_FILE_HEADER", "// Header B"):
                generate(workbook, output)
            self.assertIn("// User header", path.read_text(encoding="utf-8"))
            with (
                patch("xlsx2verilog.VERILOG_FILE_HEADER", "// Header B"),
                patch("xlsx2verilog.OVERWRITE_FILE_HEADER", True),
            ):
                generate(workbook, output)
            self.assertIn("// Header B", path.read_text(encoding="utf-8"))

        banner = StringIO()
        print_startup_banner(banner)
        self.assertEqual(
            [line.strip() for line in banner.getvalue().splitlines()],
            [
                SCRIPT_DISPLAY_NAME,
                SCRIPT_VERSION,
                SCRIPT_RELEASE_DATE,
                SCRIPT_CONTACT,
            ],
        )

        reporter = Reporter()
        reporter.error("visible error")
        reporter.warning("hidden warning")
        reporter.info("hidden info")
        diagnostics = StringIO()
        with (
            patch("xlsx2verilog.SHOW_WARNING_MESSAGES", False),
            patch("xlsx2verilog.SHOW_INFO_MESSAGES", False),
            redirect_stderr(diagnostics),
        ):
            reporter.print(color=False)
        self.assertIn("visible error", diagnostics.getvalue())
        self.assertNotIn("hidden warning", diagnostics.getvalue())
        self.assertNotIn("hidden info", diagnostics.getvalue())
        self.assertTrue(reporter.has_warnings)

    def test_diagnostic_code_switches_are_granular_and_non_suppressing(self) -> None:
        reporter = Reporter()
        reporter.warning("hidden width placeholder", code="W_WIDTH_PLACEHOLDER")
        reporter.warning("visible mismatch", code="W_WIDTH_MISMATCH")
        reporter.error("visible direction", code="E_DIRECTION")
        diagnostics = StringIO()
        with patch.dict(
            DIAGNOSTIC_VISIBILITY_BY_CODE,
            {"W_WIDTH_PLACEHOLDER": False},
        ):
            reporter.print(diagnostics, color=False)
        text = diagnostics.getvalue()
        self.assertNotIn("hidden width placeholder", text)
        self.assertIn("warning[W_WIDTH_MISMATCH][visible mismatch]", text)
        self.assertIn("error[E_DIRECTION][visible direction]", text)
        self.assertIn("=== WARNING (1) ===", text)
        self.assertTrue(reporter.has_warnings)
        self.assertTrue(reporter.has_errors)

    def test_every_internal_reporter_call_has_a_registered_code(self) -> None:
        source = (ROOT / "xlsx2verilog.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        missing: list[int] = []
        literal_codes: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr not in {"error", "warning", "info"}:
                continue
            code = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "code"),
                None,
            )
            if code is None:
                missing.append(node.lineno)
            elif isinstance(code, ast.Constant) and isinstance(code.value, str):
                literal_codes.add(code.value)
        self.assertFalse(missing, f"Reporter calls without code: {missing}")
        self.assertLessEqual(literal_codes, set(DIAGNOSTIC_VISIBILITY_BY_CODE))
        for code in DIAGNOSTIC_VISIBILITY_BY_CODE:
            self.assertRegex(
                source,
                rf'(?m)^\s*"{re.escape(code)}":\s*(?:True|False),\s+#\s*\S+',
                f"diagnostic config {code} needs a Chinese inline comment",
            )


if __name__ == "__main__":
    unittest.main()
