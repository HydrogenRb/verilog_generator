from __future__ import annotations

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
from xlsx2verilog import Reporter, generate, print_startup_banner


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
                    rf"(?m)^assign test_bus2_{name}_valid\s+= 1;$",
                )

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
                "CustomScipt xlsx2verilog",
                "Version V3.1",
                "2026.8.24",
                "Contact xxx-xxxx in case",
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


if __name__ == "__main__":
    unittest.main()
