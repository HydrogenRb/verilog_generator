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
from tests.run_review_matrix import (
    integration_sheet,
    module_sheet,
    run_matrix,
    set_cell,
    write_xlsx,
)


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
        self.assertEqual(
            [
                port.name
                for port in top.ports
                if port.name.startswith("test_bus2_")
            ],
            [
                "test_bus2_sig1_dat",
                "test_bus2_sig2_dat",
                "test_bus2_sig3_dat",
                "test_bus2_sig1_ready",
                "test_bus2_sig2_ready",
                "test_bus2_sig3_ready",
                "test_bus2_sig1_valid",
                "test_bus2_sig2_valid",
                "test_bus2_sig3_valid",
            ],
        )
        self.assertEqual(top.port_map["test_bus2_sig1_dat"].width.expression, "114")
        self.assertTrue(any("未绑定模板变量 i" in item.message for item in reporter.items))
        self.assertTrue(any("模板花括号不完整" in item.message for item in reporter.items))

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
            self.assertRegex(top, r"(?m)^`define DFT_BUS\s+64$")
            self.assertRegex(top, r"(?m)^\s*wire\s+w_apb_1;$")
            self.assertRegex(top, r"(?m)^\s*wire \[15:0\]\s+w_apb_6;$")
            self.assertIn("RISCV_CORE_TEST #(", top)
            self.assertIn("MEM_PHY #(", top)
            self.assertRegex(top, r"(?m)^\s*\.ahb_test_1\s+\(1'b0\),$")
            self.assertRegex(top, r"(?m)^\s*\.ahb_test_3\s+\(\),$")
            self.assertRegex(top, r"(?m)^\s*assign ahb_test_6\s+= 6'b0;$")
            self.assertRegex(core, r"(?m)^\s*assign apb_6\s+= 16'b0;$")
            self.assertRegex(phy, r"(?m)^\s*assign apb_1\s+= 1'b0;$")
            self.assertRegex(top, r"(?m)^`define DW_sig1\s+114$")
            self.assertRegex(top, r"(?m)^\s*sky_cs_if\.mst\s+chi_if_risc,$")
            self.assertRegex(
                top,
                r"(?m)^\s*wire \[`LANE_NUM-1:0\]\[`Test_size-1:0\]\s+w_array;$",
            )
            self.assertIn(".test_bus_sig3_dat   (test_bus_sig3_dat)", top)
            self.assertRegex(top, r"(?m)^\s*\.chi_if_risc\s+\(chi_if_risc\),$")
            self.assertIn(".array               (w_array)", top)
            self.assertRegex(
                core,
                r"(?m)^\s*output wire \[`LANE_NUM-1:0\]\[`Test_size-1:0\]\s+array,$",
            )
            self.assertRegex(core, r"(?m)^\s*assign array\s+= '0;$")

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
            self.assertRegex(text, r"(?m)^\s*sky_if\.slv\s+bus_if$")
            self.assertIn("`define ROWS 2", text)
            self.assertIn("`define COLS 8", text)
            self.assertRegex(text, r"(?m)^\s*assign matrix\s+= '0;$")
            self.assertIn(
                "assign cube[gen_zero_cube_0][gen_zero_cube_1] = 8'b0;", text
            )
            self.assertTrue(any("占位值 114" in item.message for item in reporter.items))

    def test_named_templates_cartesian_product_and_missing_io_note(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "named-templates.xlsx"
            output = root / "generated"
            rows = module_sheet(
                "GENERIC",
                [
                    ("bus_{{j}}_{{z}}", "`W_{{j}}", None, "i"),
                    ("done_{j}}", 1, None, "o"),
                    ("needs_review", 1, None, None),
                ],
            )
            set_cell(rows, 3, 9, "j的范围是{a,b}; z的范围是{x,y}")
            set_cell(rows, 4, 1, None)
            write_xlsx(workbook, [("GENERIC", rows)])

            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual([path.name for path in paths], ["GENERIC.v"])
            text = paths[0].read_text(encoding="utf-8")
            for name in ("bus_a_x", "bus_a_y", "bus_b_x", "bus_b_y"):
                self.assertIn(name, text)
            self.assertIn("done_a", text)
            self.assertIn("done_b", text)
            self.assertRegex(text, r"(?m)^\s*inout\s+wire\s+needs_review")
            self.assertIn(
                "TODO: XLSX i/o 为空，暂按 inout 生成；需处理方向缺失问题",
                text,
            )
            self.assertTrue(any("i/o 为空" in item.message for item in reporter.items))
            self.assertTrue(any("模板花括号不完整" in item.message for item in reporter.items))

    def test_template_provenance_prevents_false_direction_and_count_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "template-provenance.xlsx"
            output = root / "generated"
            top_rows = module_sheet(
                "TOP",
                [
                    ("dir_{{i}}", 1, None, "i"),
                    ("dir_debug", 1, None, "i"),
                    ("count_{{i}}", 1, None, "i"),
                    ("count_debug", 1, None, "i"),
                ],
            )
            child_rows = module_sheet(
                "CHILD",
                [
                    ("dir_{{i}}", 1, None, "i"),
                    ("dir_debug", 1, None, "o"),
                    ("count_{{i}}", 1, None, "i"),
                ],
            )
            for rows, note in (
                (top_rows, "i是{a,b}"),
                (child_rows, "i是{b,a}"),
            ):
                set_cell(rows, 3, 1, "dir")
                set_cell(rows, 3, 9, note)
                set_cell(rows, 5, 1, "count")
                set_cell(rows, 5, 9, note)
            integration_rows = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [
                            [("dir_{{i}}", "i"), ("dir_{{i}}", "i")],
                            [("count_{{i}}", "i"), ("count_{{i}}", "i")],
                        ],
                    )
                ]
            )
            write_xlsx(
                workbook,
                [
                    ("Integration", integration_rows),
                    ("TOP", top_rows),
                    ("CHILD", child_rows),
                ],
            )

            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual({path.name for path in paths}, {"TOP.v", "CHILD.v"})
            text = (output / "TOP.v").read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^\s*\.dir_b\s+\(dir_b\),$")
            self.assertRegex(text, r"(?m)^\s*\.dir_a\s+\(dir_a\),$")
            self.assertRegex(text, r"(?m)^\s*\.count_b\s+\(count_b\),$")
            self.assertRegex(text, r"(?m)^\s*\.count_a\s+\(count_a\)$")
            self.assertRegex(text, r"(?m)^\s*\.dir_debug\s+\(\),$")
            self.assertFalse(any("展开数量不一致" in item.message for item in reporter.items))
            self.assertFalse(any("方向冲突" in item.message for item in reporter.items))

    def test_top_output_drives_child_input_and_change_columns_are_ignored(self) -> None:
        def module_rows(name: str, signal_direction: str) -> list[list[object]]:
            rows: list[list[object]] = []
            set_cell(rows, 1, 2, name)
            for column, header in enumerate(
                ("分类", "端口名", "位宽", "修改", "数值", "i/o"), start=1
            ):
                set_cell(rows, 2, column, header)
            set_cell(rows, 3, 1, "plain")
            set_cell(rows, 3, 2, "signal_a")
            set_cell(rows, 3, 3, 1)
            set_cell(rows, 3, 4, "这列里的内容必须完全无效")
            set_cell(rows, 3, 6, signal_direction)
            set_cell(rows, 4, 1, "template")
            set_cell(rows, 4, 2, "bus_{{i}}")
            set_cell(rows, 4, 3, 1)
            set_cell(rows, 4, 4, "i是{wrong1,wrong2,wrong3}")
            set_cell(rows, 4, 6, "i")
            set_cell(rows, 4, 7, "i是{a,b}")
            return rows

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "ignored-change-column.xlsx"
            output = root / "generated"
            integration_rows: list[list[object]] = []
            set_cell(integration_rows, 1, 2, "TOP")
            set_cell(integration_rows, 1, 5, "CHILD")
            for column, value in (
                (2, "端口名"),
                (3, "修改"),
                (4, "i/o"),
                (5, "端口名"),
                (6, "修改列"),
                (7, "i/o"),
            ):
                set_cell(integration_rows, 2, column, value)
            for row, port, top_direction, child_direction in (
                (3, "signal_a", "o", "i"),
                (4, "bus_{{i}}", "i", "i"),
            ):
                set_cell(integration_rows, row, 2, port)
                set_cell(integration_rows, row, 3, "fake_top_port")
                set_cell(integration_rows, row, 4, top_direction)
                set_cell(integration_rows, row, 5, port)
                set_cell(integration_rows, row, 6, "fake_child_port")
                set_cell(integration_rows, row, 7, child_direction)
            write_xlsx(
                workbook,
                [
                    ("Integration", integration_rows),
                    ("TOP", module_rows("TOP", "o")),
                    ("CHILD", module_rows("CHILD", "i")),
                ],
            )

            parsed = XlsxReader().read(workbook)
            integration = parsed.by_name("Integration")
            assert integration is not None
            self.assertEqual(integration.cell(2, 3), "i/o")
            self.assertNotIn(
                "修改",
                [
                    integration.cell(row, column)
                    for row in range(1, integration.max_row + 1)
                    for column in range(1, integration.max_column + 1)
                ],
            )

            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual({path.name for path in paths}, {"TOP.v", "CHILD.v"})
            text = (output / "TOP.v").read_text(encoding="utf-8")
            self.assertIn("assign signal_a = 1'b0;", text)
            self.assertRegex(text, r"(?m)^\s*\.signal_a\s+\(signal_a\),$")
            self.assertIn("bus_a", text)
            self.assertIn("bus_b", text)
            self.assertNotIn("wrong1", text)
            self.assertNotIn("fake_top_port", text)
            self.assertFalse(
                any("TOP 输出" in item.message for item in reporter.items)
            )

    def test_techreview3_groups_conditions_and_local_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "groups-and-conditions.xlsx"
            output = root / "generated"
            rows: list[list[object]] = []
            set_cell(rows, 1, 2, "GROUPED")
            for column, header in enumerate(
                ("分类", "端口名", "位宽", "数值", "i/o"), start=1
            ):
                set_cell(rows, 2, column, header)
            port_rows = [
                (None, "clk", "`SHORT", 1, "i"),
                (None, "rst_n", "`LONG_MACRO", 16, "i"),
                ("Feature bus 条件：FEATURE_X", "if_req", 1, None, "i"),
                (None, "if_response", 8, None, "o"),
                ("条件：`FEATURE_Y", "y_signal", 1, None, "o"),
                ("tail", "bus_if", "very_long_interface_type.mst", None, "NA"),
                (None, "tail_output", 2, None, "o"),
            ]
            for row, values in enumerate(port_rows, start=3):
                for column, value in enumerate(values, start=1):
                    set_cell(rows, row, column, value)
            write_xlsx(workbook, [("GROUPED", rows)])

            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual([path.name for path in paths], ["GROUPED.v"])
            text = paths[0].read_text(encoding="utf-8")

            for category in ("no group", "Feature bus", "FEATURE_Y", "tail"):
                self.assertIn(f"// {category}", text)
            self.assertEqual(text.count("// ----- ----- ----- ----- ----- -----"), 8)
            self.assertIn("`ifdef FEATURE_X", text)
            self.assertIn("`ifdef FEATURE_Y", text)
            self.assertNotIn("`define FEATURE_X", text)
            self.assertNotIn("`define FEATURE_Y", text)
            self.assertNotIn("`ifndef", text)

            macro_lines = [line for line in text.splitlines() if line.startswith("`define")]
            self.assertEqual(len(macro_lines), 2)
            macro_value_columns = [
                re.search(r"\S+$", line).start()  # type: ignore[union-attr]
                for line in macro_lines
            ]
            self.assertEqual(len(set(macro_value_columns)), 1)

            declaration_names = [
                "clk",
                "rst_n",
                "if_req",
                "if_response",
                "y_signal",
                "bus_if",
                "tail_output",
            ]
            declaration_lines = {
                name: next(
                    line
                    for line in text.splitlines()
                    if re.search(rf"\b{re.escape(name)}(?:\s|,|$)", line)
                    and not line.lstrip().startswith("//")
                )
                for name in declaration_names
            }
            self.assertEqual(
                len(
                    {
                        line.index(name)
                        for name, line in declaration_lines.items()
                    }
                ),
                1,
            )

            assignment_lines = [
                line for line in text.splitlines() if line.lstrip().startswith("assign ")
            ]
            self.assertEqual(len(assignment_lines), 3)
            self.assertEqual(len({line.index("=") for line in assignment_lines}), 1)

            def preprocess(defined: set[str]) -> list[str]:
                active = True
                stack: list[tuple[bool, bool]] = []
                result: list[str] = []
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("`ifdef "):
                        condition = stripped.split(None, 1)[1]
                        parent_active = active
                        branch_active = parent_active and condition in defined
                        stack.append((parent_active, branch_active))
                        active = branch_active
                    elif stripped.startswith("`elsif "):
                        condition = stripped.split(None, 1)[1]
                        parent_active, branch_taken = stack[-1]
                        active = parent_active and not branch_taken and condition in defined
                        stack[-1] = (parent_active, branch_taken or active)
                    elif stripped == "`endif":
                        parent_active, _ = stack.pop()
                        active = parent_active
                    elif active:
                        result.append(line)
                self.assertFalse(stack)
                return result

            for defined, expected_ports in (
                (set(), 4),
                ({"FEATURE_X"}, 6),
                ({"FEATURE_Y"}, 5),
                ({"FEATURE_X", "FEATURE_Y"}, 7),
            ):
                active_lines = preprocess(defined)
                start = next(
                    index for index, line in enumerate(active_lines) if line.startswith("module ")
                )
                end = next(
                    index for index in range(start + 1, len(active_lines))
                    if active_lines[index] == ");"
                )
                port_lines = [
                    line
                    for line in active_lines[start + 1 : end]
                    if line.strip()
                    and not line.strip().startswith("//")
                ]
                declarations = [line for line in port_lines if line.strip() != ","]
                comma_count = sum(line.count(",") for line in port_lines)
                self.assertEqual(len(declarations), expected_ports)
                self.assertEqual(comma_count, max(expected_ports - 1, 0))
                active_assignments = {
                    re.search(r"\bassign\s+(\w+)", line).group(1)  # type: ignore[union-attr]
                    for line in active_lines
                    if re.search(r"\bassign\s+(\w+)", line)
                }
                expected_assignments = {"tail_output"}
                if "FEATURE_X" in defined:
                    expected_assignments.add("if_response")
                if "FEATURE_Y" in defined:
                    expected_assignments.add("y_signal")
                self.assertEqual(active_assignments, expected_assignments)

    def test_conditional_instance_connections_keep_valid_commas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "conditional-integration.xlsx"
            output = root / "generated"
            top_rows = module_sheet(
                "TOP",
                [
                    ("clk", 1, None, "i"),
                    ("feature_x", 1, None, "i"),
                    ("feature_y", 1, None, "i"),
                ],
            )
            child_rows = module_sheet(
                "CHILD",
                [
                    ("clk", 1, None, "i"),
                    ("feature_x", 1, None, "i"),
                    ("feature_y", 1, None, "i"),
                ],
            )
            for rows in (top_rows, child_rows):
                set_cell(rows, 3, 1, "base")
                set_cell(rows, 4, 1, "条件：FEATURE_X")
                set_cell(rows, 5, 1, "条件：FEATURE_Y")
            integration_rows = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [
                            [("clk", "i"), ("clk", "i")],
                            [("feature_x", "i"), ("feature_x", "i")],
                            [("feature_y", "i"), ("feature_y", "i")],
                        ],
                    )
                ]
            )
            write_xlsx(
                workbook,
                [("Integration", integration_rows), ("TOP", top_rows), ("CHILD", child_rows)],
            )

            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual({path.name for path in paths}, {"TOP.v", "CHILD.v"})
            text = (output / "TOP.v").read_text(encoding="utf-8")
            self.assertIn("XLSX2VERILOG_INTERNAL_HAVE_CONNECTION_CHILD", text)

            def preprocess(defined: set[str]) -> list[str]:
                macros = set(defined)
                active = True
                stack: list[tuple[bool, bool]] = []
                result: list[str] = []
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("`ifdef "):
                        condition = stripped.split(None, 1)[1]
                        parent_active = active
                        branch_active = parent_active and condition in macros
                        stack.append((parent_active, branch_active))
                        active = branch_active
                    elif stripped.startswith("`elsif "):
                        condition = stripped.split(None, 1)[1]
                        parent_active, branch_taken = stack[-1]
                        active = parent_active and not branch_taken and condition in macros
                        stack[-1] = (parent_active, branch_taken or active)
                    elif stripped == "`else":
                        parent_active, branch_taken = stack[-1]
                        active = parent_active and not branch_taken
                        stack[-1] = (parent_active, True)
                    elif stripped == "`endif":
                        active, _ = stack.pop()
                    elif stripped.startswith("`define "):
                        if active:
                            macros.add(stripped.split(None, 1)[1].split()[0])
                    elif stripped.startswith("`undef "):
                        if active:
                            macros.discard(stripped.split(None, 1)[1])
                    elif active:
                        result.append(line)
                self.assertFalse(stack)
                return result

            for defined, expected_connections in (
                (set(), 1),
                ({"FEATURE_X"}, 2),
                ({"FEATURE_Y"}, 2),
                ({"FEATURE_X", "FEATURE_Y"}, 3),
            ):
                active_lines = preprocess(defined)
                start = next(
                    index
                    for index, line in enumerate(active_lines)
                    if "u_child (" in line
                )
                end = next(
                    index
                    for index in range(start + 1, len(active_lines))
                    if active_lines[index].strip() == ");"
                )
                instance_lines = active_lines[start + 1 : end]
                associations = [line for line in instance_lines if line.strip().startswith(".")]
                commas = sum(line.count(",") for line in instance_lines)
                self.assertEqual(len(associations), expected_connections)
                self.assertEqual(commas, expected_connections - 1)

    def test_conditional_child_driver_cannot_leave_top_output_undriven(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "conditional-driver-mismatch.xlsx"
            top_rows = module_sheet("TOP", [("result", 8, None, "o")])
            child_rows = module_sheet("CHILD", [("result", 8, None, "o")])
            set_cell(child_rows, 3, 1, "条件：FEATURE_RESULT")
            integration_rows = integration_sheet(
                [
                    (
                        ["TOP", "CHILD"],
                        [[("result", "o"), ("result", "o")]],
                    )
                ]
            )
            write_xlsx(
                workbook,
                [("Integration", integration_rows), ("TOP", top_rows), ("CHILD", child_rows)],
            )

            paths, reporter = generate(workbook, root / "generated")
            self.assertEqual(paths, [])
            self.assertTrue(reporter.has_errors)
            self.assertTrue(
                any("驱动端" in item.message and "条件不一致" in item.message for item in reporter.items)
            )

    def test_techreview3_module_prefix_bilingual_comment_and_wire_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "module-prefix.xlsx"
            output = root / "generated"
            integration_rows = integration_sheet(
                [
                    (
                        ["TOP", "SOURCE", "SINK"],
                        [[("clk", "i"), ("clk", "i"), ("clk", "i")]],
                    ),
                    (
                        ["SOURCE", "SINK"],
                        [
                            [("payload", "o"), ("payload", "i")],
                            [("valid", "o"), ("valid", "i")],
                        ],
                    ),
                ]
            )
            for column in range(1, max(len(row) for row in integration_rows) + 1):
                value = (
                    integration_rows[0][column - 1]
                    if column <= len(integration_rows[0])
                    else None
                )
                if value:
                    set_cell(integration_rows, 1, column, f"module:{value}")
            write_xlsx(
                workbook,
                [
                    ("Integration", integration_rows),
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    (
                        "SOURCE",
                        module_sheet(
                            "SOURCE",
                            [
                                ("clk", 1, None, "i"),
                                ("payload", "WIDTH", 8, "o"),
                                ("valid", 1, None, "o"),
                            ],
                        ),
                    ),
                    (
                        "SINK",
                        module_sheet(
                            "SINK",
                            [
                                ("clk", 1, None, "i"),
                                ("payload", "WIDTH", 8, "i"),
                                ("valid", 1, None, "i"),
                            ],
                        ),
                    ),
                ],
            )

            paths, reporter = generate(workbook, output)
            self.assertFalse(reporter.has_errors)
            self.assertEqual({path.name for path in paths}, {"TOP.v", "SOURCE.v", "SINK.v"})
            text = (output / "TOP.v").read_text(encoding="utf-8")
            self.assertIn("// Parameters local to child modules.", text)
            self.assertIn("// 子模块局部参数。", text)
            self.assertIn("SOURCE #(", text)
            self.assertNotIn("module:SOURCE", text)
            wire_lines = [
                line for line in text.splitlines() if re.match(r"^\s*wire\b", line)
            ]
            self.assertEqual(len(wire_lines), 2)
            wire_names = ("w_payload", "w_valid")
            self.assertEqual(
                len(
                    {
                        line.index(name)
                        for line, name in zip(wire_lines, wire_names)
                    }
                ),
                1,
            )

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
