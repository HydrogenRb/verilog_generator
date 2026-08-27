from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests_script.run_review_matrix import integration_sheet, module_sheet, write_xlsx
from xlsx2verilog import (
    Reporter,
    XlsxReader,
    diffuse_variable_value,
    generate,
    list_diffusible_variables,
    parse_workbook,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (
    ROOT
    / "review_test_cases"
    / "12_test_for_techreview2"
    / "techreview2version2.xlsx"
)


class Version2TechReview2Tests(unittest.TestCase):
    def test_parameter_category_and_macro_conflict_locations(self) -> None:
        reporter = Reporter()
        _, modules, _ = parse_workbook(SAMPLE, reporter)
        core = modules["RISCV_CORE_TEST"]

        self.assertEqual(
            core.declared_parameters,
            {
                "DW_SIG1": "11",
                "DW_SIG2": "12",
                "DW_SIG3": "13",
                "RST_LANE": "1",
                "CLK_LANE": "1",
            },
        )
        self.assertNotIn("dw_sig1", core.port_map)
        self.assertNotIn("rst_lane", core.port_map)
        conflict_messages = [
            item.message for item in reporter.items if "宏 `RST_LANE 默认值冲突" in item.message
        ]
        self.assertEqual(len(conflict_messages), 1)
        self.assertIn("页签 RISCV_TOP 为 5", conflict_messages[0])
        self.assertIn("页签 MEM_PHY 为 1", conflict_messages[0])

    def test_parameter_category_values_participate_in_diffusion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / SAMPLE.name
            shutil.copy2(SAMPLE, workbook)
            targets, reporter = list_diffusible_variables(workbook)
            self.assertFalse(reporter.has_errors)
            self.assertIn("RST_LANE", [target.expression for target in targets])

            result = diffuse_variable_value(
                workbook,
                "RST_LANE",
                "3",
                confirm=lambda _prompt: "y",
            )
            self.assertEqual(result.edited_cells, 2)
            raw = XlsxReader().read(workbook, ignore_review_columns=False)
            core = raw.by_name("RISCV_CORE_TEST")
            assert core is not None
            self.assertEqual(core.cell(4, 5), "3")
            self.assertEqual(core.cell(6, 5), "3")
            self.assertFalse(
                any("parameter RST_LANE 默认值冲突" in item.message for item in result.after.items)
            )

    def test_new_review_workbook_generates_na_placeholders_and_valid_generate(self) -> None:
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

            output = root / "generated"
            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual(
                {path.name for path in paths},
                {"riscv_top.v", "riscv_core_test.v", "mem_phy.v", "mem_dat.v"},
            )

            top = (output / "riscv_top.v").read_text(encoding="utf-8")
            core = (output / "riscv_core_test.v").read_text(encoding="utf-8")
            for kind, name, value in (
                ("localparam", "DW_SIG1", "11"),
                ("localparam", "DW_SIG2", "12"),
                ("parameter", "DW_SIG3", "13"),
                ("localparam", "RST_LANE", "1"),
                ("localparam", "CLK_LANE", "1"),
            ):
                self.assertRegex(
                    core,
                    rf"{kind}\s+{name}\s+= {value}",
                )

            self.assertRegex(top, r"(?m)^\s*wire\s+\[DW_SIG3\s+-1:0\]\s+ready_to_process;$")
            self.assertRegex(top, r"(?m)^\s*wire\s+\[DW_SIG2\s+-1:0\]\s+need_to_solve;$")
            self.assertRegex(
                top,
                r"(?m)^\s*\.ready_to_process\s+\(ready_to_process\s*\)"
                r"\s*//TODO:本信号期望有逻辑功能，请完成$",
            )
            self.assertRegex(
                top,
                r"(?m)^\s*\.need_to_solve\s+\(need_to_solve\s*\)"
                r"\s*//TODO:本信号期望有逻辑功能，请完成$",
            )
            self.assertRegex(top, r"(?m)^\s*\.n_rst\s+\(n_rst\[0\]\s*\),$")
            self.assertRegex(
                top,
                r"(?m)^genvar i_gen_u_mem_dat;\ngenerate\n"
                r"for \(i_gen_u_mem_dat = 0;",
            )
            self.assertNotIn("for (genvar", top)

            indexed_connections = [
                line
                for line in top.splitlines()
                if re.search(r"\[i_gen_[a-z0-9_]+\]\),?$", line.strip())
            ]
            self.assertGreaterEqual(len(indexed_connections), 4)
            self.assertEqual(
                len({line.index("[i_gen_") for line in indexed_connections}),
                1,
            )

    def test_child_to_child_wire_uses_driver_name_without_to_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "wire-name.xlsx"
            write_xlsx(
                workbook,
                [
                    (
                        "集成",
                        integration_sheet(
                            [
                                (
                                    ["top", "source", "sink"],
                                    [[("clk", "i"), ("clk", "i"), ("clk", "i")]],
                                ),
                                (
                                    ["source", "sink"],
                                    [[("producer_out", "o"), ("consumer_in", "i")]],
                                ),
                            ]
                        ),
                    ),
                    ("TOP", module_sheet("top", [("clk", 1, None, "i")])),
                    (
                        "SOURCE",
                        module_sheet(
                            "source",
                            [("clk", 1, None, "i"), ("producer_out", 8, None, "o")],
                        ),
                    ),
                    (
                        "SINK",
                        module_sheet(
                            "sink",
                            [("clk", 1, None, "i"), ("consumer_in", 8, None, "i")],
                        ),
                    ),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual(
                {path.name for path in paths},
                {"top.v", "source.v", "sink.v"},
            )
            top = (root / "generated" / "top.v").read_text(encoding="utf-8")
            self.assertRegex(top, r"(?m)^\s*wire\s+\[8\s+-1:0\]\s+w_producer_out;$")
            self.assertNotIn("_to_", top)

    def test_explicit_top_bit_select_is_preserved_without_auto_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "bit-select.xlsx"
            write_xlsx(
                workbook,
                [
                    (
                        "集成",
                        integration_sheet(
                            [(["top", "child"], [[("n_rst[0]", "i"), ("n_rst", "i")]])]
                        ),
                    ),
                    ("TOP", module_sheet("top", [("n_rst", 5, None, "i")])),
                    ("CHILD", module_sheet("child", [("n_rst", 1, None, "i")])),
                ],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual({path.name for path in paths}, {"top.v", "child.v"})
            top = (root / "generated" / "top.v").read_text(encoding="utf-8")
            self.assertRegex(top, r"(?m)^\s*\.n_rst\s+\(n_rst\[0\]\s*\)$")
            self.assertNotIn("{{", top[top.index("U_CHILD") :])


if __name__ == "__main__":
    unittest.main()
