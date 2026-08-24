from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appendix import xlsx2verilog_merger as merger
from appendix.xlsx2verilog_merger import MergeError, merge_paths, merge_verilog_text
from xlsx2verilog import generate, write_xlsx_cell_updates


ROOT = Path(__file__).resolve().parents[2]
EDGE_SAMPLE = (
    ROOT / "review_test_cases" / "14_edge_case_test_problem" / "eage_case.xlsx"
)
INTEGRATION = "集成_RISCV_TOP"


def verilog(body: str, user: str = "\n") -> str:
    return (
        "module TOP;\n"
        "/*USER CODE BEGIN before statement*/"
        f"{user}"
        "/*USER CODE END   before statement*/\n"
        f"{body}\n"
        "endmodule\n"
    )


class MergerUnitTests(unittest.TestCase):
    def test_new_structure_replaces_generated_code_and_preserves_user_region(self) -> None:
        old = verilog("wire old_generated;", "\nassign user_logic = 1'b1;\n")
        new = verilog("wire new_generated;", "\n")
        merged, diagnostics = merge_verilog_text(new, old, "top.v")
        self.assertIn("wire new_generated;", merged)
        self.assertNotIn("wire old_generated;", merged)
        self.assertIn("assign user_logic = 1'b1;", merged)
        self.assertFalse(diagnostics)

    def test_matching_module_signal_preserves_reg_or_wire_kind(self) -> None:
        old = (
            "module TOP (\n"
            "    output reg  [7:0] status\n"
            ");\n"
            "reg  [7:0] state;\n"
            "wire legacy_only;\n"
            "/*USER CODE BEGIN before statement*/\n"
            "reg user_owned;\n"
            "/*USER CODE END   before statement*/\n"
            "endmodule\n"
            "module CHILD;\n"
            "wire [3:0] state;\n"
            "endmodule\n"
        )
        new = (
            "module TOP (\n"
            "    output wire [15:0] status\n"
            ");\n"
            "wire [15:0] state;\n"
            "wire generated_new;\n"
            "/*USER CODE BEGIN before statement*/\n"
            "\n"
            "/*USER CODE END   before statement*/\n"
            "endmodule\n"
            "module CHILD;\n"
            "reg [8:0] state;\n"
            "endmodule\n"
        )

        merged, diagnostics = merge_verilog_text(new, old, "multi.v")
        self.assertRegex(merged, r"output\s+reg\s+\[15:0\]\s+status")
        self.assertRegex(merged, r"(?m)^reg\s+\[15:0\]\s+state;")
        self.assertRegex(
            merged,
            r"(?s)module CHILD;.*?wire\s+\[8:0\]\s+state;",
        )
        self.assertIn("reg user_owned;", merged)
        self.assertNotIn("legacy_only", merged)
        self.assertEqual(
            {
                "multi.v: 保留 TOP.status 的 reg 声明类型（新生成版本为 wire）",
                "multi.v: 保留 TOP.state 的 reg 声明类型（新生成版本为 wire）",
                "multi.v: 保留 CHILD.state 的 wire 声明类型（新生成版本为 reg）",
            },
            {item.message for item in diagnostics},
        )

    def test_damaged_or_removed_nonempty_region_blocks_merge(self) -> None:
        damaged = "/*USER CODE BEGIN a*/\ntext\n/*USER CODE END b*/\n"
        with self.assertRaisesRegex(MergeError, "不匹配"):
            merge_verilog_text(damaged, damaged, "broken.v")
        old = verilog("wire old;", "\nuser_code();\n")
        new = "module TOP;\nendmodule\n"
        with self.assertRaisesRegex(MergeError, "无法安全保留"):
            merge_verilog_text(new, old, "removed.v")

    def test_check_only_is_non_mutating_and_merge_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new"
            target = root / "project"
            backup = root / "backup"
            source.mkdir()
            target.mkdir()
            old = verilog("wire old_generated;", "\n// USER\n")
            new = verilog("wire new_generated;", "\n")
            (source / "top.v").write_text(new, encoding="utf-8")
            (target / "top.v").write_text(old, encoding="utf-8")

            checked = merge_paths(source, target, check_only=True)
            self.assertTrue(checked.check_only)
            self.assertEqual((target / "top.v").read_text(encoding="utf-8"), old)
            self.assertFalse(backup.exists())

            result = merge_paths(source, target, backup_directory=backup)
            merged = (target / "top.v").read_text(encoding="utf-8")
            self.assertIn("wire new_generated;", merged)
            self.assertIn("// USER", merged)
            self.assertEqual((backup / "top.v").read_text(encoding="utf-8"), old)
            self.assertEqual(result.backup_directory, backup.resolve())

    def test_mid_transaction_failure_rolls_back_every_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new"
            target = root / "project"
            source.mkdir()
            target.mkdir()
            old_a = verilog("wire old_a;")
            old_b = verilog("wire old_b;")
            (target / "a.v").write_text(old_a, encoding="utf-8")
            (target / "b.v").write_text(old_b, encoding="utf-8")
            (source / "a.v").write_text(verilog("wire new_a;"), encoding="utf-8")
            (source / "b.v").write_text(verilog("wire new_b;"), encoding="utf-8")

            original_replace = merger.os.replace
            failed = False

            def fail_second_target(source_path: object, target_path: object) -> None:
                nonlocal failed
                target_name = Path(target_path).name
                source_name = Path(source_path).name
                if (
                    not failed
                    and target_name == "b.v"
                    and "xlsx2verilog_merger" in source_name
                ):
                    failed = True
                    raise OSError("injected write failure")
                original_replace(source_path, target_path)

            with patch.object(merger.os, "replace", side_effect=fail_second_target):
                with self.assertRaisesRegex(MergeError, "已回滚"):
                    merge_paths(source, target, create_backup=False)
            self.assertEqual((target / "a.v").read_text(encoding="utf-8"), old_a)
            self.assertEqual((target / "b.v").read_text(encoding="utf-8"), old_b)


