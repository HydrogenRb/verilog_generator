#!/usr/bin/env python3
"""Generate Verilog module stubs and a TOP integration module from an XLSX file.

The implementation intentionally uses only Python's standard library so that the
script can be copied to an offline machine without installing openpyxl.
"""

from __future__ import annotations

import argparse
import ast
import itertools
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, TextIO
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# User configuration
# ---------------------------------------------------------------------------
# False (default): treat every port as unconditional and emit no `ifdef blocks.
# True: honor “条件：MACRO” in category cells and emit conditional Verilog.
ENABLE_CONDITIONAL_BLOCKS = False


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
CELL_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
MACRO_RE = re.compile(r"^`([A-Za-z_][A-Za-z0-9_$]*)$")
INTERFACE_TYPE_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_$]*::)*"
    r"[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*$"
)
TEMPLATE_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
MISSING_TEMPLATE_OPEN_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
MISSING_TEMPLATE_CLOSE_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
UNKNOWN_WIDTH = 114
IGNORED_COLUMN_HEADERS = {"修改", "修改列"}
GROUP_SEPARATOR = "// ----- ----- ----- ----- ----- -----"
NO_GROUP = "no group"
MODULE_LABEL_RE = re.compile(r"^module\s*[:：]\s*(.+)$", re.IGNORECASE)
CONDITION_CATEGORY_RE = re.compile(
    r"条件\s*[:：]\s*`?\s*([A-Za-z_][A-Za-z0-9_$]*)", re.IGNORECASE
)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def column_number(letters: str) -> int:
    result = 0
    for letter in letters:
        result = result * 26 + ord(letter) - ord("A") + 1
    return result


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


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

        # A production workbook may carry a manual-review column named
        # “修改”. Remove it from the logical sheet at the reader boundary so
        # no downstream parser can accidentally treat its contents as a
        # header, template note, category, port, direction, or connection.
        header_rows: set[int] = set()
        for row in range(1, min(max_row, 20) + 1):
            headers = {
                clean(cells.get((row, column))).lower().replace(" ", "")
                for column in range(1, max_column + 1)
            }
            has_port_header = headers.intersection(
                {"端口名", "port", "portname", "port_name"}
            )
            has_direction_header = headers.intersection(
                {"i/o", "io", "方向", "direction", "dir"}
            )
            if has_port_header and has_direction_header:
                header_rows.add(row)
        ignored_columns = {
            column
            for row in header_rows
            for column in range(1, max_column + 1)
            if clean(cells.get((row, column))).lower().replace(" ", "")
            in IGNORED_COLUMN_HEADERS
        }
        if ignored_columns:
            retained_columns = [
                column
                for column in range(1, max_column + 1)
                if column not in ignored_columns
            ]
            logical_column = {
                physical: index
                for index, physical in enumerate(retained_columns, start=1)
            }
            cells = {
                (row, logical_column[column]): value
                for (row, column), value in cells.items()
                if column in logical_column
            }
            max_column = len(retained_columns)
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
    category: str = NO_GROUP
    condition: str | None = None
    arrays: tuple[Width, ...] = ()
    interface_type: str | None = None
    packed_dimensions: tuple[Width, ...] = ()
    direction_inferred: bool = False
    template_source: str | None = None
    template_values: tuple[str, ...] = ()

    @property
    def array(self) -> Width | None:
        """Backward-compatible access to the first unpacked dimension."""
        return self.arrays[0] if self.arrays else None

    @property
    def shape(self) -> tuple[str, ...]:
        """Comparable interface/element type and all unpacked dimensions."""
        if self.interface_type:
            base_type = self.interface_type.rsplit(".", 1)[0]
            return (f"interface:{base_type}", *(item.effective for item in self.arrays))
        return (
            *(item.effective for item in self.packed_dimensions),
            self.width.effective,
            *(item.effective for item in self.arrays),
        )

    @property
    def is_interface(self) -> bool:
        return self.interface_type is not None


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
        "na": "interface",
        "n/a": "interface",
        "interface": "interface",
        "if": "interface",
    }.get(text)


