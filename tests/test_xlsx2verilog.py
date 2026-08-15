from __future__ import annotations

import re
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from xlsx2verilog import Reporter, XlsxReader, generate, parse_workbook
from tests.run_review_matrix import run_matrix


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "test.xlsx"


class ReaderTests(unittest.TestCase):
    def test_reads_sample_without_third_party_packages(self) -> None:
        workbook = XlsxReader().read(SAMPLE)
        self.assertEqual(
            [sheet.name for sheet in workbook.sheets],
            ["集成", "RISCV_TOP", "RISCV_CORE_TEST", "MEM_PHY"],
        )
        self.assertEqual(workbook.by_name("RISCV_TOP").cell(1, 2), "RISCV_TOP")

    def test_recognizes_modules_and_integration(self) -> None:
        reporter = Reporter()
        _, modules, integration = parse_workbook(SAMPLE, reporter)
        self.assertFalse(reporter.has_errors)
        self.assertEqual(set(modules), {"RISCV_TOP", "RISCV_CORE_TEST", "MEM_PHY"})
        self.assertIsNotNone(integration)
        assert integration is not None
        self.assertEqual(integration.top_name, "RISCV_TOP")
        self.assertEqual(integration.child_names, ["RISCV_CORE_TEST", "MEM_PHY"])
        self.assertFalse(any("重复" in item.message for item in reporter.items))

    def test_width_warning_uses_review_format(self) -> None:
        generated_reporter = generate(SAMPLE, Path("unused"), check_only=True)[1]
        output = StringIO()
        with redirect_stderr(output):
            generated_reporter.print()
        self.assertIn(
            "warning[MEM_PHY.apb_1信号和RISCV_CORE_TEST.apb_1信号应该连接，但是其位宽不匹配]",
            output.getvalue(),
        )


class GenerationTests(unittest.TestCase):
    def test_sample_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            paths, reporter = generate(SAMPLE, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual(
                {path.name for path in paths},
                {"RISCV_TOP.v", "RISCV_CORE_TEST.v", "MEM_PHY.v"},
            )
            top = (output / "RISCV_TOP.v").read_text(encoding="utf-8")
            core = (output / "RISCV_CORE_TEST.v").read_text(encoding="utf-8")
            phy = (output / "MEM_PHY.v").read_text(encoding="utf-8")

            self.assertIn("parameter integer UID_SIZE = 5", top)
            self.assertIn("`define DFT_BUS 64", top)
            self.assertIn("wire w_apb_1;", top)
            self.assertIn("wire [15:0] w_apb_6;", top)
            self.assertIn("RISCV_CORE_TEST #(", top)
            self.assertIn("MEM_PHY #(", top)
            self.assertIn(".ahb_test_1(1'b0)", top)
            self.assertIn(".ahb_test_3()", top)
            self.assertIn("assign ahb_test_6 = 6'b0;", top)
            self.assertIn("assign apb_6 = 16'b0;", core)
            self.assertIn("assign apb_1 = 1'b0;", phy)

            for child_name in ("RISCV_CORE_TEST", "MEM_PHY"):
                instance_match = re.search(
                    rf"\bu_{child_name.lower()}\s*\((.*?)\n\s*\);",
                    top,
                    re.DOTALL,
                )
                self.assertIsNotNone(instance_match)
                assert instance_match is not None
                child_body = instance_match.group(1)
                for port in {
                    "RISCV_CORE_TEST": [
                        "n_rst", "clk", "dft_test_en", "dft_out_en", "uid",
                        "ahb_test_1", "ahb_test_2", "ahb_test_3", "ahb_test_4",
                        "ahb_test_5", "apb_1", "apb_2", "apb_3", "apb_4",
                        "apb_5", "apb_6",
                    ],
                    "MEM_PHY": [
                        "n_rst", "clk", "dft_bus", "dft_addr", "dft_test_en",
                        "dft_out_en", "uid", "apb_1", "apb_2", "apb_3",
                        "apb_4", "apb_5", "apb_6", "ahb_test_1", "ahb_test_2",
                        "ahb_test_3", "ahb_test_4", "ahb_test_5",
                    ],
                }[child_name]:
                    self.assertEqual(child_body.count(f".{port}("), 1)

            for content in (top, core, phy):
                self.assertEqual(content.count("module "), 1)
                self.assertEqual(content.count("endmodule"), 1)
                self.assertEqual(content.count("("), content.count(")"))

    def test_strict_mode_rejects_sample_warnings_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "strict-output"
            paths, reporter = generate(SAMPLE, output, strict=True)
            self.assertEqual(paths, [])
            self.assertTrue(reporter.has_warnings)
            self.assertFalse(output.exists())

    def test_check_only_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "check-output"
            paths, reporter = generate(SAMPLE, output, check_only=True)
            self.assertFalse(reporter.has_errors)
            self.assertEqual(len(paths), 3)
            self.assertFalse(output.exists())


class ReviewMatrixTests(unittest.TestCase):
    def test_generated_xlsx_matrix_and_inspection_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "review"
            results = run_matrix(destination)
            self.assertEqual(len(results), 6)
            self.assertTrue(all(result.passed for result in results))
            report = (destination / "检视报告.md").read_text(encoding="utf-8")
            self.assertIn("生成的 XLSX 问题", report)
            self.assertIn("项目定义的歧义", report)
            self.assertIn("脚本问题：未发现", report)

if __name__ == "__main__":
    unittest.main()
