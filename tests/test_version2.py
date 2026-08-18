from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.run_review_matrix import integration_sheet, module_sheet, write_xlsx
from xlsx2verilog import generate


ROOT = Path(__file__).resolve().parents[1]
V2_SAMPLE = ROOT / "review_test_cases" / "09_version_2" / "test.xlsx"


class Version2GenerationTests(unittest.TestCase):
    def test_real_v2_template_subsets_and_upper_macro_win(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, reporter = generate(V2_SAMPLE, Path(temporary))
            self.assertFalse(reporter.has_errors)
            self.assertEqual(len(paths), 3)
            self.assertTrue(any(item.level == "信息" and "模板端口展开不一致" in item.message for item in reporter.items))
            macro_conflicts = [item for item in reporter.items if "APB_1" in item.message and "冲突" in item.message]
            self.assertEqual(len(macro_conflicts), 1)
            self.assertEqual(macro_conflicts[0].level, "警告")

            output = Path(temporary)
            top = (output / "RISCV_TOP.v").read_text(encoding="utf-8")
            core = (output / "RISCV_CORE_TEST.v").read_text(encoding="utf-8")
            phy = (output / "MEM_PHY.v").read_text(encoding="utf-8")
            self.assertRegex(top, r"(?m)^`define APB_1\s+4$")
            self.assertNotIn("`define APB_1", core)
            self.assertNotIn("`define APB_1", phy)
            self.assertIn(".test_bus_sig3_dat", top)
            mem_instance = top[top.index("u_mem_phy") :]
            self.assertNotIn(".test_bus_sig3_dat", mem_instance)

    def test_child_parameters_are_promoted_and_parameter_widths_do_not_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "parameter-v2.xlsx"
            write_xlsx(
                workbook,
                [
                    (
                        "Integration",
                        integration_sheet(
                            [
                                (["TOP", "SOURCE", "SINK"], [[("clk", "i"), ("clk", "i"), ("clk", "i")]]),
                                (["SOURCE", "SINK"], [[("payload", "o"), ("payload", "i")]]),
                            ]
                        ),
                    ),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("SOURCE", module_sheet("SOURCE", [("clk", 1, None, "i"), ("payload", "WIDTH", 8, "o")])),
                    ("SINK", module_sheet("SINK", [("clk", 1, None, "i"), ("payload", "WIDTH", 4, "i")])),
                ],
            )
            output = root / "generated"
            _, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertFalse(any("位宽不匹配" in item.message for item in reporter.items))
            top = (output / "TOP.v").read_text(encoding="utf-8")
            self.assertIn("parameter integer WIDTH = 8", top)
            self.assertNotIn("localparam", top)
            self.assertRegex(top, r"\.WIDTH\s+\(WIDTH\)")
            self.assertRegex(top, r"wire \[WIDTH-1:0\]\s+w_payload;")

    def test_internal_literal_width_uses_maximum_low_bits_and_zero_fill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            _, reporter = generate(ROOT / "test.xlsx", output)
            self.assertFalse(reporter.has_errors)
            top = (output / "RISCV_TOP.v").read_text(encoding="utf-8")
            self.assertRegex(top, r"wire \[11\s+-1:0\]\s+w_apb_1;")
            self.assertRegex(top, r"\.apb_1\s+\(w_apb_1\[0\]")
            self.assertIn("assign w_apb_1[11-1:1] = '0;", top)


if __name__ == "__main__":
    unittest.main()
