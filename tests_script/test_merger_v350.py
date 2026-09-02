from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MERGER_PATH = ROOT / "appendix" / "xlsx2verilog_merger.py"
SPEC = importlib.util.spec_from_file_location("xlsx2verilog_merger_v350", MERGER_PATH)
assert SPEC is not None and SPEC.loader is not None
merger = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merger
SPEC.loader.exec_module(merger)


class MergerV350Tests(unittest.TestCase):
    def test_real_case_keeps_body_declaration_and_instance_ports(self) -> None:
        source = (
            ROOT
            / "appendix"
            / "tests"
            / "real_case"
            / "temp"
            / "riscv_top_from_python.v"
        ).read_text(encoding="utf-8")
        old = (
            ROOT
            / "appendix"
            / "tests"
            / "real_case"
            / "temp"
            / "riscv_top.v.xlsx2verilog_merger_backup"
            / "20260901_193618_151232"
            / "riscv_top_before.v"
        ).read_text(encoding="utf-8")

        merged, diagnostics = merger.merge_verilog_text(source, old, "real-case")

        self.assertIn(
            "input  wire [514       -1:0] test_bus2_sig3_ready,", merged
        )
        self.assertIn(
            "//wire                        test_bus2_sig3_ready, //USER: we have top",
            merged,
        )
        self.assertTrue(
            any(item.level == "warning" and "83,113" in item.message for item in diagnostics)
        )
        self.assertIn(
            ".test_rx              (test_rx) //USER: not use", merged
        )
        self.assertIn(
            ".test_rx (test_rx[0][0]), //USER: not change", merged
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

    def test_only_second_structural_line_uses_full_structure_occurrence(self) -> None:
        old = """module top;
generate
endgenerate
//generate //USER: keep second generate disabled
//endgenerate //USER: keep second endgenerate disabled
endmodule
"""
        new = """module top;
generate
endgenerate
generate
endgenerate
endmodule
"""
        merged, _ = merger.merge_verilog_text(new, old, "second-structural")
        self.assertEqual(1, merged.count("\ngenerate\n"))
        self.assertIn(
            "//generate //USER: keep second generate disabled", merged
        )
        self.assertIn(
            "//endgenerate //USER: keep second endgenerate disabled", merged
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

    def test_same_port_on_two_instances_keeps_each_original_line(self) -> None:
        old = """module top;
MOD_A U_A (
    .test_rx(old_a) //USER: keep A
);
MOD_B U_B (
    .test_rx(old_b) //USER: keep B
);
endmodule
"""
        new = """module top;
MOD_A U_A (
    .test_rx(new_a)
);
MOD_B U_B (
    .test_rx(new_b)
);
endmodule
"""
        merged, _ = merger.merge_verilog_text(new, old, "two-instances")
        self.assertIn(".test_rx(old_a) //USER: keep A", merged)
        self.assertIn(".test_rx(old_b) //USER: keep B", merged)

    def test_only_second_same_named_port_can_be_user_owned(self) -> None:
        old = """module top;
MOD_A U_A (
    .test_rx(a)
);
MOD_B U_B (
    .test_rx(old_b) //USER: keep B
);
endmodule
"""
        new = """module top;
MOD_A U_A (
    .test_rx(new_a)
);
MOD_B U_B (
    .test_rx(new_b)
);
endmodule
"""
        merged, _ = merger.merge_verilog_text(new, old, "second-only")
        self.assertIn(".test_rx(new_a)", merged)
        self.assertIn(".test_rx(old_b) //USER: keep B", merged)

    def test_generate_instance_is_part_of_port_identity(self) -> None:
        old = """module top;
MOD_A U_A (
    .test_rx(old_a) //USER: keep A
);
generate
MOD_B U_B (
    .test_rx(old_b) //USER: keep B
);
endgenerate
endmodule
"""
        new = old.replace("old_a", "new_a").replace("old_b", "new_b").replace(
            " //USER: keep A", ""
        ).replace(" //USER: keep B", "")
        merged, _ = merger.merge_verilog_text(new, old, "generate-instance")
        self.assertIn(".test_rx(old_a) //USER: keep A", merged)
        self.assertIn(".test_rx(old_b) //USER: keep B", merged)

    def test_same_port_occurrence_isolated_between_parent_modules(self) -> None:
        old = """module top_a;
MOD U_A (
    .test_rx(old_a) //USER: keep A
);
endmodule
module top_b;
MOD U_B (
    .test_rx(old_b) //USER: keep B
);
endmodule
"""
        new = """module top_a;
MOD U_A (
    .test_rx(new_a)
);
endmodule
module top_b;
MOD U_B (
    .test_rx(new_b)
);
endmodule
"""
        merged, _ = merger.merge_verilog_text(new, old, "two-modules")
        self.assertIn(".test_rx(old_a) //USER: keep A", merged)
        self.assertIn(".test_rx(old_b) //USER: keep B", merged)

    def test_ambiguous_instance_identity_stops_instead_of_choosing_first(self) -> None:
        old = """module top;
MOD U_DUP (
    .test_rx(old) //USER: keep
);
endmodule
"""
        new = """module top;
MOD U_DUP (
    .test_rx(new_a)
);
MOD U_DUP (
    .test_rx(new_b)
);
endmodule
"""
        with self.assertRaisesRegex(merger.MergeError, "多个候选"):
            merger.merge_verilog_text(new, old, "ambiguous")

    def test_known_instance_rename_stops_instead_of_occurrence_guess(self) -> None:
        old = """module top;
MOD U_OLD (
    .test_rx(old) //USER: keep
);
endmodule
"""
        new = """module top;
MOD U_NEW (
    .test_rx(new)
);
endmodule
"""
        with self.assertRaisesRegex(merger.MergeError, "实例 U_OLD"):
            merger.merge_verilog_text(new, old, "renamed-instance")

    def test_occurrence_fallback_when_instance_syntax_is_not_recognized(self) -> None:
        old = """module top;
`MOD U_A (
    .test_rx(old) //USER: keep
);
endmodule
"""
        new = """module top;
`MOD U_A (
    .test_rx(new)
);
endmodule
"""
        merged, diagnostics = merger.merge_verilog_text(
            new, old, "macro-instance"
        )
        self.assertIn(".test_rx(old) //USER: keep", merged)
        self.assertTrue(
            any("occurrence 回退匹配" in item.message for item in diagnostics)
        )

    def test_linux_bcompare_uses_backup_left_and_production_right(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = root / "backup"
            production = root / "rtl" / "top.v"
            backup_file = backup / "top.v"
            production.parent.mkdir()
            backup.mkdir()
            production.write_text("new", encoding="utf-8")
            backup_file.write_text("old", encoding="utf-8")
            result = merger.MergeResult(
                changed=[production],
                backup_directory=backup,
            )
            with (
                patch.object(merger, "AUTO_OPEN_BCOMPARE", True),
                patch.object(merger.sys, "platform", "linux"),
                patch.object(merger.subprocess, "Popen") as popen,
            ):
                diagnostics = merger.launch_bcompare(result)

            popen.assert_called_once()
            self.assertEqual(
                [
                    merger.BCOMPARE_COMMAND,
                    str(backup_file),
                    str(production),
                ],
                popen.call_args.args[0],
            )
            self.assertTrue(any(item.level == "info" for item in diagnostics))


if __name__ == "__main__":
    unittest.main()
