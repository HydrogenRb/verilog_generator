#!/usr/bin/env python3
"""Generate Verilog module stubs and a TOP integration module from an XLSX file.

The implementation intentionally uses only Python's standard library so that the
script can be copied to an offline machine without installing openpyxl.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import itertools
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, TextIO
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# User configuration
# ---------------------------------------------------------------------------
# False (default): treat every port as unconditional and emit no `ifdef blocks.
# True: honor “条件：MACRO” in category cells and emit conditional Verilog.
ENABLE_CONDITIONAL_BLOCKS = False

# Text placed at the beginning of every generated Verilog file.  Edit this
# plain multi-line string to match the project.  The stable USER markers make
# the header editable after generation as well.
VERILOG_FILE_HEADER = """//******************
// Design by: aaa
// Project name: bbb
//
//******************"""

# False (default): preserve an existing generated file's ``file header`` USER
# region.  True: replace that region with VERILOG_FILE_HEADER on every run.
OVERWRITE_FILE_HEADER = False

# Diagnostic visibility only affects terminal output.  Hidden diagnostics are
# still recorded and still affect exit codes / --strict exactly as before.
SHOW_ERROR_MESSAGES = True
SHOW_WARNING_MESSAGES = True
SHOW_INFO_MESSAGES = True

# Fine-grained terminal visibility.  Set one diagnostic code to False to hide
# only that kind of message.  Diagnostics remain recorded and continue to
# affect error / --strict return codes.  The three SHOW_* switches above are
# master switches and take precedence over this table.
DIAGNOSTIC_VISIBILITY_BY_CODE = {
    # Errors: malformed input or unsafe generation/overwrite.
    "E_GENERAL": True,
    "E_WIDTH": True,
    "E_TEMPLATE": True,
    "E_CONDITION": True,
    "E_MODULE": True,
    "E_PORT": True,
    "E_PARAMETER": True,
    "E_INTEGRATION": True,
    "E_INSTANCE": True,
    "E_DIRECTION": True,
    "E_PORT_REFERENCE": True,
    "E_GENERATE_INDEX": True,
    "E_NA_TARGET": True,
    "E_BIT_SELECT": True,
    "E_INTERFACE_CONNECTION": True,
    "E_DRIVER_CONFLICT": True,
    "E_USER_CODE": True,
    "E_FILE_IO": True,
    # Warnings: generation can continue, but engineering review is needed.
    "W_GENERAL": True,
    "W_WIDTH_PLACEHOLDER": True,
    "W_WIDTH_MISMATCH": True,
    "W_TEMPLATE_REPAIR": True,
    "W_TEMPLATE_BINDING": True,
    "W_IO_DEFAULTED": True,
    "W_MODULE_SKIPPED": True,
    "W_NO_INTEGRATION": True,
    "W_INSTANCE_UNUSED": True,
    "W_DRIVER_RISK": True,
    "W_GENERATE_RANGE": True,
    # Information: deterministic decisions and automatic recovery.
    "I_GENERAL": True,
    "I_PARAMETER_LINK": True,
    "I_DIRECTION_INFERRED": True,
    "I_TEMPLATE_PARTIAL": True,
    "I_NA_CONNECTION": True,
    "I_UNCONNECTED": True,
    "I_INSTANCE": True,
}

