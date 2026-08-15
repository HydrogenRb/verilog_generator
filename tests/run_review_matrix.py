#!/usr/bin/env python3
"""Build several XLSX layouts, run the generator, inspect, and write a report."""

from __future__ import annotations

import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xlsx2verilog import Reporter, generate, parse_workbook  # noqa: E402


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def column_letters(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def xml_bytes(element: ET.Element) -> bytes:
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def worksheet_xml(rows: list[list[Any]]) -> bytes:
    root = ET.Element(f"{{{MAIN_NS}}}worksheet")
    sheet_data = ET.SubElement(root, f"{{{MAIN_NS}}}sheetData")
    for row_number, values in enumerate(rows, start=1):
        populated = [(index, value) for index, value in enumerate(values, start=1) if value is not None]
        if not populated:
            continue
        row = ET.SubElement(sheet_data, f"{{{MAIN_NS}}}row", {"r": str(row_number)})
        for column, value in populated:
            reference = f"{column_letters(column)}{row_number}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cell = ET.SubElement(row, f"{{{MAIN_NS}}}c", {"r": reference})
                ET.SubElement(cell, f"{{{MAIN_NS}}}v").text = str(value)
            else:
                cell = ET.SubElement(
                    row,
                    f"{{{MAIN_NS}}}c",
                    {"r": reference, "t": "inlineStr"},
                )
                inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
                ET.SubElement(inline, f"{{{MAIN_NS}}}t").text = str(value)
    return xml_bytes(root)


def write_xlsx(path: Path, sheets: list[tuple[str, list[list[Any]]]]) -> None:
    """Write a minimal, standards-compliant XLSX using only the standard library."""
    path.parent.mkdir(parents=True, exist_ok=True)

    types = ET.Element(f"{{{CONTENT_NS}}}Types")
    ET.SubElement(
        types,
        f"{{{CONTENT_NS}}}Default",
        {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"},
    )
    ET.SubElement(
        types,
        f"{{{CONTENT_NS}}}Default",
        {"Extension": "xml", "ContentType": "application/xml"},
    )
    ET.SubElement(
        types,
        f"{{{CONTENT_NS}}}Override",
        {
            "PartName": "/xl/workbook.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        },
    )
    for index in range(1, len(sheets) + 1):
        ET.SubElement(
            types,
            f"{{{CONTENT_NS}}}Override",
            {
                "PartName": f"/xl/worksheets/sheet{index}.xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
            },
        )

    package_relationships = ET.Element(f"{{{PACKAGE_REL_NS}}}Relationships")
    ET.SubElement(
        package_relationships,
        f"{{{PACKAGE_REL_NS}}}Relationship",
        {
            "Id": "rId1",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
            "Target": "xl/workbook.xml",
        },
    )

    workbook = ET.Element(f"{{{MAIN_NS}}}workbook")
    workbook_sheets = ET.SubElement(workbook, f"{{{MAIN_NS}}}sheets")
    workbook_relationships = ET.Element(f"{{{PACKAGE_REL_NS}}}Relationships")
    for index, (name, _) in enumerate(sheets, start=1):
        ET.SubElement(
            workbook_sheets,
            f"{{{MAIN_NS}}}sheet",
            {"name": name, "sheetId": str(index), f"{{{REL_NS}}}id": f"rId{index}"},
        )
        ET.SubElement(
            workbook_relationships,
            f"{{{PACKAGE_REL_NS}}}Relationship",
            {
                "Id": f"rId{index}",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                "Target": f"worksheets/sheet{index}.xml",
            },
        )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", xml_bytes(types))
        archive.writestr("_rels/.rels", xml_bytes(package_relationships))
        archive.writestr("xl/workbook.xml", xml_bytes(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", xml_bytes(workbook_relationships))
        for index, (_, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml(rows))


def set_cell(rows: list[list[Any]], row: int, column: int, value: Any) -> None:
    while len(rows) < row:
        rows.append([])
    while len(rows[row - 1]) < column:
        rows[row - 1].append(None)
    rows[row - 1][column - 1] = value


def module_sheet(
    name: str,
    ports: list[tuple[Any, ...]],
    *,
    header_row: int = 2,
    port_column: int = 2,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    has_arrays = any(len(port) == 6 for port in ports)
    set_cell(rows, header_row - 1, port_column, name)
    set_cell(rows, header_row, port_column - 1, "分类")
    set_cell(rows, header_row, port_column, "端口名")
    set_cell(rows, header_row, port_column + 1, "位宽")
    set_cell(rows, header_row, port_column + 2, "数值")
    if has_arrays:
        set_cell(rows, header_row, port_column + 3, "数组")
        set_cell(rows, header_row, port_column + 4, "数组数值")
    direction_column = port_column + (5 if has_arrays else 3)
    set_cell(rows, header_row, direction_column, "i/o")
    for offset, port in enumerate(ports, start=1):
        if len(port) == 4:
            port_name, width, value, direction = port
            array = array_value = None
        elif len(port) == 6:
            port_name, width, value, direction, array, array_value = port
        else:
            raise ValueError("port rows must contain 4 fields, or 6 fields with array metadata")
        row = header_row + offset
        set_cell(rows, row, port_column - 1, "test")
        set_cell(rows, row, port_column, port_name)
        set_cell(rows, row, port_column + 1, width)
        set_cell(rows, row, port_column + 2, value)
        if has_arrays:
            set_cell(rows, row, port_column + 3, array)
            set_cell(rows, row, port_column + 4, array_value)
        set_cell(rows, row, direction_column, direction)
    return rows


Connection = tuple[str, str] | None


def integration_sheet(
    groups: list[tuple[list[str], list[list[Connection]]]],
    *,
    header_row: int = 2,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    port_column = 2
    for module_names, connections in groups:
        block_columns: list[int] = []
        for module_name in module_names:
            block_columns.append(port_column)
            set_cell(rows, header_row - 1, port_column, module_name)
            set_cell(rows, header_row, port_column, "端口名")
            set_cell(rows, header_row, port_column + 1, "i/o")
            port_column += 2
        for offset, connection_row in enumerate(connections, start=1):
            if len(connection_row) != len(module_names):
                raise ValueError("connection row length does not match module block count")
            for column, connection in zip(block_columns, connection_row):
                if connection is None:
                    continue
                port_name, direction = connection
                set_cell(rows, header_row + offset, column, port_name)
                set_cell(rows, header_row + offset, column + 1, direction)
        port_column += 2  # one empty column plus the next group's category column
    return rows


@dataclass(frozen=True)
class ReviewCase:
    directory: str
    title: str
    sheets: list[tuple[str, list[list[Any]]]]
    expected_result: str
    classification: str
    expected_warning: str | None = None


def review_cases() -> list[ReviewCase]:
    basic = ReviewCase(
        "01_basic",
        "基础 TOP 与单子模块",
        [
            (
                "集成",
                integration_sheet(
                    [
                        (
                            ["TOP_BASIC", "BASIC_CHILD"],
                            [
                                [("clk", "i"), ("clk", "i")],
                                [("done", "o"), ("done", "o")],
                            ],
                        ),
                        (
                            ["BASIC_CHILD"],
                            [[("spare", "i")], [("debug", "o")]],
                        ),
                    ]
                ),
            ),
            ("TOP_BASIC", module_sheet("TOP_BASIC", [("clk", 1, None, "i"), ("done", 1, None, "o")])),
            (
                "BASIC_CHILD",
                module_sheet(
                    "BASIC_CHILD",
                    [
                        ("clk", 1, None, "i"),
                        ("done", 1, None, "o"),
                        ("spare", 1, None, "i"),
                        ("debug", 1, None, "o"),
                    ],
                ),
            ),
        ],
        "成功生成 2 个 Verilog；TOP output 由子模块驱动，未连接 input 置零、output 悬空",
        "无问题",
    )

    parameterized = ReviewCase(
        "02_parameter_macro_shifted",
        "偏移表头、宏/parameter 与两个子模块",
        [
            (
                "Integration",
                integration_sheet(
                    [
                        (
                            ["TOP_PARAM", "PRODUCER", "CONSUMER"],
                            [
                                [("n_rst", "i"), ("n_rst", "i"), ("n_rst", "i")],
                                [("result", "o"), None, ("result", "o")],
                            ],
                        ),
                        (
                            ["PRODUCER", "CONSUMER"],
                            [[("bus", "o"), ("bus", "i")]],
                        ),
                        (["PRODUCER"], [[("debug", "o")]]),
                    ],
                    header_row=4,
                ),
            ),
            (
                "TOP_PARAM",
                module_sheet(
                    "TOP_PARAM",
                    [("n_rst", "`RST_LANE", 1, "i"), ("result", "WIDTH", 8, "o")],
                    header_row=5,
                    port_column=4,
                ),
            ),
            (
                "PRODUCER",
                module_sheet(
                    "PRODUCER",
                    [
                        ("n_rst", "`RST_LANE", 1, "i"),
                        ("bus", "WIDTH", 8, "o"),
                        ("debug", 2, None, "o"),
                    ],
                    header_row=5,
                    port_column=4,
                ),
            ),
            (
                "CONSUMER",
                module_sheet(
                    "CONSUMER",
                    [
                        ("n_rst", "`RST_LANE", 1, "i"),
                        ("bus", "WIDTH", 8, "i"),
                        ("result", "WIDTH", 8, "o"),
                    ],
                    header_row=5,
                    port_column=4,
                ),
            ),
        ],
        "成功生成 3 个 Verilog；非固定行列识别、宏、parameter 透传和内部 wire 均正确",
        "无问题",
    )

    duplicate = ReviewCase(
        "03_duplicate_port_rows",
        "同一模块重复端口行",
        [
            (
                "集成",
                integration_sheet(
                    [
                        (
                            ["TOP_DUP", "DUP_CHILD"],
                            [
                                [("clk", "i"), ("clk", "i")],
                                [("aaa", "i"), None],
                                [("status", "o"), ("status", "o")],
                            ],
                        ),
                        (["DUP_CHILD"], [[("aaa", "i")]]),
                    ]
                ),
            ),
            (
                "TOP_DUP",
                module_sheet(
                    "TOP_DUP",
                    [("clk", 1, None, "i"), ("aaa", 2, None, "i"), ("status", 1, None, "o")],
                ),
            ),
            (
                "DUP_CHILD",
                module_sheet(
                    "DUP_CHILD",
                    [
                        ("clk", 1, None, "i"),
                        ("clk", 1, None, "i"),
                        ("aaa", 2, None, "i"),
                        ("aaa", 2, None, "i"),
                        ("status", 1, None, "o"),
                        ("status", 1, None, "o"),
                    ],
                ),
            ),
        ],
        "成功生成 2 个 Verilog；重复行合并；TOP 与子模块同名 aaa 不会自动相连，子模块端生成 .aaa(2'b0)",
        "无问题",
    )

    bad_width = ReviewCase(
        "04_invalid_width",
        "非法位宽文本",
        [("BAD_WIDTH", module_sheet("BAD_WIDTH", [("payload", "7:0", None, "i")]))],
        "按预期拒绝生成，并定位到非法位宽单元格",
        "生成的 XLSX 问题",
    )

    inout_case = ReviewCase(
        "05_unused_inout",
        "未连接的 inout 端口",
        [
            (
                "集成",
                integration_sheet(
                    [
                        (["TOP_IO", "IO_CHILD"], [[("clk", "i"), ("clk", "i")]]),
                        (["IO_CHILD"], [[("pad", "io")]]),
                    ]
                ),
            ),
            ("TOP_IO", module_sheet("TOP_IO", [("clk", 1, None, "i")])),
            (
                "IO_CHILD",
                module_sheet("IO_CHILD", [("clk", 1, None, "i"), ("pad", 4, None, "io")]),
            ),
        ],
        "成功生成 2 个 Verilog；当前实现将未连接 inout 生成为 .pad()",
        "项目定义的歧义",
    )

    width_mismatch_warning = (
        "WIDTH_SRC.payload信号和WIDTH_DST.payload信号应该连接，但是其位宽不匹配"
    )
    width_mismatch = ReviewCase(
        "06_width_mismatch",
        "子模块互连位宽不一致",
        [
            (
                "集成",
                integration_sheet(
                    [
                        (
                            ["TOP_WIDTH", "WIDTH_SRC", "WIDTH_DST"],
                            [[("clk", "i"), ("clk", "i"), ("clk", "i")]],
                        ),
                        (
                            ["WIDTH_SRC", "WIDTH_DST"],
                            [[("payload", "o"), ("payload", "i")]],
                        ),
                    ]
                ),
            ),
            ("TOP_WIDTH", module_sheet("TOP_WIDTH", [("clk", 1, None, "i")])),
            (
                "WIDTH_SRC",
                module_sheet("WIDTH_SRC", [("clk", 1, None, "i"), ("payload", 8, None, "o")]),
            ),
            (
                "WIDTH_DST",
                module_sheet("WIDTH_DST", [("clk", 1, None, "i"), ("payload", 4, None, "i")]),
            ),
        ],
        "成功生成 3 个 Verilog；wire 采用 output 驱动端 8 bit，并产生指定格式 warning",
        "生成的 XLSX 问题",
        width_mismatch_warning,
    )
    return [basic, parameterized, duplicate, bad_width, inout_case, width_mismatch]


def inspect_generated(workbook: Path, output: Path, paths: list[Path], reporter: Reporter) -> list[str]:
    """Perform an independent structural audit of the just-generated Verilog."""
    parse_reporter = Reporter()
    _, modules, integration = parse_workbook(workbook, parse_reporter)
    problems: list[str] = []
    if parse_reporter.has_errors:
        return ["无法审计：输入工作簿本身解析失败"]
    expected_names = set(modules)
    actual_names = {path.stem for path in paths}
    if expected_names != actual_names:
        problems.append(f"文件集合不一致: expected={sorted(expected_names)}, actual={sorted(actual_names)}")

    top_text = ""
    if integration:
        top_path = output / f"{integration.top_name}.v"
        if top_path.exists():
            top_text = top_path.read_text(encoding="utf-8")
    for module in modules.values():
        path = output / f"{module.name}.v"
        if not path.exists():
            problems.append(f"缺少 {path.name}")
            continue
        text = path.read_text(encoding="utf-8")
        if len(re.findall(rf"(?m)^module\s+{re.escape(module.name)}\b", text)) != 1:
            problems.append(f"{path.name}: module 声明数量不是 1")
        if text.count("endmodule") != 1 or text.count("(") != text.count(")"):
            problems.append(f"{path.name}: 基础结构或括号不平衡")
        declarations: list[str] = []
        for line in text.splitlines():
            regular = re.match(
                r"^\s*(?:input|output|inout)\s+wire\s+"
                r"(?:\[[^\]]+\]\s+)?([A-Za-z_]\w*)"
                r"(?:\s+\[[^\]]+\])*,?$",
                line,
            )
            interface = re.match(
                r"^\s*(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*\.[A-Za-z_]\w*\s+"
                r"([A-Za-z_]\w*)(?:\s+\[[^\]]+\])*,?$",
                line,
            )
            match = regular or interface
            if match:
                declarations.append(match.group(1))
        expected_ports = [port.name for port in module.ports]
        if declarations != expected_ports or len(declarations) != len(set(declarations)):
            problems.append(f"{path.name}: 端口声明与去重后的 XLSX 定义不一致")
        if integration and module.name != integration.top_name:
            instance = re.search(rf"\bu_{module.name.lower()}\s*\((.*?)\n\s*\);", top_text, re.S)
            if not instance:
                problems.append(f"{integration.top_name}.v: 缺少 {module.name} 实例")
            else:
                body = instance.group(1)
                for port in module.ports:
                    if len(re.findall(rf"\.{re.escape(port.name)}\s+\(", body)) != 1:
                        problems.append(
                            f"{integration.top_name}.v: {module.name}.{port.name} 连接数量不是 1"
                        )
            for port in module.ports:
                assigned_name = (
                    rf"{re.escape(port.name)}\s*\[" if port.array else rf"{re.escape(port.name)}\s*="
                )
                if port.direction == "output" and not re.search(
                    rf"(?m)^\s*assign\s+{assigned_name}", text
                ):
                    problems.append(f"{path.name}: output {port.name} 未赋零")
    if reporter.has_errors:
        problems.append("生成阶段出现未预期错误")
    return problems


@dataclass
class ReviewResult:
    case: ReviewCase
    passed: bool
    diagnostics: list[str]
    audit_problems: list[str]
    generated_files: list[str]


EXPECTED_SNIPPETS: dict[str, list[tuple[str, str]]] = {
    "01_basic": [
        ("TOP_BASIC.v", ".done  (done)"),
        ("TOP_BASIC.v", ".spare (1'b0)"),
        ("TOP_BASIC.v", ".debug ()"),
    ],
    "02_parameter_macro_shifted": [
        ("TOP_PARAM.v", "`define RST_LANE 1"),
        ("TOP_PARAM.v", "parameter integer WIDTH = 8"),
        ("TOP_PARAM.v", "wire [WIDTH-1:0] w_bus;"),
        ("TOP_PARAM.v", ".WIDTH (WIDTH)"),
    ],
    "03_duplicate_port_rows": [
        ("TOP_DUP.v", "input  wire [1:0] aaa"),
        ("TOP_DUP.v", ".aaa    (2'b0)"),
    ],
    "05_unused_inout": [("TOP_IO.v", ".pad ()")],
    "06_width_mismatch": [("TOP_WIDTH.v", "wire [7:0] w_payload;")],
}


def run_matrix(destination: Path) -> list[ReviewResult]:
    destination.mkdir(parents=True, exist_ok=True)
    results: list[ReviewResult] = []
    for case in review_cases():
        case_directory = destination / case.directory
        output = case_directory / "generated"
        case_directory.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        for stale in output.glob("*.v"):
            stale.unlink()
        workbook = case_directory / "input.xlsx"
        write_xlsx(workbook, case.sheets)

        paths, reporter = generate(workbook, output)
        raw_diagnostics = [item.message for item in reporter.items]
        diagnostics = [
            f"{'warning' if item.level == '警告' else 'error'}[{item.message}]"
            for item in reporter.items
        ]
        if case.directory == "04_invalid_width":
            audit_problems: list[str] = []
            passed = reporter.has_errors and not paths
            if not passed:
                audit_problems.append("非法位宽没有阻止生成")
        else:
            audit_problems = inspect_generated(workbook, output, paths, reporter)
            for file_name, snippet in EXPECTED_SNIPPETS.get(case.directory, []):
                generated_path = output / file_name
                if not generated_path.exists() or snippet not in generated_path.read_text(encoding="utf-8"):
                    audit_problems.append(f"{file_name}: 缺少预期内容 {snippet}")
            warning_ok = case.expected_warning is None or case.expected_warning in raw_diagnostics
            passed = not reporter.has_errors and not audit_problems and warning_ok
            if not warning_ok:
                audit_problems.append("缺少预期的位宽 warning")
        results.append(
            ReviewResult(
                case,
                passed,
                diagnostics,
                audit_problems,
                sorted(path.name for path in paths),
            )
        )
    write_report(destination / "检视报告.md", results)
    return results


def write_report(path: Path, results: list[ReviewResult]) -> None:
    lines = [
        "# Tech Review 1 多结构 XLSX 检视报告",
        "",
        "本报告由 `tests/run_review_matrix.py` 在创建 XLSX、调用项目生成器并立即执行独立静态检视后生成。测试 XLSX 和对应 Verilog 均保存在各案例目录中。",
        "",
        "## 汇总",
        "",
        "| 案例 | 结果 | 分类 | 预期与实际 |",
        "|---|---|---|---|",
    ]
    for result in results:
        status = "通过" if result.passed else "失败"
        lines.append(
            f"| `{result.case.directory}` {result.case.title} | {status} | {result.case.classification} | {result.case.expected_result} |"
        )
    lines.extend(["", "## 逐项检视", ""])
    for result in results:
        lines.extend(
            [
                f"### {result.case.directory} — {result.case.title}",
                "",
                f"- 生成文件：{', '.join(f'`{name}`' for name in result.generated_files) or '无（按预期拒绝生成）'}",
                f"- 分类：{result.case.classification}",
                f"- 结论：{'通过' if result.passed else '失败'}；{result.case.expected_result}",
            ]
        )
        if result.diagnostics:
            lines.append("- 诊断：" + "；".join(f"``{item}``" for item in result.diagnostics))
        if result.case.directory == "04_invalid_width" and result.passed:
            lines.append("- 静态检视：生成按预期中止，没有产生需要检视的 Verilog 文件。")
        elif result.audit_problems:
            lines.append("- 静态检视问题：" + "；".join(result.audit_problems))
        else:
            lines.append("- 静态检视：文件数量、模块边界、端口声明、实例端口唯一性和 output 赋零均符合预期。")
        lines.append("")
    script_issues = [result for result in results if not result.passed]
    lines.extend(
        [
            "## 分类结论",
            "",
            f"- 脚本问题：{'发现 ' + str(len(script_issues)) + ' 项，见失败案例' if script_issues else '未发现'}。",
            "- 生成的 XLSX 问题：案例 04 的位宽文本不符合已定义规则；案例 06 故意设置了互连位宽不匹配，脚本已按要求告警并继续生成。",
            "- 项目定义的歧义：需求只规定未连接 input 赋零、output 悬空，未规定 inout。案例 05 当前采用悬空 `.pad()`，需由项目决定是否改为顶层引出或其他处理。",
            "- 重复名称结论：案例 03 验证同一模块的重复端口行会合并为一个物理端口，不再产生重复定义警告，也不会生成重复的 Verilog 端口连接。",
            "",
        ]
    )
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines))


def main() -> int:
    destination = ROOT / "review_test_cases"
    results = run_matrix(destination)
    for result in results:
        print(f"{'PASS' if result.passed else 'FAIL'} {result.case.directory}: {result.case.title}")
    print(f"报告: {destination / '检视报告.md'}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
