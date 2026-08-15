#!/usr/bin/env python3
"""Generate Verilog module stubs and a TOP integration module from an XLSX file.

The implementation intentionally uses only Python's standard library so that the
script can be copied to an offline machine without installing openpyxl.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
CELL_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
MACRO_RE = re.compile(r"^`([A-Za-z_][A-Za-z0-9_$]*)$")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def column_number(letters: str) -> int:
    result = 0
    for letter in letters:
        result = result * 26 + ord(letter) - ord("A") + 1
    return result


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def as_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        number = int(value)
        return number if number > 0 else None
    text = clean(value)
    if re.fullmatch(r"[0-9]+", text):
        number = int(text)
        return number if number > 0 else None
    return None


def verilog_value(value: Any, fallback: int = 1) -> str:
    if value is None or clean(value) == "":
        return str(fallback)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean(value)


@dataclass
class Diagnostic:
    level: str
    message: str


class Reporter:
    def __init__(self) -> None:
        self.items: list[Diagnostic] = []

    def warning(self, message: str) -> None:
        self.items.append(Diagnostic("警告", message))

    def error(self, message: str) -> None:
        self.items.append(Diagnostic("错误", message))

    @property
    def has_errors(self) -> bool:
        return any(item.level == "错误" for item in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(item.level == "警告" for item in self.items)

    def print(self) -> None:
        for item in self.items:
            prefix = "warning" if item.level == "警告" else "error"
            print(f"{prefix}[{item.message}]", file=sys.stderr)


@dataclass
class Sheet:
    name: str
    cells: dict[tuple[int, int], Any]
    max_row: int = 0
    max_column: int = 0

    def cell(self, row: int, column: int) -> Any:
        return self.cells.get((row, column))


@dataclass
class Workbook:
    sheets: list[Sheet]

    def by_name(self, name: str) -> Sheet | None:
        return next((sheet for sheet in self.sheets if sheet.name == name), None)


class XlsxReader:
    """Small OOXML reader supporting the cell types needed by the input format."""

    def read(self, path: Path) -> Workbook:
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError(f"无法打开 XLSX 文件 {path}: {exc}") from exc

        with archive:
            names = set(archive.namelist())
            required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
            missing = required - names
            if missing:
                raise ValueError(f"不是有效的 XLSX 文件，缺少: {', '.join(sorted(missing))}")

            shared_strings = self._read_shared_strings(archive, names)
            relationships = self._read_relationships(archive)
            workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
            sheets: list[Sheet] = []
            for element in workbook_root.iter():
                if local_name(element.tag) != "sheet":
                    continue
                name = element.attrib.get("name", "")
                relation_id = next(
                    (value for key, value in element.attrib.items() if local_name(key) == "id"),
                    None,
                )
                target = relationships.get(relation_id or "")
                if not target:
                    continue
                target = target.replace("\\", "/")
                if target.startswith("/"):
                    sheet_path = target.lstrip("/")
                elif target.startswith("xl/"):
                    sheet_path = target
                else:
                    sheet_path = "xl/" + target.lstrip("/")
                sheets.append(self._read_sheet(archive, sheet_path, name, shared_strings))
            return Workbook(sheets)

    @staticmethod
    def _read_shared_strings(archive: zipfile.ZipFile, names: set[str]) -> list[str]:
        if "xl/sharedStrings.xml" not in names:
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        result: list[str] = []
        for item in root:
            if local_name(item.tag) == "si":
                result.append("".join(node.text or "" for node in item.iter() if local_name(node.tag) == "t"))
        return result

    @staticmethod
    def _read_relationships(archive: zipfile.ZipFile) -> dict[str, str]:
        root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        return {
            element.attrib.get("Id", ""): element.attrib.get("Target", "")
            for element in root
            if local_name(element.tag) == "Relationship"
        }

    @staticmethod
    def _read_sheet(
        archive: zipfile.ZipFile,
        sheet_path: str,
        name: str,
        shared_strings: list[str],
    ) -> Sheet:
        root = ET.fromstring(archive.read(sheet_path))
        cells: dict[tuple[int, int], Any] = {}
        max_row = 0
        max_column = 0
        for element in root.iter():
            if local_name(element.tag) != "c":
                continue
            reference = element.attrib.get("r", "")
            match = CELL_RE.match(reference)
            if not match:
                continue
            column = column_number(match.group(1))
            row = int(match.group(2))
            cell_type = element.attrib.get("t", "")
            value_node = next((node for node in element if local_name(node.tag) == "v"), None)
            if cell_type == "inlineStr":
                value: Any = "".join(
                    node.text or "" for node in element.iter() if local_name(node.tag) == "t"
                )
            else:
                raw = value_node.text if value_node is not None else None
                if raw is None:
                    continue
                if cell_type == "s":
                    index = int(raw)
                    value = shared_strings[index] if index < len(shared_strings) else ""
                elif cell_type == "b":
                    value = raw == "1"
                elif cell_type in {"str", "e"}:
                    value = raw
                else:
                    try:
                        number = float(raw)
                        value = int(number) if number.is_integer() else number
                    except ValueError:
                        value = raw
            cells[(row, column)] = value
            max_row = max(max_row, row)
            max_column = max(max_column, column)
        return Sheet(name=name, cells=cells, max_row=max_row, max_column=max_column)


@dataclass(frozen=True)
class Width:
    kind: str
    expression: str = "1"
    default: str = "1"

    @property
    def effective(self) -> str:
        return self.expression if self.kind == "literal" else self.default


@dataclass
class Port:
    name: str
    direction: str
    width: Width
    row: int


@dataclass
class Module:
    name: str
    sheet_name: str
    ports: list[Port]
    parameters: dict[str, str] = field(default_factory=dict)
    macros: dict[str, str] = field(default_factory=dict)

    @property
    def port_map(self) -> dict[str, Port]:
        return {port.name: port for port in self.ports}


@dataclass(frozen=True)
class IntegrationBlock:
    module_name: str
    port_column: int
    direction_column: int


@dataclass
class Integration:
    sheet_name: str
    header_row: int
    groups: list[list[IntegrationBlock]]
    top_name: str
    child_names: list[str]


def normalized_direction(value: Any) -> str | None:
    text = clean(value).lower().replace(" ", "")
    return {
        "i": "input",
        "in": "input",
        "input": "input",
        "输入": "input",
        "o": "output",
        "out": "output",
        "output": "output",
        "输出": "output",
        "io": "inout",
        "i/o": "inout",
        "inout": "inout",
        "双向": "inout",
    }.get(text)


def analyze_width(raw_width: Any, default_value: Any, context: str, reporter: Reporter) -> Width:
    if raw_width is None or clean(raw_width) == "":
        number = as_positive_int(default_value)
        return Width("literal", str(number or 1), str(number or 1))
    number = as_positive_int(raw_width)
    if number is not None:
        return Width("literal", str(number), str(number))

    text = clean(raw_width)
    macro_match = MACRO_RE.fullmatch(text)
    if macro_match:
        if default_value is None or clean(default_value) == "":
            reporter.error(f"{context}: 宏 {text} 缺少“数值”默认值")
        return Width("macro", text, verilog_value(default_value))
    if IDENTIFIER_RE.fullmatch(text):
        if default_value is None or clean(default_value) == "":
            reporter.error(f"{context}: parameter {text} 缺少“数值”默认值")
        return Width("parameter", text, verilog_value(default_value))

    reporter.error(f"{context}: 不支持的位宽 {text!r}；请使用正整数、`MACRO 或 PARAMETER")
    return Width("literal", "1", "1")


def find_module_header(sheet: Sheet) -> tuple[int, dict[str, int]] | None:
    aliases = {
        "port": {"端口名", "port", "portname", "port_name"},
        "width": {"位宽", "width"},
        "value": {"数值", "value", "default", "默认值"},
        "direction": {"i/o", "io", "方向", "direction", "dir"},
    }
    for row in range(1, min(sheet.max_row, 20) + 1):
        found: dict[str, int] = {}
        for column in range(1, sheet.max_column + 1):
            text = clean(sheet.cell(row, column)).lower().replace(" ", "")
            for field_name, names in aliases.items():
                if text in names and field_name not in found:
                    found[field_name] = column
        if {"port", "width", "value", "direction"}.issubset(found):
            return row, found
    return None


def module_name_above_header(sheet: Sheet, header_row: int, port_column: int) -> str:
    for row in range(header_row - 1, 0, -1):
        value = clean(sheet.cell(row, port_column))
        if value:
            return value
    return sheet.name


def parse_module(sheet: Sheet, reporter: Reporter) -> Module | None:
    header = find_module_header(sheet)
    if not header:
        return None
    header_row, columns = header
    module_name = module_name_above_header(sheet, header_row, columns["port"])
    if not IDENTIFIER_RE.fullmatch(module_name):
        reporter.error(f"页签 {sheet.name}: 模块名 {module_name!r} 不是合法 Verilog 标识符")
        return None

    ports: list[Port] = []
    parameters: dict[str, str] = {}
    macros: dict[str, str] = {}
    seen: dict[str, Port] = {}
    for row in range(header_row + 1, sheet.max_row + 1):
        port_name = clean(sheet.cell(row, columns["port"]))
        if not port_name:
            continue
        context = f"页签 {sheet.name} 第 {row} 行"
        if not IDENTIFIER_RE.fullmatch(port_name):
            reporter.error(f"{context}: 端口名 {port_name!r} 不是合法 Verilog 标识符")
            continue
        if port_name in seen:
            # A repeated row denotes the same physical port. This is useful when
            # the integration table mentions that port in more than one logical
            # group. Verilog may only declare a named port once, so the first
            # occurrence owns its direction/width and later occurrences merge
            # into it without producing a warning.
            continue
        direction = normalized_direction(sheet.cell(row, columns["direction"]))
        if direction is None:
            reporter.error(f"{context}: 无法识别 i/o 值 {sheet.cell(row, columns['direction'])!r}")
            continue
        width = analyze_width(
            sheet.cell(row, columns["width"]),
            sheet.cell(row, columns["value"]),
            context,
            reporter,
        )
        port = Port(port_name, direction, width, row)
        seen[port_name] = port
        ports.append(port)
        if width.kind == "parameter":
            old = parameters.setdefault(width.expression, width.default)
            if old != width.default:
                reporter.error(
                    f"页签 {sheet.name}: parameter {width.expression} 默认值冲突 ({old}/{width.default})"
                )
        elif width.kind == "macro":
            macro_name = width.expression[1:]
            old = macros.setdefault(macro_name, width.default)
            if old != width.default:
                reporter.error(
                    f"页签 {sheet.name}: 宏 `{macro_name} 默认值冲突 ({old}/{width.default})"
                )
    if not ports:
        reporter.error(f"页签 {sheet.name}: 没有可生成的端口")
    return Module(module_name, sheet.name, ports, parameters, macros)


def find_integration(sheet: Sheet) -> Integration | None:
    for row in range(1, min(sheet.max_row, 20) + 1):
        port_columns = [
            column
            for column in range(1, sheet.max_column + 1)
            if clean(sheet.cell(row, column)).lower().replace(" ", "")
            in {"端口名", "port", "portname", "port_name"}
        ]
        if len(port_columns) < 2:
            continue
        blocks: list[IntegrationBlock] = []
        for port_column in port_columns:
            direction_column = port_column + 1
            direction_header = clean(sheet.cell(row, direction_column)).lower().replace(" ", "")
            if direction_header not in {"i/o", "io", "方向", "direction", "dir"}:
                continue
            module_name = module_name_above_header(sheet, row, port_column)
            blocks.append(IntegrationBlock(module_name, port_column, direction_column))
        if len(blocks) < 2:
            continue
        groups: list[list[IntegrationBlock]] = []
        for block in blocks:
            if not groups or block.port_column - groups[-1][-1].port_column > 2:
                groups.append([block])
            else:
                groups[-1].append(block)
        top_name = groups[0][0].module_name
        child_names: list[str] = []
        for group in groups:
            for block in group:
                if block.module_name != top_name and block.module_name not in child_names:
                    child_names.append(block.module_name)
        return Integration(sheet.name, row, groups, top_name, child_names)
    return None


def parse_workbook(path: Path, reporter: Reporter) -> tuple[Workbook, dict[str, Module], Integration | None]:
    workbook = XlsxReader().read(path)
    integrations = [item for sheet in workbook.sheets if (item := find_integration(sheet))]
    integration = integrations[0] if integrations else None
    if len(integrations) > 1:
        names = ", ".join(item.sheet_name for item in integrations)
        reporter.error(f"检测到多个集成页签 ({names})，当前规则只允许一个")

    modules: dict[str, Module] = {}
    for sheet in workbook.sheets:
        if integration and sheet.name == integration.sheet_name:
            continue
        module = parse_module(sheet, reporter)
        if module is None:
            reporter.warning(f"页签 {sheet.name}: 未识别为模块定义，已跳过")
            continue
        if module.name in modules:
            reporter.error(f"模块名 {module.name} 在多个页签中重复")
        else:
            modules[module.name] = module

    if integration is None:
        reporter.warning("未检测到集成页签，将只生成模块桩文件")
    else:
        referenced = [integration.top_name, *integration.child_names]
        for name in referenced:
            if name not in modules:
                reporter.error(f"集成页签引用了不存在的模块定义 {name}")
    if not modules:
        reporter.error("工作簿中没有识别到模块定义页签")
    return workbook, modules, integration


def width_expression(width: Width, parameter_map: dict[str, str] | None = None) -> str:
    if width.kind == "parameter" and parameter_map:
        return parameter_map.get(width.expression, width.expression)
    return width.expression


def width_range(width: Width, parameter_map: dict[str, str] | None = None) -> str:
    expression = width_expression(width, parameter_map)
    if width.kind == "literal" and expression == "1":
        return ""
    if width.kind == "literal":
        return f"[{int(expression) - 1}:0] "
    return f"[{expression}-1:0] "


def zero_value(width: Width, parameter_map: dict[str, str] | None = None) -> str:
    expression = width_expression(width, parameter_map)
    if width.kind == "literal":
        return f"{expression}'b0"
    return f"{{{expression}{{1'b0}}}}"


def render_macros(macros: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for name, value in macros.items():
        lines.extend([f"`ifndef {name}", f"`define {name} {value}", "`endif"])
    if lines:
        lines.append("")
    return lines


def render_module_header(module: Module, macros: dict[str, str] | None = None) -> list[str]:
    lines = ["// Generated by xlsx2verilog.py. Do not edit by hand."]
    lines.extend(render_macros(macros if macros is not None else module.macros))
    if module.parameters:
        lines.append(f"module {module.name} #(")
        parameter_items = list(module.parameters.items())
        for index, (name, value) in enumerate(parameter_items):
            comma = "," if index < len(parameter_items) - 1 else ""
            lines.append(f"    parameter integer {name} = {value}{comma}")
        lines.append(") (")
    else:
        lines.append(f"module {module.name} (")
    for index, port in enumerate(module.ports):
        comma = "," if index < len(module.ports) - 1 else ""
        lines.append(
            f"    {port.direction} wire {width_range(port.width)}{port.name}{comma}"
        )
    lines.append(");")
    return lines


def render_stub(module: Module) -> str:
    lines = render_module_header(module)
    output_ports = [port for port in module.ports if port.direction == "output"]
    if output_ports:
        lines.append("")
        lines.append("    // Module placeholder: drive every output to zero.")
        for port in output_ports:
            lines.append(f"    assign {port.name} = {zero_value(port.width)};")
    lines.extend(["", "endmodule", ""])
    return "\n".join(lines)


def safe_name(text: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_$]+", "_", text).strip("_")
    if not result or result[0].isdigit():
        result = "signal_" + result
    return result


def unique_name(base: str, used: set[str]) -> str:
    candidate = safe_name(base)
    suffix = 2
    while candidate in used:
        candidate = f"{safe_name(base)}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


@dataclass
class Wire:
    name: str
    width: Width
    parameter_map: dict[str, str]


def render_integration(
    sheet: Sheet,
    integration: Integration,
    modules: dict[str, Module],
    reporter: Reporter,
) -> str:
    top = modules[integration.top_name]
    children = [modules[name] for name in integration.child_names if name in modules]
    children_by_name = {module.name: module for module in children}
    top_ports = top.port_map

    all_macros: dict[str, str] = {}
    for module in [top, *children]:
        for name, value in module.macros.items():
            previous = all_macros.setdefault(name, value)
            if previous != value:
                reporter.error(f"集成模块: 宏 `{name} 默认值冲突 ({previous}/{value})")

    parameter_maps: dict[str, dict[str, str]] = {top.name: {name: name for name in top.parameters}}
    local_parameters: list[tuple[str, str]] = []
    used_local_parameters = set(top.parameters)
    for child in children:
        mapping: dict[str, str] = {}
        for name, value in child.parameters.items():
            if name in top.parameters:
                mapping[name] = name
                if top.parameters[name] != value:
                    reporter.warning(
                        f"集成模块: {child.name}.{name} 默认值 {value} 被 TOP 参数默认值 {top.parameters[name]} 覆盖"
                    )
            else:
                local_name_candidate = unique_name(
                    f"P_{child.name}_{name}", used_local_parameters
                )
                mapping[name] = local_name_candidate
                local_parameters.append((local_name_candidate, value))
        parameter_maps[child.name] = mapping

    bindings: dict[str, dict[str, str | None]] = {child.name: {} for child in children}
    top_driven_outputs: set[str] = set()
    wires: list[Wire] = []
    used_signals = set(top_ports)

    def get_port(module_name: str, port_name: str, row: int) -> Port | None:
        module = modules.get(module_name)
        if module is None:
            return None
        port = module.port_map.get(port_name)
        if port is None:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {module_name} 没有端口 {port_name}"
            )
        return port

    def validate_sheet_direction(block: IntegrationBlock, port: Port, row: int) -> None:
        raw_direction = sheet.cell(row, block.direction_column)
        listed_direction = normalized_direction(raw_direction)
        if listed_direction is None:
            reporter.warning(
                f"集成页签 {sheet.name} 第 {row} 行: {block.module_name}.{port.name} 的 i/o 值 {raw_direction!r} 无法识别"
            )
        elif listed_direction != port.direction:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {block.module_name}.{port.name} 的方向与模块定义不一致 ({listed_direction}/{port.direction})"
            )

    def bind(module_name: str, port: Port, expression: str | None, row: int) -> None:
        if module_name == top.name:
            return
        target = bindings.setdefault(module_name, {})
        if port.name in target and target[port.name] != expression:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {module_name}.{port.name} 被重复连接"
            )
            return
        target[port.name] = expression

    first_group = integration.groups[0]
    top_block = first_group[0]
    for row in range(integration.header_row + 1, sheet.max_row + 1):
        top_port_name = clean(sheet.cell(row, top_block.port_column))
        row_entries = [
            (block, clean(sheet.cell(row, block.port_column)))
            for block in first_group[1:]
            if clean(sheet.cell(row, block.port_column))
        ]
        if not top_port_name and not row_entries:
            continue
        if not top_port_name:
            reporter.warning(
                f"集成页签 {sheet.name} 第 {row} 行: TOP 端口为空，子模块端口按未连接处理"
            )
            for block, child_port_name in row_entries:
                child_port = get_port(block.module_name, child_port_name, row)
                if child_port:
                    validate_sheet_direction(block, child_port, row)
                    expression = (
                        zero_value(child_port.width, parameter_maps.get(block.module_name))
                        if child_port.direction == "input"
                        else None
                    )
                    bind(block.module_name, child_port, expression, row)
            continue
        top_port = get_port(top.name, top_port_name, row)
        if top_port is None:
            continue
        validate_sheet_direction(top_block, top_port, row)
        for block, child_port_name in row_entries:
            child_port = get_port(block.module_name, child_port_name, row)
            if child_port is None:
                continue
            validate_sheet_direction(block, child_port, row)
            if top_port.direction == "input" and child_port.direction == "output":
                reporter.error(
                    f"集成页签 {sheet.name} 第 {row} 行: TOP 输入 {top_port.name} 与子模块输出 {block.module_name}.{child_port.name} 方向冲突"
                )
            if top_port.direction == "output" and child_port.direction == "input":
                reporter.error(
                    f"集成页签 {sheet.name} 第 {row} 行: TOP 输出 {top_port.name} 不能作为子模块输入的驱动"
                )
            if top_port.width.effective != child_port.width.effective:
                reporter.warning(
                    f"{top.name}.{top_port.name}信号和{block.module_name}.{child_port.name}信号应该连接，但是其位宽不匹配"
                )
            bind(block.module_name, child_port, top_port.name, row)
            if top_port.direction == "output" and child_port.direction in {"output", "inout"}:
                top_driven_outputs.add(top_port.name)

    for group_index, group in enumerate(integration.groups[1:], start=2):
        if len(group) == 1:
            block = group[0]
            for row in range(integration.header_row + 1, sheet.max_row + 1):
                port_name = clean(sheet.cell(row, block.port_column))
                if not port_name:
                    continue
                port = get_port(block.module_name, port_name, row)
                if port is None:
                    continue
                validate_sheet_direction(block, port, row)
                if block.module_name == top.name:
                    continue
                expression = (
                    zero_value(port.width, parameter_maps.get(block.module_name))
                    if port.direction == "input"
                    else None
                )
                bind(block.module_name, port, expression, row)
            continue

        for row in range(integration.header_row + 1, sheet.max_row + 1):
            entries: list[tuple[IntegrationBlock, Port]] = []
            for block in group:
                port_name = clean(sheet.cell(row, block.port_column))
                if not port_name:
                    continue
                port = get_port(block.module_name, port_name, row)
                if port:
                    validate_sheet_direction(block, port, row)
                    entries.append((block, port))
            if not entries:
                continue
            if len(entries) == 1:
                block, port = entries[0]
                reporter.warning(
                    f"集成页签 {sheet.name} 第 {row} 行: 内部连接只有 {block.module_name}.{port.name} 一端，按未连接处理"
                )
                if block.module_name != top.name:
                    expression = (
                        zero_value(port.width, parameter_maps.get(block.module_name))
                        if port.direction == "input"
                        else None
                    )
                    bind(block.module_name, port, expression, row)
                continue

            drivers = [(block, port) for block, port in entries if port.direction == "output"]
            if len(drivers) == 0:
                reporter.warning(
                    f"集成页签 {sheet.name} 第 {row} 行: 内部连接没有 output 驱动端"
                )
                width_source = entries[0]
            elif len(drivers) > 1:
                names = ", ".join(f"{block.module_name}.{port.name}" for block, port in drivers)
                reporter.error(f"集成页签 {sheet.name} 第 {row} 行: 内部连接存在多个驱动端 ({names})")
                width_source = drivers[0]
            else:
                width_source = drivers[0]
            block, source_port = width_source
            source_block, source_port_for_warning = width_source
            source_width = source_port_for_warning.width.effective
            for item_block, port in entries:
                if (item_block, port) == width_source or port.width.effective == source_width:
                    continue
                reporter.warning(
                    f"{source_block.module_name}.{source_port_for_warning.name}信号和{item_block.module_name}.{port.name}信号应该连接，但是其位宽不匹配"
                )
            common_names = {port.name for _, port in entries}
            signal_base = next(iter(common_names)) if len(common_names) == 1 else "_to_".join(
                port.name for _, port in entries
            )
            signal_name = unique_name(f"w_{signal_base}", used_signals)
            wires.append(
                Wire(signal_name, source_port.width, parameter_maps.get(block.module_name, {}))
            )
            for item_block, port in entries:
                if item_block.module_name == top.name:
                    if port.direction == "output":
                        top_driven_outputs.add(port.name)
                    continue
                bind(item_block.module_name, port, signal_name, row)

    # Named-port instantiations may omit ports, but explicitly tie/open all omitted
    # child ports to make the generated integration deterministic and lint-friendly.
    for child in children:
        for port in child.ports:
            if port.name not in bindings[child.name]:
                reporter.warning(
                    f"集成页签 {sheet.name}: 未列出 {child.name}.{port.name}，自动按未连接端口处理"
                )
                bindings[child.name][port.name] = (
                    zero_value(port.width, parameter_maps[child.name])
                    if port.direction == "input"
                    else None
                )

    lines = render_module_header(top, all_macros)
    if local_parameters:
        lines.append("")
        lines.append("    // Parameters local to child modules.")
        for name, value in local_parameters:
            lines.append(f"    localparam integer {name} = {value};")
    if wires:
        lines.append("")
        lines.append("    // Internal child-to-child connections.")
        for wire in wires:
            lines.append(f"    wire {width_range(wire.width, wire.parameter_map)}{wire.name};")

    undriven_outputs = [
        port for port in top.ports if port.direction == "output" and port.name not in top_driven_outputs
    ]
    if undriven_outputs:
        lines.append("")
        lines.append("    // TOP outputs without a child driver are tied to zero.")
        for port in undriven_outputs:
            lines.append(f"    assign {port.name} = {zero_value(port.width)};")

    for child in children:
        lines.append("")
        parameter_map = parameter_maps[child.name]
        if child.parameters:
            lines.append(f"    {child.name} #(")
            items = list(child.parameters)
            for index, name in enumerate(items):
                comma = "," if index < len(items) - 1 else ""
                lines.append(f"        .{name}({parameter_map[name]}){comma}")
            lines.append(f"    ) u_{child.name.lower()} (")
        else:
            lines.append(f"    {child.name} u_{child.name.lower()} (")
        for index, port in enumerate(child.ports):
            comma = "," if index < len(child.ports) - 1 else ""
            expression = bindings[child.name].get(port.name)
            rendered = "" if expression is None else expression
            lines.append(f"        .{port.name}({rendered}){comma}")
        lines.append("    );")
    lines.extend(["", "endmodule", ""])
    return "\n".join(lines)


def generate(
    workbook_path: Path,
    output_directory: Path,
    strict: bool = False,
    check_only: bool = False,
) -> tuple[list[Path], Reporter]:
    reporter = Reporter()
    workbook, modules, integration = parse_workbook(workbook_path, reporter)
    if reporter.has_errors:
        return [], reporter

    rendered: dict[str, str] = {}
    top_name = integration.top_name if integration else None
    for module in modules.values():
        if module.name != top_name:
            rendered[module.name] = render_stub(module)
    if integration:
        sheet = workbook.by_name(integration.sheet_name)
        if sheet is None:
            reporter.error(f"找不到集成页签 {integration.sheet_name}")
        else:
            rendered[top_name or "TOP"] = render_integration(
                sheet, integration, modules, reporter
            )
    elif top_name is None:
        for module in modules.values():
            rendered[module.name] = render_stub(module)

    if reporter.has_errors or (strict and reporter.has_warnings):
        return [], reporter
    paths = [output_directory / f"{name}.v" for name in rendered]
    if not check_only:
        output_directory.mkdir(parents=True, exist_ok=True)
        for path, content in zip(paths, rendered.values()):
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            temporary.replace(path)
    return paths, reporter


def choose_workbook() -> Path:
    # Excel creates lock files such as "~$test.xlsx" while a workbook is open;
    # those are not valid workbooks and must not appear in the selection menu.
    candidates = sorted(
        path for path in Path.cwd().glob("*.xlsx") if not path.name.startswith("~$")
    )
    if not candidates:
        raise ValueError("当前目录没有 .xlsx 文件，请在命令行指定工作簿路径")
    if len(candidates) == 1 or not sys.stdin.isatty():
        if len(candidates) > 1 and not sys.stdin.isatty():
            raise ValueError("当前目录有多个 .xlsx 文件，请在命令行指定其中一个")
        return candidates[0]
    print("请选择要生成的工作簿：")
    for index, path in enumerate(candidates, start=1):
        print(f"  [{index}] {path.name}")
    while True:
        response = input(f"输入序号 (1-{len(candidates)}): ").strip()
        if response.isdigit() and 1 <= int(response) <= len(candidates):
            return candidates[int(response) - 1]
        print("输入无效，请重试。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从 XLSX 模块定义和集成关系生成 Verilog（零第三方依赖）"
    )
    parser.add_argument("workbook", nargs="?", type=Path, help="输入 .xlsx；省略时提供终端选择")
    parser.add_argument("-o", "--output", type=Path, default=Path("generated"), help="输出目录")
    parser.add_argument("--check", action="store_true", help="只解析和校验，不写文件")
    parser.add_argument("--strict", action="store_true", help="存在任何警告时也不写文件并返回失败")
    parser.add_argument("--list", action="store_true", help="列出识别结果，不生成文件")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workbook_path = args.workbook or choose_workbook()
        if not workbook_path.is_file():
            raise ValueError(f"输入文件不存在: {workbook_path}")
        if workbook_path.suffix.lower() != ".xlsx":
            raise ValueError("输入文件必须是 .xlsx 格式")
        if args.list:
            reporter = Reporter()
            workbook, modules, integration = parse_workbook(workbook_path, reporter)
            print("页签: " + ", ".join(sheet.name for sheet in workbook.sheets))
            print("模块: " + (", ".join(modules) or "无"))
            if integration:
                print(f"TOP: {integration.top_name}")
                print("子模块: " + (", ".join(integration.child_names) or "无"))
            reporter.print()
            return 2 if reporter.has_errors or (args.strict and reporter.has_warnings) else 0

        paths, reporter = generate(
            workbook_path.resolve(),
            args.output.resolve(),
            strict=args.strict,
            check_only=args.check,
        )
        reporter.print()
        failed = reporter.has_errors or (args.strict and reporter.has_warnings)
        if failed:
            print("校验失败，未生成文件。", file=sys.stderr)
            return 2
        if args.check:
            print(f"校验完成：预计生成 {len(paths)} 个 .v 文件。")
        else:
            print(f"生成完成：{len(paths)} 个 .v 文件写入 {args.output.resolve()}")
            for path in paths:
                print(f"  {path.name}")
        return 0
    except (ValueError, OSError, ET.ParseError, KeyError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
