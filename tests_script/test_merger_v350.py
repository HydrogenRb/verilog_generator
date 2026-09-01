from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MERGER_PATH = ROOT / "appendix" / "xlsx2verilog_merger.py"
SPEC = importlib.util.spec_from_file_location("xlsx2verilog_merger_v350", MERGER_PATH)
assert SPEC is not None and SPEC.loader is not None
merger = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merger
SPEC.loader.exec_module(merger)


class MergerV350Tests(unittest.TestCase):
    def test_real_case_keeps_body_user_declaration_out_of_top_port(self) -> None:
        source = (
            ROOT / "appendix" / "tests" / "real_case" / "temp" / "riscv_top.v"
        ).read_text(encoding="utf-8")
        old = (
            ROOT
            / "appendix"
            / "tests"
            / "real_case"
            / "temp"
            / "riscv_top.v.xlsx2verilog_merger_backup"
            / "20260901_140301_561400"
            / "riscv_top.v"
        ).read_text(encoding="utf-8")

        merged, diagnostics = merger.merge_verilog_text(source, old, "real-case")

        self.assertIn(
            "input  wire [114       -1:0] test_bus2_sig3_ready,", merged
        )
        self.assertIn(
            "//wire                        test_bus2_sig3_ready, //USER: we have top",
            merged,
        )
        self.assertTrue(
            any(item.level == "warning" and "83,112" in item.message for item in diagnostics)
        )

    def test_manual_fallback_assign_and_user_block_are_both_preserved(self) -> None:
        old = """module t (output wire [8-1:0] aaa);
//assign aaa = {8{1'b0}}; //USER: use manual drive
/*USER CODE BEGIN after statement*/
assign aaa = xxx;
/*USER CODE END   after statement*/
endmodule
"""
        new = """module t (output wire [8-1:0] aaa);
assign aaa = {8{1'b0}};
/*USER CODE BEGIN after statement*/

/*USER CODE END   after statement*/
endmodule
"""
        merged, _ = merger.merge_verilog_text(new, old, "manual-assign")
        self.assertIn("//assign aaa = {8{1'b0}}; //USER: use manual drive", merged)
        self.assertIn("assign aaa = xxx;", merged)

    def test_commented_generate_control_lines_are_preserved(self) -> None:
        old = """module t;
//genvar i; //USER: custom block
//generate //USER: custom block
for (i=0; i<2; i=i+1) begin : G
end
//endgenerate //USER: custom block
endmodule
"""
        new = """module t;
genvar i;
generate
for (i=0; i<2; i=i+1) begin : G
end
endgenerate
endmodule
"""
        merged, diagnostics = merger.merge_verilog_text(new, old, "generate")
        self.assertIn("//genvar i; //USER: custom block", merged)
        self.assertIn("//generate //USER: custom block", merged)
        self.assertIn("//endgenerate //USER: custom block", merged)
        self.assertEqual(
            3,
            sum("旧文件第" in item.message for item in diagnostics),
        )

    def test_multiple_body_declarations_warn_instead_of_error(self) -> None:
        old = """module t (output wire sig);
//wire sig; //USER: keep body declaration
endmodule
"""
        new = """module t (output wire sig);
wire sig;
wire sig;
endmodule
"""
        merged, diagnostics = merger.merge_verilog_text(new, old, "duplicates")
        self.assertEqual(1, merged.count("//wire sig; //USER: keep body declaration"))
        warning = next(
            item
            for item in diagnostics
            if item.level == "warning" and "新文件第 2,3 行" in item.message
        )
        self.assertIn("新文件第 2,3 行", warning.message)

    def test_multiple_declarators_each_keep_wire_reg_kind(self) -> None:
        old = "module t;\nreg [7:0] a, b;\nendmodule\n"
        new = "module t;\nwire [15:0] a, b;\nendmodule\n"
        merged, _ = merger.merge_verilog_text(new, old, "multi-declarator")
        self.assertIn("reg [15:0] a, b;", merged)


if __name__ == "__main__":
    unittest.main()
