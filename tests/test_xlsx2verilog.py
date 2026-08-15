from __future__ import annotations

import re
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from xlsx2verilog import (
    Reporter,
    XlsxReader,
    arrow_menu,
    evaluate_int_expression,
    generate,
    parse_workbook,
)
from tests.run_review_matrix import integration_sheet, module_sheet, run_matrix, write_xlsx


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

        top = modules["RISCV_TOP"]
        self.assertEqual(
            [port.name for port in top.ports if port.name.startswith("test_bus_")],
            [
                "test_bus_sig1_dat",
                "test_bus_sig2_dat",
                "test_bus_sig3_dat",
                "test_bus_sig1_ready",
                "test_bus_sig2_ready",
                "test_bus_sig3_ready",
                "test_bus_sig1_valid",
                "test_bus_sig2_valid",
                "test_bus_sig3_valid",
            ],
        )
        interface = top.port_map["chi_if_risc"]
        self.assertTrue(interface.is_interface)
        self.assertEqual(interface.interface_type, "sky_cs_if.mst")
        core_array = modules["RISCV_CORE_TEST"].port_map["array"]
        self.assertEqual(core_array.width.expression, "`Test_size")
        self.assertEqual(
            [item.expression for item in core_array.packed_dimensions], ["`LANE_NUM"]
        )
        self.assertEqual(core_array.arrays, ())

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
                len(
                    [
                        item
                        for item in reporter.items
                        if "多个子模块驱动端" in item.message
                    ]
                ),
                3,
            )
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
            self.assertRegex(top, r"(?m)^\s*\.ahb_test_1\s+\(1'b0\),$")
            self.assertRegex(top, r"(?m)^\s*\.ahb_test_3\s+\(\),$")
            self.assertIn("assign ahb_test_6 = 6'b0;", top)
            self.assertIn("assign apb_6 = 16'b0;", core)
            self.assertIn("assign apb_1 = 1'b0;", phy)
            self.assertIn("`define DW_sig1 114", top)
            self.assertIn("sky_cs_if.mst chi_if_risc", top)
            self.assertIn(
                "wire [`LANE_NUM-1:0][`Test_size-1:0] w_array;", top
            )
            self.assertIn(".test_bus_sig3_dat   (test_bus_sig3_dat)", top)
            self.assertIn(".chi_if_risc         (chi_if_risc)", top)
            self.assertIn(".array               (w_array)", top)
            self.assertIn(
                "output wire [`LANE_NUM-1:0][`Test_size-1:0] array", core
            )
            self.assertIn("assign array = '0;", core)

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
                        "apb_5", "apb_6", "test_bus_sig1_dat",
                        "test_bus_sig2_dat", "test_bus_sig3_dat",
                        "test_bus_sig1_ready", "test_bus_sig2_ready",
                        "test_bus_sig3_ready", "test_bus_sig1_valid",
                        "test_bus_sig2_valid", "test_bus_sig3_valid",
                        "chi_if_risc", "array",
                    ],
                    "MEM_PHY": [
                        "n_rst", "clk", "dft_bus", "dft_addr", "dft_test_en",
                        "dft_out_en", "uid", "apb_1", "apb_2", "apb_3",
                        "apb_4", "apb_5", "apb_6", "ahb_test_1", "ahb_test_2",
                        "ahb_test_3", "ahb_test_4", "ahb_test_5",
                        "test_bus_sig1_dat", "test_bus_sig2_dat",
                        "test_bus_sig3_dat", "test_bus_sig1_ready",
                        "test_bus_sig2_ready", "test_bus_sig3_ready",
                        "test_bus_sig1_valid", "test_bus_sig2_valid",
                        "test_bus_sig3_valid", "array",
                    ],
                }[child_name]:
                    self.assertEqual(
                        len(re.findall(rf"\.{re.escape(port)}\s+\(", child_body)),
                        1,
                    )

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

    def test_module_and_instance_fields_are_column_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "format.xlsx"
            output = root / "generated"
            write_xlsx(
                workbook,
                [
                    (
                        "Integration",
                        integration_sheet(
                            [
                                (
                                    ["FORMAT_TOP", "FORMAT_CHILD"],
                                    [
                                        [("clk", "i"), ("clk", "i")],
                                        [("result", "o"), ("result", "o")],
                                    ],
                                ),
                                (
                                    ["FORMAT_CHILD"],
                                    [[("short", "i")], [("much_longer", "i")]],
                                ),
                            ]
                        ),
                    ),
                    (
                        "FORMAT_TOP",
                        module_sheet(
                            "FORMAT_TOP",
                            [("clk", 1, None, "i"), ("result", "WIDTH", 8, "o")],
                        ),
                    ),
                    (
                        "FORMAT_CHILD",
                        module_sheet(
                            "FORMAT_CHILD",
                            [
                                ("clk", 1, None, "i"),
                                ("short", 1, None, "i"),
                                ("much_longer", "WIDTH", 8, "i"),
                                ("result", "WIDTH", 8, "o"),
                            ],
                        ),
                    ),
                ],
            )

            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual(len(paths), 2)
            top = (output / "FORMAT_TOP.v").read_text(encoding="utf-8")

            declaration_lines = [
                line
                for line in top.splitlines()
                if re.match(r"^    (?:input|output|inout)\s+wire\b", line)
            ]
            self.assertEqual(len(declaration_lines), 2)
            self.assertTrue(declaration_lines[0].startswith("    input  wire"))
            self.assertTrue(declaration_lines[1].startswith("    output wire"))
            name_columns = [
                line.index(name)
                for line, name in zip(declaration_lines, ("clk", "result"))
            ]
            self.assertEqual(len(set(name_columns)), 1)

            self.assertRegex(top, r"(?m)^        \.WIDTH\s+\(WIDTH\)$")
            self.assertRegex(top, r"(?m)^        \.short\s{7}\(1'b0\),$")
            self.assertRegex(top, r"(?m)^        \.much_longer \(\{WIDTH\{1'b0\}\}\),$")

    def test_unpacked_array_declarations_connections_and_zero_drives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "arrays.xlsx"
            output = root / "generated"
            write_xlsx(
                workbook,
                [
                    (
                        "Integration",
                        integration_sheet(
                            [
                                (
                                    ["ARRAY_TOP", "ARRAY_SRC", "ARRAY_DST"],
                                    [[("clk", "i"), ("clk", "i"), ("clk", "i")]],
                                ),
                                (
                                    ["ARRAY_SRC", "ARRAY_DST"],
                                    [[("data", "o"), ("data", "i")]],
                                ),
                                (["ARRAY_DST"], [[("spare", "i")]]),
                            ]
                        ),
                    ),
                    (
                        "ARRAY_TOP",
                        module_sheet(
                            "ARRAY_TOP",
                            [
                                ("clk", 1, None, "i"),
                                ("monitor", "DATA_WIDTH", 32, "o", "DEPTH", 4),
                            ],
                        ),
                    ),
                    (
                        "ARRAY_SRC",
                        module_sheet(
                            "ARRAY_SRC",
                            [
                                ("clk", 1, None, "i"),
                                ("data", "DATA_WIDTH", 32, "o", "DEPTH", 4),
                            ],
                        ),
                    ),
                    (
                        "ARRAY_DST",
                        module_sheet(
                            "ARRAY_DST",
                            [
                                ("clk", 1, None, "i"),
                                ("data", "DATA_WIDTH", 32, "i", "DEPTH", 4),
                                ("spare", "DATA_WIDTH", 32, "i", "DEPTH", 4),
                            ],
                        ),
                    ),
                ],
            )

            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual(len(paths), 3)
            top = (output / "ARRAY_TOP.v").read_text(encoding="utf-8")
            source = (output / "ARRAY_SRC.v").read_text(encoding="utf-8")
            destination = (output / "ARRAY_DST.v").read_text(encoding="utf-8")

            self.assertIn(
                "output wire [DATA_WIDTH-1:0] monitor [DEPTH-1:0]", top
            )
            self.assertIn(
                "output wire [DATA_WIDTH-1:0] data [DEPTH-1:0]", source
            )
            self.assertIn(
                "input wire [DATA_WIDTH-1:0] data [DEPTH-1:0]", destination
            )
            self.assertIn(
                "wire [DATA_WIDTH-1:0] w_data [DEPTH-1:0];", top
            )
            self.assertRegex(top, r"(?m)^        \.spare \('\{default:'0\}\)$")
            self.assertIn(
                "for (genvar gen_zero_data = 0; gen_zero_data < DEPTH; "
                "gen_zero_data = gen_zero_data + 1) begin : g_zero_data",
                source,
            )
            self.assertIn(
                "assign data[gen_zero_data] = {DATA_WIDTH{1'b0}};", source
            )

    def test_expressions_multiple_dimensions_and_interface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "advanced.xlsx"
            output = root / "generated"
            write_xlsx(
                workbook,
                [
                    (
                        "ADVANCED",
                        module_sheet(
                            "ADVANCED",
                            [
                                ("calculated", "2+3*4", None, "o", None, None),
                                ("uncertain", "WIDTH+OFFSET", "unknown", "o", None, None),
                                ("matrix", "`ROWS * `COLS", "2*8", "o", None, None),
                                ("cube", 8, None, "o", "`A * `B", "2*3"),
                                ("bus_if", "sky_if.slv", None, "NA", None, None),
                            ],
                        ),
                    )
                ],
            )

            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual([path.name for path in paths], ["ADVANCED.v"])
            text = paths[0].read_text(encoding="utf-8")
            self.assertRegex(text, r"output wire \[13:0\]\s+calculated")
            self.assertRegex(text, r"output wire \[113:0\]\s+uncertain")
            self.assertRegex(
                text, r"output wire \[`ROWS-1:0\]\[`COLS-1:0\]\s+matrix"
            )
            self.assertRegex(
                text, r"output wire \[7:0\]\s+cube \[`A-1:0\] \[`B-1:0\]"
            )
            self.assertIn("sky_if.slv bus_if", text)
            self.assertIn("`define ROWS 2", text)
            self.assertIn("`define COLS 8", text)
            self.assertIn("assign matrix = '0;", text)
            self.assertIn(
                "assign cube[gen_zero_cube_0][gen_zero_cube_1] = 8'b0;", text
            )
            self.assertTrue(any("占位值 114" in item.message for item in reporter.items))

    def test_integer_expression_evaluator_is_safe(self) -> None:
        self.assertEqual(evaluate_int_expression("2 + 3 * 4"), 14)
        self.assertEqual(evaluate_int_expression("(24 / 3) << 1"), 16)
        self.assertIsNone(evaluate_int_expression("2 ** 10"))
        self.assertIsNone(evaluate_int_expression("__import__('os')"))
        self.assertIsNone(evaluate_int_expression(str(1 << 40)))


class TerminalMenuTests(unittest.TestCase):
    @staticmethod
    def choose(keys: list[str]) -> tuple[int | None, str]:
        sequence = iter(keys)
        output = StringIO()
        selected = arrow_menu(
            "Pick one",
            ["first", "second", "third"],
            key_reader=lambda: next(sequence),
            output=output,
        )
        return selected, output.getvalue()

    def test_down_and_enter_select_an_option(self) -> None:
        selected, rendered = self.choose(["down", "enter"])
        self.assertEqual(selected, 1)
        self.assertIn("Pick one", rendered)
        self.assertIn("> second", rendered)

    def test_up_wraps_to_last_option(self) -> None:
        selected, _ = self.choose(["up", "enter"])
        self.assertEqual(selected, 2)

    def test_down_wraps_to_first_option(self) -> None:
        selected, _ = self.choose(["down", "down", "down", "enter"])
        self.assertEqual(selected, 0)

    def test_escape_cancels(self) -> None:
        selected, _ = self.choose(["escape"])
        self.assertIsNone(selected)


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
