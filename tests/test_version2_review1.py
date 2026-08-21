from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tests.run_review_matrix import integration_sheet, module_sheet, set_cell, write_xlsx
from xlsx2verilog import (
    Reporter,
    XlsxReader,
    analyze_port_dimensions,
    choose_integration_sheet,
    discover_integrations,
    diffuse_variable_value,
    generate,
    list_diffusible_variables,
    parse_workbook,
)


ROOT = Path(__file__).resolve().parents[1]
SPECIAL = ROOT / "review_test_cases" / "10_special_case" / "review2case.xlsx"


class Version2TechReview1Tests(unittest.TestCase):
    @staticmethod
    def write_multi_integration_workbook(path: Path) -> None:
        write_xlsx(
            path,
            [
                (
                    "集成_riscv_top",
                    integration_sheet(
                        [(["riscv_top", "core"], [[("clk", "i"), ("clk", "i")]])]
                    ),
                ),
                (
                    "集成_debug_top",
                    integration_sheet(
                        [(["debug_top", "trace"], [[("clk", "i"), ("clk", "i")]])]
                    ),
                ),
                ("RISCV_TOP", module_sheet("riscv_top", [("clk", 1, None, "i")])),
                ("CORE", module_sheet("core", [("clk", 1, None, "i")])),
                ("DEBUG_TOP", module_sheet("debug_top", [("clk", 1, None, "i")])),
                ("TRACE", module_sheet("trace", [("clk", 1, None, "i")])),
            ],
        )

    def test_multiple_named_integrations_require_and_honor_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook_path = root / "multi.xlsx"
            self.write_multi_integration_workbook(workbook_path)

            workbook = XlsxReader().read(workbook_path)
            self.assertEqual(
                [item.sheet_name for item in discover_integrations(workbook)],
                ["集成_riscv_top", "集成_debug_top"],
            )

            missing_selection = Reporter()
            parse_workbook(workbook_path, missing_selection)
            self.assertTrue(missing_selection.has_errors)
            self.assertTrue(
                any("检测到多个集成页签" in item.message for item in missing_selection.items)
            )

            selected_reporter = Reporter()
            _, modules, integration = parse_workbook(
                workbook_path,
                selected_reporter,
                integration_sheet="集成_RISCV_TOP",
            )
            self.assertFalse(selected_reporter.has_errors)
            self.assertIsNotNone(integration)
            assert integration is not None
            self.assertEqual(integration.sheet_name, "集成_riscv_top")
            self.assertEqual(set(modules), {"RISCV_TOP", "CORE"})

            output = root / "generated"
            paths, generated_reporter = generate(
                workbook_path,
                output,
                integration_sheet="集成_debug_top",
            )
            self.assertFalse(generated_reporter.has_errors)
            self.assertEqual(
                {path.name for path in paths},
                {"debug_top.v", "trace.v"},
            )
            self.assertFalse((output / "riscv_top.v").exists())
            self.assertFalse((output / "core.v").exists())

    def test_integration_menu_selects_only_when_multiple_candidates_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for sheet_name in ("集成", "集成_uart_top"):
                with self.subTest(sheet_name=sheet_name):
                    workbook_path = root / f"{sheet_name}.xlsx"
                    write_xlsx(
                        workbook_path,
                        [
                            (
                                sheet_name,
                                integration_sheet(
                                    [(["uart_top", "uart"], [[("clk", "i"), ("clk", "i")]])]
                                ),
                            ),
                            ("UART_TOP", module_sheet("uart_top", [("clk", 1, None, "i")])),
                            ("UART", module_sheet("uart", [("clk", 1, None, "i")])),
                        ],
                    )

                    def unexpected_menu(_title: str, _options: list[str]) -> int | None:
                        self.fail("单个集成页签不应打开选择菜单")

                    self.assertEqual(
                        choose_integration_sheet(workbook_path, menu=unexpected_menu),
                        sheet_name,
                    )

            multi_path = root / "multi.xlsx"
            self.write_multi_integration_workbook(multi_path)
            captured: list[tuple[str, list[str]]] = []

            def select_second(title: str, options: list[str]) -> int | None:
                captured.append((title, options))
                return 1

            self.assertEqual(
                choose_integration_sheet(multi_path, menu=select_second),
                "集成_debug_top",
            )
            self.assertEqual(len(captured), 1)
            self.assertIn("集成_riscv_top", captured[0][1][0])
            self.assertIn("TOP DEBUG_TOP", captured[0][1][1])

    def test_multiplication_is_dimension_separator_and_parentheses_keep_arithmetic(self) -> None:
        reporter = Reporter()
        width, packed, _ = analyze_port_dimensions(
            "LANE_NUM*LANE_W", "(1+2)*5", None, None, "case", reporter,
            fallback_uncertain=False,
        )
        self.assertFalse(reporter.has_errors)
        self.assertEqual([item.expression for item in (*packed, width)], ["LANE_NUM", "LANE_W"])
        self.assertEqual([item.default for item in (*packed, width)], ["3", "5"])

        mismatch = Reporter()
        analyze_port_dimensions(
            "LANE_NUM*LANE_W", "15", None, None, "case", mismatch,
            fallback_uncertain=False,
        )
        self.assertTrue(mismatch.has_errors)
        self.assertTrue(any("* 维度数量不匹配" in item.message for item in mismatch.items))

        flat_error = Reporter()
        analyze_port_dimensions(
            "LANE_NUM", "3*5", None, None, "case", flat_error,
            fallback_uncertain=False,
        )
        self.assertTrue(flat_error.has_errors)

        parenthesized = Reporter()
        flat, packed, _ = analyze_port_dimensions(
            "LANE_NUM", "(3*5)", None, None, "case", parenthesized,
            fallback_uncertain=False,
        )
        self.assertFalse(parenthesized.has_errors)
        self.assertEqual(packed, ())
        self.assertEqual(flat.default, "15")

    def test_local_and_upper_defaults_spread_but_macro_conflicts_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "spread.xlsx"
            write_xlsx(
                workbook,
                [
                    (
                        "integration",
                        integration_sheet(
                            [(["top", "child"], [[("Data", "i"), ("Data", "i")]])]
                        ),
                    ),
                    (
                        "mixed top",
                        module_sheet("top", [("Data", "WIDTH", 8, "i")]),
                    ),
                    (
                        "ChildSheet",
                        module_sheet(
                            "child",
                            [("Data", "WIDTH", None, "i"), ("Other", "WIDTH", 4, "i")],
                        ),
                    ),
                ],
            )
            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual({path.name for path in paths}, {"top.v", "child.v"})
            top = (root / "generated" / "top.v").read_text(encoding="utf-8")
            self.assertIn("module TOP", top)
            self.assertIn("input wire [WIDTH -1:0] data", top)
            self.assertIn("\nCHILD #(\n", top)
            self.assertIn(") U_CHILD (", top)
            self.assertIn("\n    .data ", top)
            self.assertNotIn("\n    CHILD", top)

            conflict = root / "macro-conflict.xlsx"
            write_xlsx(
                conflict,
                [
                    (
                        "Integration",
                        integration_sheet(
                            [(["TOP", "CHILD"], [[("data", "i"), ("data", "i")]])]
                        ),
                    ),
                    ("TOP", module_sheet("TOP", [("data", "`SIZE", 8, "i")])),
                    ("CHILD", module_sheet("CHILD", [("data", "`SIZE", 4, "i")])),
                ],
            )
            _, conflict_reporter = generate(conflict, root / "rejected")
            self.assertTrue(conflict_reporter.has_errors)
            self.assertTrue(any("宏 `SIZE 默认值冲突" in item.message for item in conflict_reporter.items))

    def test_value_diffusion_backs_up_repairs_error_and_ignores_change_column(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "error.xlsx"
            rows = module_sheet("repair_me", [("DATA", "WIDTH", None, "i")])
            set_cell(rows, 2, 7, "修改")
            set_cell(rows, 3, 7, "`SHOULD_NEVER_APPEAR")
            write_xlsx(workbook, [("AnyCase", rows)])
            original = workbook.read_bytes()

            _, before_modules, _ = parse_workbook(workbook, Reporter())
            self.assertIn("REPAIR_ME", before_modules)
            before_reporter = Reporter()
            parse_workbook(workbook, before_reporter)
            self.assertTrue(before_reporter.has_errors)
            targets, _ = list_diffusible_variables(workbook)
            self.assertEqual([item.expression for item in targets], ["WIDTH"])

            result = diffuse_variable_value(
                workbook, "width", "(3+5)", confirm=lambda _: "y", timestamp="fixed"
            )
            self.assertFalse(result.cancelled)
            self.assertEqual(result.edited_cells, 1)
            self.assertIsNotNone(result.backup_path)
            assert result.backup_path is not None
            self.assertEqual(result.backup_path.read_bytes(), original)
            self.assertFalse(result.after.has_errors)
            raw = XlsxReader().read(workbook, ignore_review_columns=False)
            self.assertEqual(raw.by_name("AnyCase").cell(3, 4), "(3+5)")

    def test_template_diffusion_writes_explicit_range_and_cancel_is_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "templates.xlsx"
            rows = module_sheet(
                "TEMPLATE_MOD",
                [("Signal_{{z}}", "BUS_{{z}}", None, "i")],
            )
            set_cell(rows, 3, 8, "z的范围是{dat,req,rsp}")
            write_xlsx(workbook, [("Template", rows)])
            before = workbook.read_bytes()
            cancelled = diffuse_variable_value(
                workbook, "BUS_REQ", "5", confirm=lambda _: "n"
            )
            self.assertTrue(cancelled.cancelled)
            self.assertEqual(workbook.read_bytes(), before)
            self.assertFalse((root / "backup").exists())

            diffuse_variable_value(
                workbook, "BUS_REQ", "(3+5)", confirm=lambda _: "y", timestamp="range"
            )
            raw = XlsxReader().read(workbook, ignore_review_columns=False)
            self.assertEqual(
                raw.by_name("Template").cell(3, 4),
                "范围是{114,(3+5),114}",
            )

    def test_special_case_low_bit_binding_and_generate_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "review2case.xlsx"
            shutil.copy2(SPECIAL, workbook)
            diffuse_variable_value(
                workbook, "`APB_1", "4", confirm=lambda _: "y", timestamp="apb"
            )
            repaired = diffuse_variable_value(
                workbook, "`LANE_NUM", "5", confirm=lambda _: "y", timestamp="lane"
            )
            self.assertFalse(repaired.after.has_errors)
            paths, reporter = generate(workbook, root / "generated")
            self.assertFalse(reporter.has_errors)
            self.assertEqual(len(paths), 4)
            self.assertTrue(any("generate 使用安全次数 5" in item.message for item in reporter.items))
            top = (root / "generated" / "riscv_top.v").read_text(encoding="utf-8")
            self.assertIn(".dft_test_en          (dft_test_en[0]", top)
            self.assertIn("genvar i;\ngenerate\nfor (i = 0; i < 5; i = i + 1)", top)
            self.assertIn("MEM_DAT #(", top)
            self.assertIn(") U_MEM_DAT (", top)
            self.assertIn(".bus_in             (bus_in            [i]", top)
            self.assertIn(".dyadic_bus_out_rsp (dyadic_bus_out_rsp[i]", top)


if __name__ == "__main__":
    unittest.main()