# Startup identification.  These lines are centered to one shared width.
SCRIPT_DISPLAY_NAME = "CustomScipt xlsx2verilog"
SCRIPT_VERSION = "Version V3.12"
SCRIPT_RELEASE_DATE = "2026.8.24"
SCRIPT_CONTACT = "Contact xxx-xxxx in case"


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
CELL_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
MACRO_RE = re.compile(r"^`([A-Za-z_][A-Za-z0-9_$]*)$")
MACRO_REFERENCE_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_$]*)")
PARAMETER_EXPRESSION_TOKEN_RE = re.compile(
    r"(?P<space>[ \t]+)|"
    r"(?P<macro>`[A-Za-z_][A-Za-z0-9_$]*)|"
    r"(?P<system>\$[A-Za-z_][A-Za-z0-9_$]*)|"
    r"(?P<based>(?:[0-9][0-9_]*)?'[sS]?[dDhHbBoO][0-9a-fA-F_xXzZ?]+)|"
    r"(?P<unbased>'[01xXzZ])|"
    r"(?P<number>[0-9][0-9_]*)|"
    r"(?P<identifier>[A-Za-z_][A-Za-z0-9_$]*)|"
    r"(?P<operator><<<|>>>|===|!==|<<|>>|<=|>=|==|!=|&&|\|\||"
    r"\*\*|::|[+\-*/%&|^~!<>(){}\[\]?:,])"
)
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
INDEX_MARKER_RE = re.compile(r"^(.*)\[([A-Za-z_][A-Za-z0-9_$]*)\]\s*$")
BIT_SELECT_RE = re.compile(r"^(.*)\[\s*([0-9]+)\s*\]\s*$")
ANONYMOUS_NA_RE = re.compile(
    r"^(?:na|n/a)(?:\s*\[\s*[A-Za-z_][A-Za-z0-9_$]*\s*\])?"
    r"(?:\s*->\s*[^\s].*)?$",
    re.IGNORECASE,
)
NA_REFERENCE_RE = re.compile(
    r"^(?:na|n/a)(?:\s*\[\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\])?"
    r"(?:\s*->\s*(.+))?$",
    re.IGNORECASE,
)
VERILOG_CONSTANT_RE = re.compile(
    r"^(?:[0-9]+|'[sS]?[dDhHbBoO]?[0-9a-fA-F_xXzZ?]+|"
    r"[0-9]+\s*'[sS]?[dDhHbBoO][0-9a-fA-F_xXzZ?]+)$"
)
PARAMETER_CATEGORIES = {"parameter", "parameters", "参数", "参数定义"}
NA_CONNECTION_TODO = "//TODO:本信号期望有逻辑功能，请完成"
MAX_TEMPLATE_RANGE_ITEMS = 4096
USER_CODE_MARKER_RE = re.compile(
    r"(?m)^[ \t]*/\*USER CODE (BEGIN|END)[ \t]+(.+?)[ \t]*\*/[ \t]*\r?$"
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
    code: str


class Reporter:
    def __init__(self) -> None:
        self.items: list[Diagnostic] = []

    def warning(self, message: str, *, code: str = "W_GENERAL") -> None:
        self.items.append(Diagnostic("警告", message, code))

    def info(self, message: str, *, code: str = "I_GENERAL") -> None:
        """Record an informational decision that must not fail ``--strict``."""
        self.items.append(Diagnostic("信息", message, code))

    def error(self, message: str, *, code: str = "E_GENERAL") -> None:
        self.items.append(Diagnostic("错误", message, code))

    @property
    def has_errors(self) -> bool:
        return any(item.level == "错误" for item in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(item.level == "警告" for item in self.items)

    def print(
        self,
        stream: TextIO | None = None,
        *,
        color: bool | None = None,
    ) -> None:
        """Print diagnostics grouped by severity, with ANSI color on a TTY."""
        target = stream if stream is not None else sys.stderr
        if color is None:
            color = bool(getattr(target, "isatty", lambda: False)())
        styles = {
            "错误": ("error", "\033[31m"),
            "警告": ("warning", "\033[33m"),
            "信息": ("info", "\033[36m"),
        }
        visibility = {
            "错误": SHOW_ERROR_MESSAGES,
            "警告": SHOW_WARNING_MESSAGES,
            "信息": SHOW_INFO_MESSAGES,
        }
        reset = "\033[0m" if color else ""
        for level in ("错误", "警告", "信息"):
            if not visibility[level]:
                continue
            grouped = [
                item
                for item in self.items
                if item.level == level
                and DIAGNOSTIC_VISIBILITY_BY_CODE.get(item.code, True)
            ]
            if not grouped:
                continue
            prefix, style = styles[level]
            paint = style if color else ""
            print(
                f"{paint}=== {prefix.upper()} ({len(grouped)}) ==={reset}",
                file=target,
            )
            for item in grouped:
                print(
                    f"{paint}{prefix}[{item.code}][{item.message}]{reset}",
                    file=target,
                )


@dataclass
class Sheet:
    name: str
    cells: dict[tuple[int, int], Any]
    max_row: int = 0
    max_column: int = 0
    xml_path: str = ""
    ignored_columns: frozenset[int] = frozenset()

    def cell(self, row: int, column: int) -> Any:
        return self.cells.get((row, column))


@dataclass
class Workbook:
    sheets: list[Sheet]

    def by_name(self, name: str) -> Sheet | None:
        return next((sheet for sheet in self.sheets if sheet.name == name), None)


class XlsxReader:
    """Small OOXML reader supporting the cell types needed by the input format."""

    def read(self, path: Path, *, ignore_review_columns: bool = True) -> Workbook:
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
                sheets.append(
                    self._read_sheet(
                        archive,
                        sheet_path,
                        name,
                        shared_strings,
                        ignore_review_columns=ignore_review_columns,
                    )
                )
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
        *,
        ignore_review_columns: bool,
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
        raw_ignored_columns = frozenset(ignored_columns)
        if ignored_columns and ignore_review_columns:
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
            raw_ignored_columns = frozenset()
        return Sheet(
            name=name,
            cells=cells,
            max_row=max_row,
            max_column=max_column,
            xml_path=sheet_path,
            ignored_columns=raw_ignored_columns,
        )


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
        """Backward-compatible access to the first spreadsheet array dimension."""
        return self.arrays[0] if self.arrays else None

    @property
    def shape(self) -> tuple[str, ...]:
        """Comparable interface/element type and every declared dimension."""
        if self.interface_type:
            base_type = self.interface_type.rsplit(".", 1)[0]
            return (f"interface:{base_type}", *(item.effective for item in self.arrays))
        return (
            *(item.effective for item in self.arrays),
            *(item.effective for item in self.packed_dimensions),
            self.width.effective,
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
    declared_parameters: dict[str, str] = field(default_factory=dict)
    parameter_expressions: dict[str, str] = field(default_factory=dict)
    parameter_comments: dict[str, str] = field(default_factory=dict)
    externally_configurable_parameters: set[str] = field(default_factory=set)

    @property
    def port_map(self) -> dict[str, Port]:
        return {port.name: port for port in self.ports}


@dataclass(frozen=True)
class IntegrationBlock:
    module_name: str
    port_column: int
    direction_column: int
    anonymous_na: bool = False


@dataclass(frozen=True)
class InstanceSpec:
    module_name: str
    instance_name: str | None = None
    count: int | None = None
    raw_count: str | None = None
    row: int = 0


@dataclass
class Integration:
    sheet_name: str
    header_row: int
    groups: list[list[IntegrationBlock]]
    top_name: str
    child_names: list[str]
    instance_specs: dict[str, InstanceSpec] = field(default_factory=dict)


def integration_parameter_rows(
    sheet: Sheet, integration: Integration
) -> dict[int, set[int]]:
    """Return inherited ``parameter`` rows for every connection group."""
    result: dict[int, set[int]] = {}
    for group_index, group in enumerate(integration.groups):
        category_column = group[0].port_column - 1
        active = False
        rows: set[int] = set()
        for row in range(integration.header_row + 1, sheet.max_row + 1):
            raw_category = (
                clean(sheet.cell(row, category_column))
                if category_column >= 1
                else ""
            )
            if raw_category:
                active = is_parameter_category(raw_category)
            if active and any(
                clean(sheet.cell(row, block.port_column)) for block in group
            ):
                rows.add(row)
        result[group_index] = rows
    return result


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


def split_top_level_product(
    value: Any, *, require_spaces: bool | None = None
) -> list[str]:
    """Split every top-level ``*``; parentheses explicitly keep arithmetic flat.

    ``require_spaces`` is retained as an ignored compatibility argument for
    callers from older integrations.  V2 Tech Review 1 deliberately makes
    whitespace around ``*`` semantically irrelevant.
    """
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


def dimension_defaults(
    raw_default: Any,
    count: int,
    context: str,
    reporter: Reporter,
) -> list[Any]:
    parts = split_top_level_product(raw_default)
    if not parts:
        return [None] * count
    if len(parts) == count:
        return parts
    reporter.error(
        f"{context}: 位宽与数值的 * 维度数量不匹配 ({count}/{len(parts)})",
        code="E_WIDTH",
    )
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
    if default_value is None or not clean(default_value):
        # Missing values may be supplied by another use of the same symbol or
        # by an upper module.  Resolution and the final diagnostic happen only
        # after the whole hierarchy is known.
        return ""
    if fallback_uncertain:
        reporter.warning(
            f"{context}: 位宽默认值无法确定，使用占位值 {UNKNOWN_WIDTH}",
            code="W_WIDTH_PLACEHOLDER",
        )
        return str(UNKNOWN_WIDTH)
    reporter.error(
        f"{context}: 无法计算默认值 {clean(default_value)!r}",
        code="E_WIDTH",
    )
    return "1"


def normalized_parameter_default(
    default_value: Any,
    context: str,
    reporter: Reporter,
    *,
    fallback_uncertain: bool,
) -> str:
    """Normalize an explicit parameter default, including a macro reference."""
    text = clean(default_value)
    macro_match = MACRO_RE.fullmatch(text)
    if macro_match:
        return f"`{macro_match.group(1).upper()}"
    return normalized_width_default(
        default_value,
        context,
        reporter,
        fallback_uncertain=fallback_uncertain,
    )


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
        text = f"`{macro_match.group(1).upper()}"
        default = normalized_width_default(
            default_value,
            f"{context}: 宏 {text}",
            reporter,
            fallback_uncertain=fallback_uncertain,
        )
        return Width("macro", text, default)
    if IDENTIFIER_RE.fullmatch(text):
        text = text.upper()
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
            f"{context}: 表达式 {text!r} 无法确定位宽，使用占位值 {UNKNOWN_WIDTH}",
            code="W_WIDTH_PLACEHOLDER",
        )
        return Width("literal", str(UNKNOWN_WIDTH), str(UNKNOWN_WIDTH))

    reporter.error(
        f"{context}: 不支持的位宽 {text!r}；请使用正整数、`MACRO 或 PARAMETER",
        code="E_WIDTH",
    )
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
    width_parts = split_top_level_product(raw_width)
    if len(width_parts) > 1:
        defaults = dimension_defaults(
            default_value, len(width_parts), context, reporter
        )
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
        defaults = dimension_defaults(default_value, 1, context, reporter)
        width = analyze_width(
            raw_width,
            defaults[0],
            context,
            reporter,
            fallback_uncertain=fallback_uncertain,
        )
        packed_dimensions = []
        arrays = []

    array_parts = split_top_level_product(raw_array)
    if array_parts:
        defaults = dimension_defaults(
            array_default, len(array_parts), f"{context} 数组", reporter
        )
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
            f"{context}: 模板花括号不完整 {text!r}，按 {normalized!r} 处理；请修正 XLSX",
            code="W_TEMPLATE_REPAIR",
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

    def record(variable: str, values: list[str]) -> None:
        previous = result.get(variable)
        if previous is None:
            result[variable] = values
        elif previous != values:
            reporter.error(
                f"{context}: 模板变量 {variable} 在同一行定义了冲突的取值列表",
                code="E_TEMPLATE",
            )

    for column in range(1, sheet.max_column + 1):
        if column in sheet.ignored_columns:
            continue
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
                reporter.error(
                    f"{context}: 模板变量 {variable} 的取值列表包含空值",
                    code="E_TEMPLATE",
                )
                continue
            if len(values) != len(set(values)):
                reporter.error(
                    f"{context}: 模板变量 {variable} 的取值列表包含重复值",
                    code="E_TEMPLATE",
                )
                continue
            record(variable, values)
        for match in re.finditer(
            r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
            r"([0-9]+)\s*[:：]\s*([0-9]+)(?![A-Za-z0-9_])",
            text,
        ):
            variable, first_text, last_text = match.groups()
            first = int(first_text)
            last = int(last_text)
            count = abs(last - first) + 1
            if count > MAX_TEMPLATE_RANGE_ITEMS:
                reporter.error(
                    f"{context}: 模板变量 {variable} 的范围包含 {count} 项，"
                    f"超过上限 {MAX_TEMPLATE_RANGE_ITEMS}",
                    code="E_TEMPLATE",
                )
                continue
            step = 1 if last >= first else -1
            record(variable, [str(value) for value in range(first, last + step, step)])
    return result


def substitute_template(text: Any, values: dict[str, str]) -> str:
    return TEMPLATE_RE.sub(lambda match: values.get(match.group(1), match.group(0)), clean(text))


def uppercase_macro_references(text: Any) -> str:
    """Uppercase complete Verilog macro identifiers in an expanded expression."""
    return MACRO_REFERENCE_RE.sub(
        lambda match: f"`{match.group(1).upper()}", clean(text)
    )


def normalize_parameter_expression(
    value: Any,
    context: str,
    reporter: Reporter,
) -> str:
    """Validate and normalize a generated Verilog parameter expression.

    The expression is never evaluated: the parameter row's ``数值`` column is
    the independent static value used by hierarchy/width checks.  This scanner
    only admits tokens used by single-line Verilog constant expressions, which
    lets parameter references, macro references/calls, system functions and
    operators pass through without opening a statement/comment injection path.
    Generated parameter and macro identifiers follow the project's uppercase
    naming rule; system function names such as ``$clog2`` keep their spelling.
    """
    expression = clean(value)
    if not expression:
        return ""
    if any(marker in expression for marker in ("\r", "\n", ";", "//", "/*", "*/")):
        reporter.error(
            f"{context}: parameter 生成表达式必须是安全的单行 Verilog 常量表达式，"
            "不能包含换行、分号或注释",
            code="E_PARAMETER",
        )
        return ""

    normalized: list[str] = []
    delimiters: list[str] = []
    matching_open = {")": "(", "]": "[", "}": "{"}
    position = 0
    while position < len(expression):
        match = PARAMETER_EXPRESSION_TOKEN_RE.match(expression, position)
        if match is None:
            fragment = expression[position : position + 16]
            reporter.error(
                f"{context}: parameter 生成表达式 {expression!r} 含不支持的内容 "
                f"{fragment!r}",
                code="E_PARAMETER",
            )
            return ""
        token = match.group(0)
        kind = match.lastgroup
        if kind == "macro":
            token = f"`{token[1:].upper()}"
        elif kind == "identifier":
            token = token.upper()
        elif kind == "operator":
            if token in "([{":
                delimiters.append(token)
            elif token in ")]}" and (
                not delimiters or delimiters.pop() != matching_open[token]
            ):
                reporter.error(
                    f"{context}: parameter 生成表达式 {expression!r} 的括号不匹配",
                    code="E_PARAMETER",
                )
                return ""
        normalized.append(token)
        position = match.end()

    if delimiters:
        reporter.error(
            f"{context}: parameter 生成表达式 {expression!r} 的括号不匹配",
            code="E_PARAMETER",
        )
        return ""
    return "".join(normalized)


def template_default_value(raw_default: Any, values: list[str], index: int) -> Any:
    text = clean(raw_default)
    if not text:
        return raw_default
    # A template-specific default may either be the entire cell (``{1,2}``)
    # or one factor in a multidimensional default
    # (``范围是{32,64}*8``).  Double braces are accepted because production
    # workbooks sometimes use them to visually mirror ``{{i}}``.
    range_match = re.search(
        r"(?:范围\s*(?:是|为|[:：=])\s*)?\{\{?([^{}]+)\}\}?",
        text,
    )
    if range_match:
        parts = [
            item.strip()
            for item in re.split(r"[,，、;；]", range_match.group(1))
        ]
        if len(parts) == len(values) and all(parts):
            return text[: range_match.start()] + parts[index] + text[range_match.end() :]
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
                f"{context}: 分类条件缺少合法宏名 {text!r}；示例：条件：FEATURE_X",
                code="E_CONDITION",
            )
        return text, None
    condition = condition_match.group(1)
    category = (
        text[: condition_match.start()] + " " + text[condition_match.end() :]
    ).strip(" \t,，;；:/：")
    return category or condition, condition if ENABLE_CONDITIONAL_BLOCKS else None


def is_parameter_category(value: Any) -> bool:
    """Whether a category starts an explicit module-parameter section."""
    return clean(value).casefold() in PARAMETER_CATEGORIES


def port_dimensions(port: Port) -> tuple[Width, ...]:
    return (*port.packed_dimensions, port.width, *port.arrays)


def replace_port_dimensions(port: Port, transform: Callable[[Width], Width]) -> None:
    """Apply one immutable-Width transformation to every dimension of a port."""
    port.packed_dimensions = tuple(transform(item) for item in port.packed_dimensions)
    port.width = transform(port.width)
    port.arrays = tuple(transform(item) for item in port.arrays)


def rebuild_module_symbols(module: Module) -> None:
    parameters = dict(module.declared_parameters)
    macros: dict[str, str] = {}
    for name, expression in module.parameter_expressions.items():
        macro_match = MACRO_RE.fullmatch(expression)
        if macro_match:
            macros.setdefault(
                macro_match.group(1), module.declared_parameters.get(name, "")
            )
    for port in module.ports:
        for dimension in port_dimensions(port):
            if dimension.kind == "parameter":
                parameters.setdefault(dimension.expression, dimension.default)
            elif dimension.kind == "macro":
                macros.setdefault(dimension.expression.lstrip("`"), dimension.default)
    module.parameters = parameters
    module.macros = macros


def resolve_module_local_defaults(module: Module, reporter: Reporter) -> None:
    """Spread equal, non-empty defaults to blank uses inside one module."""
    values: dict[tuple[str, str], set[str]] = {}
    for name, default in module.declared_parameters.items():
        if default:
            values.setdefault(("parameter", name), set()).add(default)
    for port in module.ports:
        for dimension in port_dimensions(port):
            if dimension.kind in {"macro", "parameter"} and dimension.default:
                values.setdefault(
                    (dimension.kind, dimension.expression), set()
                ).add(dimension.default)
    for (kind, name), defaults in values.items():
        if len(defaults) > 1:
            label = f"宏 {name}" if kind == "macro" else f"parameter {name}"
            reporter.error(
                f"页签 {module.sheet_name}: {label} 默认值冲突 "
                f"({'/'.join(sorted(defaults))})",
                code="E_PARAMETER" if kind == "parameter" else "E_WIDTH",
            )
    unambiguous = {
        key: next(iter(defaults))
        for key, defaults in values.items()
        if len(defaults) == 1
    }

    def spread(width: Width) -> Width:
        if width.kind not in {"macro", "parameter"} or width.default:
            return width
        default = unambiguous.get((width.kind, width.expression))
        return replace(width, default=default) if default else width

    for port in module.ports:
        replace_port_dimensions(port, spread)
    rebuild_module_symbols(module)


def parse_module(sheet: Sheet, reporter: Reporter) -> Module | None:
    header = find_module_header(sheet)
    if not header:
        return None
    header_row, columns = header
    source_module_name = module_name_above_header(sheet, header_row, columns["port"])
    if not IDENTIFIER_RE.fullmatch(source_module_name):
        reporter.error(
            f"页签 {sheet.name}: 模块名 {source_module_name!r} 不是合法 Verilog 标识符",
            code="E_MODULE",
        )
        return None
    module_name = source_module_name.upper()

    ports: list[Port] = []
    declared_parameters: dict[str, str] = {}
    parameter_expressions: dict[str, str] = {}
    parameter_comments: dict[str, str] = {}
    declared_parameter_rows: dict[str, int] = {}
    seen: dict[str, Port] = {}
    active_template_values: dict[str, list[str]] = {}
    active_category = NO_GROUP
    active_condition: str | None = None
    active_parameter_category = False
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
            active_parameter_category = is_parameter_category(active_category)
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
            subject = "parameter 名" if active_parameter_category else "端口名"
            reporter.error(
                f"{context}: {subject}模板变量 {names} 未找到取值列表；"
                f"请在同一分类中使用 {example}是{{a,b}} 或 {example}={{a,b}}",
                code="E_TEMPLATE",
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

        if active_parameter_category:
            for expansion_index, expansion in enumerate(expansions):
                assignments = ", ".join(
                    f"{name}={value}" for name, value in expansion.items()
                )
                expanded_context = (
                    f"{context} ({assignments})" if assignments else context
                )
                parameter_name = substitute_template(
                    raw_port_name, expansion
                ).upper()
                if not IDENTIFIER_RE.fullmatch(parameter_name):
                    reporter.error(
                        f"{expanded_context}: parameter 名 {parameter_name!r} "
                        "不是合法 Verilog 标识符",
                        code="E_PARAMETER",
                    )
                    continue
                raw_default = sheet.cell(row, columns["value"])
                if len(port_variables) == 1:
                    variable = port_variables[0]
                    raw_default = template_default_value(
                        raw_default,
                        active_template_values[variable],
                        expansion_index,
                    )
                raw_default = uppercase_macro_references(
                    substitute_template(raw_default, expansion)
                )
                raw_expression = substitute_template(
                    sheet.cell(row, columns["width"]), expansion
                )
                expression = ""
                if clean(raw_expression):
                    expression = normalize_parameter_expression(
                        raw_expression,
                        f"{expanded_context}: parameter {parameter_name} 的“位宽”",
                        reporter,
                    )
                    default = normalized_width_default(
                        raw_default,
                        f"{expanded_context}: parameter {parameter_name} 的匹配数值",
                        reporter,
                        fallback_uncertain=bool(expansion),
                    )
                else:
                    default = normalized_parameter_default(
                        raw_default,
                        f"{expanded_context}: parameter {parameter_name}",
                        reporter,
                        fallback_uncertain=bool(expansion),
                    )
                previous = declared_parameters.get(parameter_name)
                previous_expression = parameter_expressions.get(parameter_name, "")
                if previous is not None and (
                    previous != default or previous_expression != expression
                ):
                    reporter.error(
                        f"页签 {sheet.name}: parameter {parameter_name} 默认值冲突；"
                        f"第 {declared_parameter_rows[parameter_name]} 行为 "
                        f"{previous_expression or previous} (匹配值 {previous})，"
                        f"第 {row} 行为 {expression or default} (匹配值 {default})",
                        code="E_PARAMETER",
                    )
                    continue
                declared_parameters.setdefault(parameter_name, default)
                if expression:
                    parameter_expressions.setdefault(parameter_name, expression)
                    parameter_comments.setdefault(parameter_name, default)
                declared_parameter_rows.setdefault(parameter_name, row)
            continue

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

        expanded_names_in_row: dict[str, tuple[str, ...]] = {}
        for expansion_index, expansion in enumerate(expansions):
            raw_expansion_values = tuple(
                expansion[name] for name in port_variables
            )
            port_name = substitute_template(raw_port_name, expansion)
            if port_variables:
                previous_values = expanded_names_in_row.setdefault(
                    port_name, raw_expansion_values
                )
                if previous_values != raw_expansion_values:
                    reporter.error(
                        f"{context}: 模板展开产生重复端口 "
                        f"{port_name!r}",
                        code="E_TEMPLATE",
                    )
                    continue
            assignments = ", ".join(f"{name}={value}" for name, value in expansion.items())
            expanded_context = f"{context} ({assignments})" if assignments else context
            if not IDENTIFIER_RE.fullmatch(port_name):
                reporter.error(
                    f"{expanded_context}: 端口名 {port_name!r} 不是合法 Verilog 标识符",
                    code="E_PORT",
                )
                continue
            normalized_template_source = (
                raw_port_name if port_variables else None
            )
            normalized_template_values = tuple(
                expansion[name] for name in port_variables
            )
            if port_name in seen:
                # A repeated row denotes the same physical port. The first
                # occurrence owns its type/direction and later rows merge into it.
                previous = seen[port_name]
                if (
                    previous.template_source is not None
                    or normalized_template_source is not None
                ) and (
                    previous.template_source != normalized_template_source
                    or previous.template_values != normalized_template_values
                ):
                    reporter.error(
                        f"{expanded_context}: 模板来源或展开值与已有端口 "
                        f"{port_name!r} 冲突",
                        code="E_TEMPLATE",
                    )
                continue

            raw_width = substitute_template(base_width, expansion)
            raw_array = substitute_template(base_array, expansion)
            raw_width = uppercase_macro_references(raw_width)
            raw_array = uppercase_macro_references(raw_array)
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
                    f"使用占位值 {UNKNOWN_WIDTH}；请补充变量取值或修正变量名",
                    code="W_TEMPLATE_BINDING",
                )
                raw_width = str(UNKNOWN_WIDTH)
            unresolved_array = template_variables(raw_array)
            if unresolved_array:
                names = "、".join(unresolved_array)
                reporter.warning(
                    f"{expanded_context}: 数组维度引用未绑定模板变量 {names}，"
                    f"使用占位值 {UNKNOWN_WIDTH}；请补充变量取值或修正变量名",
                    code="W_TEMPLATE_BINDING",
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
                        f"{sheet.cell(row, columns['direction'])!r}",
                        code="W_IO_DEFAULTED",
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
                        f"{expanded_context}: i/o 为空，暂按 inout 生成；请确认并补充方向",
                        code="W_IO_DEFAULTED",
                    )
                else:
                    direction = listed_direction or ""
                if direction not in {"input", "output", "inout"}:
                    reporter.error(
                        f"{expanded_context}: 无法识别 i/o 值 "
                        f"{raw_direction!r}",
                        code="E_DIRECTION",
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
                template_source=normalized_template_source,
                template_values=normalized_template_values,
            )
            seen[port_name] = port
            ports.append(port)
    if not ports:
        reporter.error(
            f"页签 {sheet.name}: 没有可生成的端口",
            code="E_PORT",
        )
    module = Module(
        module_name,
        sheet.name,
        ports,
        declared_parameters=declared_parameters,
        parameter_expressions=parameter_expressions,
        parameter_comments=parameter_comments,
    )
    resolve_module_local_defaults(module, reporter)
    return module


def find_instance_specs(sheet: Sheet) -> dict[str, InstanceSpec]:
    """Read the optional 模块名/例化名/例化次数 side table."""
    aliases = {
        "module": {"模块名", "module", "module_name", "modulename"},
        "instance": {"例化名", "实例名", "instance", "instance_name", "instancename"},
        "count": {"例化次数", "实例数量", "instancecount", "instance_count", "count"},
    }
    for header_row in range(1, min(sheet.max_row, 20) + 1):
        columns: dict[str, int] = {}
        for column in range(1, sheet.max_column + 1):
            text = clean(sheet.cell(header_row, column)).casefold().replace(" ", "")
            for field_name, names in aliases.items():
                if text in names and field_name not in columns:
                    columns[field_name] = column
        if not {"module", "instance", "count"}.issubset(columns):
            continue
        specs: dict[str, InstanceSpec] = {}
        for row in range(header_row + 1, sheet.max_row + 1):
            module_name = clean(sheet.cell(row, columns["module"])).upper()
            if not module_name:
                continue
            instance_name = clean(sheet.cell(row, columns["instance"])) or None
            raw_count = clean(sheet.cell(row, columns["count"])) or None
            count = evaluate_int_expression(raw_count) if raw_count else None
            specs[module_name] = InstanceSpec(
                module_name,
                instance_name=instance_name,
                count=count,
                raw_count=raw_count,
                row=row,
            )
        return specs
    return {}


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
        # Some production integration sheets omit the repeated “端口名/i/o”
        # labels for a later module block.  A module label immediately above
        # a populated column is sufficient to recover that block; direction is
        # then validated against the module definition.
        for column in range(1, sheet.max_column + 1):
            if column in port_columns or not clean(sheet.cell(row - 1, column)):
                continue
            if (column - port_columns[0]) % 2:
                # Module blocks are port/direction pairs.  This prevents a
                # title above a neighbouring category column from being
                # mistaken for an omitted module header.
                continue
            if clean(sheet.cell(row, column)):
                continue
            recovered_name = module_name_above_header(sheet, row, column)
            if not IDENTIFIER_RE.fullmatch(recovered_name):
                continue
            if any(
                clean(sheet.cell(data_row, column))
                for data_row in range(row + 1, sheet.max_row + 1)
            ):
                port_columns.append(column)
        port_columns.sort()

        def column_has_only_na_references(column: int) -> bool:
            values = [
                clean(sheet.cell(data_row, column))
                for data_row in range(row + 1, sheet.max_row + 1)
                if clean(sheet.cell(data_row, column))
            ]
            return bool(values) and all(
                ANONYMOUS_NA_RE.fullmatch(value) for value in values
            )

        # An explicit NA endpoint does not represent a module and therefore
        # does not need a module name.  Its own repeated 端口名/i/o header is
        # optional as well.  First separate header-bearing anonymous columns;
        # they must have no explicit label above them and must immediately
        # follow a named port/direction pair.
        unlabeled_na_header_columns = {
            port_column
            for port_column in port_columns
            if not any(
                clean(sheet.cell(label_row, port_column))
                for label_row in range(row - 1, 0, -1)
            )
            and column_has_only_na_references(port_column)
        }
        named_port_columns = [
            port_column
            for port_column in port_columns
            if port_column not in unlabeled_na_header_columns
        ]
        anonymous_na_columns = [
            port_column
            for port_column in unlabeled_na_header_columns
            if port_column - 2 in named_port_columns
        ]

        # Also recognise an entirely headerless column immediately following
        # a normal port/direction pair.  Requiring every populated cell to be
        # NA or NA[index] keeps ordinary notes and spacer columns out of the
        # integration graph.
        paired_columns = {
            column
            for port_column in port_columns
            for column in (port_column, port_column + 1)
        }
        for port_column in named_port_columns:
            column = port_column + 2
            if column > sheet.max_column or column in paired_columns:
                continue
            if clean(sheet.cell(row - 1, column)) or clean(sheet.cell(row, column)):
                continue
            if column_has_only_na_references(column):
                anonymous_na_columns.append(column)

        blocks: list[IntegrationBlock] = []
        for port_column in sorted((*named_port_columns, *anonymous_na_columns)):
            if port_column in anonymous_na_columns:
                direction_column = (
                    port_column + 1
                    if port_column in unlabeled_na_header_columns
                    else 0
                )
                blocks.append(
                    IntegrationBlock("", port_column, direction_column, True)
                )
                continue
            direction_column = port_column + 1
            direction_header = clean(sheet.cell(row, direction_column)).lower().replace(" ", "")
            if direction_header and direction_header not in {
                "i/o", "io", "方向", "direction", "dir"
            }:
                continue
            module_name = module_name_above_header(sheet, row, port_column).upper()
            blocks.append(IntegrationBlock(module_name, port_column, direction_column))
        if len([block for block in blocks if not block.anonymous_na]) < 2:
            continue
        groups: list[list[IntegrationBlock]] = []
        for block in blocks:
            previous = groups[-1][-1] if groups else None
            previous_end = (
                previous.direction_column or previous.port_column
                if previous is not None
                else 0
            )
            if previous is None or block.port_column - previous_end > 1:
                groups.append([block])
            else:
                groups[-1].append(block)
        top_name = groups[0][0].module_name
        child_names: list[str] = []
        for group in groups:
            for block in group:
                if (
                    not block.anonymous_na
                    and block.module_name != top_name
                    and block.module_name not in child_names
                ):
                    child_names.append(block.module_name)
        return Integration(
            sheet.name,
            row,
            groups,
            top_name,
            child_names,
            find_instance_specs(sheet),
        )
    return None


def is_named_integration_sheet(name: str) -> bool:
    """Whether a sheet uses the preferred 集成/集成_xxx naming convention."""
    normalized = clean(name).casefold()
    return (
        normalized == "集成"
        or normalized.startswith("集成_")
        or normalized == "integration"
        or normalized.startswith("integration_")
    )


def discover_integrations(workbook: Workbook) -> list[Integration]:
    """Return valid integration sheets in workbook order.

    Preferred names are used when present, while structural discovery remains
    as a backward-compatible fallback for older workbooks.
    """
    integrations = [
        item for sheet in workbook.sheets if (item := find_integration(sheet))
    ]
    named = [
        item for item in integrations if is_named_integration_sheet(item.sheet_name)
    ]
    return named or integrations


def resolve_hierarchy_defaults(
    modules: dict[str, Module],
    integration: Integration | None,
    reporter: Reporter,
    integration_sheet: Sheet | None = None,
) -> None:
    """Resolve defaults without implicitly exporting local parameters."""
    ordered = list(modules.values())

    linked_parameter_defaults: dict[tuple[str, str], str] = {}
    if integration is not None and integration_sheet is not None:
        rows_by_group = integration_parameter_rows(integration_sheet, integration)
        for group_index, group in enumerate(integration.groups):
            for row in rows_by_group[group_index]:
                entries: list[tuple[Module, str]] = []
                for block in group:
                    if block.anonymous_na:
                        continue
                    module = modules.get(block.module_name)
                    name = clean(
                        integration_sheet.cell(row, block.port_column)
                    ).upper()
                    if module is not None and name in module.parameters:
                        entries.append((module, name))
                known = [
                    (module, name, module.parameters[name])
                    for module, name in entries
                    if module.parameters[name]
                ]
                if not known:
                    continue
                preferred = next(
                    (
                        item
                        for item in known
                        if item[0].name == integration.top_name
                    ),
                    known[0],
                )
                value = preferred[2]
                for module, name in entries:
                    linked_parameter_defaults[(module.name, name)] = value

        for module in ordered:
            for name, value in list(module.declared_parameters.items()):
                linked = linked_parameter_defaults.get((module.name, name))
                if not value and linked:
                    module.declared_parameters[name] = linked

            def spread_linked(width: Width) -> Width:
                if width.kind != "parameter" or width.default:
                    return width
                linked = linked_parameter_defaults.get(
                    (module.name, width.expression)
                )
                return replace(width, default=linked) if linked else width

            for port in module.ports:
                replace_port_dimensions(port, spread_linked)
            resolve_module_local_defaults(module, reporter)

    def finalize_modules() -> None:
        # Only now are truly unresolved values diagnosed. Template-generated
        # symbols retain the documented 114 placeholder so a usable review
        # file can still be emitted; ordinary symbols are hard errors.
        for module in ordered:
            for name, value in list(module.declared_parameters.items()):
                if value:
                    continue
                reporter.error(
                    f"页签 {module.sheet_name}: parameter {name} 缺少“数值”默认值，"
                    "且没有有效的显式 parameter 链接",
                    code="E_PARAMETER",
                )
                module.declared_parameters[name] = "1"
            resolve_module_local_defaults(module, reporter)
            for port in module.ports:
                def finalize(width: Width) -> Width:
                    if width.kind not in {"macro", "parameter"} or width.default:
                        return width
                    label = (
                        f"宏 {width.expression}"
                        if width.kind == "macro"
                        else f"parameter {width.expression}"
                    )
                    if port.template_source is not None:
                        reporter.warning(
                            f"页签 {module.sheet_name} 第 {port.row} 行: {label} "
                            f"缺少可扩散数值，使用占位值 {UNKNOWN_WIDTH}",
                            code="W_WIDTH_PLACEHOLDER",
                        )
                        return replace(width, default=str(UNKNOWN_WIDTH))
                    reporter.error(
                        f"页签 {module.sheet_name} 第 {port.row} 行: "
                        f"{label} 缺少“数值”默认值",
                        code="E_PARAMETER" if width.kind == "parameter" else "E_WIDTH",
                    )
                    return replace(width, default="1")

                replace_port_dimensions(port, finalize)
            rebuild_module_symbols(module)

    if integration is None:
        # Independent stub modules own independent macro namespaces.  Only
        # same-module conflicts (already checked) apply without a hierarchy.
        finalize_modules()
        return

    hierarchy = [
        modules[name]
        for name in [integration.top_name, *integration.child_names]
        if name in modules
    ]

    macro_values: dict[str, set[str]] = {}
    macro_sources: dict[str, list[tuple[str, str]]] = {}
    for module in hierarchy:
        for name, value in module.macros.items():
            if value:
                source = (module.sheet_name, value)
                if source not in macro_sources.setdefault(name, []):
                    macro_sources[name].append(source)
        for port in module.ports:
            for width in port_dimensions(port):
                if not width.default:
                    continue
                if width.kind == "macro":
                    macro_values.setdefault(width.expression, set()).add(width.default)

    for name, values in macro_values.items():
        if len(values) > 1:
            details = "；".join(
                f"页签 {sheet_name} 为 {value}"
                for sheet_name, value in macro_sources.get(name.lstrip("`"), [])
            )
            if not details:
                details = "/".join(sorted(values))
            reporter.error(
                f"集成模块: 宏 {name} 默认值冲突：{details}",
                code="E_WIDTH",
            )

    def inherited(width: Width) -> Width:
        if width.kind != "macro" or width.default:
            return width
        candidates = macro_values.get(width.expression, set())
        if len(candidates) == 1:
            return replace(width, default=next(iter(candidates)))
        return width

    for module in hierarchy:
        for port in module.ports:
            replace_port_dimensions(port, inherited)

    finalize_modules()


def parse_workbook(
    path: Path,
    reporter: Reporter,
    integration_sheet: str | None = None,
) -> tuple[Workbook, dict[str, Module], Integration | None]:
    workbook = XlsxReader().read(path)
    integrations = discover_integrations(workbook)
    integration: Integration | None = None
    if integration_sheet:
        integration = next(
            (
                item
                for item in integrations
                if item.sheet_name.casefold() == integration_sheet.casefold()
            ),
            None,
        )
        if integration is None:
            names = ", ".join(item.sheet_name for item in integrations) or "无"
            reporter.error(
                f"指定的集成页签 {integration_sheet!r} 不存在或格式无效；可选页签: {names}",
                code="E_INTEGRATION",
            )
    elif len(integrations) == 1:
        integration = integrations[0]
    elif len(integrations) > 1:
        names = ", ".join(item.sheet_name for item in integrations)
        reporter.error(
            f"检测到多个集成页签 ({names})；请在终端菜单选择，"
            "或通过 --integration 指定",
            code="E_INTEGRATION",
        )

    modules: dict[str, Module] = {}
    integration_sheet_names = {item.sheet_name for item in integrations}
    referenced = (
        {integration.top_name, *integration.child_names}
        if integration is not None
        else None
    )
    for sheet in workbook.sheets:
        if sheet.name in integration_sheet_names:
            continue
        if referenced is not None:
            header = find_module_header(sheet)
            if header is None:
                continue
            header_row, columns = header
            candidate_name = module_name_above_header(
                sheet, header_row, columns["port"]
            ).upper()
            if candidate_name not in referenced:
                continue
        module = parse_module(sheet, reporter)
        if module is None:
            reporter.warning(
                f"页签 {sheet.name}: 未识别为模块定义，已跳过",
                code="W_MODULE_SKIPPED",
            )
            continue
        if module.name in modules:
            reporter.error(
                f"模块名 {module.name} 在多个页签中重复",
                code="E_MODULE",
            )
        else:
            modules[module.name] = module

    if integration is None and not integrations:
        reporter.warning(
            "未检测到集成页签，将只生成模块桩文件",
            code="W_NO_INTEGRATION",
        )
    elif integration is not None:
        hierarchy_names = [integration.top_name, *integration.child_names]
        for name in hierarchy_names:
            if name not in modules:
                reporter.error(
                    f"集成页签引用了不存在的模块定义 {name}",
                    code="E_INTEGRATION",
                )
        for name, spec in integration.instance_specs.items():
            context = f"集成页签 {integration.sheet_name} 例化配置第 {spec.row} 行"
            if name not in hierarchy_names:
                reporter.warning(
                    f"{context}: 模块 {name} 不在当前集成层次中，配置已忽略",
                    code="W_INSTANCE_UNUSED",
                )
                continue
            if spec.instance_name and not IDENTIFIER_RE.fullmatch(spec.instance_name):
                reporter.error(
                    f"{context}: 例化名 {spec.instance_name!r} 不是合法 Verilog 标识符",
                    code="E_INSTANCE",
                )
            if spec.raw_count and spec.count is None:
                reporter.error(
                    f"{context}: 例化次数 {spec.raw_count!r} 必须是正整数或可计算的正整数表达式",
                    code="E_INSTANCE",
                )
            if name == integration.top_name and (
                spec.instance_name is not None or spec.raw_count is not None
            ):
                reporter.info(
                    f"{context}: TOP 模块不需要例化名或例化次数，相关值已忽略",
                    code="I_INSTANCE",
                )
    if not modules:
        reporter.error(
            "工作簿中没有识别到模块定义页签",
            code="E_MODULE",
        )
    selected_integration_sheet = (
        workbook.by_name(integration.sheet_name) if integration is not None else None
    )
    resolve_hierarchy_defaults(
        modules,
        integration,
        reporter,
        integration_sheet=selected_integration_sheet,
    )
    return workbook, modules, integration


def inspect_all_integrations(path: Path) -> Reporter:
    """Validate every hierarchy independently for workbook-wide edit modes."""
    workbook = XlsxReader().read(path)
    integrations = discover_integrations(workbook)
    selectors: list[str | None] = (
        [item.sheet_name for item in integrations]
        if len(integrations) > 1
        else [None]
    )
    combined = Reporter()
    seen: set[tuple[str, str]] = set()
    for selector in selectors:
        current = Reporter()
        parse_workbook(path, current, integration_sheet=selector)
        for item in current.items:
            key = (item.level, item.message)
            if key not in seen:
                seen.add(key)
                combined.items.append(item)
    return combined


def width_expression(width: Width, parameter_map: dict[str, str] | None = None) -> str:
    if width.kind == "parameter" and parameter_map:
        return parameter_map.get(width.expression, width.expression)
    return width.expression


def width_range(width: Width, parameter_map: dict[str, str] | None = None) -> str:
    expression = width_expression(width, parameter_map)
    if width.kind == "literal" and expression == "1":
        return ""
    return f"[{expression} -1:0]"


def explicit_dimension_range(
    width: Width, parameter_map: dict[str, str] | None = None
) -> str:
    expression = width_expression(width, parameter_map)
    return f"[{expression} -1:0]"


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
        symbolic_suffix = " -1:0"
        if body.endswith(symbolic_suffix):
            expression = body[: -len(symbolic_suffix)].rstrip()
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
    return f" [{expression} -1:0]"


def array_ranges(
    arrays: tuple[Width, ...], parameter_map: dict[str, str] | None = None
) -> str:
    return "".join(array_range(array, parameter_map) for array in arrays)


def port_packed_dimension_ranges(
    port: Port,
    parameter_map: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """Return all ordinary-signal dimensions on the declaration's left side."""
    return all_packed_dimension_ranges(
        port.arrays,
        port.packed_dimensions,
        port.width,
        parameter_map,
    )


def all_packed_dimension_ranges(
    arrays: tuple[Width, ...],
    packed_dimensions: tuple[Width, ...],
    width: Width,
    parameter_map: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """Place spreadsheet array dimensions before the element packed width."""
    leading = tuple(
        explicit_dimension_range(item, parameter_map)
        for item in (*arrays, *packed_dimensions)
    )
    trailing = width_range(width, parameter_map)
    return (*leading, trailing) if trailing else leading


def zero_value(width: Width, parameter_map: dict[str, str] | None = None) -> str:
    expression = width_expression(width, parameter_map)
    if width.kind == "literal":
        return f"{expression}'b0"
    return f"{{{expression}{{1'b0}}}}"


def connection_zero_value(port: Port, parameter_map: dict[str, str] | None = None) -> str:
    if port.arrays or port.packed_dimensions:
        return "'0"
    return zero_value(port.width, parameter_map)


def port_uses_parameter_width(port: Port) -> bool:
    """Whether V2 delegates this port's width compatibility to elaboration."""
    return any(
        width.kind == "parameter"
        for width in (*port.packed_dimensions, port.width, *port.arrays)
    )


def resolved_width_value(width: Width, macros: dict[str, str]) -> int | None:
    if width.kind == "parameter":
        return None
    expression = width.expression
    if width.kind == "macro":
        expression = macros.get(expression.lstrip("`"), width.default)
    return evaluate_int_expression(expression)


def simple_packed_width(port: Port, macros: dict[str, str]) -> int | None:
    """Return a width suitable for a low-bit adapter, or ``None`` if unsafe."""
    if (
        port.is_interface
        or port.arrays
        or port.packed_dimensions
        or port_uses_parameter_width(port)
    ):
        return None
    return resolved_width_value(port.width, macros)


def comparable_port_shape(
    port: Port, macros: dict[str, str]
) -> tuple[object, ...] | None:
    """Resolve literal/macro dimensions while exempting parameter dimensions."""
    if port_uses_parameter_width(port):
        return None
    if port.interface_type:
        base_type = port.interface_type.rsplit(".", 1)[0]
        values = tuple(resolved_width_value(item, macros) for item in port.arrays)
        return (f"interface:{base_type}", *values)
    dimensions = (*port.arrays, *port.packed_dimensions, port.width)
    values = tuple(resolved_width_value(item, macros) for item in dimensions)
    return values if all(value is not None for value in values) else port.shape


def low_bits(expression: str, width: int) -> str:
    return f"{expression}[0]" if width == 1 else f"{expression}[{width} -1:0]"


def fit_source_width(expression: str, source_width: int, target_width: int) -> str:
    """Resize an rvalue using low-bit truncation or zero extension."""
    if source_width == target_width:
        return expression
    if source_width > target_width:
        return low_bits(expression, target_width)
    return f"{{{{{target_width - source_width}{{1'b0}}}}, {expression}}}"


def append_zero_assignment(
    lines: list[str],
    port: Port,
    parameter_map: dict[str, str] | None = None,
    indent: str = "",
    target_width: int = 0,
) -> None:
    """Append one assignment; every ordinary array is emitted as packed."""
    value = (
        "'0"
        if port.arrays or port.packed_dimensions
        else zero_value(port.width, parameter_map)
    )
    target = f"{port.name:<{target_width}}" if target_width else port.name
    lines.append(f"{indent}assign {target} = {value};")


def append_conditioned_zero_assignment(
    lines: list[str],
    port: Port,
    parameter_map: dict[str, str] | None = None,
    indent: str = "",
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
    indent: str = "",
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
    # Macro defaults are documentation only.  Real projects commonly provide
    # the same names globally, so active definitions here would redefine them.
    lines = [
        f"// `define {name:<{name_width}} {value}" for name, value in macros.items()
    ]
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
    ranges = port_packed_dimension_ranges(port)
    packed = align_packed_dimensions(ranges, dimension_widths)
    packed_field = f" {packed}" if dimension_widths else ""
    if port.direction == "inout":
        return f"inout{packed_field}"
    return f"{port.direction:<{direction_width}} wire{packed_field}"


def render_file_header() -> list[str]:
    """Return the configurable, optionally preserved generated-file header."""
    return [
        "/*USER CODE BEGIN file header*/",
        *VERILOG_FILE_HEADER.splitlines(),
        "/*USER CODE END   file header*/",
        "",
        "// Generated by xlsx2verilog.py. Do not edit outside USER CODE regions.",
    ]


def render_module_header(module: Module, macros: dict[str, str] | None = None) -> list[str]:
    lines = render_file_header()
    lines.extend(render_macros(macros if macros is not None else module.macros))
    if module.parameters:
        lines.append(f"module {module.name} #(")
        parameter_items = list(module.parameters.items())
        parameter_name_width = max(len(name) for name, _ in parameter_items)
        for index, (name, value) in enumerate(parameter_items):
            comma = "," if index < len(parameter_items) - 1 else ""
            keyword = (
                "parameter"
                if name in module.externally_configurable_parameters
                else "localparam"
            )
            rendered_value = module.parameter_expressions.get(name, value)
            comment_value = module.parameter_comments.get(name)
            comment = f"  // {comment_value}" if comment_value else ""
            lines.append(
                f"    {keyword:<10} {name:<{parameter_name_width}} = "
                f"{rendered_value}{comma}{comment}"
            )
        lines.append(") (")
    else:
        lines.append(f"module {module.name} (")
    regular_ports = [port for port in module.ports if not port.is_interface]
    direction_width = max((len(port.direction) for port in regular_ports), default=0)
    packed_ranges_by_port = [
        port_packed_dimension_ranges(port)
        for port in regular_ports
    ]
    dimension_widths = packed_dimension_widths(packed_ranges_by_port)
    prefixes = {
        id(port): port_declaration_prefix(port, direction_width, dimension_widths)
        for port in module.ports
    }
    prefix_width = max((len(prefix) for prefix in prefixes.values()), default=0)
    port_name_width = max((len(port.name) for port in module.ports), default=0)
    array_fields = {
        id(port): array_ranges(port.arrays) if port.is_interface else ""
        for port in module.ports
    }
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


def user_code_block(label: str) -> list[str]:
    """Return one stable, intentionally empty user-editable Verilog region."""
    return [
        f"/*USER CODE BEGIN {label}*/",
        "",
        f"/*USER CODE END   {label}*/",
    ]


def render_stub(module: Module, macros: dict[str, str] | None = None) -> str:
    lines = render_module_header(module, macros)
    lines.extend(["", *user_code_block("before statement")])
    output_ports = [port for port in module.ports if port.direction == "output"]
    if output_ports:
        lines.append("")
        lines.append("// Module placeholder: drive every output to zero.")
        lines.append("// 模块占位逻辑：所有输出均置零。")
        target_width = max((len(port.name) for port in output_ports), default=0)
        for port in output_ports:
            append_conditioned_zero_assignment(
                lines, port, target_width=target_width
            )
    lines.extend(["endmodule", ""])
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
    requires_todo: bool = False


@dataclass(frozen=True)
class Assignment:
    target: str
    expression: str
    conditions: tuple[str, ...] = ()


@dataclass
class GenerateSpec:
    index: str
    extents: list[int] = field(default_factory=list)


def split_index_marker(reference: Any) -> tuple[str, str | None]:
    """Return an integration reference without its trailing ``[i]`` marker."""
    text = clean(reference)
    match = INDEX_MARKER_RE.fullmatch(text)
    if match is None:
        return text, None
    return match.group(1).strip(), match.group(2)


def split_bit_select(reference: Any) -> tuple[str, str | None, int | None]:
    """Split a constant bit select such as ``n_rst[0]`` from a port name."""
    text = clean(reference)
    match = BIT_SELECT_RE.fullmatch(text)
    if match is None:
        return text, None, None
    index = int(match.group(2))
    return match.group(1).strip(), f"[{index}]", index


def parse_na_connection(value: Any) -> tuple[str | None, str | None] | None:
    """Return ``(index, target)`` for NA, NA[i], or NA->target."""
    match = NA_REFERENCE_RE.fullmatch(clean(value))
    if match is None:
        return None
    target = clean(match.group(2)) or None
    return match.group(1), target


def is_verilog_constant(value: str) -> bool:
    return VERILOG_CONSTANT_RE.fullmatch(clean(value)) is not None


def aligned_binding_expressions(
    ordered: list[tuple[Port, Binding]],
) -> list[str]:
    """Align trailing generate indices separately from signal expressions."""
    parts: list[tuple[str, str]] = []
    has_generate_index = False
    for _, binding in ordered:
        expression = binding.expression or ""
        match = INDEX_MARKER_RE.fullmatch(expression)
        if match is None:
            parts.append((expression, ""))
            continue
        base = match.group(1).rstrip()
        suffix = f"[{match.group(2)}]"
        parts.append((base, suffix))
        has_generate_index = True
    if not has_generate_index:
        width = max((len(base) for base, _ in parts), default=0)
        return [f"{base:<{width}}" for base, _ in parts]
    base_width = max((len(base) for base, _ in parts), default=0)
    suffix_width = max((len(suffix) for _, suffix in parts), default=0)
    return [
        f"{base:<{base_width}}{suffix:<{suffix_width}}"
        for base, suffix in parts
    ]


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
    rendered_expressions = aligned_binding_expressions(ordered)
    if not any(binding.conditions for _, binding in ordered):
        for index, ((port, binding), rendered) in enumerate(
            zip(ordered, rendered_expressions)
        ):
            comma = "," if index < len(ordered) - 1 else ""
            todo = f" {NA_CONNECTION_TODO}" if binding.requires_todo else ""
            lines.append(
                f"    .{port.name:<{port_name_width}} "
                f"({rendered}){comma}{todo}"
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
    for (port, binding), rendered in zip(ordered, rendered_expressions):
        for condition in binding.conditions:
            lines.append(f"`ifdef {condition}")
        lines.extend(
            [
                f"`ifdef {marker}",
                "    ,",
                "`else",
                f"`define {marker}",
                "`endif",
            ]
        )
        todo = f" {NA_CONNECTION_TODO}" if binding.requires_todo else ""
        lines.append(
            f"    .{port.name:<{port_name_width}} "
            f"({rendered}){todo}"
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
    all_macro_sources: dict[str, str] = {}
    for module in [top, *children]:
        for name, value in module.macros.items():
            previous = all_macros.setdefault(name, value)
            previous_sheet = all_macro_sources.setdefault(name, module.sheet_name)
            if previous != value:
                reporter.error(
                    f"集成模块: 宏 `{name} 默认值冲突："
                    f"页签 {previous_sheet} 为 {previous}；"
                    f"页签 {module.sheet_name} 为 {value}；"
                    "上下层同名宏必须使用相同数值",
                    code="E_WIDTH",
                )

    # A parameter is local by default.  Only rows explicitly placed under a
    # ``parameter`` category in the integration sheet create an override path.
    parameter_rows_by_group = integration_parameter_rows(sheet, integration)
    # parameter_maps are used when a child-owned dimension must be expressed
    # in TOP scope.  Unlinked local parameters resolve to their numeric match
    # values; linked parameters resolve to a TOP localparam name.
    parameter_maps: dict[str, dict[str, str]] = {
        top.name: {name: name for name in top.parameters}
    }
    instance_parameter_maps: dict[str, dict[str, str]] = {}
    for child in children:
        parameter_maps[child.name] = dict(child.parameters)
        instance_parameter_maps[child.name] = {}

    used_top_parameter_names = set(top.parameters)

    def add_top_local_parameter(
        base_name: str,
        source_module: Module,
        source_name: str,
        row: int,
    ) -> str:
        value = source_module.parameters[source_name]
        candidate = base_name
        if candidate in top.parameters and top.parameters[candidate] != value:
            candidate = unique_name(
                f"LOCAL_{source_module.name}_{source_name}", used_top_parameter_names
            ).upper()
        else:
            used_top_parameter_names.add(candidate)
        if candidate not in top.parameters:
            top.parameters[candidate] = value
            expression = source_module.parameter_expressions.get(source_name)
            if expression:
                top.parameter_expressions[candidate] = expression
                top.parameter_comments[candidate] = source_module.parameter_comments.get(
                    source_name, value
                )
            parameter_maps[top.name][candidate] = candidate
            reporter.info(
                f"集成页签 {sheet.name} 第 {row} 行: 自动在 TOP 创建 "
                f"localparam {candidate}，用于显式 parameter 链接",
                code="I_PARAMETER_LINK",
            )
        return candidate

    for group_index, group in enumerate(integration.groups):
        for row in sorted(parameter_rows_by_group[group_index]):
            entries: list[tuple[IntegrationBlock, Module, str]] = []
            for block in group:
                if block.anonymous_na:
                    continue
                reference = clean(sheet.cell(row, block.port_column)).upper()
                if not reference:
                    continue
                module = modules.get(block.module_name)
                if module is None:
                    continue
                if reference not in module.parameters:
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: "
                        f"{block.module_name} 没有 parameter {reference}",
                        code="E_PARAMETER",
                    )
                    continue
                entries.append((block, module, reference))
            if len(entries) < 2:
                if entries:
                    block, _, name = entries[0]
                    reporter.info(
                        f"集成页签 {sheet.name} 第 {row} 行: parameter "
                        f"{block.module_name}.{name} 没有链接对端，保持 local",
                        code="I_PARAMETER_LINK",
                    )
                continue

            top_entry = next(
                (entry for entry in entries if entry[0].module_name == top.name), None
            )
            if top_entry is not None:
                _, source_module, source_name = top_entry
                local_name = source_name
                local_value = source_module.parameters[source_name]
            else:
                _, source_module, source_name = entries[0]
                local_name = add_top_local_parameter(
                    source_name, source_module, source_name, row
                )
                local_value = top.parameters[local_name]

            for block, module, name in entries:
                if module.name == top.name:
                    continue
                instance_parameter_maps[module.name][name] = local_name
                parameter_maps[module.name][name] = local_name
                module.externally_configurable_parameters.add(name)
                if module.parameters[name] != local_value:
                    reporter.info(
                        f"集成页签 {sheet.name} 第 {row} 行: {module.name}.{name} "
                        f"匹配值 {module.parameters[name]} 由 TOP localparam "
                        f"{local_name}={local_value} 覆盖",
                        code="I_PARAMETER_LINK",
                    )

    bindings: dict[str, dict[str, Binding]] = {child.name: {} for child in children}
    generate_specs: dict[str, GenerateSpec] = {}
    top_output_drivers: dict[str, list[str]] = {}
    top_output_driver_conditions: dict[str, list[str | None]] = {}
    wires: list[Wire] = []
    adapter_assignments: list[Assignment] = []
    used_signals = set(top_ports)
    instance_names: dict[str, str] = {}
    used_instance_names: set[str] = set()
    generated_indices: dict[str, str] = {}
    used_generated_indices: set[str] = set()
    for child in children:
        configured = integration.instance_specs.get(child.name)
        instance_name = (
            configured.instance_name
            if configured and configured.instance_name
            else f"U_{child.name}"
        )
        if instance_name in used_instance_names:
            reporter.error(
                f"集成页签 {sheet.name}: 例化名 {instance_name} 被多个模块重复使用",
                code="E_INSTANCE",
            )
        used_instance_names.add(instance_name)
        instance_names[child.name] = instance_name
        generated_indices[child.name] = unique_name(
            f"i_gen_{instance_name}", used_generated_indices
        ).lower()

    def indexed_dimension(port: Port) -> Width | None:
        dimensions = (
            (*port.arrays, *port.packed_dimensions, port.width)
            if not port.is_interface
            else port.arrays
        )
        return dimensions[0] if dimensions else None

    def register_generate_marker(
        child_name: str,
        index: str,
        indexed_port: Port,
        row: int,
    ) -> int | None:
        spec = generate_specs.setdefault(child_name, GenerateSpec(index))
        if spec.index != index:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {child_name} 同时使用了 "
                f"[{spec.index}] 和 [{index}]，无法生成同一个循环",
                code="E_GENERATE_INDEX",
            )
            return None
        first = indexed_dimension(indexed_port)
        if first is None:
            return None
        extent = evaluate_int_expression(first.default or first.expression)
        if extent is not None:
            spec.extents.append(extent)
        return extent

    def add_assignment(
        target: str,
        expression: str,
        *conditions: str | None,
    ) -> None:
        assignment = Assignment(
            target,
            expression,
            tuple(dict.fromkeys(item for item in conditions if item is not None)),
        )
        if assignment not in adapter_assignments:
            adapter_assignments.append(assignment)

    def shapes_match(left: Port, right: Port) -> bool:
        left_shape = comparable_port_shape(left, all_macros)
        right_shape = comparable_port_shape(right, all_macros)
        return left_shape is None or right_shape is None or left_shape == right_shape

    def get_port(module_name: str, port_name: str, row: int) -> Port | None:
        module = modules.get(module_name)
        if module is None:
            return None
        port = module.port_map.get(port_name)
        if port is None:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {module_name} 没有端口 {port_name}",
                code="E_PORT_REFERENCE",
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
        ports = [
            port
            for port in module.ports
            if port.template_source == reference
        ]
        if not ports:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {module_name} 没有与模板端口 {reference} 匹配的展开端口",
                code="E_PORT_REFERENCE",
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
        template_value_sets = {
            frozenset(port.template_values for port in ports)
            for _, ports in templated
        }
        if len(template_counts) > 1 or len(template_value_sets) > 1:
            details = "; ".join(
                f"{block.module_name}={len(ports)}[{', '.join(port.name for port in ports)}]"
                for block, ports in expanded
            )
            reporter.info(
                f"集成页签 {sheet.name} 第 {row} 行: 模板端口展开不一致，"
                f"按展开值连接已有端点，单端项按未连接处理 ({details})",
                code="I_TEMPLATE_PARTIAL",
            )

        ordered_values: list[tuple[str, ...]] = []
        for _, ports in templated:
            for port in ports:
                if port.template_values not in ordered_values:
                    ordered_values.append(port.template_values)
        if not ordered_values:
            ordered_values = [()]
        result: list[list[tuple[IntegrationBlock, Port]]] = []
        for expansion_values in ordered_values:
            row_items: list[tuple[IntegrationBlock, Port]] = []
            for block, ports in expanded:
                if not ports:
                    continue
                if ports[0].template_source is None:
                    port = ports[0]
                else:
                    port = next(
                        (
                            item
                            for item in ports
                            if item.template_values == expansion_values
                        ),
                        None,
                    )
                    if port is None:
                        continue
                row_items.append((block, port))
            result.append(row_items)
        return result

    def validate_sheet_direction(block: IntegrationBlock, port: Port, row: int) -> None:
        raw_direction = sheet.cell(row, block.direction_column)
        listed_direction = normalized_direction(raw_direction)
        if listed_direction is None:
            if not clean(raw_direction):
                reporter.info(
                    f"集成页签 {sheet.name} 第 {row} 行: "
                    f"{block.module_name}.{port.name} 的 i/o 为空，"
                    f"按模块定义 {port.direction} 校验",
                    code="I_DIRECTION_INFERRED",
                )
                return
            else:
                reporter.warning(
                    f"集成页签 {sheet.name} 第 {row} 行: {block.module_name}.{port.name} 的 i/o 值 {raw_direction!r} 无法识别",
                    code="W_IO_DEFAULTED",
                )
                return
        if listed_direction != port.direction and port.direction_inferred:
            reporter.warning(
                f"集成页签 {sheet.name} 第 {row} 行: {block.module_name}.{port.name} "
                f"由空 i/o 推断为 inout，与集成页签的 {listed_direction} 不同；请人工确认",
                code="W_IO_DEFAULTED",
            )
        elif listed_direction != port.direction:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {block.module_name}.{port.name} 的方向与模块定义不一致 ({listed_direction}/{port.direction})",
                code="E_DIRECTION",
            )

    def bind(
        module_name: str,
        port: Port,
        expression: str | None,
        row: int,
        extra_conditions: tuple[str | None, ...] = (),
        *,
        requires_todo: bool = False,
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
        binding = Binding(expression, conditions, requires_todo)
        if port.name in target and target[port.name] != binding:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {module_name}.{port.name} 被重复连接",
                code="E_PORT_REFERENCE",
            )
            return
        target[port.name] = binding

    def bind_na_placeholder(
        block: IntegrationBlock,
        port: Port,
        row: int,
        index: str | None = None,
        target: str | None = None,
    ) -> None:
        if block.module_name == top.name:
            reporter.info(
                f"集成页签 {sheet.name} 第 {row} 行: TOP 端口 "
                f"{top.name}.{port.name} 不能通过 NA 创建内部占位信号",
                code="I_NA_CONNECTION",
            )
            return
        if index and port.is_interface:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: interface "
                f"{block.module_name}.{port.name} 不支持 NA[{index}] generate",
                code="E_INTERFACE_CONNECTION",
            )
            return
        if target and is_verilog_constant(target):
            if index:
                register_generate_marker(block.module_name, index, port, row)
            expression = target if port.direction == "input" else None
            bind(block.module_name, port, expression, row)
            reporter.info(
                f"集成页签 {sheet.name} 第 {row} 行: "
                f"{block.module_name}.{port.name} 连接到常量 {target}"
                if expression is not None
                else f"集成页签 {sheet.name} 第 {row} 行: "
                f"{block.module_name}.{port.name} 是输出端，NA->{target} 按开路处理",
                code="I_NA_CONNECTION",
            )
            return
        if target:
            if not IDENTIFIER_RE.fullmatch(target):
                reporter.error(
                    f"集成页签 {sheet.name} 第 {row} 行: NA 自定义名称 "
                    f"{target!r} 不是合法 Verilog 标识符或受支持常量",
                    code="E_NA_TARGET",
                )
                return
            if target in used_signals:
                reporter.error(
                    f"集成页签 {sheet.name} 第 {row} 行: NA 自定义名称 "
                    f"{target} 与已有信号重名",
                    code="E_NA_TARGET",
                )
                return
            signal_name = target
            used_signals.add(signal_name)
        else:
            signal_name = unique_name(port.name, used_signals)
        placeholder_arrays = port.arrays
        expression = signal_name
        if index:
            extent = register_generate_marker(block.module_name, index, port, row)
            configured = integration.instance_specs.get(block.module_name)
            count = (configured.count if configured else None) or extent or 1
            placeholder_arrays = (
                Width("literal", str(count), str(count)),
                *placeholder_arrays,
            )
            expression += f"[{generated_indices[block.module_name]}]"
        wires.append(
            Wire(
                name=signal_name,
                width=port.width,
                arrays=placeholder_arrays,
                parameter_map=parameter_maps.get(block.module_name, {}),
                interface_type=(
                    port.interface_type.rsplit(".", 1)[0]
                    if port.interface_type
                    else None
                ),
                packed_dimensions=port.packed_dimensions,
            )
        )
        bind(
            block.module_name,
            port,
            expression,
            row,
            requires_todo=True,
        )
        na_label = f"NA[{index}]" if index else "NA"
        if target:
            na_label += f"->{target}"
        reporter.info(
            f"集成页签 {sheet.name} 第 {row} 行: "
            f"{block.module_name}.{port.name} 连接到 {na_label}，"
            f"已创建 {signal_name} 占位信号并加入 TODO",
            code="I_NA_CONNECTION",
        )

    first_group = integration.groups[0]
    top_block = first_group[0]
    first_group_child_blocks = [
        block for block in first_group[1:] if not block.anonymous_na
    ]
    first_group_na_blocks = [block for block in first_group if block.anonymous_na]
    for row in range(integration.header_row + 1, sheet.max_row + 1):
        if row in parameter_rows_by_group[0]:
            continue
        top_reference = sheet.cell(row, top_block.port_column)
        top_port_name, top_bit_select, top_bit_index = split_bit_select(
            top_reference
        )
        top_index: str | None = None
        if top_bit_select is None:
            top_port_name, top_index = split_index_marker(top_reference)
        row_entries: list[tuple[IntegrationBlock, str, str | None]] = []
        row_na_entries: list[tuple[IntegrationBlock, str | None, str | None]] = []
        for block in first_group_child_blocks:
            reference = clean(sheet.cell(row, block.port_column))
            if not reference:
                continue
            na_reference = parse_na_connection(reference)
            if na_reference is not None:
                index, target = na_reference
                row_na_entries.append((block, index, target))
            else:
                port_name, index = split_index_marker(reference)
                row_entries.append((block, port_name, index))
        for block in first_group_na_blocks:
            reference = clean(sheet.cell(row, block.port_column))
            if not reference:
                continue
            parsed_na = parse_na_connection(reference)
            if parsed_na is not None:
                row_na_entries.append((block, *parsed_na))
        row_na_indices = {
            index for _, index, _ in row_na_entries if index is not None
        }
        row_na_targets = {
            target for _, _, target in row_na_entries if target is not None
        }
        if len(row_na_targets) > 1:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: NA 使用了多个目标 "
                f"({', '.join(sorted(row_na_targets))})",
                code="E_NA_TARGET",
            )
            continue
        row_na_target = next(iter(row_na_targets), None)
        if not top_port_name and not row_entries and not row_na_entries:
            continue
        if not top_port_name:
            if not row_entries:
                reporter.info(
                    f"集成页签 {sheet.name} 第 {row} 行: NA 没有可拉出的模块端口",
                    code="I_NA_CONNECTION",
                )
                continue
            if len(row_na_indices) > 1:
                reporter.error(
                    f"集成页签 {sheet.name} 第 {row} 行: NA 使用了多个 "
                    f"generate 索引 ({', '.join(sorted(row_na_indices, key=str))})",
                    code="E_GENERATE_INDEX",
                )
                continue
            reporter.info(
                f"集成页签 {sheet.name} 第 {row} 行: TOP 端口为空，子模块端口按未连接处理",
                code="I_UNCONNECTED",
            )
            for block, child_port_name, _ in row_entries:
                for child_port in get_ports(block.module_name, child_port_name, row):
                    validate_sheet_direction(block, child_port, row)
                    if row_na_entries:
                        bind_na_placeholder(
                            block,
                            child_port,
                            row,
                            next(iter(row_na_indices), None),
                            next(iter(row_na_targets), None),
                        )
                        continue
                    expression = (
                        connection_zero_value(
                            child_port, parameter_maps.get(block.module_name)
                        )
                        if child_port.direction == "input"
                        else None
                    )
                    bind(block.module_name, child_port, expression, row)
            continue
        if row_na_entries:
            na_labels = [
                (f"NA[{index}]" if index else "NA")
                + (f"->{target}" if target else "")
                for _, index, target in row_na_entries
            ]
            reporter.info(
                f"集成页签 {sheet.name} 第 {row} 行: TOP 端口 "
                f"{top.name}.{top_port_name} 连接到 {', '.join(na_labels)}，"
                "保留为顶层观察端口，不查询 NA 所在列的模块端口",
                code="I_NA_CONNECTION",
            )
        expanded = [(top_block, get_ports(top.name, top_port_name, row))]
        expanded.extend(
            (block, get_ports(block.module_name, child_port_name, row))
            for block, child_port_name, _ in row_entries
        )
        row_markers = {top_block.module_name: top_index}
        row_markers.update(
            {block.module_name: marker for block, _, marker in row_entries}
        )
        for aligned in aligned_expansions(expanded, row):
            if not aligned:
                continue
            if aligned[0][0] != top_block:
                for block, child_port in aligned:
                    reporter.info(
                        f"集成页签 {sheet.name} 第 {row} 行: 模板展开项 "
                        f"{block.module_name}.{child_port.name} 无 TOP 对端，按未连接处理",
                        code="I_UNCONNECTED",
                    )
                    bind(
                        block.module_name,
                        child_port,
                        connection_zero_value(
                            child_port, parameter_maps.get(block.module_name)
                        )
                        if child_port.direction == "input"
                        else None,
                        row,
                    )
                continue
            _, top_port = aligned[0]
            validate_sheet_direction(top_block, top_port, row)
            if top_bit_select is not None:
                if top_port.is_interface or top_port.arrays or top_port.packed_dimensions:
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: "
                        f"{top.name}.{top_port.name}{top_bit_select} 仅支持单维 packed 端口",
                        code="E_BIT_SELECT",
                    )
                resolved_top_width = simple_packed_width(top_port, all_macros)
                if (
                    resolved_top_width is not None
                    and top_bit_index is not None
                    and top_bit_index >= resolved_top_width
                ):
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: "
                        f"{top.name}.{top_port.name}{top_bit_select} 超出位宽 "
                        f"{resolved_top_width}",
                        code="E_BIT_SELECT",
                    )
            if row_na_target is not None:
                if not is_verilog_constant(row_na_target):
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: TOP 连接区的 "
                        f"NA->{row_na_target} 仅支持常量；自定义线名请用于子模块互连区",
                        code="E_NA_TARGET",
                    )
                    continue
                if top_port.direction != "output":
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: 只有 TOP output "
                        f"可以由 NA->{row_na_target} 赋值，{top.name}.{top_port.name} "
                        f"为 {top_port.direction}",
                        code="E_NA_TARGET",
                    )
                    continue
                add_assignment(top_port.name, row_na_target, top_port.condition)
                top_output_driver_conditions.setdefault(top_port.name, []).append(None)
                reporter.info(
                    f"集成页签 {sheet.name} 第 {row} 行: TOP 输出 "
                    f"{top.name}.{top_port.name} 已由 NA->{row_na_target} 赋值",
                    code="I_NA_CONNECTION",
                )
                for block, child_port in aligned[1:]:
                    validate_sheet_direction(block, child_port, row)
                    expression = (
                        row_na_target if child_port.direction == "input" else None
                    )
                    bind(block.module_name, child_port, expression, row)
                    if expression is None:
                        reporter.info(
                            f"集成页签 {sheet.name} 第 {row} 行: "
                            f"{block.module_name}.{child_port.name} 为输出端，"
                            f"在 NA->{row_na_target} 常量网络中按开路处理",
                            code="I_NA_CONNECTION",
                        )
                continue
            for block, child_port in aligned[1:]:
                validate_sheet_direction(block, child_port, row)
                if top_port.direction == "input" and child_port.direction == "output":
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: TOP 输入 {top_port.name} 与子模块输出 {block.module_name}.{child_port.name} 方向冲突",
                        code="E_DIRECTION",
                    )
                # A TOP output may intentionally fan out to child inputs. If
                # no child output/inout drives it, the undriven-output pass
                # below creates the TOP signal, ties it to zero, and the same
                # signal then drives every connected child input.
                mismatch = not shapes_match(top_port, child_port)
                child_width = simple_packed_width(child_port, all_macros)
                if top_bit_select is not None:
                    # An explicit selection is an intentional user-authored
                    # connection expression.  Do not replace it with an
                    # automatic zero extension or low-bit adapter.
                    mismatch = False
                if mismatch:
                    reporter.warning(
                        f"{top.name}.{top_port.name}信号和"
                        f"{block.module_name}.{child_port.name}信号应该连接，"
                        "但是其位宽不匹配",
                        code="W_WIDTH_MISMATCH",
                    )
                child_index = row_markers.get(block.module_name)
                marker = top_index or child_index
                if top_index and child_index and top_index != child_index:
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: {top.name}.{top_port.name} "
                        f"与 {block.module_name}.{child_port.name} 使用不同索引指示符",
                        code="E_GENERATE_INDEX",
                    )
                if marker:
                    register_generate_marker(
                        block.module_name,
                        marker,
                        top_port if top_index else child_port,
                        row,
                    )
                expression = top_port.name
                if top_bit_select is not None:
                    expression += top_bit_select
                elif top_index and marker:
                    expression += f"[{generated_indices[block.module_name]}]"
                top_width = (
                    1
                    if top_bit_select is not None
                    else simple_packed_width(top_port, all_macros)
                )
                if (
                    mismatch
                    and top_width is not None
                    and child_width is not None
                    and child_port.direction == "input"
                ):
                    expression = fit_source_width(
                        expression, top_width, child_width
                    )
                elif (
                    mismatch
                    and top_width is not None
                    and child_width is not None
                    and child_port.direction == "output"
                    and top_port.direction in {"output", "inout"}
                ):
                    if child_width < top_width:
                        expression = low_bits(top_port.name, child_width)
                        add_assignment(
                            f"{top_port.name}[{top_width}-1:{child_width}]",
                            "'0",
                            top_port.condition,
                            child_port.condition,
                        )
                    elif child_width > top_width:
                        adapter_name = unique_name(
                            f"w_{block.module_name}_{child_port.name}_adapter",
                            used_signals,
                        )
                        wires.append(
                            Wire(
                                adapter_name,
                                Width("literal", str(child_width), str(child_width)),
                                (),
                                {},
                            )
                        )
                        expression = adapter_name
                        add_assignment(
                            top_port.name,
                            low_bits(adapter_name, top_width),
                            top_port.condition,
                            child_port.condition,
                        )
                bind(
                    block.module_name,
                    child_port,
                    expression,
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
                            f"({', '.join(drivers)})",
                            code="W_DRIVER_RISK",
                        )

    for group_index, group in enumerate(integration.groups[1:], start=1):
        if len(group) == 1:
            block = group[0]
            for row in range(integration.header_row + 1, sheet.max_row + 1):
                if row in parameter_rows_by_group[group_index]:
                    continue
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
            if row in parameter_rows_by_group[group_index]:
                continue
            expanded: list[tuple[IntegrationBlock, list[Port]]] = []
            na_blocks: list[
                tuple[IntegrationBlock, str | None, str | None]
            ] = []
            for block in group:
                reference = clean(sheet.cell(row, block.port_column))
                if not reference:
                    continue
                na_reference = parse_na_connection(reference)
                if na_reference is not None:
                    index, target = na_reference
                    na_blocks.append((block, index, target))
                    continue
                port_name, _ = split_index_marker(reference)
                ports = get_ports(block.module_name, reference, row)
                for port in ports:
                    validate_sheet_direction(block, port, row)
                expanded.append((block, ports))
            if na_blocks and not expanded:
                reporter.info(
                    f"集成页签 {sheet.name} 第 {row} 行: 内部连接全部为 NA，"
                    "没有可拉出的模块端口",
                    code="I_NA_CONNECTION",
                )
                continue
            for entries in aligned_expansions(expanded, row):
                if not entries:
                    continue
                if len(entries) == 1:
                    block, port = entries[0]
                    if na_blocks:
                        indices = {
                            index for _, index, _ in na_blocks if index is not None
                        }
                        targets = {
                            target for _, _, target in na_blocks if target is not None
                        }
                        if len(indices) > 1:
                            reporter.error(
                                f"集成页签 {sheet.name} 第 {row} 行: NA 使用了多个 "
                                f"generate 索引 ({', '.join(sorted(indices))})",
                                code="E_GENERATE_INDEX",
                            )
                            continue
                        if len(targets) > 1:
                            reporter.error(
                                f"集成页签 {sheet.name} 第 {row} 行: NA 使用了多个 "
                                f"目标 ({', '.join(sorted(targets))})",
                                code="E_NA_TARGET",
                            )
                            continue
                        bind_na_placeholder(
                            block,
                            port,
                            row,
                            next(iter(indices), None),
                            next(iter(targets), None),
                        )
                        continue
                    reporter.info(
                        f"集成页签 {sheet.name} 第 {row} 行: 内部连接只有 {block.module_name}.{port.name} 一端，按未连接处理",
                        code="I_UNCONNECTED",
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
                        f"集成页签 {sheet.name} 第 {row} 行: interface 不能与普通端口直接互连",
                        code="E_INTERFACE_CONNECTION",
                    )
                    continue
                if all(interface_flags):
                    width_source = entries[0]
                    drivers: list[tuple[IntegrationBlock, Port]] = []
                else:
                    drivers = [
                        (block, port)
                        for block, port in entries
                        if port.direction == "output"
                    ]
                    if len(drivers) == 0:
                        reporter.warning(
                            f"集成页签 {sheet.name} 第 {row} 行: 内部连接没有 output 驱动端",
                            code="W_DRIVER_RISK",
                        )
                        width_source = entries[0]
                    elif len(drivers) > 1:
                        names = ", ".join(
                            f"{block.module_name}.{port.name}" for block, port in drivers
                        )
                        reporter.error(
                            f"集成页签 {sheet.name} 第 {row} 行: 内部连接存在多个驱动端 ({names})",
                            code="E_DRIVER_CONFLICT",
                        )
                        width_source = drivers[0]
                    else:
                        width_source = drivers[0]
                block, source_port = width_source
                source_block, source_port_for_warning = width_source
                for item_block, port in entries:
                    if (item_block, port) == width_source or shapes_match(
                        source_port_for_warning, port
                    ):
                        continue
                    reporter.warning(
                        f"{source_block.module_name}.{source_port_for_warning.name}"
                        f"信号和{item_block.module_name}.{port.name}信号应该连接，"
                        "但是其位宽不匹配",
                        code="W_WIDTH_MISMATCH",
                    )
                signal_base = source_port.name
                signal_name = unique_name(f"w_{signal_base}", used_signals)
                numeric_widths = [
                    simple_packed_width(port, all_macros) for _, port in entries
                ]
                can_resize = all(width is not None for width in numeric_widths)
                maximum_width = (
                    max(width for width in numeric_widths if width is not None)
                    if can_resize
                    else None
                )
                wire_width = (
                    Width("literal", str(maximum_width), str(maximum_width))
                    if maximum_width is not None
                    else source_port.width
                )
                wires.append(
                    Wire(
                        name=signal_name,
                        width=wire_width,
                        arrays=source_port.arrays,
                        parameter_map=(
                            {}
                            if maximum_width is not None
                            else parameter_maps.get(block.module_name, {})
                        ),
                        interface_type=(
                            source_port.interface_type.rsplit(".", 1)[0]
                            if source_port.interface_type
                            else None
                        ),
                        packed_dimensions=source_port.packed_dimensions,
                    )
                )
                for entry_index, (item_block, port) in enumerate(entries):
                    if item_block.module_name == top.name:
                        if port.direction == "output":
                            top_output_driver_conditions.setdefault(
                                port.name, []
                            ).append(source_port.condition)
                        continue
                    expression = signal_name
                    item_width = numeric_widths[entry_index]
                    if (
                        maximum_width is not None
                        and item_width is not None
                        and item_width < maximum_width
                    ):
                        expression = low_bits(signal_name, item_width)
                    bind(item_block.module_name, port, expression, row)

                if not all(interface_flags):
                    if len(drivers) == 0:
                        add_assignment(signal_name, "'0")
                    elif maximum_width is not None and len(drivers) == 1:
                        driver_block, driver_port = drivers[0]
                        driver_index = entries.index((driver_block, driver_port))
                        driver_width = numeric_widths[driver_index]
                        if driver_width is not None and driver_width < maximum_width:
                            add_assignment(
                                f"{signal_name}[{maximum_width}-1:{driver_width}]",
                                "'0",
                                driver_port.condition,
                            )

    # Named-port instantiations may omit ports, but explicitly tie/open all omitted
    # child ports to make the generated integration deterministic and lint-friendly.
    for child in children:
        for port in child.ports:
            if port.name not in bindings[child.name]:
                reporter.info(
                    f"集成页签 {sheet.name}: 未列出 {child.name}.{port.name}，自动按未连接端口处理",
                    code="I_UNCONNECTED",
                )
                bindings[child.name][port.name] = Binding(
                    (
                        connection_zero_value(port, parameter_maps[child.name])
                        if port.direction == "input"
                        else None
                    ),
                    (port.condition,) if port.condition else (),
                )

    generate_counts: dict[str, int] = {}
    for child in children:
        child_name = child.name
        marker_spec = generate_specs.get(child_name)
        configured = integration.instance_specs.get(child_name)
        configured_count = configured.count if configured else None
        if configured_count is not None:
            count = configured_count
            generate_counts[child_name] = count
            reporter.info(
                f"集成模块: {child_name} 使用显式例化次数 {count}",
                code="I_INSTANCE",
            )
            if marker_spec and marker_spec.extents:
                safe_extent = min(marker_spec.extents)
                if count > safe_extent:
                    reporter.warning(
                        f"集成模块: {child_name} 的例化次数 {count} 超过 "
                        f"[{marker_spec.index}] 可解析安全范围 {safe_extent}，存在索引越界风险",
                        code="W_GENERATE_RANGE",
                    )
            continue
        if marker_spec is None:
            continue
        if marker_spec.extents:
            count = min(marker_spec.extents)
            detail = "/".join(
                str(value) for value in dict.fromkeys(marker_spec.extents)
            )
            reporter.info(
                f"集成模块: {child_name} 的 [{marker_spec.index}] 可解析循环范围为 "
                f"{detail}，generate 使用安全次数 {count}",
                code="I_INSTANCE",
            )
        else:
            count = 1
            reporter.warning(
                f"集成模块: {child_name} 的 [{marker_spec.index}] 无法解析循环次数，"
                "generate 默认使用 1",
                code="W_GENERATE_RANGE",
            )
        generate_counts[child_name] = count

    lines = render_module_header(top, all_macros)
    lines.extend(["", *user_code_block("before statement")])
    if wires:
        lines.append("")
        lines.append("    // Internal connections and NA placeholder signals.")
        lines.append("    // 子模块内部连线及 NA 占位信号。")
        wire_packed_ranges = [
            ()
            if wire.interface_type
            else all_packed_dimension_ranges(
                wire.arrays,
                wire.packed_dimensions,
                wire.width,
                wire.parameter_map,
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
                    f"wire {packed}"
                    if wire_dimension_widths
                    else "wire"
                )
        wire_prefix_width = max(len(prefix) for prefix in wire_prefixes)
        wire_name_width = max(len(wire.name) for wire in wires)
        wire_array_fields = [
            (
                array_ranges(wire.arrays, wire.parameter_map)
                if wire.interface_type
                else ""
            )
            for wire in wires
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

    if adapter_assignments:
        lines.append("")
        lines.append(
            "// Width adapters: keep low bits and zero-fill undriven high bits."
        )
        lines.append("// 位宽适配：保留低位，未驱动的高位补零。")
        assignment_target_width = max(
            len(assignment.target) for assignment in adapter_assignments
        )
        for assignment in adapter_assignments:
            for condition in assignment.conditions:
                lines.append(f"`ifdef {condition}")
            lines.append(
                f"assign {assignment.target:<{assignment_target_width}} = "
                f"{assignment.expression};"
            )
            lines.extend("`endif" for _ in reversed(assignment.conditions))

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
        lines.append("// TOP outputs without an active child driver are tied to zero.")
        lines.append("// 没有有效子模块驱动的 TOP 输出在当前配置下置零。")
        target_width = max((len(port.name) for port in fallback_outputs), default=0)
        for port in fallback_outputs:
            append_fallback_zero_assignment(
                lines,
                port,
                top_output_driver_conditions.get(port.name, []),
                target_width=target_width,
            )

    for child in children:
        lines.extend(["", *user_code_block(f"before {child.name}")])
        instance_parameter_map = instance_parameter_maps[child.name]
        generate_count = generate_counts.get(child.name)
        instance_name = instance_names[child.name]
        if generate_count is not None:
            index = generated_indices[child.name]
            count = generate_count
            lines.append(f"genvar {index};")
            lines.append("generate")
            lines.append(
                f"for ({index} = 0; {index} < {count}; "
                f"{index} = {index} + 1) begin : G_{instance_name}"
            )
        if instance_parameter_map:
            lines.append(f"{child.name} #(")
            items = list(instance_parameter_map)
            parameter_name_width = max(len(name) for name in items)
            parameter_value_width = max(
                len(instance_parameter_map[name]) for name in items
            )
            for item_index, name in enumerate(items):
                comma = "," if item_index < len(items) - 1 else ""
                lines.append(
                    f"    .{name:<{parameter_name_width}} "
                    f"({instance_parameter_map[name]:<{parameter_value_width}}){comma}"
                )
            lines.append(f") {instance_name} (")
        else:
            lines.append(f"{child.name} {instance_name} (")
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
        lines.append(");")
        if generate_count is not None:
            lines.append("end")
            lines.append("endgenerate")
        lines.extend(["", *user_code_block(f"after {child.name}")])
    lines.extend(["endmodule", ""])
    return "\n".join(lines)


@dataclass(frozen=True)
class UserCodeRegion:
    label: str
    occurrence: int
    content_start: int
    content_end: int
    content: str


def parse_user_code_regions(
    text: str,
    context: str,
    reporter: Reporter,
) -> list[UserCodeRegion] | None:
    """Parse protected regions, rejecting damaged markers before any overwrite."""
    markers = list(USER_CODE_MARKER_RE.finditer(text))
    begin_tokens = text.count("/*USER CODE BEGIN")
    end_tokens = text.count("/*USER CODE END")
    matched_begins = sum(match.group(1) == "BEGIN" for match in markers)
    matched_ends = sum(match.group(1) == "END" for match in markers)
    if begin_tokens != matched_begins or end_tokens != matched_ends:
        reporter.error(
            f"{context}: 用户代码段标记格式损坏，拒绝覆盖文件",
            code="E_USER_CODE",
        )
        return None

    regions: list[UserCodeRegion] = []
    occurrences: dict[str, int] = {}
    active: tuple[str, int] | None = None
    for marker in markers:
        kind = marker.group(1)
        label = marker.group(2).strip()
        if kind == "BEGIN":
            if active is not None:
                reporter.error(
                    f"{context}: 用户代码段不允许嵌套，拒绝覆盖文件",
                    code="E_USER_CODE",
                )
                return None
            active = (label, marker.end())
            continue
        if active is None:
            reporter.error(
                f"{context}: 用户代码段 END 缺少对应 BEGIN，拒绝覆盖文件",
                code="E_USER_CODE",
            )
            return None
        begin_label, content_start = active
        if label != begin_label:
            reporter.error(
                f"{context}: 用户代码段 BEGIN {begin_label!r} 与 END {label!r} "
                "不匹配，拒绝覆盖文件",
                code="E_USER_CODE",
            )
            return None
        occurrence = occurrences.get(label, 0)
        occurrences[label] = occurrence + 1
        regions.append(
            UserCodeRegion(
                label,
                occurrence,
                content_start,
                marker.start(),
                text[content_start : marker.start()],
            )
        )
        active = None
    if active is not None:
        reporter.error(
            f"{context}: 用户代码段 BEGIN {active[0]!r} 缺少对应 END，拒绝覆盖文件",
            code="E_USER_CODE",
        )
        return None
    return regions


def preserve_user_code(
    generated: str,
    existing: str,
    path: Path,
    reporter: Reporter,
) -> str:
    """Merge protected content from an existing generated file into new output."""
    old_regions = parse_user_code_regions(existing, str(path), reporter)
    new_regions = parse_user_code_regions(generated, f"新生成内容 {path.name}", reporter)
    if old_regions is None or new_regions is None:
        return generated
    old_by_key = {
        (region.label, region.occurrence): region for region in old_regions
    }
    new_keys = {(region.label, region.occurrence) for region in new_regions}
    orphaned = [
        region
        for key, region in old_by_key.items()
        if key not in new_keys and region.content.strip()
    ]
    if orphaned:
        labels = "、".join(
            f"{region.label}#{region.occurrence + 1}" for region in orphaned
        )
        reporter.error(
            f"{path}: 旧文件中的用户代码段在新结构中已无对应位置 ({labels})，"
            "拒绝覆盖文件",
            code="E_USER_CODE",
        )
        return generated

    merged = generated
    for region in reversed(new_regions):
        if OVERWRITE_FILE_HEADER and region.label == "file header":
            continue
        previous = old_by_key.get((region.label, region.occurrence))
        if previous is None:
            continue
        merged = (
            merged[: region.content_start]
            + previous.content
            + merged[region.content_end :]
        )
    return merged


def generate(
    workbook_path: Path,
    output_directory: Path,
    strict: bool = False,
    check_only: bool = False,
    integration_sheet: str | None = None,
) -> tuple[list[Path], Reporter]:
    reporter = Reporter()
    workbook, modules, integration = parse_workbook(
        workbook_path, reporter, integration_sheet=integration_sheet
    )
    if reporter.has_errors:
        return [], reporter

    rendered: dict[str, str] = {}
    top_name = integration.top_name if integration else None
    if integration:
        sheet = workbook.by_name(integration.sheet_name)
        if sheet is None:
            reporter.error(
                f"找不到集成页签 {integration.sheet_name}",
                code="E_INTEGRATION",
            )
        else:
            # Resolve explicit parameter links before rendering child headers;
            # linked child parameters are overridable, every other parameter
            # remains local.
            rendered_top = render_integration(
                sheet, integration, modules, reporter
            )
            for module in modules.values():
                if module.name != top_name:
                    rendered[module.name] = render_stub(module, {})
            # Keep the macro-owning upper module first in the returned path
            # order.  Callers that pass this order to a shared preprocessor
            # then see the upper definitions before child module references.
            rendered = {top_name or "TOP": rendered_top, **rendered}
    elif top_name is None:
        for module in modules.values():
            rendered[module.name] = render_stub(module)

    paths = [output_directory / f"{name.lower()}.v" for name in rendered]
    merged_contents: list[str] = []
    for path, content in zip(paths, rendered.values()):
        if path.is_file():
            try:
                existing = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                reporter.error(
                    f"无法读取已有生成文件 {path}: {exc}",
                    code="E_FILE_IO",
                )
            else:
                content = preserve_user_code(content, existing, path, reporter)
        merged_contents.append(content)
    if reporter.has_errors or (strict and reporter.has_warnings):
        return [], reporter
    if not check_only:
        output_directory.mkdir(parents=True, exist_ok=True)
        for path, content in zip(paths, merged_contents):
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            temporary.replace(path)
    return paths, reporter


@dataclass(frozen=True)
class DiffusionTarget:
    kind: str
    expression: str

    @property
    def label(self) -> str:
        kind = "宏" if self.kind == "macro" else "parameter"
        return f"{self.expression} ({kind})"


@dataclass
class DiffusionResult:
    backup_path: Path | None
    edited_cells: int
    before: Reporter
    after: Reporter
    cancelled: bool = False


def canonical_dimension_symbol(value: Any) -> DiffusionTarget | None:
    text = clean(value)
    macro = MACRO_RE.fullmatch(text)
    if macro:
        return DiffusionTarget("macro", f"`{macro.group(1).upper()}")
    if IDENTIFIER_RE.fullmatch(text):
        return DiffusionTarget("parameter", text.upper())
    return None


def iter_editable_module_rows(
    workbook: Workbook, reporter: Reporter
) -> Iterable[
    tuple[Sheet, int, dict[str, int], dict[str, list[str]], bool]
]:
    """Yield physical XLSX module rows while never consulting 修改 columns."""
    for sheet in workbook.sheets:
        header = find_module_header(sheet)
        if header is None:
            continue
        header_row, columns = header
        active_values: dict[str, list[str]] = {}
        active_parameter_category = False
        category_column = columns["port"] - 1
        for row in range(header_row + 1, sheet.max_row + 1):
            if not clean(sheet.cell(row, columns["port"])):
                continue
            category = (
                clean(sheet.cell(row, category_column))
                if category_column >= 1
                and category_column not in sheet.ignored_columns
                else ""
            )
            if category:
                active_values = {}
                active_parameter_category = is_parameter_category(category)
            context = f"页签 {sheet.name} 第 {row} 行"
            active_values.update(template_values_in_row(sheet, row, context, reporter))
            yield (
                sheet,
                row,
                columns,
                dict(active_values),
                active_parameter_category,
            )


def expanded_factor_targets(
    factor: str,
    domains: dict[str, list[str]],
) -> list[tuple[DiffusionTarget, int, int]]:
    """Return target, expansion index and expansion count for one factor."""
    variables = template_variables(factor)
    if not variables:
        target = canonical_dimension_symbol(uppercase_macro_references(factor))
        return [(target, 0, 1)] if target else []
    if any(variable not in domains for variable in variables):
        return []
    combinations = list(itertools.product(*(domains[name] for name in variables)))
    result: list[tuple[DiffusionTarget, int, int]] = []
    for index, combination in enumerate(combinations):
        expansion = dict(zip(variables, combination))
        text = uppercase_macro_references(substitute_template(factor, expansion))
        target = canonical_dimension_symbol(text)
        if target:
            result.append((target, index, len(combinations)))
    return result


def list_diffusible_variables(path: Path) -> tuple[list[DiffusionTarget], Reporter]:
    reporter = Reporter()
    workbook = XlsxReader().read(path, ignore_review_columns=False)
    found: dict[tuple[str, str], DiffusionTarget] = {}
    for sheet, row, columns, domains, is_parameter in iter_editable_module_rows(
        workbook, reporter
    ):
        if is_parameter:
            for target, _, _ in expanded_factor_targets(
                clean(sheet.cell(row, columns["port"])), domains
            ):
                found.setdefault((target.kind, target.expression), target)
            continue
        for field in ("width", "array"):
            column = columns.get(field)
            if column is None:
                continue
            for factor in split_top_level_product(sheet.cell(row, column)):
                for target, _, _ in expanded_factor_targets(factor, domains):
                    found.setdefault((target.kind, target.expression), target)
    targets = sorted(
        found.values(),
        key=lambda item: (0 if item.kind == "macro" else 1, item.expression),
    )
    return targets, reporter


def normalize_diffusion_value(value: Any) -> str:
    text = clean(value).replace("（", "(").replace("）", ")")
    is_natural = text.isdigit() and int(text) > 0
    is_parenthesized = text.startswith("(") and text.endswith(")")
    if not (is_natural or is_parenthesized) or evaluate_int_expression(text) is None:
        raise ValueError("扩散值必须是正自然数，或可安全计算且整体带括号的整数表达式")
    return text


def range_values(value: Any, count: int) -> list[str] | None:
    text = clean(value)
    match = re.search(r"(?:范围\s*(?:是|为|[:：=])\s*)?\{\{?([^{}]+)\}\}?", text)
    if match is None:
        return None
    values = [item.strip() for item in re.split(r"[,，、;；]", match.group(1))]
    return values if len(values) == count and all(values) else None


def seeded_default(factor: str) -> str:
    number = evaluate_int_expression(factor)
    return str(number) if number is not None else str(UNKNOWN_WIDTH)


def spread_default_cell(
    raw_dimensions: Any,
    raw_default: Any,
    domains: dict[str, list[str]],
    selected: DiffusionTarget,
    value: str,
) -> str | None:
    factors = split_top_level_product(raw_dimensions)
    if not factors:
        return None
    defaults = split_top_level_product(raw_default)
    if len(defaults) != len(factors):
        defaults = [seeded_default(factor) for factor in factors]
    changed = False
    for factor_index, factor in enumerate(factors):
        matches = [
            (index, count)
            for target, index, count in expanded_factor_targets(factor, domains)
            if target == selected
        ]
        if not matches:
            continue
        variables = template_variables(factor)
        if not variables:
            defaults[factor_index] = value
            changed = True
            continue
        count = matches[0][1]
        existing = range_values(defaults[factor_index], count)
        if existing is None:
            scalar = clean(defaults[factor_index])
            if evaluate_int_expression(scalar) is not None:
                existing = [scalar] * count
            else:
                existing = [str(UNKNOWN_WIDTH)] * count
        else:
            existing = [
                item if evaluate_int_expression(item) is not None else str(UNKNOWN_WIDTH)
                for item in existing
            ]
        for index, _ in matches:
            existing[index] = value
        defaults[factor_index] = "范围是{" + ",".join(existing) + "}"
        changed = True
    return "*".join(defaults) if changed else None


def column_letters(column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def set_worksheet_cell(root: ET.Element, row_number: int, column: int, value: str) -> None:
    namespace = root.tag.split("}", 1)[0].lstrip("{") if "}" in root.tag else ""
    tag = lambda name: f"{{{namespace}}}{name}" if namespace else name
    sheet_data = next(
        (node for node in root.iter() if local_name(node.tag) == "sheetData"),
        None,
    )
    if sheet_data is None:
        raise ValueError("XLSX worksheet 缺少 sheetData")
    row = next(
        (
            node
            for node in sheet_data
            if local_name(node.tag) == "row"
            and int(node.attrib.get("r", "0") or 0) == row_number
        ),
        None,
    )
    if row is None:
        row = ET.Element(tag("row"), {"r": str(row_number)})
        position = next(
            (
                index
                for index, node in enumerate(sheet_data)
                if int(node.attrib.get("r", "0") or 0) > row_number
            ),
            len(sheet_data),
        )
        sheet_data.insert(position, row)
    reference = f"{column_letters(column)}{row_number}"
    cell = next(
        (
            node
            for node in row
            if local_name(node.tag) == "c" and node.attrib.get("r") == reference
        ),
        None,
    )
    if cell is None:
        cell = ET.Element(tag("c"), {"r": reference})
        position = next(
            (
                index
                for index, node in enumerate(row)
                if local_name(node.tag) == "c"
                and column_number(CELL_RE.match(node.attrib.get("r", "A1")).group(1))
                > column
            ),
            len(row),
        )
        row.insert(position, cell)
    cell.attrib["t"] = "inlineStr"
    for child in list(cell):
        cell.remove(child)
    inline = ET.SubElement(cell, tag("is"))
    ET.SubElement(inline, tag("t")).text = value


def write_xlsx_cell_updates(
    path: Path,
    updates: dict[str, dict[tuple[int, int], str]],
) -> None:
    temporary_handle = tempfile.NamedTemporaryFile(
        prefix=f".{path.stem}_",
        suffix=".xlsx.tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(temporary_handle.name)
    temporary_handle.close()
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(
            temporary, "w"
        ) as destination:
            for item in source.infolist():
                content = source.read(item.filename)
                sheet_updates = updates.get(item.filename)
                if sheet_updates:
                    root = ET.fromstring(content)
                    for (row, column), value in sheet_updates.items():
                        set_worksheet_cell(root, row, column, value)
                    content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                destination.writestr(item, content)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def diffuse_variable_value(
    path: Path,
    variable: str,
    value: Any,
    *,
    confirm: Callable[[str], str] = input,
    timestamp: str | None = None,
) -> DiffusionResult:
    """Back up an XLSX and spread one macro/parameter value in place."""
    path = path.resolve()
    normalized_value = normalize_diffusion_value(value)
    targets, discovery_reporter = list_diffusible_variables(path)
    requested = clean(variable)
    if requested.startswith("`"):
        selected = DiffusionTarget("macro", f"`{requested[1:].upper()}")
    else:
        selected = DiffusionTarget("parameter", requested.upper())
    if selected not in targets:
        available = ", ".join(target.expression for target in targets) or "无"
        raise ValueError(f"未找到可扩散变量 {variable!r}；可选值: {available}")

    before = inspect_all_integrations(path)
    for item in discovery_reporter.items:
        before.items.append(item)
    workbook = XlsxReader().read(path, ignore_review_columns=False)
    updates: dict[str, dict[tuple[int, int], str]] = {}
    for sheet, row, columns, domains, is_parameter in iter_editable_module_rows(
        workbook, Reporter()
    ):
        if is_parameter:
            replacement = spread_default_cell(
                sheet.cell(row, columns["port"]),
                sheet.cell(row, columns["value"]),
                domains,
                selected,
                normalized_value,
            )
            value_column = columns["value"]
            if replacement is not None and replacement != clean(
                sheet.cell(row, value_column)
            ):
                updates.setdefault(sheet.xml_path, {})[(row, value_column)] = replacement
            continue
        for dimension_field, value_field in (
            ("width", "value"),
            ("array", "array_value"),
        ):
            dimension_column = columns.get(dimension_field)
            value_column = columns.get(value_field)
            if dimension_column is None or value_column is None:
                continue
            replacement = spread_default_cell(
                sheet.cell(row, dimension_column),
                sheet.cell(row, value_column),
                domains,
                selected,
                normalized_value,
            )
            if replacement is not None and replacement != clean(
                sheet.cell(row, value_column)
            ):
                updates.setdefault(sheet.xml_path, {})[(row, value_column)] = replacement
    edited_cells = sum(len(items) for items in updates.values())
    if edited_cells == 0:
        raise ValueError(f"变量 {selected.expression} 没有需要修改的数值单元格")

    answer = confirm(
        "即将原地修改 XLSX。脚本会自动备份，但请确认你也已做好备份。继续？[y/n]: "
    ).strip().lower()
    while answer not in {"y", "n"}:
        answer = confirm("请输入 y 或 n: ").strip().lower()
    if answer == "n":
        return DiffusionResult(None, 0, before, before, cancelled=True)

    stamp = timestamp or dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_directory = path.parent / "backup"
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup_path = backup_directory / f"{path.stem}_{stamp}{path.suffix}"
    suffix = 2
    while backup_path.exists():
        backup_path = backup_directory / f"{path.stem}_{stamp}_{suffix}{path.suffix}"
        suffix += 1
    shutil.copy2(path, backup_path)
    write_xlsx_cell_updates(path, updates)
    after = inspect_all_integrations(path)
    return DiffusionResult(backup_path, edited_cells, before, after)


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


def choose_integration_sheet(
    workbook_path: Path,
    *,
    menu: Callable[[str, list[str]], int | None] = arrow_menu,
) -> str | None:
    """Select one hierarchy only when a workbook contains multiple candidates."""
    workbook = XlsxReader().read(workbook_path)
    integrations = discover_integrations(workbook)
    if not integrations:
        return None
    if len(integrations) == 1:
        return integrations[0].sheet_name
    selected = menu(
        "请选择集成页签（↑/↓，Enter 确认，Esc 返回）：",
        [
            f"{item.sheet_name}  →  TOP {item.top_name}"
            for item in integrations
        ],
    )
    if selected is None:
        raise MenuCancelled("已取消选择集成页签")
    return integrations[selected].sheet_name


def interactive_main() -> int:
    actions = [
        "生成 Verilog",
        "查看识别结果",
        "校验工作簿",
        "严格校验",
        "扩散变量值（修改 XLSX）",
        "退出",
    ]
    while True:
        selected = arrow_menu("XLSX → Verilog（↑/↓，Enter 确认）：", actions)
        if selected is None or selected == 5:
            print("已退出。")
            return 0
        try:
            workbook = choose_workbook()
        except MenuCancelled:
            continue
        except ValueError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        integration_arguments: list[str] = []
        if selected in {0, 1, 2, 3}:
            try:
                integration_sheet = choose_integration_sheet(workbook)
            except MenuCancelled:
                continue
            except (ValueError, OSError, ET.ParseError, KeyError) as exc:
                print(f"错误: {exc}", file=sys.stderr)
                return 2
            if integration_sheet:
                integration_arguments = ["--integration", integration_sheet]
        if selected == 0:
            response = input("输出目录 [generated]: ").strip()
            output = response or "generated"
            return main(
                [str(workbook), "--output", output, *integration_arguments]
            )
        if selected == 1:
            return main([str(workbook), "--list", *integration_arguments])
        if selected == 2:
            return main([str(workbook), "--check", *integration_arguments])
        if selected == 3:
            return main(
                [str(workbook), "--check", "--strict", *integration_arguments]
            )
        targets, discovery_reporter = list_diffusible_variables(workbook)
        discovery_reporter.print()
        if not targets:
            print("错误: 工作簿中没有可扩散的宏或 parameter", file=sys.stderr)
            return 2
        target_index = arrow_menu(
            "请选择一次要扩散的变量（↑/↓，Enter 确认，Esc 返回）：",
            [target.label for target in targets],
        )
        if target_index is None:
            continue
        value = input("请输入正自然数或整体带括号的整数表达式: ").strip()
        return main(
            [
                str(workbook),
                "--spread-value",
                targets[target_index].expression,
                value,
            ]
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从 XLSX 模块定义和集成关系生成 Verilog（零第三方依赖）"
    )
    parser.add_argument("workbook", nargs="?", type=Path, help="输入 .xlsx；省略时提供终端选择")
    parser.add_argument("-o", "--output", type=Path, default=Path("generated"), help="输出目录")
    parser.add_argument("--check", action="store_true", help="只解析和校验，不写文件")
    parser.add_argument("--strict", action="store_true", help="存在任何警告时也不写文件并返回失败")
    parser.add_argument("--list", action="store_true", help="列出识别结果，不生成文件")
    parser.add_argument(
        "--integration",
        metavar="SHEET",
        help="多集成工作簿中选择一个集成页签；单个候选时可省略",
    )
    parser.add_argument(
        "--spread-value",
        nargs=2,
        metavar=("VARIABLE", "VALUE"),
        help="备份后原地扩散一个宏/parameter 数值；执行前仍需输入 y/n",
    )
    return parser


def print_startup_banner(stream: TextIO | None = None) -> None:
    """Print the V3.12 identification block with one shared centered width."""
    target = stream or sys.stdout
    items = [
        SCRIPT_DISPLAY_NAME,
        SCRIPT_VERSION,
        SCRIPT_RELEASE_DATE,
        SCRIPT_CONTACT,
    ]
    width = max(len(item) for item in items)
    for item in items:
        print(item.center(width), file=target)


def main(argv: Iterable[str] | None = None) -> int:
    if argv is None:
        print_startup_banner()
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
        if args.spread_value:
            print("正在执行修改前完整检查……")
            precheck = inspect_all_integrations(workbook_path)
            precheck.print()
            print(
                f"预检查完成：{sum(item.level == '错误' for item in precheck.items)} 个 error，"
                f"{sum(item.level == '警告' for item in precheck.items)} 个 warning。"
            )
            variable, value = args.spread_value
            result = diffuse_variable_value(workbook_path, variable, value)
            if result.cancelled:
                print("已取消，XLSX 未修改，也未创建备份。")
                return 0
            result.after.print()
            print(
                f"扩散完成：修改 {result.edited_cells} 个单元格；"
                f"备份位于 {result.backup_path}"
            )
            failed = result.after.has_errors
            if failed:
                print("扩散后仍存在 error，请继续处理其他冲突。", file=sys.stderr)
                return 2
            print("扩散后校验无 error。")
            return 0
        if args.list:
            reporter = Reporter()
            workbook, modules, integration = parse_workbook(
                workbook_path,
                reporter,
                integration_sheet=args.integration,
            )
            print("页签: " + ", ".join(sheet.name for sheet in workbook.sheets))
            candidates = discover_integrations(workbook)
            if len(candidates) > 1:
                print(
                    "集成候选: "
                    + ", ".join(
                        f"{item.sheet_name}(TOP={item.top_name})"
                        for item in candidates
                    )
                )
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
            integration_sheet=args.integration,
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