def evaluate_int_expression(value: Any) -> int | None:
    """Safely evaluate a small, integer-only arithmetic expression."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        integer = int(value)
        return integer if integer > 0 else None
    text = clean(value)
    if not text:
        return None
    try:
        root = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError):
        return None

    node_count = 0

    def visit(node: ast.AST) -> int:
        nonlocal node_count
        node_count += 1
        if node_count > 64:
            raise ValueError
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(
            node.value, bool
        ):
            if abs(node.value) > (1 << 31):
                raise ValueError
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = visit(node.operand)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp):
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, (ast.Div, ast.FloorDiv)):
                if right == 0:
                    raise ValueError
                result = int(left / right)
            elif isinstance(node.op, ast.Mod):
                if right == 0:
                    raise ValueError
                result = left % right
            elif isinstance(node.op, ast.LShift):
                if right < 0 or right > 30:
                    raise ValueError
                result = left << right
            elif isinstance(node.op, ast.RShift):
                if right < 0 or right > 30:
                    raise ValueError
                result = left >> right
            elif isinstance(node.op, ast.BitAnd):
                result = left & right
            elif isinstance(node.op, ast.BitOr):
                result = left | right
            elif isinstance(node.op, ast.BitXor):
                result = left ^ right
            else:
                raise ValueError
            if abs(result) > (1 << 31):
                raise ValueError
            return result
        raise ValueError

    try:
        result = visit(root)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return result if result > 0 else None


def split_top_level_product(value: Any, *, require_spaces: bool) -> list[str]:
    """Split top-level multiplication, optionally requiring spaces around `*`."""
    text = clean(value)
    if not text:
        return []
    depth = 0
    start = 0
    parts: list[str] = []
    for index, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "*" and depth == 0:
            spaced = index > 0 and index + 1 < len(text) and text[index - 1].isspace() and text[
                index + 1
            ].isspace()
            if require_spaces and not spaced:
                continue
            part = text[start:index].strip()
            if not part:
                return [text]
            parts.append(part)
            start = index + 1
    if not parts:
        return [text]
    final = text[start:].strip()
    if not final:
        return [text]
    parts.append(final)
    return parts


def dimension_defaults(raw_default: Any, count: int) -> list[Any]:
    if count <= 1:
        return [raw_default]
    parts = split_top_level_product(raw_default, require_spaces=False)
    if len(parts) == count:
        return parts
    return [None] * count


def normalized_width_default(
    default_value: Any,
    context: str,
    reporter: Reporter,
    *,
    fallback_uncertain: bool,
) -> str:
    evaluated = evaluate_int_expression(default_value)
    if evaluated is not None:
        return str(evaluated)
    if fallback_uncertain:
        reporter.warning(f"{context}: 位宽默认值无法确定，使用占位值 {UNKNOWN_WIDTH}")
        return str(UNKNOWN_WIDTH)
    if default_value is None or not clean(default_value):
        reporter.error(f"{context}: 缺少“数值”默认值")
    else:
        reporter.error(f"{context}: 无法计算默认值 {clean(default_value)!r}")
    return "1"


def analyze_width(
    raw_width: Any,
    default_value: Any,
    context: str,
    reporter: Reporter,
    *,
    fallback_uncertain: bool = False,
) -> Width:
    if raw_width is None or clean(raw_width) == "":
        number = evaluate_int_expression(default_value)
        return Width("literal", str(number or 1), str(number or 1))
    number = evaluate_int_expression(raw_width)
    if number is not None:
        return Width("literal", str(number), str(number))

    text = clean(raw_width)
    macro_match = MACRO_RE.fullmatch(text)
    if macro_match:
        default = normalized_width_default(
            default_value,
            f"{context}: 宏 {text}",
            reporter,
            fallback_uncertain=fallback_uncertain,
        )
        return Width("macro", text, default)
    if IDENTIFIER_RE.fullmatch(text):
        default = normalized_width_default(
            default_value,
            f"{context}: parameter {text}",
            reporter,
            fallback_uncertain=fallback_uncertain,
        )
        return Width("parameter", text, default)

    if re.fullmatch(r"[A-Za-z0-9_$`()\s`+\-*/%<>&|^]+", text) and re.search(
        r"[+\-*/%<>&|^]", text
    ):
        inferred = evaluate_int_expression(default_value)
        if inferred is not None:
            return Width("literal", str(inferred), str(inferred))
        reporter.warning(
            f"{context}: 表达式 {text!r} 无法确定位宽，使用占位值 {UNKNOWN_WIDTH}"
        )
        return Width("literal", str(UNKNOWN_WIDTH), str(UNKNOWN_WIDTH))

    reporter.error(f"{context}: 不支持的位宽 {text!r}；请使用正整数、`MACRO 或 PARAMETER")
    return Width("literal", "1", "1")


def analyze_port_dimensions(
    raw_width: Any,
    default_value: Any,
    raw_array: Any,
    array_default: Any,
    context: str,
    reporter: Reporter,
    *,
    fallback_uncertain: bool,
) -> tuple[Width, tuple[Width, ...], tuple[Width, ...]]:
    width_parts = split_top_level_product(raw_width, require_spaces=True)
    if len(width_parts) > 1:
        defaults = dimension_defaults(default_value, len(width_parts))
        dimensions = [
            analyze_width(
                part,
                defaults[index],
                f"{context} 第 {index + 1} 维",
                reporter,
                fallback_uncertain=fallback_uncertain,
            )
            for index, part in enumerate(width_parts)
        ]
        width = dimensions[-1]
        packed_dimensions = dimensions[:-1]
        arrays: list[Width] = []
    else:
        width = analyze_width(
            raw_width,
            default_value,
            context,
            reporter,
            fallback_uncertain=fallback_uncertain,
        )
        packed_dimensions = []
        arrays = []

    array_parts = split_top_level_product(raw_array, require_spaces=True)
    if array_parts:
        defaults = dimension_defaults(array_default, len(array_parts))
        arrays.extend(
            analyze_width(
                part,
                defaults[index],
                f"{context} 数组第 {index + 1} 维",
                reporter,
                fallback_uncertain=fallback_uncertain,
            )
            for index, part in enumerate(array_parts)
        )
    return width, tuple(packed_dimensions), tuple(arrays)


def normalize_template_text(value: Any, context: str, reporter: Reporter) -> str:
    """Normalize a recoverable one-brace typo while preserving named templates."""
    text = clean(value)
    normalized = MISSING_TEMPLATE_OPEN_RE.sub(r"{{\1}}", text)
    normalized = MISSING_TEMPLATE_CLOSE_RE.sub(r"{{\1}}", normalized)
    if normalized != text:
        reporter.warning(
            f"{context}: 模板花括号不完整 {text!r}，按 {normalized!r} 处理；请修正 XLSX"
        )
    return normalized


def template_variables(text: Any) -> list[str]:
    """Return distinct template variable names in their first-seen order."""
    return list(dict.fromkeys(TEMPLATE_RE.findall(clean(text))))


def template_values_in_row(
    sheet: Sheet, row: int, context: str, reporter: Reporter
) -> dict[str, list[str]]:
    """Read named domains such as ``j的范围是{a,b}`` from a row."""
    result: dict[str, list[str]] = {}
    for column in range(1, sheet.max_column + 1):
        text = clean(sheet.cell(row, column))
        for match in re.finditer(r"(?<!\{)\{([^{}]*)\}(?!\})", text):
            prefix = text[: match.start()]
            variable_match = re.search(
                r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:的[^{}]*|是|为|[:：=]|\bin\s*)$",
                prefix,
                re.IGNORECASE,
            )
            if not variable_match:
                continue
            variable = variable_match.group(1)
            values = [item.strip() for item in re.split(r"[,，、;；]", match.group(1))]
            if not values or not all(values):
                reporter.error(f"{context}: 模板变量 {variable} 的取值列表包含空值")
                continue
            if len(values) != len(set(values)):
                reporter.error(f"{context}: 模板变量 {variable} 的取值列表包含重复值")
                continue
            previous = result.setdefault(variable, values)
            if previous != values:
                reporter.error(
                    f"{context}: 模板变量 {variable} 在同一行定义了冲突的取值列表"
                )
    return result


def substitute_template(text: Any, values: dict[str, str]) -> str:
    return TEMPLATE_RE.sub(lambda match: values.get(match.group(1), match.group(0)), clean(text))


def template_default_value(raw_default: Any, values: list[str], index: int) -> Any:
    text = clean(raw_default)
    if not text:
        return raw_default
    stripped = text[1:-1] if text.startswith("{") and text.endswith("}") else text
    parts = [item.strip() for item in re.split(r"[,，、;；]", stripped)]
    if len(parts) == len(values) and all(evaluate_int_expression(item) is not None for item in parts):
        return parts[index]
    return raw_default


def find_module_header(sheet: Sheet) -> tuple[int, dict[str, int]] | None:
    aliases = {
        "port": {"端口名", "port", "portname", "port_name"},
        "width": {"位宽", "width"},
        "value": {"数值", "value", "default", "默认值"},
        "array": {"数组", "数组维度", "数组深度", "array", "depth"},
        "array_value": {
            "数组数值",
            "数组默认值",
            "arrayvalue",
            "array_default",
            "depthdefault",
            "depth_default",
        },
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
            match = MODULE_LABEL_RE.fullmatch(value)
            return match.group(1).strip() if match else value
    match = MODULE_LABEL_RE.fullmatch(sheet.name)
    return match.group(1).strip() if match else sheet.name


def parse_category(
    raw_category: Any, context: str, reporter: Reporter
) -> tuple[str, str | None]:
    text = clean(raw_category)
    if not text:
        return NO_GROUP, None
    condition_match = CONDITION_CATEGORY_RE.search(text)
    if condition_match is None:
        if re.search(r"条件\s*[:：]", text):
            reporter.error(
                f"{context}: 分类条件缺少合法宏名 {text!r}；示例：条件：FEATURE_X"
            )
        return text, None
    condition = condition_match.group(1)
    category = (
        text[: condition_match.start()] + " " + text[condition_match.end() :]
    ).strip(" \t,，;；:/：")
    return category or condition, condition if ENABLE_CONDITIONAL_BLOCKS else None


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
    active_template_values: dict[str, list[str]] = {}
    active_category = NO_GROUP
    active_condition: str | None = None
    category_column = columns["port"] - 1
    for row in range(header_row + 1, sheet.max_row + 1):
        raw_port_name = clean(sheet.cell(row, columns["port"]))
        if not raw_port_name:
            continue
        context = f"页签 {sheet.name} 第 {row} 行"
        raw_port_name = normalize_template_text(raw_port_name, context, reporter)
        row_category = (
            clean(sheet.cell(row, category_column)) if category_column >= 1 else ""
        )
        if row_category:
            active_category, active_condition = parse_category(
                row_category, context, reporter
            )
            active_template_values = {}
        row_template_values = template_values_in_row(sheet, row, context, reporter)
        if row_template_values:
            active_template_values.update(row_template_values)

        port_variables = template_variables(raw_port_name)
        missing_variables = [
            variable for variable in port_variables if variable not in active_template_values
        ]
        if missing_variables:
            names = "、".join(missing_variables)
            example = missing_variables[0]
            reporter.error(
                f"{context}: 端口名模板变量 {names} 未找到取值列表；"
                f"请在同一分类中使用 {example}是{{a,b}} 或 {example}={{a,b}}"
            )
            continue
        if port_variables:
            expansions = [
                dict(zip(port_variables, combination))
                for combination in itertools.product(
                    *(active_template_values[variable] for variable in port_variables)
                )
            ]
        else:
            expansions = [{}]

        base_width = normalize_template_text(
            sheet.cell(row, columns["width"]), f"{context} 位宽", reporter
        )
        base_array = normalize_template_text(
            sheet.cell(row, columns["array"]) if "array" in columns else None,
            f"{context} 数组",
            reporter,
        )
        base_default = sheet.cell(row, columns["value"])
        base_array_default = (
            sheet.cell(row, columns["array_value"])
            if "array_value" in columns
            else None
        )

        for expansion_index, expansion in enumerate(expansions):
            port_name = substitute_template(raw_port_name, expansion)
            assignments = ", ".join(f"{name}={value}" for name, value in expansion.items())
            expanded_context = f"{context} ({assignments})" if assignments else context
            if not IDENTIFIER_RE.fullmatch(port_name):
                reporter.error(
                    f"{expanded_context}: 端口名 {port_name!r} 不是合法 Verilog 标识符"
                )
                continue
            if port_name in seen:
                # A repeated row denotes the same physical port. The first
                # occurrence owns its type/direction and later rows merge into it.
                continue

            raw_width = substitute_template(base_width, expansion)
            raw_array = substitute_template(base_array, expansion)
            raw_default = base_default
            raw_array_default = base_array_default
            if len(port_variables) == 1:
                variable = port_variables[0]
                domain = active_template_values[variable]
                raw_default = template_default_value(
                    raw_default, domain, expansion_index
                )
                raw_array_default = template_default_value(
                    raw_array_default, domain, expansion_index
                )
            raw_default = substitute_template(raw_default, expansion)
            raw_array_default = substitute_template(raw_array_default, expansion)

            unresolved_width = template_variables(raw_width)
            if unresolved_width:
                names = "、".join(unresolved_width)
                reporter.warning(
                    f"{expanded_context}: 位宽引用未绑定模板变量 {names}，"
                    f"使用占位值 {UNKNOWN_WIDTH}；请补充变量取值或修正变量名"
                )
                raw_width = str(UNKNOWN_WIDTH)
            unresolved_array = template_variables(raw_array)
            if unresolved_array:
                names = "、".join(unresolved_array)
                reporter.warning(
                    f"{expanded_context}: 数组维度引用未绑定模板变量 {names}，"
                    f"使用占位值 {UNKNOWN_WIDTH}；请补充变量取值或修正变量名"
                )
                raw_array = str(UNKNOWN_WIDTH)

            listed_direction = normalized_direction(
                sheet.cell(row, columns["direction"])
            )
            direction_inferred = False
            interface_type = clean(raw_width) if INTERFACE_TYPE_RE.fullmatch(clean(raw_width)) else None
            if interface_type:
                direction = "interface"
                if listed_direction not in {None, "interface"}:
                    reporter.warning(
                        f"{expanded_context}: interface {interface_type} 忽略 i/o 值 "
                        f"{sheet.cell(row, columns['direction'])!r}"
                    )
                width = Width("literal", "1", "1")
                _, _, arrays = analyze_port_dimensions(
                    1,
                    1,
                    raw_array,
                    raw_array_default,
                    expanded_context,
                    reporter,
                    fallback_uncertain=bool(expansion),
                )
            else:
                raw_direction = sheet.cell(row, columns["direction"])
                if listed_direction is None and not clean(raw_direction):
                    direction = "inout"
                    direction_inferred = True
                    reporter.warning(
                        f"{expanded_context}: i/o 为空，暂按 inout 生成；请确认并补充方向"
                    )
                else:
                    direction = listed_direction or ""
                if direction not in {"input", "output", "inout"}:
                    reporter.error(
                        f"{expanded_context}: 无法识别 i/o 值 "
                        f"{raw_direction!r}"
                    )
                    continue
                width, packed_dimensions, arrays = analyze_port_dimensions(
                    raw_width,
                    raw_default,
                    raw_array,
                    raw_array_default,
                    expanded_context,
                    reporter,
                    fallback_uncertain=bool(expansion),
                )

            if interface_type:
                packed_dimensions = ()
            port = Port(
                name=port_name,
                direction=direction,
                width=width,
                row=row,
                category=active_category,
                condition=active_condition,
                arrays=arrays,
                interface_type=interface_type,
                packed_dimensions=packed_dimensions,
                direction_inferred=direction_inferred,
                template_source=raw_port_name if port_variables else None,
                template_values=tuple(expansion[name] for name in port_variables),
            )
            seen[port_name] = port
            ports.append(port)
            for dimension in (*packed_dimensions, width, *arrays):
                if dimension.kind == "parameter":
                    old = parameters.setdefault(dimension.expression, dimension.default)
                    if old != dimension.default:
                        reporter.error(
                            f"页签 {sheet.name}: parameter {dimension.expression} 默认值冲突 ({old}/{dimension.default})"
                        )
                elif dimension.kind == "macro":
                    macro_name = dimension.expression[1:]
                    old = macros.setdefault(macro_name, dimension.default)
                    if old != dimension.default:
                        reporter.error(
                            f"页签 {sheet.name}: 宏 `{macro_name} 默认值冲突 ({old}/{dimension.default})"
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
        return f"[{int(expression) - 1}:0]"
    return f"[{expression}-1:0]"


def explicit_dimension_range(
    width: Width, parameter_map: dict[str, str] | None = None
) -> str:
    expression = width_expression(width, parameter_map)
    if width.kind == "literal":
        return f"[{int(expression) - 1}:0]"
    return f"[{expression}-1:0]"


def packed_range(
    packed_dimensions: tuple[Width, ...],
    width: Width,
    parameter_map: dict[str, str] | None = None,
) -> str:
    return "".join(
        packed_dimension_ranges(packed_dimensions, width, parameter_map)
    )


def packed_dimension_ranges(
    packed_dimensions: tuple[Width, ...],
    width: Width,
    parameter_map: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """Return one compact Verilog range for every packed dimension."""
    if not packed_dimensions:
        item = width_range(width, parameter_map)
        return (item,) if item else ()
    return tuple(
        explicit_dimension_range(item, parameter_map)
        for item in (*packed_dimensions, width)
    )


def packed_dimension_widths(
    ranges_by_signal: list[tuple[str, ...]],
) -> tuple[int, ...]:
    """Compute independent display widths for each packed dimension column."""
    dimension_count = max(
        (len(ranges) for ranges in ranges_by_signal), default=0
    )
    return tuple(
        max(
            (
                len(ranges[index])
                for ranges in ranges_by_signal
                if index < len(ranges)
            ),
            default=0,
        )
        for index in range(dimension_count)
    )


def align_packed_dimensions(
    ranges: tuple[str, ...], dimension_widths: tuple[int, ...]
) -> str:
    """Left-align symbolic names while keeping each range boundary aligned."""
    fields: list[str] = []
    for index, field_width in enumerate(dimension_widths):
        if index >= len(ranges):
            fields.append(" " * field_width)
            continue
        range_text = ranges[index]
        body = range_text[1:-1]
        symbolic_suffix = "-1:0"
        if body.endswith(symbolic_suffix):
            expression = body[: -len(symbolic_suffix)]
            expression_width = field_width - 2 - len(symbolic_suffix)
            # Put padding between the expression and subtraction.  Verilog
            # permits this whitespace, so the parameter/macro starts at '['
            # while '-1:0]' remains in a stable column.
            fields.append(
                f"[{expression:<{expression_width}}{symbolic_suffix}]"
            )
        else:
            # Literal ranges have no symbolic name to left-align.  Keep their
            # ':0]' suffix aligned with symbolic ranges in the same dimension.
            fields.append(f"[{body:>{field_width - 2}}]")
    return "".join(fields)


def array_range(array: Width | None, parameter_map: dict[str, str] | None = None) -> str:
    if array is None:
        return ""
    expression = width_expression(array, parameter_map)
    if array.kind == "literal":
        return f" [{int(expression) - 1}:0]"
    return f" [{expression}-1:0]"


def array_ranges(
    arrays: tuple[Width, ...], parameter_map: dict[str, str] | None = None
) -> str:
    return "".join(array_range(array, parameter_map) for array in arrays)


def zero_value(width: Width, parameter_map: dict[str, str] | None = None) -> str:
    expression = width_expression(width, parameter_map)
    if width.kind == "literal":
        return f"{expression}'b0"
    return f"{{{expression}{{1'b0}}}}"


def connection_zero_value(port: Port, parameter_map: dict[str, str] | None = None) -> str:
    if port.arrays:
        return "'{default:'0}"
    if port.packed_dimensions:
        return "'0"
    return zero_value(port.width, parameter_map)


def append_zero_assignment(
    lines: list[str],
    port: Port,
    parameter_map: dict[str, str] | None = None,
    indent: str = "    ",
    target_width: int = 0,
) -> None:
    """Append a scalar/vector assignment or an element-wise array generate loop."""
    if not port.arrays:
        value = "'0" if port.packed_dimensions else zero_value(port.width, parameter_map)
        target = f"{port.name:<{target_width}}" if target_width else port.name
        lines.append(f"{indent}assign {target} = {value};")
        return
    indices: list[str] = []
    current_indent = indent
    for dimension_index, array in enumerate(port.arrays):
        suffix = f"_{dimension_index}" if len(port.arrays) > 1 else ""
        index = safe_name(f"gen_zero_{port.name}{suffix}")
        indices.append(index)
        depth = width_expression(array, parameter_map)
        lines.append(
            f"{current_indent}for (genvar {index} = 0; {index} < {depth}; "
            f"{index} = {index} + 1) begin : g_zero_{safe_name(port.name)}{suffix}"
        )
        current_indent += "    "
    indexed_port = port.name + "".join(f"[{index}]" for index in indices)
    value = "'0" if port.packed_dimensions else zero_value(port.width, parameter_map)
    lines.append(f"{current_indent}assign {indexed_port} = {value};")
    for _ in reversed(port.arrays):
        current_indent = current_indent[:-4]
        lines.append(f"{current_indent}end")


def append_conditioned_zero_assignment(
    lines: list[str],
    port: Port,
    parameter_map: dict[str, str] | None = None,
    indent: str = "    ",
    target_width: int = 0,
) -> None:
    """Drive an output only in configurations where that port exists."""
    if port.condition:
        lines.append(f"`ifdef {port.condition}")
    append_zero_assignment(
        lines,
        port,
        parameter_map=parameter_map,
        indent=indent,
        target_width=target_width,
    )
    if port.condition:
        lines.append("`endif")


def append_fallback_zero_assignment(
    lines: list[str],
    port: Port,
    driver_conditions: list[str | None],
    parameter_map: dict[str, str] | None = None,
    indent: str = "    ",
    target_width: int = 0,
) -> None:
    """Tie a TOP output low only while none of its child drivers exists."""
    if not driver_conditions:
        append_conditioned_zero_assignment(
            lines,
            port,
            parameter_map=parameter_map,
            indent=indent,
            target_width=target_width,
        )
        return

    # An unconditional driver, or a driver guarded by the TOP port's own
    # condition, exists whenever the TOP port exists and needs no fallback.
    if any(
        condition is None or condition == port.condition
        for condition in driver_conditions
    ):
        return

    conditions = list(
        dict.fromkeys(
            condition for condition in driver_conditions if condition is not None
        )
    )
    if port.condition:
        lines.append(f"`ifdef {port.condition}")
    for index, condition in enumerate(conditions):
        directive = "ifdef" if index == 0 else "elsif"
        lines.append(f"`{directive} {condition}")
        lines.append(f"{indent}// Active child output drives this TOP port.")
        lines.append(f"{indent}// 子模块输出当前有效，不启用备用置零赋值。")
    lines.append("`else")
    append_zero_assignment(
        lines,
        port,
        parameter_map=parameter_map,
        indent=indent,
        target_width=target_width,
    )
    lines.append("`endif")
    if port.condition:
        lines.append("`endif")


def render_macros(macros: dict[str, str]) -> list[str]:
    if not macros:
        return []
    name_width = max(len(name) for name in macros)
    lines = [f"`define {name:<{name_width}} {value}" for name, value in macros.items()]
    if lines:
        lines.append("")
    return lines


def port_groups(ports: list[Port]) -> list[list[Port]]:
    groups: list[list[Port]] = []
    for port in ports:
        identity = (port.category, port.condition)
        if not groups or (groups[-1][0].category, groups[-1][0].condition) != identity:
            groups.append([port])
        else:
            groups[-1].append(port)
    return groups


def category_comment(category: str) -> str:
    text = re.sub(r"[\r\n]+", " ", clean(category)).strip()
    return text or NO_GROUP


def append_conditional_comma(lines: list[str], future_groups: list[list[Port]]) -> None:
    """Emit one comma iff at least one later conditional group is enabled."""
    if not future_groups:
        return
    if any(group[0].condition is None for group in future_groups):
        lines.append("    ,")
        return
    conditions = list(
        dict.fromkeys(
            group[0].condition
            for group in future_groups
            if group[0].condition is not None
        )
    )
    for index, condition in enumerate(conditions):
        directive = "ifdef" if index == 0 else "elsif"
        lines.extend([f"`{directive} {condition}", "    ,"])
    lines.append("`endif")


def port_declaration_prefix(
    port: Port,
    direction_width: int,
    dimension_widths: tuple[int, ...],
) -> str:
    if port.is_interface:
        return port.interface_type or "interface"
    ranges = packed_dimension_ranges(port.packed_dimensions, port.width)
    packed = align_packed_dimensions(ranges, dimension_widths)
    packed_field = f" {packed}" if dimension_widths else ""
    return f"{port.direction:<{direction_width}} wire{packed_field}"


def render_module_header(module: Module, macros: dict[str, str] | None = None) -> list[str]:
    lines = ["// Generated by xlsx2verilog.py. Do not edit by hand."]
    lines.extend(render_macros(macros if macros is not None else module.macros))
    if module.parameters:
        lines.append(f"module {module.name} #(")
        parameter_items = list(module.parameters.items())
        parameter_name_width = max(len(name) for name, _ in parameter_items)
        for index, (name, value) in enumerate(parameter_items):
            comma = "," if index < len(parameter_items) - 1 else ""
            lines.append(
                f"    parameter integer {name:<{parameter_name_width}} = {value}{comma}"
            )
        lines.append(") (")
    else:
        lines.append(f"module {module.name} (")
    regular_ports = [port for port in module.ports if not port.is_interface]
    direction_width = max((len(port.direction) for port in regular_ports), default=0)
    packed_ranges_by_port = [
        packed_dimension_ranges(port.packed_dimensions, port.width)
        for port in regular_ports
    ]
    dimension_widths = packed_dimension_widths(packed_ranges_by_port)
    prefixes = {
        id(port): port_declaration_prefix(port, direction_width, dimension_widths)
        for port in module.ports
    }
    prefix_width = max((len(prefix) for prefix in prefixes.values()), default=0)
    port_name_width = max((len(port.name) for port in module.ports), default=0)
    array_fields = {id(port): array_ranges(port.arrays) for port in module.ports}
    array_width = max((len(field) for field in array_fields.values()), default=0)
    groups = port_groups(module.ports)
    has_conditions = any(group[0].condition is not None for group in groups)
    port_index = 0
    for group_index, group in enumerate(groups):
        if group_index:
            lines.append("")
        condition = group[0].condition
        if condition:
            lines.append(f"`ifdef {condition}")
        lines.extend(
            [
                f"    {GROUP_SEPARATOR}",
                f"    // {category_comment(group[0].category)}",
                f"    {GROUP_SEPARATOR}",
            ]
        )
        for group_port_index, port in enumerate(group):
            if has_conditions:
                comma = "," if group_port_index < len(group) - 1 else ""
            else:
                comma = "," if port_index < len(module.ports) - 1 else ""
            prefix = prefixes[id(port)]
            declaration = (
                f"    {prefix:<{prefix_width}} {port.name:<{port_name_width}}"
                f"{array_fields[id(port)]:>{array_width}}"
            )
            line = declaration.rstrip() + comma
            if port.direction_inferred:
                line += "  /* TODO: XLSX i/o 为空，暂按 inout 生成；需处理方向缺失问题 */"
            lines.append(line)
            port_index += 1
        if has_conditions and group_index < len(groups) - 1:
            append_conditional_comma(lines, groups[group_index + 1 :])
        if condition:
            lines.append("`endif")
    lines.append(");")
    return lines


def render_stub(module: Module) -> str:
    lines = render_module_header(module)
    output_ports = [port for port in module.ports if port.direction == "output"]
    if output_ports:
        lines.append("")
        lines.append("    // Module placeholder: drive every output to zero.")
        lines.append("    // 模块占位逻辑：所有输出均置零。")
        target_width = max(
            (len(port.name) for port in output_ports if not port.arrays), default=0
        )
        for port in output_ports:
            append_conditioned_zero_assignment(
                lines, port, target_width=target_width
            )
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
    arrays: tuple[Width, ...]
    parameter_map: dict[str, str]
    interface_type: str | None = None
    packed_dimensions: tuple[Width, ...] = ()


@dataclass(frozen=True)
class Binding:
    expression: str | None
    conditions: tuple[str, ...] = ()


def append_instance_connections(
    lines: list[str],
    child: Module,
    child_bindings: dict[str, Binding],
    reserved_macros: set[str],
) -> None:
    """Append a named-port list whose commas remain valid after preprocessing.

    The temporary marker is necessary when every child port is conditional:
    there may be no unconditional first association to anchor leading commas.
    It is undefined before and after this one instance.
    """
    ordered = [(port, child_bindings[port.name]) for port in child.ports]
    port_name_width = max((len(port.name) for port in child.ports), default=0)
    expression_width = max(
        (
            len(binding.expression or "")
            for _, binding in ordered
        ),
        default=0,
    )
    if not any(binding.conditions for _, binding in ordered):
        for index, (port, binding) in enumerate(ordered):
            comma = "," if index < len(ordered) - 1 else ""
            rendered = "" if binding.expression is None else binding.expression
            lines.append(
                f"        .{port.name:<{port_name_width}} "
                f"({rendered:<{expression_width}}){comma}"
            )
        return

    marker_base = safe_name(
        f"XLSX2VERILOG_INTERNAL_HAVE_CONNECTION_{child.name}"
    ).upper()
    marker = marker_base
    suffix = 2
    while marker in reserved_macros:
        marker = f"{marker_base}_{suffix}"
        suffix += 1
    reserved_macros.add(marker)

    lines.extend([f"`ifdef {marker}", f"`undef {marker}", "`endif"])
    for port, binding in ordered:
        for condition in binding.conditions:
            lines.append(f"`ifdef {condition}")
        lines.extend(
            [
                f"`ifdef {marker}",
                "        ,",
                "`else",
                f"`define {marker}",
                "`endif",
            ]
        )
        rendered = "" if binding.expression is None else binding.expression
        lines.append(
            f"        .{port.name:<{port_name_width}} "
            f"({rendered:<{expression_width}})"
        )
        lines.extend("`endif" for _ in reversed(binding.conditions))
    lines.extend([f"`ifdef {marker}", f"`undef {marker}", "`endif"])


def render_integration(
    sheet: Sheet,
    integration: Integration,
    modules: dict[str, Module],
    reporter: Reporter,
) -> str:
    top = modules[integration.top_name]
    children = [modules[name] for name in integration.child_names if name in modules]
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

    bindings: dict[str, dict[str, Binding]] = {child.name: {} for child in children}
    top_output_drivers: dict[str, list[str]] = {}
    top_output_driver_conditions: dict[str, list[str | None]] = {}
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

    def get_ports(module_name: str, reference: str, row: int) -> list[Port]:
        context = f"集成页签 {sheet.name} 第 {row} 行"
        reference = normalize_template_text(reference, context, reporter)
        matches = list(TEMPLATE_RE.finditer(reference))
        if not matches:
            port = get_port(module_name, reference, row)
            return [port] if port else []
        module = modules.get(module_name)
        if module is None:
            return []
        # Match the template row that produced the port, not merely the final
        # name. A regex for ``data_{{i}}`` would also consume an unrelated
        # physical port such as ``data_debug`` and create false count/direction
        # conflicts in the integration sheet.
        ports = [port for port in module.ports if port.template_source == reference]
        if not ports:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {module_name} 没有与模板端口 {reference} 匹配的展开端口"
            )
        return ports

    def aligned_expansions(
        expanded: list[tuple[IntegrationBlock, list[Port]]], row: int
    ) -> list[list[tuple[IntegrationBlock, Port]]]:
        if not expanded:
            return []
        templated = [
            (block, ports)
            for block, ports in expanded
            if ports and ports[0].template_source is not None
        ]
        template_counts = {len(ports) for _, ports in templated}
        if len(template_counts) > 1:
            details = "; ".join(
                f"{block.module_name}={len(ports)}[{', '.join(port.name for port in ports)}]"
                for block, ports in expanded
            )
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: 模板端口展开数量不一致 ({details})"
            )
            return []

        if templated:
            _, reference_ports = templated[0]
            ordered_values = [port.template_values for port in reference_ports]
            reference_values = set(ordered_values)
            for block, ports in templated[1:]:
                current_values = {port.template_values for port in ports}
                if current_values != reference_values:
                    expected = ", ".join("/".join(values) for values in ordered_values)
                    actual = ", ".join(
                        "/".join(port.template_values) for port in ports
                    )
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: 模板端口展开取值不一致 "
                        f"(基准=[{expected}], {block.module_name}=[{actual}])"
                    )
                    return []

        count = next(iter(template_counts), 1)
        result: list[list[tuple[IntegrationBlock, Port]]] = []
        for index in range(count):
            row_items: list[tuple[IntegrationBlock, Port]] = []
            expansion_values = (
                templated[0][1][index].template_values if templated else ()
            )
            for block, ports in expanded:
                if not ports:
                    continue
                if ports[0].template_source is None:
                    port = ports[0]
                else:
                    port = next(
                        item for item in ports if item.template_values == expansion_values
                    )
                row_items.append((block, port))
            result.append(row_items)
        return result

    def validate_sheet_direction(block: IntegrationBlock, port: Port, row: int) -> None:
        raw_direction = sheet.cell(row, block.direction_column)
        listed_direction = normalized_direction(raw_direction)
        if listed_direction is None:
            if not clean(raw_direction):
                listed_direction = "inout"
                reporter.warning(
                    f"集成页签 {sheet.name} 第 {row} 行: "
                    f"{block.module_name}.{port.name} 的 i/o 为空，按 inout 校验；请补充方向"
                )
            else:
                reporter.warning(
                    f"集成页签 {sheet.name} 第 {row} 行: {block.module_name}.{port.name} 的 i/o 值 {raw_direction!r} 无法识别"
                )
                return
        if listed_direction != port.direction and port.direction_inferred:
            reporter.warning(
                f"集成页签 {sheet.name} 第 {row} 行: {block.module_name}.{port.name} "
                f"由空 i/o 推断为 inout，与集成页签的 {listed_direction} 不同；请人工确认"
            )
        elif listed_direction != port.direction:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {block.module_name}.{port.name} 的方向与模块定义不一致 ({listed_direction}/{port.direction})"
            )

    def bind(
        module_name: str,
        port: Port,
        expression: str | None,
        row: int,
        extra_conditions: tuple[str | None, ...] = (),
    ) -> None:
        if module_name == top.name:
            return
        target = bindings.setdefault(module_name, {})
        conditions = tuple(
            dict.fromkeys(
                condition
                for condition in (port.condition, *extra_conditions)
                if condition is not None
            )
        )
        binding = Binding(expression, conditions)
        if port.name in target and target[port.name] != binding:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {module_name}.{port.name} 被重复连接"
            )
            return
        target[port.name] = binding

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
                for child_port in get_ports(block.module_name, child_port_name, row):
                    validate_sheet_direction(block, child_port, row)
                    expression = (
                        connection_zero_value(
                            child_port, parameter_maps.get(block.module_name)
                        )
                        if child_port.direction == "input"
                        else None
                    )
                    bind(block.module_name, child_port, expression, row)
            continue
        expanded = [(top_block, get_ports(top.name, top_port_name, row))]
        expanded.extend(
            (block, get_ports(block.module_name, child_port_name, row))
            for block, child_port_name in row_entries
        )
        for aligned in aligned_expansions(expanded, row):
            if not aligned or aligned[0][0] != top_block:
                continue
            _, top_port = aligned[0]
            validate_sheet_direction(top_block, top_port, row)
            for block, child_port in aligned[1:]:
                validate_sheet_direction(block, child_port, row)
                if top_port.direction == "input" and child_port.direction == "output":
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: TOP 输入 {top_port.name} 与子模块输出 {block.module_name}.{child_port.name} 方向冲突"
                    )
                # A TOP output may intentionally fan out to child inputs. If
                # no child output/inout drives it, the undriven-output pass
                # below creates the TOP signal, ties it to zero, and the same
                # signal then drives every connected child input.
                if top_port.shape != child_port.shape:
                    reporter.warning(
                        f"{top.name}.{top_port.name}信号和{block.module_name}.{child_port.name}信号应该连接，但是其位宽不匹配"
                    )
                bind(
                    block.module_name,
                    child_port,
                    top_port.name,
                    row,
                    extra_conditions=(top_port.condition,),
                )
                if top_port.direction == "output" and child_port.direction in {
                    "output",
                    "inout",
                }:
                    drivers = top_output_drivers.setdefault(top_port.name, [])
                    driver_name = f"{block.module_name}.{child_port.name}"
                    if driver_name not in drivers:
                        drivers.append(driver_name)
                        top_output_driver_conditions.setdefault(
                            top_port.name, []
                        ).append(child_port.condition)
                    if len(drivers) == 2:
                        reporter.warning(
                            f"集成页签 {sheet.name} 第 {row} 行: TOP 输出 "
                            f"{top.name}.{top_port.name} 存在多个子模块驱动端 "
                            f"({', '.join(drivers)})"
                        )

    for group_index, group in enumerate(integration.groups[1:], start=2):
        if len(group) == 1:
            block = group[0]
            for row in range(integration.header_row + 1, sheet.max_row + 1):
                port_name = clean(sheet.cell(row, block.port_column))
                if not port_name:
                    continue
                for port in get_ports(block.module_name, port_name, row):
                    validate_sheet_direction(block, port, row)
                    if block.module_name == top.name:
                        continue
                    expression = (
                        connection_zero_value(port, parameter_maps.get(block.module_name))
                        if port.direction == "input"
                        else None
                    )
                    bind(block.module_name, port, expression, row)
            continue

        for row in range(integration.header_row + 1, sheet.max_row + 1):
            expanded: list[tuple[IntegrationBlock, list[Port]]] = []
            for block in group:
                port_name = clean(sheet.cell(row, block.port_column))
                if not port_name:
                    continue
                ports = get_ports(block.module_name, port_name, row)
                for port in ports:
                    validate_sheet_direction(block, port, row)
                expanded.append((block, ports))
            for entries in aligned_expansions(expanded, row):
                if not entries:
                    continue
                if len(entries) == 1:
                    block, port = entries[0]
                    reporter.warning(
                        f"集成页签 {sheet.name} 第 {row} 行: 内部连接只有 {block.module_name}.{port.name} 一端，按未连接处理"
                    )
                    if block.module_name != top.name:
                        expression = (
                            connection_zero_value(
                                port, parameter_maps.get(block.module_name)
                            )
                            if port.direction == "input"
                            else None
                        )
                        bind(block.module_name, port, expression, row)
                    continue

                interface_flags = [port.is_interface for _, port in entries]
                if any(interface_flags) and not all(interface_flags):
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: interface 不能与普通端口直接互连"
                    )
                    continue
                if all(interface_flags):
                    width_source = entries[0]
                else:
                    drivers = [
                        (block, port)
                        for block, port in entries
                        if port.direction == "output"
                    ]
                    if len(drivers) == 0:
                        reporter.warning(
                            f"集成页签 {sheet.name} 第 {row} 行: 内部连接没有 output 驱动端"
                        )
                        width_source = entries[0]
                    elif len(drivers) > 1:
                        names = ", ".join(
                            f"{block.module_name}.{port.name}" for block, port in drivers
                        )
                        reporter.error(
                            f"集成页签 {sheet.name} 第 {row} 行: 内部连接存在多个驱动端 ({names})"
                        )
                        width_source = drivers[0]
                    else:
                        width_source = drivers[0]
                block, source_port = width_source
                source_block, source_port_for_warning = width_source
                source_shape = source_port_for_warning.shape
                for item_block, port in entries:
                    if (item_block, port) == width_source or port.shape == source_shape:
                        continue
                    reporter.warning(
                        f"{source_block.module_name}.{source_port_for_warning.name}信号和{item_block.module_name}.{port.name}信号应该连接，但是其位宽不匹配"
                    )
                common_names = {port.name for _, port in entries}
                signal_base = (
                    next(iter(common_names))
                    if len(common_names) == 1
                    else "_to_".join(port.name for _, port in entries)
                )
                signal_name = unique_name(f"w_{signal_base}", used_signals)
                wires.append(
                    Wire(
                        name=signal_name,
                        width=source_port.width,
                        arrays=source_port.arrays,
                        parameter_map=parameter_maps.get(block.module_name, {}),
                        interface_type=(
                            source_port.interface_type.rsplit(".", 1)[0]
                            if source_port.interface_type
                            else None
                        ),
                        packed_dimensions=source_port.packed_dimensions,
                    )
                )
                for item_block, port in entries:
                    if item_block.module_name == top.name:
                        if port.direction == "output":
                            top_output_driver_conditions.setdefault(
                                port.name, []
                            ).append(source_port.condition)
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
                bindings[child.name][port.name] = Binding(
                    (
                        connection_zero_value(port, parameter_maps[child.name])
                        if port.direction == "input"
                        else None
                    ),
                    (port.condition,) if port.condition else (),
                )

    lines = render_module_header(top, all_macros)
    if local_parameters:
        lines.append("")
        lines.append("    // Parameters local to child modules.")
        lines.append("    // 子模块局部参数。")
        local_name_width = max(len(name) for name, _ in local_parameters)
        for name, value in local_parameters:
            lines.append(
                f"    localparam integer {name:<{local_name_width}} = {value};"
            )
    if wires:
        lines.append("")
        lines.append("    // Internal child-to-child connections.")
        lines.append("    // 子模块之间的内部连线。")
        wire_packed_ranges = [
            ()
            if wire.interface_type
            else packed_dimension_ranges(
                wire.packed_dimensions, wire.width, wire.parameter_map
            )
            for wire in wires
        ]
        wire_dimension_widths = packed_dimension_widths(wire_packed_ranges)
        wire_prefixes: list[str] = []
        for wire, packed_ranges in zip(wires, wire_packed_ranges):
            if wire.interface_type:
                wire_prefixes.append(wire.interface_type)
            else:
                packed = align_packed_dimensions(
                    packed_ranges, wire_dimension_widths
                )
                wire_prefixes.append(
                    f"wire {packed}" if wire_dimension_widths else "wire"
                )
        wire_prefix_width = max(len(prefix) for prefix in wire_prefixes)
        wire_name_width = max(len(wire.name) for wire in wires)
        wire_array_fields = [
            array_ranges(wire.arrays, wire.parameter_map) for wire in wires
        ]
        wire_array_width = max(
            (len(field) for field in wire_array_fields), default=0
        )
        for wire, prefix, array_field in zip(
            wires, wire_prefixes, wire_array_fields
        ):
            suffix = "();" if wire.interface_type else ";"
            declaration = (
                f"    {prefix:<{wire_prefix_width}} {wire.name:<{wire_name_width}}"
                f"{array_field:>{wire_array_width}}"
            )
            lines.append(declaration.rstrip() + suffix)

    fallback_outputs = [
        port
        for port in top.ports
        if port.direction == "output"
        and not any(
            condition is None or condition == port.condition
            for condition in top_output_driver_conditions.get(port.name, [])
        )
    ]
    if fallback_outputs:
        lines.append("")
        lines.append("    // TOP outputs without an active child driver are tied to zero.")
        lines.append("    // 没有有效子模块驱动的 TOP 输出在当前配置下置零。")
        target_width = max(
            (len(port.name) for port in fallback_outputs if not port.arrays),
            default=0,
        )
        for port in fallback_outputs:
            append_fallback_zero_assignment(
                lines,
                port,
                top_output_driver_conditions.get(port.name, []),
                target_width=target_width,
            )

    for child in children:
        lines.append("")
        parameter_map = parameter_maps[child.name]
        if child.parameters:
            lines.append(f"    {child.name} #(")
            items = list(child.parameters)
            parameter_name_width = max(len(name) for name in items)
            parameter_value_width = max(
                len(parameter_map[name]) for name in items
            )
            for index, name in enumerate(items):
                comma = "," if index < len(items) - 1 else ""
                lines.append(
                    f"        .{name:<{parameter_name_width}} "
                    f"({parameter_map[name]:<{parameter_value_width}}){comma}"
                )
            lines.append(f"    ) u_{child.name.lower()} (")
        else:
            lines.append(f"    {child.name} u_{child.name.lower()} (")
        reserved_macros = set(all_macros)
        reserved_macros.update(
            port.condition
            for module in [top, *children]
            for port in module.ports
            if port.condition
        )
        append_instance_connections(
            lines, child, bindings[child.name], reserved_macros
        )
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


def discover_workbooks() -> list[Path]:
    # Excel creates lock files such as "~$test.xlsx" while a workbook is open;
    # those are not valid workbooks and must not appear in the selection menu.
    return sorted(
        path for path in Path.cwd().glob("*.xlsx") if not path.name.startswith("~$")
    )


def read_terminal_key() -> str:
    """Read one logical key without third-party packages."""
    if sys.platform == "win32":
        import msvcrt

        character = msvcrt.getwch()
        if character in {"\x00", "\xe0"}:
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "other")
        if character == "\r":
            return "enter"
        if character == "\x1b":
            return "escape"
        if character == "\x03":
            raise KeyboardInterrupt
        return character.lower()

    import select
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        character = sys.stdin.read(1)
        if character == "\x1b":
            sequence = ""
            while len(sequence) < 2 and select.select([sys.stdin], [], [], 0.03)[0]:
                sequence += sys.stdin.read(1)
            return {"[A": "up", "[B": "down"}.get(sequence, "escape")
        if character in {"\r", "\n"}:
            return "enter"
        if character == "\x03":
            raise KeyboardInterrupt
        return character.lower()
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


def arrow_menu(
    title: str,
    options: list[str],
    *,
    key_reader: Callable[[], str] | None = None,
    output: TextIO | None = None,
) -> int | None:
    """Return the selected option index using Up/Down and Enter; Esc/Q cancels."""
    if not options:
        return None
    read_key = key_reader or read_terminal_key
    stream = output or sys.stdout
    selected = 0
    stream.write(f"{title}\n")
    first_draw = True
    while True:
        if not first_draw:
            stream.write(f"\x1b[{len(options)}A")
        for index, option in enumerate(options):
            marker = ">" if index == selected else " "
            stream.write(f"\x1b[2K\r {marker} {option}\n")
        stream.flush()
        first_draw = False
        key = read_key()
        if key == "up":
            selected = (selected - 1) % len(options)
        elif key == "down":
            selected = (selected + 1) % len(options)
        elif key == "enter":
            stream.write("\n")
            stream.flush()
            return selected
        elif key in {"escape", "q"}:
            stream.write("\n")
            stream.flush()
            return None
        elif key.isdigit() and 1 <= int(key) <= len(options):
            stream.write("\n")
            stream.flush()
            return int(key) - 1


class MenuCancelled(ValueError):
    """Raised when the user backs out of an interactive selection."""


def choose_workbook() -> Path:
    candidates = discover_workbooks()
    if not candidates:
        raise ValueError("当前目录没有 .xlsx 文件，请在命令行指定工作簿路径")
    if len(candidates) == 1 or not sys.stdin.isatty():
        if len(candidates) > 1 and not sys.stdin.isatty():
            raise ValueError("当前目录有多个 .xlsx 文件，请在命令行指定其中一个")
        return candidates[0]
    selected = arrow_menu("请选择工作簿（↑/↓，Enter 确认，Esc 返回）：", [p.name for p in candidates])
    if selected is None:
        raise MenuCancelled("已取消选择工作簿")
    return candidates[selected]


def interactive_main() -> int:
    actions = ["生成 Verilog", "查看识别结果", "校验工作簿", "严格校验", "退出"]
    while True:
        selected = arrow_menu("XLSX → Verilog（↑/↓，Enter 确认）：", actions)
        if selected is None or selected == 4:
            print("已退出。")
            return 0
        try:
            workbook = choose_workbook()
        except MenuCancelled:
            continue
        except ValueError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        if selected == 0:
            response = input("输出目录 [generated]: ").strip()
            output = response or "generated"
            return main([str(workbook), "--output", output])
        if selected == 1:
            return main([str(workbook), "--list"])
        if selected == 2:
            return main([str(workbook), "--check"])
        return main([str(workbook), "--check", "--strict"])


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
    raw_arguments = list(argv) if argv is not None else sys.argv[1:]
    if argv is None and not raw_arguments and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return interactive_main()
        except KeyboardInterrupt:
            print("\n已取消。")
            return 130
    args = build_parser().parse_args(raw_arguments)
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
