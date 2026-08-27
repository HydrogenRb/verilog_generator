from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from appendix import xlsx2verilog_merger as merger
from appendix.xlsx2verilog_merger import MergeError, merge_paths, merge_verilog_text
from xlsx2verilog import SCRIPT_VERSION, generate, write_xlsx_cell_updates


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
        self.assertEqual(len(diagnostics), 1)
        self.assertIn("保留 USER CODE 段 before statement#1", diagnostics[0].message)

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
                "multi.v: 保留 USER CODE 段 before statement#1",
            },
            {item.message for item in diagnostics},
        )

    def test_v331_preserves_commented_assign_declaration_and_port_lines(
        self,
    ) -> None:
        old = verilog(
            "//input wire [7:0] sig_a; //user: not use\n"
            "//assign sig_a //user: assign later\n"
            "CHILD U_CHILD (\n"
            "    .sigA (temp_w_sig_a), //user: change the input source\n"
            "    //.sigB (temp_w_sigb) //user： not use\n"
            ");"
        )
        new = verilog(
            "input wire [15:0] sig_a;\n"
            "assign sig_a = '0;\n"
            "CHILD U_CHILD (\n"
            "    .sigA (generated_sig_a),\n"
            "    .sigB (generated_sig_b)\n"
            ");"
        )

        merged, diagnostics = merge_verilog_text(new, old, "v331-lines.v")
        self.assertIn("//input wire [7:0] sig_a; //user: not use", merged)
        self.assertIn("//assign sig_a //user: assign later", merged)
        self.assertIn(
            ".sigA (temp_w_sig_a), //user: change the input source",
            merged,
        )
        self.assertIn("//.sigB (temp_w_sigb) //user： not use", merged)
        self.assertNotIn("[15:0] sig_a", merged)
        self.assertNotIn("generated_sig_a", merged)
        self.assertNotIn("generated_sig_b", merged)
        self.assertEqual(
            {
                "signal 声明",
                "手工 assign",
                "实例端口连接",
            },
            {
                label
                for label in ("signal 声明", "手工 assign", "实例端口连接")
                if any(label in item.message for item in diagnostics)
            },
        )
        self.assertEqual(
            sum("实例端口连接" in item.message for item in diagnostics), 2
        )

    def test_v331_user_line_targets_must_exist_and_be_unambiguous(self) -> None:
        removed_signal = verilog("//wire removed; //user: do not lose")
        with self.assertRaisesRegex(MergeError, "缺少 //USER: signal 声明"):
            merge_verilog_text(verilog("wire other;"), removed_signal, "removed.v")

        old_port = verilog(
            "CHILD U_OLD (\n"
            "    .sigA (manual) //user: keep\n"
            ");"
        )
        duplicated_port = verilog(
            "CHILD U_A (\n"
            "    .sigA (a)\n"
            ");\n"
            "CHILD U_B (\n"
            "    .sigA (b)\n"
            ");"
        )
        with self.assertRaisesRegex(MergeError, "匹配到 2 条"):
            merge_verilog_text(duplicated_port, old_port, "ambiguous-port.v")

    def test_main_and_merger_versions_are_v345(self) -> None:
        self.assertEqual(SCRIPT_VERSION, "Version V3.45")
        self.assertEqual(merger.VERSION, SCRIPT_VERSION)

    def test_v34_recursively_matches_unique_target_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new"
            target = root / "production"
            nested = target / "rtl" / "core"
            source.mkdir()
            nested.mkdir(parents=True)
            (source / "top.v").write_text(
                verilog("wire new_generated;"), encoding="utf-8"
            )
            (nested / "top.v").write_text(
                verilog("wire old_generated;"), encoding="utf-8"
            )

            result = merge_paths(source, target, create_backup=False)
            self.assertIn("wire new_generated;", (nested / "top.v").read_text(encoding="utf-8"))
            self.assertFalse((target / "top.v").exists())
            self.assertEqual(result.changed, [(nested / "top.v").resolve()])

    def test_v34_recursive_target_duplicate_reports_every_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new"
            target = root / "production"
            source.mkdir()
            (target / "a").mkdir(parents=True)
            (target / "b").mkdir(parents=True)
            (source / "top.v").write_text(verilog("wire next;"), encoding="utf-8")
            first = target / "a" / "top.v"
            second = target / "b" / "TOP.V"
            first.write_text(verilog("wire first;"), encoding="utf-8")
            second.write_text(verilog("wire second;"), encoding="utf-8")

            with self.assertRaises(MergeError) as caught:
                merge_paths(source, target, check_only=True)
            message = str(caught.exception)
            self.assertIn("重名", message)
            self.assertIn(str(first.resolve()), message)
            self.assertIn(str(second.resolve()), message)

    def test_v34_cli_uses_configured_default_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new"
            target = root / "production"
            source.mkdir()
            target.mkdir()
            (source / "top.v").write_text(verilog("wire next;"), encoding="utf-8")
            (target / "top.v").write_text(verilog("wire old;"), encoding="utf-8")
            output = StringIO()
            errors = StringIO()
            with (
                patch.object(merger, "DEFAULT_TARGET_PROJECT", target),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                code = merger.main([str(source), "--check"])
            self.assertEqual(code, 0, errors.getvalue())
            self.assertIn("使用文件顶部默认生产目标", output.getvalue())
            self.assertIn("检查完成", output.getvalue())

            output = StringIO()
            errors = StringIO()
            with (
                patch.object(merger, "DEFAULT_TARGET_PROJECT", None),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                code = merger.main([str(source), "--check"])
            self.assertEqual(code, 2)
            self.assertIn("DEFAULT_TARGET_PROJECT 未配置", errors.getvalue())

    def test_v345_user_marker_preserves_commented_parameters(self) -> None:
        old = verilog(
            "//localparam AAA = 1; //USER:no change\n"
            "//parameter BBB = 70; //USER： no change"
        )
        new = verilog(
            "localparam AAA = 2;\n"
            "parameter BBB = 3;"
        )
        merged, diagnostics = merge_verilog_text(new, old, "parameters.v")
        self.assertIn("//localparam AAA = 1; //USER:no change", merged)
        self.assertIn("//parameter BBB = 70; //USER： no change", merged)
        self.assertNotIn("localparam AAA = 2", merged)
        self.assertNotIn("parameter BBB = 3", merged)
        self.assertEqual(
            sum("parameter 声明" in item.message for item in diagnostics),
            2,
        )

    def test_v345_ignores_duplicate_target_names_unrelated_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new"
            target = root / "production"
            source.mkdir()
            (target / "rtl").mkdir(parents=True)
            (target / "legacy_a").mkdir(parents=True)
            (target / "legacy_b").mkdir(parents=True)
            (source / "a.v").write_text(verilog("wire next_a;"), encoding="utf-8")
            (target / "rtl" / "a.v").write_text(
                verilog("wire old_a;"), encoding="utf-8"
            )
            (target / "legacy_a" / "same_b.v").write_text(
                verilog("wire b1;"), encoding="utf-8"
            )
            (target / "legacy_b" / "SAME_B.V").write_text(
                verilog("wire b2;"), encoding="utf-8"
            )

            result = merge_paths(source, target, create_backup=False)
            self.assertEqual(result.changed, [(target / "rtl" / "a.v").resolve()])
            self.assertIn(
                "wire next_a;",
                (target / "rtl" / "a.v").read_text(encoding="utf-8"),
            )

    def test_v345_cli_confirms_each_change_and_prints_production_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new"
            target = root / "production"
            source.mkdir()
            target.mkdir()
            for name in ("a.v", "b.v"):
                (source / name).write_text(
                    verilog(f"wire new_{name[0]};"), encoding="utf-8"
                )
                (target / name).write_text(
                    verilog(f"wire old_{name[0]};"), encoding="utf-8"
                )
            output = StringIO()
            errors = StringIO()
            with (
                patch("builtins.input", side_effect=["y", "n"]) as confirmed,
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                code = merger.main(
                    [str(source), str(target), "--no-backup"]
                )
            self.assertEqual(code, 0, errors.getvalue())
            self.assertEqual(confirmed.call_count, 2)
            self.assertIn("wire new_a;", (target / "a.v").read_text(encoding="utf-8"))
            self.assertIn("wire old_b;", (target / "b.v").read_text(encoding="utf-8"))
            rendered = output.getvalue()
            self.assertIn("跳过 1", rendered)
            self.assertIn("生产文件与路径", rendered)
            self.assertIn(str((target / "a.v").resolve()), rendered)
            self.assertNotIn(str((target / "b.v").resolve()) + "\n\n", rendered)

    def test_added_and_removed_instances_follow_the_new_structure(self) -> None:
        old = (
            "module TOP;\n"
            "/*USER CODE BEGIN before statement*/\n"
            "// root hook\n"
            "/*USER CODE END   before statement*/\n"
            "/*USER CODE BEGIN before CHILD_A*/\n"
            "/*USER CODE END   before CHILD_A*/\n"
            "CHILD_A U_A();\n"
            "/*USER CODE BEGIN after CHILD_A*/\n"
            "/*USER CODE END   after CHILD_A*/\n"
            "endmodule\n"
        )
        added = old.replace(
            "endmodule\n",
            "/*USER CODE BEGIN before CHILD_B*/\n"
            "/*USER CODE END   before CHILD_B*/\n"
            "CHILD_B U_B();\n"
            "/*USER CODE BEGIN after CHILD_B*/\n"
            "/*USER CODE END   after CHILD_B*/\n"
            "endmodule\n",
        )
        merged, _ = merge_verilog_text(added, old, "added.v")
        self.assertIn("CHILD_B U_B();", merged)
        self.assertIn("// root hook", merged)

        removed, _ = merge_verilog_text(old, added, "removed-empty.v")
        self.assertNotIn("CHILD_B U_B();", removed)
        with_user_hook = added.replace(
            "/*USER CODE BEGIN before CHILD_B*/\n",
            "/*USER CODE BEGIN before CHILD_B*/\n// keep B hook\n",
        )
        with self.assertRaisesRegex(MergeError, "before CHILD_B#1"):
            merge_verilog_text(old, with_user_hook, "removed-nonempty.v")

    def test_parameter_signal_kind_and_explicit_user_assign_are_preserved(
        self,
    ) -> None:
        old = verilog(
            "parameter MODE = 1;\n"
            "localparam LOCKED = 3;\n"
            "reg [7:0] status;\n"
            "assign status = 8'hA5; //USER: keep old assignment",
        )
        new = verilog(
            "localparam MODE = 2;\n"
            "parameter LOCKED = 4;\n"
            "wire [15:0] status;\n"
            "assign status = 16'h0000;",
        )
        merged, diagnostics = merge_verilog_text(new, old, "manual-edits.v")
        self.assertIn("parameter MODE = 2;", merged)
        self.assertNotIn("parameter MODE = 1;", merged)
        self.assertIn("localparam LOCKED = 4;", merged)
        self.assertRegex(merged, r"(?m)^reg \[15:0\] status;$")
        self.assertIn("assign status = 8'hA5; //USER: keep old assignment", merged)
        self.assertNotIn("16'h0000", merged)
        self.assertEqual(
            {
                "manual-edits.v: 保留 TOP.status 的 reg 声明类型"
                "（新生成版本为 wire）",
                "manual-edits.v: 保留 TOP.MODE 的 parameter 参数类型"
                "（新生成版本为 localparam）",
                "manual-edits.v: 保留 TOP.LOCKED 的 localparam 参数类型"
                "（新生成版本为 parameter）",
                "manual-edits.v: 保留 TOP.status 的 //USER: 手工 assign",
            },
            {item.message for item in diagnostics},
        )

    def test_unmarked_assign_uses_new_code_and_marked_assign_requires_a_target(
        self,
    ) -> None:
        old = verilog("assign status = 8'hA5;")
        new = verilog("assign status = 16'h0000;")
        merged, diagnostics = merge_verilog_text(new, old, "unmarked.v")
        self.assertIn("assign status = 16'h0000;", merged)
        self.assertNotIn("8'hA5", merged)
        self.assertFalse(diagnostics)

        marked_old = verilog(
            "assign removed_status = 8'hA5; //USER: must not disappear"
        )
        with self.assertRaisesRegex(MergeError, "缺少 //USER: 手工 assign"):
            merge_verilog_text(new, marked_old, "removed-assign.v")

        multiline_marker = verilog(
            "assign removed_status =\n"
            "    8'hA5; //USER: unsupported multiline assignment"
        )
        with self.assertRaisesRegex(MergeError, "完整的单行 assign"):
            merge_verilog_text(new, multiline_marker, "multiline-assign.v")

    def test_user_assign_can_follow_a_unique_changed_bit_select(self) -> None:
        old = verilog(
            "assign status[7:4] = 4'hA; // USER: preserve changed slice"
        )
        new = verilog("assign status[15:8] = 8'h00;")
        merged, diagnostics = merge_verilog_text(new, old, "slice.v")
        self.assertIn(
            "assign status[7:4] = 4'hA; // USER: preserve changed slice",
            merged,
        )
        self.assertNotIn("status[15:8]", merged)
        self.assertEqual(len(diagnostics), 1)
        self.assertIn("按唯一根信号匹配", diagnostics[0].message)

        ambiguous = verilog(
            "assign status[15:8] = 8'h00;\n"
            "assign status[7:0] = 8'h00;"
        )
        with self.assertRaisesRegex(MergeError, "匹配到 2 条"):
            merge_verilog_text(ambiguous, old, "ambiguous-slice.v")

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

    def test_default_backup_is_anchored_on_new_code_side_and_logs_actions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            new_side = root / "new_code_side"
            production_side = root / "production_side"
            source = new_side / "generated"
            target = production_side / "rtl"
            source.mkdir(parents=True)
            target.mkdir(parents=True)
            old = verilog("wire old_generated;")
            (source / "top.v").write_text(
                verilog("wire new_generated;"), encoding="utf-8"
            )
            (target / "top.v").write_text(old, encoding="utf-8")

            result = merge_paths(source, target)
            self.assertIsNotNone(result.backup_directory)
            assert result.backup_directory is not None
            result.backup_directory.relative_to(new_side.resolve())
            with self.assertRaises(ValueError):
                result.backup_directory.relative_to(production_side)
            self.assertEqual(
                (result.backup_directory / "top.v").read_text(encoding="utf-8"),
                old,
            )
            messages = [item.message for item in result.diagnostics]
            self.assertTrue(any("将覆盖目标文件" in item for item in messages))
            self.assertTrue(any("已备份旧生产文件" in item for item in messages))
            self.assertTrue(any("已写入合并结果" in item for item in messages))

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
                r"wire\s+\[10\s+-1:0\]\[CLK_BUS_0\s+-1:0\]\s+"
                r"high_clk_after_pll_0;",
            )
            old_top = old_top.replace(
                "/*USER CODE BEGIN before statement*/",
                "/*USER CODE BEGIN before statement*/\n// USER: keep me",
                1,
            )
            old_top, declaration_updates = re.subn(
                r"(?m)^wire(?=\s+\[10\s+-1:0\]\[CLK_BUS_0\s+-1:0\]\s+"
                r"high_clk_after_pll_0;)",
                "reg",
                old_top,
                count=1,
            )
            self.assertEqual(declaration_updates, 1)
            old_top, parameter_updates = re.subn(
                r"(?m)^(\s*)localparam(?=\s+RST_LANE\s+=)",
                r"\1parameter",
                old_top,
                count=1,
            )
            self.assertEqual(parameter_updates, 1)
            old_top, assignment_updates = re.subn(
                r"(?m)^assign\s+uid\s+=.*?;$",
                "assign uid = {UID_SIZE{1'b1}}; //USER: keep project UID",
                old_top,
                count=1,
            )
            self.assertEqual(assignment_updates, 1)
            top_path.write_text(old_top, encoding="utf-8", newline="\n")

            shutil.copy2(EDGE_SAMPLE, adjusted)
            # The integration sheet is sheet1 in the V3 review workbook;
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
                r"reg\s+\[9\s+-1:0\]\[CLK_BUS_0\s+-1:0\]\s+"
                r"high_clk_after_pll_0;",
            )
            self.assertRegex(merged_top, r"(?m)^\s*parameter\s+RST_LANE\s+=")
            self.assertIn(
                "assign uid = {UID_SIZE{1'b1}}; //USER: keep project UID",
                merged_top,
            )
            self.assertIn("// USER: keep me", merged_top)
            self.assertEqual(len(result.changed), 1)
            self.assertIsNotNone(result.backup_directory)


if __name__ == "__main__":
    unittest.main()