class EdgeCaseMergeReview(unittest.TestCase):
    def test_adjusted_edge_case_overwrites_structure_but_keeps_user_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_generated = root / "old_generated"
            new_generated = root / "new_generated"
            project = root / "project"
            adjusted = root / "eage_case_count_9.xlsx"

            old_paths, old_reporter = generate(
                EDGE_SAMPLE,
                old_generated,
                integration_sheet=INTEGRATION,
            )
            self.assertFalse(old_reporter.has_errors)
            self.assertEqual(len(old_paths), 5)
            shutil.copytree(old_generated, project)
            top_path = project / "riscv_top.v"
            old_top = top_path.read_text(encoding="utf-8")
            self.assertIn("i_gen_u_riscv_crg < 10", old_top)
            self.assertRegex(
                old_top,
                r"wire\s+\[10\s+-1:0\]\[114\s+-1:0\]\s+high_clk_after_pll_0;",
            )
            old_top = old_top.replace(
                "/*USER CODE BEGIN before statement*/",
                "/*USER CODE BEGIN before statement*/\n// USER: keep me",
                1,
            )
            old_top, declaration_updates = re.subn(
                r"(?m)^wire(?=\s+\[10\s+-1:0\]\[114\s+-1:0\]\s+"
                r"high_clk_after_pll_0;)",
                "reg",
                old_top,
                count=1,
            )
            self.assertEqual(declaration_updates, 1)
            top_path.write_text(old_top, encoding="utf-8", newline="\n")

            shutil.copy2(EDGE_SAMPLE, adjusted)
            # The integration sheet is sheet1 in the V3.12 review workbook;
            # row 6 / column 36 is RISCV_CRG's explicit instance count.
            write_xlsx_cell_updates(
                adjusted,
                {"xl/worksheets/sheet1.xml": {(6, 36): "9"}},
            )
            new_paths, new_reporter = generate(
                adjusted,
                new_generated,
                integration_sheet=INTEGRATION,
            )
            self.assertFalse(new_reporter.has_errors)
            self.assertEqual(len(new_paths), 5)

            result = merge_paths(new_generated, project)
            merged_top = top_path.read_text(encoding="utf-8")
            self.assertIn("i_gen_u_riscv_crg < 9", merged_top)
            self.assertNotIn("i_gen_u_riscv_crg < 10", merged_top)
            self.assertRegex(
                merged_top,
                r"reg\s+\[9\s+-1:0\]\[114\s+-1:0\]\s+high_clk_after_pll_0;",
            )
            self.assertIn("// USER: keep me", merged_top)
            self.assertEqual(len(result.changed), 1)
            self.assertIsNotNone(result.backup_directory)


if __name__ == "__main__":
    unittest.main()
