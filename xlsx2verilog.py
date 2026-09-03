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

# True 时只写出当前选择的集成 TOP，不再生成任何子模块桩文件。
ONLY_TOP = False

# parameter 维度按“数值”列比较后不一致时始终给出 warning。False 仅告警；
# True 还会按完整 packed 总位宽做低位连接，并对未驱动高位自动补 0。
AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH = False

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
    "E_GENERAL": True,  # 其他未分类错误：显示具体失败原因。
    "E_WIDTH": True,  # 位宽错误：显示非法、缺失或冲突的位宽信息。
    "E_TEMPLATE": True,  # 模板错误：显示变量范围、展开或花括号问题。
    "E_CONDITION": True,  # 条件错误：显示非法或互相冲突的条件宏。
    "E_MODULE": True,  # 模块错误：显示模块缺失、重名或名称非法。
    "E_PORT": True,  # 端口错误：显示端口名称、方向或重复定义问题。
    "E_PARAMETER": True,  # 参数错误：显示参数声明、表达式或链接冲突。
    "E_INTEGRATION": True,  # 集成错误：显示页签结构或层次选择问题。
    "E_INSTANCE": True,  # 例化错误：显示实例名、次数或元数据问题。
    "E_DIRECTION": True,  # 方向错误：显示 input/output 连接冲突。
    "E_PORT_REFERENCE": True,  # 端口引用错误：显示缺失或重复连接的端口。
    "E_GENERATE_INDEX": True,  # 循环索引错误：显示 generate 标记冲突。
    "E_NA_TARGET": True,  # NA 目标错误：显示非法、不适用或同一行多目标的情况。
    "E_BIT_SELECT": True,  # 位选择错误：显示不支持或越界的 bit select。
    "E_INTERFACE_CONNECTION": True,  # 接口错误：显示 interface 连接限制。
    "E_DRIVER_CONFLICT": True,  # 驱动冲突：显示同一网络的多个 output。
    "E_USER_CODE": True,  # 用户代码错误：显示 USER CODE 标记损坏或丢失。
    "E_FILE_IO": True,  # 文件错误：显示读取、写入、备份或替换失败。
    # Warnings: generation can continue, but engineering review is needed.
    "W_GENERAL": True,  # 其他未分类警告：显示需要人工确认的风险。
    "W_WIDTH_PLACEHOLDER": True,  # 位宽占位：显示使用 114 代替未知值。
    "W_WIDTH_MISMATCH": True,  # 位宽不匹配：显示相连信号的形状差异。
    "W_PARAMETER_WIDTH_MISMATCH": True,  # 参数位宽不匹配：按数值列发现参数或多维 packed 形状不一致。
    "W_ZERO_WIDTH": True,  # 零位宽：显示已生成 [0 -1:0] 但需确认工具链行为。
    "W_NA_CONSTANT_WIDTH": True,  # NA 常量：显示定宽常量大于目标或无法安全匹配。
    "W_NA_TARGET_CONFLICT": True,  # NA 名称冲突：复用已有信号，提示检查方向和多驱动风险。
    "W_PARAMETER_NOT_EXPORTED": True,  # 参数未上拉：例化次数等位置引用了 TOP 中不存在的 parameter。
    "W_PARAMETER_AUTO_LOCAL": True,  # 参数自动局部化：子模块位宽 parameter 未显式链接，TOP 正文已创建 localparam。
    "W_PARAMETER_NA_REPAIR": True,  # parameter NA 初始化修复：逗号列表含空项，已忽略空项并继续校验元素数。
    "W_TEMPLATE_REPAIR": True,  # 模板修复：显示自动修复的花括号内容。
    "W_TEMPLATE_BINDING": True,  # 模板绑定：显示变量无法可靠对应的问题。
    "W_IO_DEFAULTED": True,  # 方向推断风险：显示空或非法 i/o 的处理结果。
    "W_MODULE_SKIPPED": True,  # 模块跳过：显示无法识别而未生成的页签。
    "W_NO_INTEGRATION": True,  # 无集成页：显示仅生成独立模块的提示。
    "W_INSTANCE_UNUSED": True,  # 实例元数据未使用：显示找不到对应模块。
    "W_DRIVER_RISK": True,  # 驱动风险：显示无 output 或可能多驱动的网络。
    "W_GENERATE_RANGE": True,  # 循环范围风险：显示默认次数或越界可能。
    # Information: deterministic decisions and automatic recovery.
    "I_GENERAL": True,  # 其他普通信息：显示脚本采用的确定性处理。
    "I_PARAMETER_LINK": True,  # 参数链接：显示 TOP localparam 和覆盖关系。
    "I_DIRECTION_INFERRED": True,  # 方向继承：显示采用模块页 i/o 的结果。
    "I_TEMPLATE_PARTIAL": True,  # 模板部分匹配：显示缺少对端的展开项。
    "I_NA_CONNECTION": False,  # NA 连接：显示占位、常量、观察或开路处理。
    "I_UNCONNECTED": False,  # 未连接端口：显示接零、开路或自动补全处理。
    "I_INSTANCE": True,  # 例化信息：显示实例名、次数和 generate 范围。
    "I_ROW_COMMENTED": True,  # 注释行：显示含 *注释* 的 XLSX 行已停用。
}

# Startup identification.  These lines are centered to one shared width.
SCRIPT_DISPLAY_NAME = "CustomScipt xlsx2verilog"
SCRIPT_VERSION = "Version V3.5.03"
SCRIPT_RELEASE_DATE = "2026.9.3"
SCRIPT_CONTACT = "Contact xxx-xxxx in case"


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
CELL_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
MACRO_RE = re.compile(r"^`([A-Za-z_][A-Za-z0-9_$]*)$")
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
MACRO_TEMPLATE_TOKEN_RE = re.compile(
    r"`(?:[A-Za-z0-9_$]|\{\{[A-Za-z_][A-Za-z0-9_]*\}\})+"
)
MISSING_TEMPLATE_OPEN_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
MISSING_TEMPLATE_CLOSE_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
UNKNOWN_WIDTH = 114
# parameter 分类中的 NA[i]->NAME 会生成 packed 常量数组；每个元素默认
# 使用 32 bit，便于容纳普通整数、宏和 parameter 表达式。
PARAMETER_NA_ELEMENT_WIDTH = 32
IGNORED_COLUMN_HEADERS = {"修改", "修改列"}
GROUP_SEPARATOR = "// ----- ----- ----- ----- ----- -----"
NO_GROUP = "no group"
MODULE_LABEL_RE = re.compile(r"^module\s*[:：]\s*(.+)$", re.IGNORECASE)
INSTANCE_LABEL_RE = re.compile(
    r"\s+例化名\s*[:：]\s*([A-Za-z_][A-Za-z0-9_$]*)\s*$",
    re.IGNORECASE,
)
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
SIZED_VERILOG_CONSTANT_RE = re.compile(
    r"^(?P<width>[0-9][0-9_]*)\s*'[sS]?[dDhHbBoO]"
    r"[0-9a-fA-F_xXzZ?]+$"
)
PARAMETER_CATEGORIES = {"parameter", "parameters", "参数", "参数定义"}
MACRO_CATEGORIES = {"macro", "macros", "宏", "宏定义"}
COMMENT_ROW_RE = re.compile(r"\*\s*注释\s*\*")
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


def row_is_commented(sheet: Sheet, row: int) -> bool:
    """Whether an XLSX row is explicitly disabled by a ``*注释*`` marker."""
    return any(
        column not in sheet.ignored_columns
        and COMMENT_ROW_RE.search(clean(sheet.cell(row, column))) is not None
        for column in range(1, sheet.max_column + 1)
    )


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
    declared_macros: dict[str, str] = field(default_factory=dict)
    disabled_ports: set[str] = field(default_factory=set)
    disabled_templates: set[str] = field(default_factory=set)
    disabled_parameters: set[str] = field(default_factory=set)
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
    commented: bool = False
    label_instance_name: str | None = None


@dataclass(frozen=True)
class IntegrationChild:
    """One child instance endpoint in an integration sheet.

    ``module_name`` identifies the generated Verilog module type. ``key`` is
    the internal identity used to keep multiple instances of the same module
    independent.  Old workbooks without an inline instance label retain the
    module name as their key.
    """

    key: str
    module_name: str
    label_instance_name: str | None = None
    commented: bool = False


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
    commented_child_names: set[str] = field(default_factory=set)
    child_instances: list[IntegrationChild] = field(default_factory=list)


def integration_block_key(block: IntegrationBlock, top_name: str) -> str:
    if block.anonymous_na or block.module_name == top_name:
        return block.module_name
    if block.label_instance_name:
        return f"{block.module_name}::{block.label_instance_name}"
    return block.module_name


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
            if row_is_commented(sheet, row):
                continue
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


def natural_number_text(value: Any) -> str | None:
    """Return one normalized nonnegative integer literal, without expressions."""
    text = clean(value)
    if re.fullmatch(r"[0-9](?:_?[0-9])*", text) is None:
        return None
    return str(int(text.replace("_", "")))


def direct_parameter_expression(value: Any) -> str | None:
    """Accept the V3.4 direct parameter override forms: number or macro."""
    number = natural_number_text(value)
    if number is not None:
        return number
    text = clean(value)
    return text if MACRO_RE.fullmatch(text) is not None else None


def evaluate_int_expression(value: Any, *, allow_zero: bool = False) -> int | None:
    """Safely evaluate a small integer expression.

    Most physical sizing/count callers use the default strictly-positive rule.
    Parameter/macro matching values and the explicit-width parser opt into
    zero; the latter emits a dedicated toolchain-risk warning.
    """
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= minimum else None
    if isinstance(value, float) and value.is_integer():
        integer = int(value)
        return integer if integer >= minimum else None
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
    return result if result >= minimum else None


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
    # A macro/parameter default may intentionally be zero (for example a
    # disabled optional bus).  analyze_width() preserves that dimension and
    # emits W_ZERO_WIDTH; instance-count callers remain strictly positive.
    evaluated = evaluate_int_expression(default_value, allow_zero=True)
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
        return f"`{macro_match.group(1)}"
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
    def accepted(width: Width) -> Width:
        candidate = width.expression if width.kind == "literal" else width.default
        if evaluate_int_expression(candidate, allow_zero=True) == 0:
            reporter.warning(
                f"{context}: 使用零位宽 {width.expression}；仍按 "
                f"[{width.expression} -1:0] 生成，请确认综合/仿真工具对零宽网络的处理",
                code="W_ZERO_WIDTH",
            )
        return width

    if raw_width is None or clean(raw_width) == "":
        number = evaluate_int_expression(default_value)
        return Width("literal", str(number or 1), str(number or 1))
    number = evaluate_int_expression(raw_width, allow_zero=True)
    if number is not None:
        return accepted(Width("literal", str(number), str(number)))

    text = clean(raw_width)
    macro_match = MACRO_RE.fullmatch(text)
    if macro_match:
        text = f"`{macro_match.group(1)}"
        default = normalized_width_default(
            default_value,
            f"{context}: 宏 {text}",
            reporter,
            fallback_uncertain=fallback_uncertain,
        )
        return accepted(Width("macro", text, default))
    if IDENTIFIER_RE.fullmatch(text):
        text = text.upper()
        default = normalized_width_default(
            default_value,
            f"{context}: parameter {text}",
            reporter,
            fallback_uncertain=fallback_uncertain,
        )
        return accepted(Width("parameter", text, default))

    if re.fullmatch(r"[A-Za-z0-9_$`()\s`+\-*/%<>&|^]+", text) and re.search(
        r"[+\-*/%<>&|^]", text
    ):
        inferred = evaluate_int_expression(default_value, allow_zero=True)
        if inferred is not None:
            return accepted(Width("literal", str(inferred), str(inferred)))
        reporter.warning(
            f"{context}: 表达式 {text!r} 无法确定位宽，使用占位值 {UNKNOWN_WIDTH}",
            code="W_WIDTH_PLACEHOLDER",
        )
        return Width("literal", str(UNKNOWN_WIDTH), str(UNKNOWN_WIDTH))

    reporter.error(
        f"{context}: 不支持的位宽 {text!r}；请使用非负整数、`MACRO 或 PARAMETER",
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


def substitute_template_expression(text: Any, values: dict[str, str]) -> str:
    """Expand templates while uppercasing only macro names that were templated.

    Verilog macros are case-sensitive.  Ordinary macro references therefore
    preserve the workbook spelling, while the historical `` `DW_{{i}}``
    convention intentionally canonicalizes its expanded macro to uppercase.
    """
    source = clean(text)

    def expand_macro(match: re.Match[str]) -> str:
        token = match.group(0)
        expanded = substitute_template(token, values)
        return (
            expanded.upper()
            if TEMPLATE_RE.search(token) and not TEMPLATE_RE.search(expanded)
            else expanded
        )

    source = MACRO_TEMPLATE_TOKEN_RE.sub(expand_macro, source)
    return substitute_template(source, values)


def preserve_macro_references(text: Any) -> str:
    """Return an expression without changing case-sensitive macro names."""
    return clean(text)


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
    Generated parameter identifiers remain uppercase. Macro and system-function
    identifiers preserve the XLSX spelling because both may be case-sensitive.
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
            token = token
        elif kind == "identifier":
            # ``default`` is a case-sensitive SystemVerilog assignment-pattern
            # keyword. Other identifiers keep the generator's parameter-name
            # normalization rule.
            token = "default" if token.casefold() == "default" else token.upper()
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


def parse_module_label_details(value: Any) -> tuple[str, bool, str | None]:
    """Return module type, comment flag, and an inline instance override."""
    text = clean(value)
    match = MODULE_LABEL_RE.fullmatch(text)
    if match is not None:
        text = match.group(1).strip()
    commented = COMMENT_ROW_RE.search(text) is not None
    if commented:
        text = COMMENT_ROW_RE.sub("", text).strip()
    instance_match = INSTANCE_LABEL_RE.search(text)
    instance_name = instance_match.group(1) if instance_match is not None else None
    if instance_match is not None:
        text = text[: instance_match.start()].strip()
    return text, commented, instance_name


def parse_module_label(value: Any) -> tuple[str, bool]:
    """Backward-compatible module label parser."""
    name, commented, _ = parse_module_label_details(value)
    return name, commented


def module_label_above_header(
    sheet: Sheet, header_row: int, port_column: int
) -> tuple[str, bool]:
    for row in range(header_row - 1, 0, -1):
        value = clean(sheet.cell(row, port_column))
        if value:
            return parse_module_label(value)
    return parse_module_label(sheet.name)


def module_label_details_above_header(
    sheet: Sheet, header_row: int, port_column: int
) -> tuple[str, bool, str | None]:
    for row in range(header_row - 1, 0, -1):
        value = clean(sheet.cell(row, port_column))
        if value:
            return parse_module_label_details(value)
    return parse_module_label_details(sheet.name)


def module_name_above_header(sheet: Sheet, header_row: int, port_column: int) -> str:
    return module_label_above_header(sheet, header_row, port_column)[0]


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


def is_macro_category(value: Any) -> bool:
    """Whether a category starts a metadata-only macro-default section."""
    return clean(value).casefold() in MACRO_CATEGORIES


def port_dimensions(port: Port) -> tuple[Width, ...]:
    return (*port.packed_dimensions, port.width, *port.arrays)


def replace_port_dimensions(port: Port, transform: Callable[[Width], Width]) -> None:
    """Apply one immutable-Width transformation to every dimension of a port."""
    port.packed_dimensions = tuple(transform(item) for item in port.packed_dimensions)
    port.width = transform(port.width)
    port.arrays = tuple(transform(item) for item in port.arrays)


def rebuild_module_symbols(module: Module) -> None:
    parameters = dict(module.declared_parameters)
    macros: dict[str, str] = dict(module.declared_macros)
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
    for name, default in module.declared_macros.items():
        if default:
            values.setdefault(("macro", f"`{name}"), set()).add(default)
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
    declared_macros: dict[str, str] = {}
    parameter_expressions: dict[str, str] = {}
    parameter_comments: dict[str, str] = {}
    declared_parameter_rows: dict[str, int] = {}
    seen: dict[str, Port] = {}
    active_template_values: dict[str, list[str]] = {}
    active_category = NO_GROUP
    active_condition: str | None = None
    active_parameter_category = False
    active_macro_category = False
    disabled_row_count = 0
    disabled_ports: set[str] = set()
    disabled_templates: set[str] = set()
    disabled_parameters: set[str] = set()
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
            active_macro_category = is_macro_category(active_category)
            active_template_values = {}
        row_template_values = template_values_in_row(sheet, row, context, reporter)
        if row_template_values:
            active_template_values.update(row_template_values)
        port_variables = template_variables(raw_port_name)
        commented = row_is_commented(sheet, row)
        missing_variables = [
            variable for variable in port_variables if variable not in active_template_values
        ]
        if missing_variables and not commented:
            names = "、".join(missing_variables)
            example = missing_variables[0]
            subject = (
                "parameter 名"
                if active_parameter_category
                else "宏名"
                if active_macro_category
                else "端口名"
            )
            reporter.error(
                f"{context}: {subject}模板变量 {names} 未找到取值列表；"
                f"请在同一分类中使用 {example}是{{a,b}} 或 {example}={{a,b}}",
                code="E_TEMPLATE",
            )
            continue
        if port_variables and not missing_variables:
            expansions = [
                dict(zip(port_variables, combination))
                for combination in itertools.product(
                    *(active_template_values[variable] for variable in port_variables)
                )
            ]
        else:
            expansions = [{}]

        if commented:
            disabled_row_count += 1
            if active_parameter_category:
                disabled_parameters.update(
                    substitute_template(raw_port_name, expansion).upper()
                    for expansion in expansions
                )
            elif not active_macro_category:
                disabled_templates.add(raw_port_name)
                disabled_ports.update(
                    substitute_template(raw_port_name, expansion)
                    for expansion in expansions
                )
            reporter.info(
                f"{context}: 检测到 *注释*，该 XLSX 行不参与生成",
                code="I_ROW_COMMENTED",
            )
            continue

        if active_macro_category:
            for expansion_index, expansion in enumerate(expansions):
                assignments = ", ".join(
                    f"{name}={value}" for name, value in expansion.items()
                )
                expanded_context = (
                    f"{context} ({assignments})" if assignments else context
                )
                macro_name = substitute_template(raw_port_name, expansion)
                if port_variables:
                    macro_name = macro_name.upper()
                if macro_name.startswith("`"):
                    macro_name = macro_name[1:]
                if not IDENTIFIER_RE.fullmatch(macro_name):
                    reporter.error(
                        f"{expanded_context}: 宏名 {macro_name!r} "
                        "不是合法 Verilog 标识符",
                        code="E_WIDTH",
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
                raw_default = substitute_template_expression(raw_default, expansion)
                default = normalized_width_default(
                    raw_default,
                    f"{expanded_context}: 宏 `{macro_name}",
                    reporter,
                    fallback_uncertain=bool(expansion),
                )
                previous = declared_macros.get(macro_name)
                if previous is not None and previous != default:
                    reporter.error(
                        f"页签 {sheet.name}: 宏 `{macro_name} 默认值冲突 "
                        f"({previous}/{default})",
                        code="E_WIDTH",
                    )
                    continue
                declared_macros.setdefault(macro_name, default)
            continue

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
                raw_default = preserve_macro_references(
                    substitute_template_expression(raw_default, expansion)
                )
                raw_expression = substitute_template_expression(
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

            raw_width = substitute_template_expression(base_width, expansion)
            raw_array = substitute_template_expression(base_array, expansion)
            raw_width = preserve_macro_references(raw_width)
            raw_array = preserve_macro_references(raw_array)
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
            raw_default = substitute_template_expression(raw_default, expansion)
            raw_array_default = substitute_template_expression(
                raw_array_default, expansion
            )

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
    if not ports and not (declared_parameters or declared_macros or disabled_row_count):
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
        declared_macros=declared_macros,
        disabled_ports=disabled_ports,
        disabled_templates=disabled_templates,
        disabled_parameters=disabled_parameters,
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
            recovered_name, _ = module_label_above_header(sheet, row, column)
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
            module_name, commented, label_instance_name = module_label_details_above_header(
                sheet, row, port_column
            )
            blocks.append(
                IntegrationBlock(
                    module_name.upper(),
                    port_column,
                    direction_column,
                    commented=commented,
                    label_instance_name=label_instance_name,
                )
            )
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
        commented_child_names = {
            integration_block_key(block, top_name)
            for group in groups
            for block in group
            if block.commented and block.module_name != top_name
        }
        child_instances: list[IntegrationChild] = []
        seen_child_keys: set[str] = set()
        for group in groups:
            for block in group:
                if block.anonymous_na or block.module_name == top_name:
                    continue
                key = integration_block_key(block, top_name)
                if key in seen_child_keys:
                    continue
                seen_child_keys.add(key)
                child_instances.append(
                    IntegrationChild(
                        key,
                        block.module_name,
                        block.label_instance_name,
                        block.commented,
                    )
                )
        return Integration(
            sheet.name,
            row,
            groups,
            top_name,
            child_names,
            find_instance_specs(sheet),
            commented_child_names,
            child_instances,
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
                        direct_value = natural_number_text(
                            integration_sheet.cell(row, block.direction_column)
                        )
                        if direct_value is not None:
                            linked_parameter_defaults[(module.name, name)] = direct_value
                            continue
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
            for name, value in list(module.declared_macros.items()):
                if value:
                    continue
                reporter.error(
                    f"页签 {module.sheet_name}: 宏 `{name} 缺少“数值”默认值",
                    code="E_WIDTH",
                )
                module.declared_macros[name] = "1"
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
                macro_values.setdefault(f"`{name}", set()).add(value)
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


def parse_define_sheet(sheet: Sheet, reporter: Reporter) -> dict[str, str]:
    """Read the dedicated ``define`` sheet as centralized macro metadata.

    Supported headers are ``宏名/名称/name/macro/define/端口名`` plus
    ``数值/value/default/默认值``.  This remains metadata: generated Verilog
    emits the same commented ``// `define`` reference lines as module-local
    macro rows and never creates active preprocessor definitions.
    """
    name_aliases = {
        "宏名", "名称", "name", "macro", "define", "端口名", "port", "portname"
    }
    value_aliases = {"数值", "value", "default", "默认值", "匹配值"}
    header: tuple[int, int, int] | None = None
    for row in range(1, min(sheet.max_row, 20) + 1):
        name_column: int | None = None
        value_column: int | None = None
        for column in range(1, sheet.max_column + 1):
            value = clean(sheet.cell(row, column)).casefold().replace(" ", "")
            if value in name_aliases and name_column is None:
                name_column = column
            if value in value_aliases and value_column is None:
                value_column = column
        if name_column is not None and value_column is not None:
            header = (row, name_column, value_column)
            break
    if header is None:
        reporter.error(
            f"页签 {sheet.name}: define 页签缺少宏名和数值表头",
            code="E_WIDTH",
        )
        return {}

    header_row, name_column, value_column = header
    macros: dict[str, str] = {}
    source_rows: dict[str, int] = {}
    for row in range(header_row + 1, sheet.max_row + 1):
        raw_name = clean(sheet.cell(row, name_column))
        if not raw_name or COMMENT_ROW_RE.search(raw_name):
            continue
        raw_name = re.sub(r"^`define\s+", "", raw_name, flags=re.IGNORECASE)
        raw_name = raw_name.lstrip("`").strip()
        if not IDENTIFIER_RE.fullmatch(raw_name):
            reporter.error(
                f"页签 {sheet.name} 第 {row} 行: 宏名 {raw_name!r} "
                "不是合法 Verilog 标识符",
                code="E_WIDTH",
            )
            continue
        default = normalized_width_default(
            sheet.cell(row, value_column),
            f"页签 {sheet.name} 第 {row} 行: 宏 `{raw_name}",
            reporter,
            fallback_uncertain=False,
        )
        previous = macros.get(raw_name)
        if previous is not None and previous != default:
            reporter.error(
                f"页签 {sheet.name}: 宏 `{raw_name} 默认值冲突；第 "
                f"{source_rows[raw_name]} 行为 {previous}，第 {row} 行为 {default}",
                code="E_WIDTH",
            )
            continue
        macros.setdefault(raw_name, default)
        source_rows.setdefault(raw_name, row)
    return macros


def parse_workbook(
    path: Path,
    reporter: Reporter,
    integration_sheet: str | None = None,
) -> tuple[Workbook, dict[str, Module], Integration | None]:
    workbook = XlsxReader().read(path)
    define_sheets = [
        sheet for sheet in workbook.sheets if sheet.name.casefold() == "define"
    ]
    if len(define_sheets) > 1:
        reporter.error("工作簿中只能有一个 define 页签", code="E_WIDTH")
    central_macros = (
        parse_define_sheet(define_sheets[0], reporter) if define_sheets else {}
    )
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
        if sheet.name.casefold() == "define":
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

    if central_macros:
        for module in modules.values():
            for name, value in central_macros.items():
                previous = module.declared_macros.get(name)
                if previous is not None and previous != value:
                    reporter.error(
                        f"页签 {module.sheet_name}: 宏 `{name}={previous} 与 "
                        f"define 页签统一值 {value} 冲突",
                        code="E_WIDTH",
                    )
                    continue
                module.declared_macros.setdefault(name, value)
            rebuild_module_symbols(module)

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
            if spec.raw_count and spec.count is None and not (
                MACRO_RE.fullmatch(spec.raw_count)
                or IDENTIFIER_RE.fullmatch(spec.raw_count)
            ):
                reporter.error(
                    f"{context}: 例化次数 {spec.raw_count!r} 必须是正整数、"
                    "可计算的正整数表达式、`MACRO 或 TOP parameter",
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
        if evaluate_int_expression(expression, allow_zero=True) == 0:
            return "'0"
        return f"{expression}'b0"
    return f"{{{expression}{{1'b0}}}}"


def connection_zero_value(port: Port, parameter_map: dict[str, str] | None = None) -> str:
    """Render the same full-shape zero used by an explicit ``NA->0``."""
    return connection_constant_value("0", port, parameter_map)


def connection_constant_value(
    value: str,
    port: Port,
    parameter_map: dict[str, str] | None = None,
    *,
    reporter: Reporter | None = None,
    context: str = "NA 常量",
) -> str:
    """Fit an NA constant to the destination's complete packed shape.

    Plain 0/1 are expanded across every packed dimension.  A sized Verilog
    literal is zero-extended when narrower; an oversized literal is retained
    (Verilog will context-truncate it) and reported for engineering review.
    """
    constant = clean(value)
    dimensions = (*port.arrays, *port.packed_dimensions, port.width)
    bit_count = "*".join(
        width_expression(dimension, parameter_map) for dimension in dimensions
    )
    if constant in {"0", "1"}:
        resolved_for_zero = [
            evaluate_int_expression(
                dimension.expression
                if dimension.kind == "literal"
                else dimension.default,
                allow_zero=True,
            )
            for dimension in dimensions
        ]
        if any(item == 0 for item in resolved_for_zero):
            return "'0"
        return f"{{{bit_count}{{1'b{constant}}}}}"

    sized_match = SIZED_VERILOG_CONSTANT_RE.fullmatch(constant)
    if sized_match is None:
        return constant
    source_width = int(sized_match.group("width").replace("_", ""))
    resolved_dimensions = [
        evaluate_int_expression(
            dimension.expression
            if dimension.kind == "literal"
            else dimension.default,
            allow_zero=True,
        )
        for dimension in dimensions
    ]
    if any(item is None for item in resolved_dimensions):
        if reporter is not None:
            reporter.warning(
                f"{context}: 无法静态计算目标总位宽，保留常量 {constant}，"
                "请在 elaboration 后确认",
                code="W_NA_CONSTANT_WIDTH",
            )
        return constant
    target_width = 1
    for dimension in resolved_dimensions:
        assert dimension is not None
        target_width *= dimension
    if source_width < target_width:
        return f"{{{{{target_width - source_width}{{1'b0}}}}, {constant}}}"
    if source_width > target_width and reporter is not None:
        reporter.warning(
            f"{context}: 常量 {constant} 的声明位宽 {source_width} 大于"
            f"目标总位宽 {target_width}，生成结果保留原常量并由 Verilog 上下文截断；"
            "请确认高位丢失是否符合设计",
            code="W_NA_CONSTANT_WIDTH",
        )
    return constant


def port_uses_parameter_width(port: Port) -> bool:
    """Whether V2 delegates this port's width compatibility to elaboration."""
    return any(
        width.kind == "parameter"
        for width in (*port.packed_dimensions, port.width, *port.arrays)
    )


def resolved_width_value(width: Width, macros: dict[str, str]) -> int | None:
    expression = width.expression
    if width.kind == "macro":
        expression = macros.get(expression.lstrip("`"), width.default)
    elif width.kind == "parameter":
        expression = width.default
    return evaluate_int_expression(expression)


def simple_packed_width(port: Port, macros: dict[str, str]) -> int | None:
    """Return a width suitable for a low-bit adapter, or ``None`` if unsafe."""
    if (
        port.is_interface
        or port.arrays
        or port.packed_dimensions
        or (
            port_uses_parameter_width(port)
            and not AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH
        )
    ):
        return None
    return resolved_width_value(port.width, macros)


def comparable_port_shape(
    port: Port, macros: dict[str, str]
) -> tuple[object, ...] | None:
    """Resolve literal, macro, and parameter dimensions from matching values."""
    if port.interface_type:
        base_type = port.interface_type.rsplit(".", 1)[0]
        values = tuple(resolved_width_value(item, macros) for item in port.arrays)
        return (f"interface:{base_type}", *values)
    dimensions = (*port.arrays, *port.packed_dimensions, port.width)
    values = tuple(resolved_width_value(item, macros) for item in dimensions)
    return values if all(value is not None for value in values) else port.shape


def packed_total_width(port: Port, macros: dict[str, str]) -> int | None:
    """Resolve the complete packed size used by parameter-aware adapters."""
    if port.is_interface:
        return None
    dimensions = (*port.arrays, *port.packed_dimensions, port.width)
    values = [resolved_width_value(item, macros) for item in dimensions]
    if any(value is None for value in values):
        return None
    total = 1
    for value in values:
        total *= value or 0
    return total


def connection_numeric_width(port: Port, macros: dict[str, str]) -> int | None:
    """Width available to connection adapters under the configured policy."""
    if port_uses_parameter_width(port):
        return (
            packed_total_width(port, macros)
            if AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH
            else None
        )
    return simple_packed_width(port, macros)


def low_bits(expression: str, width: int) -> str:
    return f"{expression}[0]" if width == 1 else f"{expression}[{width} -1:0]"


def fit_source_width(
    expression: str,
    source_width: int,
    target_width: int,
    *,
    sized_zero_fill: bool = False,
) -> str:
    """Resize an rvalue using low-bit truncation or zero extension.

    Parameter-aware adaptation uses a sized zero literal such as ``4'b0``.
    This keeps the complete extension inside the destination port expression
    and avoids a separate unsized ``'0`` assignment on an intermediate net.
    """
    if source_width == target_width:
        return expression
    if source_width > target_width:
        return low_bits(expression, target_width)
    fill_width = target_width - source_width
    zero_fill = (
        f"{fill_width}'b0"
        if sized_zero_fill
        else f"{{{fill_width}{{1'b0}}}}"
    )
    return f"{{{zero_fill}, {expression}}}"


def append_zero_assignment(
    lines: list[str],
    port: Port,
    parameter_map: dict[str, str] | None = None,
    indent: str = "",
    target_width: int = 0,
) -> None:
    """Append one full-shape zero assignment using the connection convention."""
    value = connection_zero_value(port, parameter_map)
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


def render_module_header(
    module: Module,
    macros: dict[str, str] | None = None,
    *,
    before_module_user: bool = False,
) -> list[str]:
    lines = render_file_header()
    lines.extend(render_macros(macros if macros is not None else module.macros))
    if before_module_user:
        lines.extend([*user_code_block("before module"), ""])
    if module.parameters:
        lines.append(f"module {module.name} #(")
        parameter_items = list(module.parameters.items())
        parameter_name_width = max(len(name) for name, _ in parameter_items)
        for index, (name, value) in enumerate(parameter_items):
            comma = "," if index < len(parameter_items) - 1 else ""
            rendered_value = module.parameter_expressions.get(name, value)
            comment_value = module.parameter_comments.get(name)
            comment = f"  // {comment_value}" if comment_value else ""
            lines.append(
                f"    {'parameter':<10} {name:<{parameter_name_width}} = "
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
    lines.extend(["", *user_code_block("after statement"), "endmodule", ""])
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
    requires_todo: bool = False


@dataclass
class GenerateSpec:
    index: str
    extents: list[int] = field(default_factory=list)
    extent_expressions: list[str] = field(default_factory=list)


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
    child_instances = integration.child_instances or [
        IntegrationChild(name, name)
        for name in integration.child_names
        if name in modules
    ]
    child_instances = [
        child for child in child_instances if child.module_name in modules
    ]
    child_modules = {
        child.key: modules[child.module_name] for child in child_instances
    }
    # Macro collection is per module type; duplicate instances must not create
    # artificial conflicts with their own module definition.
    children = [modules[name] for name in integration.child_names if name in modules]
    top_ports = top.port_map

    def block_key(block: IntegrationBlock) -> str:
        return integration_block_key(block, top.name)

    def child_for_key(key: str) -> IntegrationChild:
        return next(child for child in child_instances if child.key == key)

    def instance_spec_for_key(key: str) -> InstanceSpec | None:
        child = child_for_key(key)
        return integration.instance_specs.get(child.module_name)

    instance_names: dict[str, str] = {}
    used_instance_names: set[str] = set()
    generated_indices: dict[str, str] = {}
    for child in child_instances:
        configured = instance_spec_for_key(child.key)
        instance_name = (
            child.label_instance_name
            or (configured.instance_name if configured and configured.instance_name else None)
            or f"U_{child.module_name}"
        )
        if instance_name in used_instance_names:
            reporter.error(
                f"集成页签 {sheet.name}: 例化名 {instance_name} 被多个模块重复使用",
                code="E_INSTANCE",
            )
        used_instance_names.add(instance_name)
        instance_names[child.key] = instance_name
        generated_indices[child.key] = "i"

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

    # Parameters stay local unless an integration parameter row explicitly
    # links them or TOP wiring needs an otherwise out-of-scope child width.
    parameter_rows_by_group = integration_parameter_rows(sheet, integration)
    commented_integration_rows_reported: set[int] = set()

    if any(
        block.commented and block.module_name == top.name
        for group in integration.groups
        for block in group
    ):
        reporter.error(
            f"集成页签 {sheet.name}: TOP 模块 {top.name} 不能使用 *注释* 停用",
            code="E_MODULE",
        )
    for child_name in sorted(integration.commented_child_names):
        child = child_for_key(child_name)
        reporter.info(
            f"集成页签 {sheet.name}: 模块 {child.module_name} 的实例 "
            f"{child.label_instance_name or child.module_name} 标记为 *注释*，"
            "保留全部解析和连线结果，但注释其完整实例",
            code="I_ROW_COMMENTED",
        )

    def skip_commented_integration_row(row: int) -> bool:
        if not row_is_commented(sheet, row):
            return False
        if row not in commented_integration_rows_reported:
            commented_integration_rows_reported.add(row)
            reporter.info(
                f"集成页签 {sheet.name} 第 {row} 行: 检测到 *注释*，"
                "该连接行不参与生成",
                code="I_ROW_COMMENTED",
            )
        return True
    # parameter_maps are used when a child-owned dimension must be expressed
    # in TOP scope.  Unlinked local parameters resolve to their numeric match
    # values; linked parameters resolve to a TOP localparam name.
    parameter_maps: dict[str, dict[str, str]] = {
        top.name: {name: name for name in top.parameters}
    }
    instance_parameter_maps: dict[str, dict[str, str]] = {}
    wire_parameter_overrides: dict[str, dict[str, str]] = {}
    for child in child_instances:
        module = child_modules[child.key]
        parameter_maps[child.key] = {
            name: name for name in module.parameters
        }
        instance_parameter_maps[child.key] = {}
        wire_parameter_overrides[child.key] = {}

    used_top_parameter_names = set(top.parameters)
    integration_local_parameters: dict[str, str] = {}
    integration_local_parameter_dimensions: dict[str, tuple[str, ...]] = {}
    parameter_na_generate_counts: dict[str, str] = {}
    parameter_na_generate_indices: dict[str, str] = {}

    def add_integration_local_parameter(
        requested_name: str,
        value: str,
        row: int,
        *,
        reason: str = "parameter NA",
        dimensions: tuple[str, ...] = (),
    ) -> str:
        """Create one V3.4 body-local parameter used by integration wiring."""
        candidate = requested_name.upper()
        if not IDENTIFIER_RE.fullmatch(candidate):
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: parameter NA 名称 "
                f"{requested_name!r} 不是合法 Verilog 标识符",
                code="E_NA_TARGET",
            )
            candidate = unique_name("LOCAL_PARAMETER_NA", used_top_parameter_names).upper()
        elif candidate in top.parameters:
            candidate = unique_name(
                f"LOCAL_NA_{candidate}", used_top_parameter_names
            ).upper()
        elif candidate in integration_local_parameters:
            if (
                integration_local_parameters[candidate] == value
                and integration_local_parameter_dimensions[candidate] == dimensions
            ):
                return candidate
            candidate = unique_name(
                f"LOCAL_NA_{candidate}", used_top_parameter_names
            ).upper()
        else:
            used_top_parameter_names.add(candidate)
        integration_local_parameters[candidate] = value
        integration_local_parameter_dimensions[candidate] = dimensions
        reporter.info(
            f"集成页签 {sheet.name} 第 {row} 行: 自动在 TOP 语句区创建 "
            f"localparam {candidate}={value}（{reason}）",
            code="I_PARAMETER_LINK",
        )
        return candidate

    def parameter_na_array_value(
        raw_value: str,
        fallback_value: str,
        row: int,
        expected_count: int | None,
    ) -> str:
        """Validate and render a SystemVerilog assignment-pattern initializer."""
        context = f"集成页签 {sheet.name} 第 {row} 行 parameter NA[i]"
        value = clean(raw_value)
        if not value:
            normalized = normalize_parameter_expression(
                fallback_value, context, reporter
            )
            return f"'{{default: {normalized or '0'}}}"
        wrapped = False
        if value.startswith("'{") and value.endswith("}"):
            wrapped = True
            body = value[2:-1]
        elif value.startswith("{") and value.endswith("}"):
            # Production workbooks often omit the assignment-pattern apostrophe.
            # A surrounding brace pair is unambiguous in this dedicated field.
            wrapped = True
            body = value[1:-1]
        else:
            body = value

        parts: list[str] = []
        start = 0
        delimiters: list[str] = []
        matching_open = {")": "(", "]": "[", "}": "{"}
        for index, character in enumerate(body):
            if character in "([{":
                delimiters.append(character)
            elif character in ")]}" and delimiters:
                if delimiters[-1] == matching_open[character]:
                    delimiters.pop()
            elif character == "," and not delimiters:
                parts.append(body[start:index].strip())
                start = index + 1
        parts.append(body[start:].strip())

        is_default_pattern = bool(
            wrapped and re.match(r"(?i)^\s*default\s*:", body)
        )
        positional = len(parts) > 1 or (wrapped and not is_default_pattern)
        if positional:
            empty_count = sum(not part for part in parts)
            if empty_count:
                reporter.warning(
                    f"{context}: 初始化列表含 {empty_count} 个空项，已忽略；"
                    "请确认逗号位置符合预期",
                    code="W_PARAMETER_NA_REPAIR",
                )
                parts = [part for part in parts if part]
            if not parts:
                normalized_fallback = normalize_parameter_expression(
                    fallback_value, context, reporter
                )
                return f"'{{default: {normalized_fallback or '0'}}}"
            if expected_count is not None and len(parts) != expected_count:
                reporter.error(
                    f"{context}: 初始化列表有效元素数 {len(parts)} 与例化次数 "
                    f"{expected_count} 不一致",
                    code="E_PARAMETER",
                )
            normalized_parts = [
                normalize_parameter_expression(part, context, reporter)
                for part in parts
            ]
            if not all(normalized_parts):
                return "'{default: '0}"
            return "'{" + ",".join(normalized_parts) + "}"

        normalized = normalize_parameter_expression(body, context, reporter)
        if not normalized:
            return "'{default: '0}"
        if wrapped:
            return f"'{{{normalized}}}"
        return f"'{{default: {normalized}}}"

    def configured_instance_count_expression(child_key: str, row: int) -> str:
        child = child_for_key(child_key)
        configured = instance_spec_for_key(child_key)
        if configured is not None and configured.raw_count:
            if configured.count is not None:
                return str(configured.count)
            macro_match = MACRO_RE.fullmatch(configured.raw_count)
            if macro_match is not None:
                return configured.raw_count
            if IDENTIFIER_RE.fullmatch(configured.raw_count):
                return configured.raw_count.upper()
        reporter.warning(
            f"集成页签 {sheet.name} 第 {row} 行: parameter NA 索引无法找到 "
            f"{child.module_name} 的例化次数，使用 1",
            code="W_GENERATE_RANGE",
        )
        return "1"

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
            direct_entries: list[tuple[IntegrationBlock, Module, str, str]] = []
            na_entries: list[tuple[IntegrationBlock, str | None, str | None, str]] = []
            na_endpoint_values: list[
                tuple[IntegrationBlock, Module, str, str]
            ] = []
            row_has_na = any(
                parse_na_connection(clean(sheet.cell(row, block.port_column)))
                is not None
                for block in group
            )
            for block in group:
                raw_reference = clean(sheet.cell(row, block.port_column))
                if not raw_reference:
                    continue
                na_reference = parse_na_connection(raw_reference)
                if na_reference is not None:
                    if block.module_name == top.name:
                        reporter.error(
                            f"集成页签 {sheet.name} 第 {row} 行: parameter 行最左侧 "
                            "是 TOP 来源，不能填写 NA",
                            code="E_PARAMETER",
                        )
                        continue
                    raw_value = (
                        clean(sheet.cell(row, block.direction_column))
                        if block.direction_column
                        else ""
                    )
                    na_entries.append((block, *na_reference, raw_value))
                    continue
                if block.anonymous_na:
                    continue
                reference = raw_reference.upper()
                module = modules.get(block.module_name)
                if module is None:
                    continue
                if reference not in module.parameters:
                    if reference in module.disabled_parameters:
                        reporter.info(
                            f"集成页签 {sheet.name} 第 {row} 行: parameter "
                            f"{block.module_name}.{reference} 已由模块页 *注释* 停用，"
                            "传参已忽略",
                            code="I_ROW_COMMENTED",
                        )
                        continue
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: "
                        f"{block.module_name} 没有 parameter {reference}",
                        code="E_PARAMETER",
                    )
                    continue
                raw_direct_value = clean(sheet.cell(row, block.direction_column))
                if row_has_na and raw_direct_value:
                    # With a parameter NA endpoint, i/o is initializer data for
                    # the generated localparam rather than a second override.
                    entries.append((block, module, reference))
                    na_endpoint_values.append(
                        (block, module, reference, raw_direct_value)
                    )
                    continue
                direct_value = direct_parameter_expression(raw_direct_value)
                if direct_value is not None:
                    direct_entries.append((block, module, reference, direct_value))
                    continue
                if raw_direct_value:
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: parameter "
                        f"{module.name}.{reference} 的 i/o={raw_direct_value!r} "
                        "必须留空、填写自然数或填写反引号宏",
                        code="E_PARAMETER",
                    )
                    continue
                entries.append((block, module, reference))

            child_entries = [
                entry for entry in entries if entry[1].name != top.name
            ]
            if na_entries:
                if len(na_entries) != 1:
                    labels = ", ".join(
                        clean(sheet.cell(row, item[0].port_column))
                        for item in na_entries
                    )
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: parameter 行只能有一个 "
                        f"NA 来源 ({labels})",
                        code="E_PARAMETER",
                    )
                    continue
                if not child_entries:
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: parameter NA 没有 "
                        "可连接的子模块 parameter",
                        code="E_PARAMETER",
                    )
                    continue
                _na_block, na_index, na_target, raw_na_value = na_entries[0]
                _, first_module, first_name = child_entries[0]
                value_sources: list[tuple[str, str]] = []
                if raw_na_value:
                    value_sources.append(("NA 端点 i/o", raw_na_value))
                value_sources.extend(
                    (
                        f"{module.name}.{name} 的 i/o",
                        raw_value,
                    )
                    for _, module, name, raw_value in na_endpoint_values
                    if raw_value
                )
                distinct_values = list(
                    dict.fromkeys(clean(value) for _, value in value_sources)
                )
                if len(distinct_values) > 1:
                    details = "；".join(
                        f"{label}={value!r}" for label, value in value_sources
                    )
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: parameter NA 存在"
                        f"多个不一致的初始化来源（{details}）；请只保留一个值，"
                        "或让各 i/o 内容完全一致",
                        code="E_PARAMETER",
                    )
                    continue
                explicit_value = distinct_values[0] if distinct_values else ""
                top_entries = [
                    entry for entry in entries if entry[1].name == top.name
                ]
                if len(top_entries) > 1 and not explicit_value:
                    names = ", ".join(entry[2] for entry in top_entries)
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: parameter NA 同时"
                        f"连接多个 TOP parameter（{names}），无法确定初始化来源",
                        code="E_PARAMETER",
                    )
                    continue
                top_source = top_entries[0][2] if top_entries else ""
                initializer_value = explicit_value or top_source
                if na_target is None:
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: parameter NA 必须使用 "
                        "NA->名称、NA[index]->名称或 NA->数字",
                        code="E_PARAMETER",
                    )
                    continue
                target_number = natural_number_text(na_target)
                if target_number is not None:
                    local_base = first_name
                    local_value = target_number
                    local_dimensions: tuple[str, ...] = ()
                    if initializer_value:
                        reporter.info(
                            f"集成页签 {sheet.name} 第 {row} 行: NA->{target_number} "
                            "已直接指定常量，其他 parameter/i/o 来源不参与赋值",
                            code="I_PARAMETER_LINK",
                        )
                else:
                    local_base = na_target
                    if na_index is not None:
                        child_key = block_key(child_entries[0][0])
                        count_expression = configured_instance_count_expression(
                            child_key, row
                        )
                        fallback_value = first_module.parameters[first_name]
                        local_value = parameter_na_array_value(
                            initializer_value,
                            fallback_value,
                            row,
                            evaluate_int_expression(count_expression),
                        )
                        local_dimensions = (
                            f"[{count_expression} -1:0]",
                            f"[{PARAMETER_NA_ELEMENT_WIDTH} -1:0]",
                        )
                    elif initializer_value:
                        local_value = normalize_parameter_expression(
                            initializer_value,
                            f"集成页签 {sheet.name} 第 {row} 行 parameter NA",
                            reporter,
                        )
                        if not local_value:
                            continue
                        local_dimensions = ()
                    else:
                        local_value = str(UNKNOWN_WIDTH)
                        local_dimensions = ()
                local_name = add_integration_local_parameter(
                    local_base,
                    local_value,
                    row,
                    dimensions=local_dimensions,
                )
                for child_block, module, name in child_entries:
                    child_key = block_key(child_block)
                    instance_value = (
                        f"{local_name}[{generated_indices[child_key]}]"
                        if na_index is not None
                        else local_name
                    )
                    if na_index is not None:
                        previous_index = parameter_na_generate_indices.get(child_key)
                        if previous_index is not None and previous_index != na_index:
                            reporter.error(
                                f"集成页签 {sheet.name} 第 {row} 行: "
                                f"{module.name} 的 parameter NA 同时使用 "
                                f"[{previous_index}] 和 [{na_index}]",
                                code="E_GENERATE_INDEX",
                            )
                        parameter_na_generate_counts[child_key] = count_expression
                        parameter_na_generate_indices[child_key] = na_index
                    instance_parameter_maps[child_key][name] = instance_value
                    parameter_maps[child_key][name] = local_name
                    wire_parameter_overrides[child_key][name] = local_name
                    module.externally_configurable_parameters.add(name)
                continue

            top_direct = next(
                (item for item in direct_entries if item[1].name == top.name), None
            )
            for block, module, name, direct_value in direct_entries:
                if module.name == top.name:
                    reporter.info(
                        f"集成页签 {sheet.name} 第 {row} 行: TOP parameter "
                        f"{top.name}.{name} 的 i/o={direct_value} 作为本行直接来源",
                        code="I_PARAMETER_LINK",
                    )
                    continue
                child_key = block_key(block)
                instance_parameter_maps[child_key][name] = direct_value
                parameter_maps[child_key][name] = direct_value
                if natural_number_text(direct_value) is not None:
                    wire_parameter_overrides[child_key][name] = (
                        add_integration_local_parameter(
                            name,
                            direct_value,
                            row,
                            reason="数字 parameter 位宽来源",
                        )
                    )
                module.externally_configurable_parameters.add(name)
                reporter.info(
                    f"集成页签 {sheet.name} 第 {row} 行: parameter "
                    f"{module.name}.{name} 直接传入 {direct_value}",
                    code="I_PARAMETER_LINK",
                )

            if top_direct is not None:
                source_value = top_direct[3]
                for child_block, module, name in child_entries:
                    child_key = block_key(child_block)
                    instance_parameter_maps[child_key][name] = source_value
                    parameter_maps[child_key][name] = source_value
                    if natural_number_text(source_value) is not None:
                        wire_parameter_overrides[child_key][name] = (
                            add_integration_local_parameter(
                                name,
                                source_value,
                                row,
                                reason="TOP 数字 parameter 位宽来源",
                            )
                        )
                    module.externally_configurable_parameters.add(name)
                continue

            remaining_entries = [
                entry
                for entry in entries
                if entry[1].name == top.name
                or entry[2] not in instance_parameter_maps[block_key(entry[0])]
            ]
            if len(remaining_entries) < 2:
                if remaining_entries:
                    block, _, name = remaining_entries[0]
                    reporter.info(
                        f"集成页签 {sheet.name} 第 {row} 行: parameter "
                        f"{block.module_name}.{name} 没有链接对端，保持 local",
                        code="I_PARAMETER_LINK",
                    )
                continue

            top_entry = next(
                (
                    entry
                    for entry in remaining_entries
                    if entry[0].module_name == top.name
                ),
                None,
            )
            if top_entry is not None:
                _, source_module, source_name = top_entry
                local_name = source_name
                local_value = source_module.parameters[source_name]
            else:
                _, source_module, source_name = remaining_entries[0]
                local_name = add_top_local_parameter(
                    source_name, source_module, source_name, row
                )
                local_value = top.parameters[local_name]

            for child_block, module, name in remaining_entries:
                if module.name == top.name:
                    continue
                child_key = block_key(child_block)
                instance_parameter_maps[child_key][name] = local_name
                parameter_maps[child_key][name] = local_name
                source_expression = source_module.parameter_expressions.get(
                    source_name, ""
                )
                if (
                    source_module.name == top.name
                    and not source_expression
                    and natural_number_text(local_value) is not None
                    and name != local_name
                ):
                    wire_parameter_overrides[child_key][name] = (
                        add_integration_local_parameter(
                            name,
                            local_name,
                            row,
                            reason="TOP 数字 parameter 位宽来源",
                        )
                    )
                module.externally_configurable_parameters.add(name)
                if module.parameters[name] != local_value:
                    reporter.info(
                        f"集成页签 {sheet.name} 第 {row} 行: {module.name}.{name} "
                        f"匹配值 {module.parameters[name]} 由 TOP localparam "
                        f"{local_name}={local_value} 覆盖",
                        code="I_PARAMETER_LINK",
                    )

    bindings: dict[str, dict[str, Binding]] = {
        child.key: {} for child in child_instances
    }
    generate_specs: dict[str, GenerateSpec] = {}
    top_output_drivers: dict[str, list[str]] = {}
    top_output_driver_conditions: dict[str, list[str | None]] = {}
    wires: list[Wire] = []
    adapter_assignments: list[Assignment] = []
    used_signals = set(top_ports)
    named_na_wires: dict[str, Wire] = {}
    def configured_count_width(child_key: str) -> Width | None:
        child = child_for_key(child_key)
        configured = instance_spec_for_key(child_key)
        if configured is None or configured.raw_count is None:
            return None
        if configured.count is not None:
            value = str(configured.count)
            return Width("literal", value, value)
        macro_match = MACRO_RE.fullmatch(configured.raw_count)
        if macro_match:
            name = macro_match.group(1)
            default = all_macros.get(name, "")
            if not default:
                reporter.warning(
                    f"集成模块: {child.module_name} 的例化次数使用宏 `{name}，"
                    "但当前层次没有可用于范围检查的宏数值",
                    code="W_GENERATE_RANGE",
                )
            return Width("macro", f"`{name}", default)
        if IDENTIFIER_RE.fullmatch(configured.raw_count):
            name = configured.raw_count.upper()
            default = top.parameters.get(name, "")
            if not default:
                reporter.warning(
                    f"集成模块: {child.module_name} 的例化次数使用 parameter {name}，"
                    "但该 parameter 没有被拉到 TOP",
                    code="W_PARAMETER_NOT_EXPORTED",
                )
            return Width("parameter", name, default)
        return None

    configured_count_widths = {
        child.key: width
        for child in child_instances
        if (width := configured_count_width(child.key)) is not None
    }

    def indexed_dimension(port: Port) -> Width | None:
        dimensions = (
            (*port.arrays, *port.packed_dimensions, port.width)
            if not port.is_interface
            else port.arrays
        )
        return dimensions[0] if dimensions else None

    warned_auto_local_parameters: set[tuple[str, str]] = set()

    auto_local_parameters: dict[tuple[str, str], str] = {}

    def ensure_top_local_parameter(
        child_key: str,
        name: str,
        row: int,
        default_hint: str = "",
    ) -> str:
        """Create a body-local TOP constant for one child-owned width."""
        cache_key = (child_key, name)
        if cache_key in auto_local_parameters:
            return auto_local_parameters[cache_key]
        module = child_modules[child_key]
        declared_by_child = name in module.parameters
        default = module.parameters.get(name) or default_hint or str(UNKNOWN_WIDTH)
        if name in top.parameters and top.parameters[name] == default:
            # A same-name/same-value TOP declaration is already in scope. Reuse
            # it instead of creating a shadowing body localparam.
            candidate = name
        else:
            requested_name = name
            if (
                name in integration_local_parameters
                and integration_local_parameters[name] != default
            ):
                requested_name = f"{module.name}_{name}"
            candidate = add_integration_local_parameter(
                requested_name,
                default,
                row,
                reason=f"{module.name}.{name} 未显式链接的位宽 parameter",
            )
        if declared_by_child:
            instance_parameter_maps[child_key].setdefault(name, candidate)
        parameter_maps[child_key][name] = candidate
        if declared_by_child:
            module.externally_configurable_parameters.add(name)
        auto_local_parameters[cache_key] = candidate
        location = (
            "现有 TOP 参数声明"
            if candidate in top.parameters
            else "TOP 语句区 localparam"
        )
        reporter.info(
            f"集成页签 {sheet.name} 第 {row} 行: {location} "
            f"{candidate}={default} 用于 {module.name}.{name}，并同步实例传参",
            code="I_PARAMETER_LINK",
        )
        return candidate

    def report_auto_local_parameter(child_key: str, name: str, row: int) -> None:
        key = (child_key, name)
        if key in warned_auto_local_parameters:
            return
        module = child_modules[child_key]
        if (
            name in top.parameters
            and top.parameters[name] == module.parameters.get(name)
        ):
            return
        warned_auto_local_parameters.add(key)
        reporter.warning(
            f"集成页签 {sheet.name} 第 {row} 行: {module.name}.{name} "
            "parameter 没有显式链接；已按数值列在 TOP 语句区创建 localparam",
            code="W_PARAMETER_AUTO_LOCAL",
        )

    def register_generate_marker(
        child_key: str,
        index: str,
        indexed_port: Port,
        row: int,
    ) -> int | None:
        spec = generate_specs.setdefault(child_key, GenerateSpec(index))
        child = child_for_key(child_key)
        if spec.index != index:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: {child.module_name} 同时使用了 "
                f"[{spec.index}] 和 [{index}]，无法生成同一个循环",
                code="E_GENERATE_INDEX",
            )
            return None
        first = indexed_dimension(indexed_port)
        if first is None:
            return None
        expression = first.expression
        if first.kind == "parameter" and child_key != top.name:
            mapped = instance_parameter_maps.get(child_key, {}).get(expression)
            if mapped is not None:
                expression = mapped
            else:
                report_auto_local_parameter(child_key, expression, row)
                expression = ensure_top_local_parameter(
                    child_key, expression, row, first.default
                )
        spec.extent_expressions.append(expression)
        extent = evaluate_int_expression(first.default or first.expression)
        if extent is not None:
            spec.extents.append(extent)
        return extent

    def wire_parameter_map(
        child_key: str, module: Module, port: Port, row: int
    ) -> dict[str, str]:
        """Map child-owned parameter dimensions into the generated TOP scope."""
        result: dict[str, str] = {}
        explicit_map = instance_parameter_maps.get(child_key, {})
        width_overrides = wire_parameter_overrides.get(child_key, {})
        for dimension in port_dimensions(port):
            if dimension.kind != "parameter":
                continue
            name = dimension.expression
            if module.name == top.name:
                result[name] = name
                continue
            width_override = width_overrides.get(name)
            if width_override is not None:
                result[name] = width_override
                continue
            mapped = explicit_map.get(name)
            if mapped is not None:
                if MACRO_RE.fullmatch(mapped) is not None:
                    # Macros are global and therefore remain the most direct
                    # expression in TOP scope.
                    result[name] = mapped
                elif natural_number_text(mapped) is not None:
                    # A direct numeric instance override must not freeze the
                    # generated TOP wire width.  Keep the child's parameter
                    # name as requested by the V3.4 source-tracing contract.
                    result[name] = name
                elif mapped in top.parameters:
                    top_expression = top.parameter_expressions.get(mapped, "")
                    if MACRO_RE.fullmatch(top_expression) is not None:
                        result[name] = top_expression
                    elif (
                        not top_expression
                        and natural_number_text(top.parameters[mapped]) is not None
                    ):
                        result[name] = name
                    else:
                        result[name] = mapped
                else:
                    # Includes V3.4 body-local parameters created by NA.
                    result[name] = mapped
                continue
            result[name] = name
            report_auto_local_parameter(child_key, name, row)
            result[name] = ensure_top_local_parameter(
                child_key, name, row, dimension.default
            )
        return result

    def add_assignment(
        target: str,
        expression: str,
        *conditions: str | None,
        requires_todo: bool = False,
    ) -> None:
        assignment = Assignment(
            target,
            expression,
            tuple(dict.fromkeys(item for item in conditions if item is not None)),
            requires_todo,
        )
        if assignment not in adapter_assignments:
            adapter_assignments.append(assignment)

    def shapes_match(left: Port, right: Port) -> bool:
        left_shape = comparable_port_shape(left, all_macros)
        right_shape = comparable_port_shape(right, all_macros)
        return left_shape is None or right_shape is None or left_shape == right_shape

    def width_mismatch_code(left: Port, right: Port) -> str:
        return (
            "W_PARAMETER_WIDTH_MISMATCH"
            if port_uses_parameter_width(left) or port_uses_parameter_width(right)
            else "W_WIDTH_MISMATCH"
        )

    def get_port(module_name: str, port_name: str, row: int) -> Port | None:
        module = modules.get(module_name)
        if module is None:
            return None
        port = module.port_map.get(port_name)
        if port is None:
            if port_name in module.disabled_ports:
                reporter.info(
                    f"集成页签 {sheet.name} 第 {row} 行: "
                    f"{module_name}.{port_name} 已由模块页 *注释* 停用，连接已忽略",
                    code="I_ROW_COMMENTED",
                )
                return None
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
        if reference in module.disabled_templates:
            reporter.info(
                f"集成页签 {sheet.name} 第 {row} 行: "
                f"{module_name}.{reference} 已由模块页 *注释* 停用，连接已忽略",
                code="I_ROW_COMMENTED",
            )
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
        child_key: str,
        port: Port,
        expression: str | None,
        row: int,
        extra_conditions: tuple[str | None, ...] = (),
        *,
        requires_todo: bool = False,
    ) -> None:
        if child_key == top.name:
            return
        target = bindings.setdefault(child_key, {})
        module_name = child_modules[child_key].name
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
        child_key = block_key(block)
        if target and is_verilog_constant(target):
            if index:
                register_generate_marker(child_key, index, port, row)
            expression = (
                connection_constant_value(
                    target,
                    port,
                    parameter_maps.get(child_key),
                    reporter=reporter,
                    context=(
                        f"集成页签 {sheet.name} 第 {row} 行: "
                        f"{block.module_name}.{port.name}"
                    ),
                )
                if port.direction == "input"
                else None
            )
            bind(child_key, port, expression, row)
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
            target_conflicts_existing = (
                target in used_signals and target not in named_na_wires
            )
            if target_conflicts_existing:
                reporter.warning(
                    f"集成页签 {sheet.name} 第 {row} 行: NA 自定义名称 "
                    f"{target} 与已有信号重名；已直接复用，不重复声明，"
                    "请检查方向、位宽和多驱动风险",
                    code="W_NA_TARGET_CONFLICT",
                )
            signal_name = target
            used_signals.add(signal_name)
        else:
            target_conflicts_existing = False
            # Keep anonymous NA names independent of module/instance names.
            # Prefer the port name, then an explicit NA prefix.  A worksheet
            # coordinate is only needed for a second real collision; unlike a
            # module-qualified name it remains stable when an instance is
            # renamed and does not imply an RTL hierarchy relationship.
            if port.name not in used_signals:
                base_name = port.name
            elif f"na_{port.name}" not in used_signals:
                base_name = f"na_{port.name}"
            else:
                base_name = f"na_{port.name}_r{row}_c{block.port_column}"
            signal_name = unique_name(base_name, used_signals)
        placeholder_arrays = port.arrays
        expression = signal_name
        if index:
            extent = register_generate_marker(child_key, index, port, row)
            count_width = configured_count_widths.get(child_key)
            if count_width is None:
                count = extent or 1
                count_width = Width("literal", str(count), str(count))
            placeholder_arrays = (
                count_width,
                *placeholder_arrays,
            )
            expression += f"[{generated_indices[child_key]}]"
        placeholder_wire = Wire(
            name=signal_name,
            width=port.width,
            arrays=placeholder_arrays,
            parameter_map=wire_parameter_map(
                child_key, modules[block.module_name], port, row
            ),
            interface_type=(
                port.interface_type.rsplit(".", 1)[0]
                if port.interface_type
                else None
            ),
            packed_dimensions=port.packed_dimensions,
        )
        existing_placeholder = named_na_wires.get(signal_name) if target else None
        if existing_placeholder is None:
            if not target_conflicts_existing:
                wires.append(placeholder_wire)
                if target:
                    named_na_wires[signal_name] = placeholder_wire
        else:
            existing_shape = (
                existing_placeholder.interface_type,
                tuple(item.effective for item in existing_placeholder.arrays),
                tuple(
                    item.effective
                    for item in existing_placeholder.packed_dimensions
                ),
                existing_placeholder.width.effective,
            )
            requested_shape = (
                placeholder_wire.interface_type,
                tuple(item.effective for item in placeholder_wire.arrays),
                tuple(item.effective for item in placeholder_wire.packed_dimensions),
                placeholder_wire.width.effective,
            )
            if existing_shape != requested_shape:
                reporter.warning(
                    f"集成页签 {sheet.name} 第 {row} 行: NA->{signal_name} "
                    "重复目标的位宽/维度与首次创建不同；复用首次声明，请人工确认",
                    code="W_WIDTH_MISMATCH",
                )
        bind(
            child_key,
            port,
            expression,
            row,
            requires_todo=True,
        )
        na_label = f"NA[{index}]" if index else "NA"
        if target:
            na_label += f"->{target}"
        if target_conflicts_existing:
            na_action = f"已复用已有信号 {signal_name} 并加入 TODO"
        elif existing_placeholder is not None:
            na_action = f"已复用命名占位信号 {signal_name} 并加入 TODO"
        else:
            na_action = f"已创建 {signal_name} 占位信号并加入 TODO"
        reporter.info(
            f"集成页签 {sheet.name} 第 {row} 行: "
            f"{block.module_name}.{port.name} 连接到 {na_label}，"
            f"{na_action}",
            code="I_NA_CONNECTION",
        )

    def bind_top_na_observer(
        port: Port,
        row: int,
        target: str,
        bit_select: str | None,
    ) -> None:
        """Create a named wire that observes one real TOP endpoint."""
        if port.is_interface:
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: TOP interface "
                f"{top.name}.{port.name} 不支持 NA->{target} 命名观察 wire",
                code="E_INTERFACE_CONNECTION",
            )
            return
        if not IDENTIFIER_RE.fullmatch(target):
            reporter.error(
                f"集成页签 {sheet.name} 第 {row} 行: NA 自定义名称 "
                f"{target!r} 不是合法 Verilog 标识符",
                code="E_NA_TARGET",
            )
            return
        target_conflicts_existing = (
            target in used_signals and target not in named_na_wires
        )
        if target_conflicts_existing:
            reporter.warning(
                f"集成页签 {sheet.name} 第 {row} 行: NA 自定义名称 "
                f"{target} 与已有信号重名；已直接复用，不重复声明，"
                "请检查方向、位宽和多驱动风险",
                code="W_NA_TARGET_CONFLICT",
            )
        used_signals.add(target)
        observed_port = port
        observed_expression = port.name
        if bit_select is not None:
            observed_port = replace(
                port,
                width=Width("literal", "1", "1"),
                arrays=(),
                packed_dimensions=(),
            )
            observed_expression += bit_select
        observer_wire = Wire(
            name=target,
            width=observed_port.width,
            arrays=observed_port.arrays,
            parameter_map=parameter_maps.get(top.name, {}),
            packed_dimensions=observed_port.packed_dimensions,
        )
        if target not in named_na_wires and not target_conflicts_existing:
            wires.append(observer_wire)
            named_na_wires[target] = observer_wire
        elif target in named_na_wires:
            reporter.warning(
                f"集成页签 {sheet.name} 第 {row} 行: NA->{target} 已存在；"
                "复用同名观察 wire，请确认多个来源不会形成多驱动",
                code="W_DRIVER_RISK",
            )
        add_assignment(
            target,
            observed_expression,
            port.condition,
            requires_todo=True,
        )
        reporter.info(
            f"集成页签 {sheet.name} 第 {row} 行: TOP 端口 "
            f"{top.name}.{port.name} 通过 NA->{target} "
            + (
                "复用已有信号"
                if target_conflicts_existing
                else "创建命名观察 wire"
            ),
            code="I_NA_CONNECTION",
        )

    first_group = integration.groups[0]
    top_block = first_group[0]
    first_group_child_blocks = [
        block for block in first_group[1:] if not block.anonymous_na
    ]
    first_group_na_blocks = [block for block in first_group if block.anonymous_na]
    for row in range(integration.header_row + 1, sheet.max_row + 1):
        if skip_commented_integration_row(row):
            continue
        if row in parameter_rows_by_group[0]:
            continue
        top_reference = sheet.cell(row, top_block.port_column)
        top_na_reference = parse_na_connection(top_reference)
        top_port_name = ""
        top_bit_select: str | None = None
        top_bit_index: int | None = None
        top_index: str | None = None
        if top_na_reference is None:
            top_port_name, top_bit_select, top_bit_index = split_bit_select(
                top_reference
            )
            if top_bit_select is None:
                top_port_name, top_index = split_index_marker(top_reference)
        row_entries: list[tuple[IntegrationBlock, str, str | None]] = []
        row_na_entries: list[tuple[IntegrationBlock, str | None, str | None]] = []
        if top_na_reference is not None:
            row_na_entries.append((top_block, *top_na_reference))
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
                f"集成页签 {sheet.name} 第 {row} 行: TOP 端口为空或为 NA，"
                "子模块端口按占位/未连接处理",
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
                            child_port, parameter_maps.get(block_key(block))
                        )
                        if child_port.direction == "input"
                        else None
                    )
                    bind(block_key(block), child_port, expression, row)
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
        row_markers = {block_key(top_block): top_index}
        row_markers.update(
            {block_key(block): marker for block, _, marker in row_entries}
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
                        block_key(block),
                        child_port,
                        connection_zero_value(
                            child_port, parameter_maps.get(block_key(block))
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
                    bind_top_na_observer(
                        top_port,
                        row,
                        row_na_target,
                        top_bit_select,
                    )
                elif top_port.direction != "output":
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: 只有 TOP output "
                        f"可以由 NA->{row_na_target} 赋值，{top.name}.{top_port.name} "
                        f"为 {top_port.direction}",
                        code="E_NA_TARGET",
                    )
                else:
                    top_constant = connection_constant_value(
                        row_na_target,
                        top_port,
                        parameter_maps.get(top.name),
                        reporter=reporter,
                        context=(
                            f"集成页签 {sheet.name} 第 {row} 行: "
                            f"{top.name}.{top_port.name}"
                        ),
                    )
                    add_assignment(top_port.name, top_constant, top_port.condition)
                    top_output_driver_conditions.setdefault(top_port.name, []).append(None)
                    reporter.info(
                        f"集成页签 {sheet.name} 第 {row} 行: TOP 输出 "
                        f"{top.name}.{top_port.name} 已由 NA->{row_na_target} 赋值",
                        code="I_NA_CONNECTION",
                    )
                    for block, child_port in aligned[1:]:
                        validate_sheet_direction(block, child_port, row)
                        expression = (
                            connection_constant_value(
                                row_na_target,
                                child_port,
                                parameter_maps.get(block_key(block)),
                                reporter=reporter,
                                context=(
                                    f"集成页签 {sheet.name} 第 {row} 行: "
                                    f"{block.module_name}.{child_port.name}"
                                ),
                            )
                            if child_port.direction == "input"
                            else None
                        )
                        bind(block_key(block), child_port, expression, row)
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
                child_width = connection_numeric_width(child_port, all_macros)
                parameter_width_adapter = (
                    AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH
                    and (
                        port_uses_parameter_width(top_port)
                        or port_uses_parameter_width(child_port)
                    )
                )
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
                        code=width_mismatch_code(top_port, child_port),
                    )
                child_key = block_key(block)
                child_index = row_markers.get(child_key)
                marker = top_index or child_index
                if top_index and child_index and top_index != child_index:
                    reporter.error(
                        f"集成页签 {sheet.name} 第 {row} 行: {top.name}.{top_port.name} "
                        f"与 {block.module_name}.{child_port.name} 使用不同索引指示符",
                        code="E_GENERATE_INDEX",
                    )
                if marker:
                    register_generate_marker(
                        child_key,
                        marker,
                        top_port if top_index else child_port,
                        row,
                    )
                expression = top_port.name
                if top_bit_select is not None:
                    expression += top_bit_select
                elif top_index and marker:
                    expression += f"[{generated_indices[child_key]}]"
                top_width = (
                    1
                    if top_bit_select is not None
                    else connection_numeric_width(top_port, all_macros)
                )
                if (
                    mismatch
                    and top_width is not None
                    and top_width > 0
                    and child_width is not None
                    and child_width > 0
                    and child_port.direction == "input"
                ):
                    expression = fit_source_width(
                        expression,
                        top_width,
                        child_width,
                        sized_zero_fill=parameter_width_adapter,
                    )
                elif (
                    mismatch
                    and top_width is not None
                    and top_width > 0
                    and child_width is not None
                    and child_width > 0
                    and child_port.direction == "output"
                    and top_port.direction in {"output", "inout"}
                ):
                    if child_width < top_width:
                        expression = low_bits(top_port.name, child_width)
                        add_assignment(
                            f"{top_port.name}[{top_width}-1:{child_width}]",
                            (
                                f"{top_width - child_width}'b0"
                                if parameter_width_adapter
                                else "'0"
                            ),
                            top_port.condition,
                            child_port.condition,
                        )
                    elif child_width > top_width:
                        adapter_name = unique_name(
                            f"w_{child_port.name}_adapter",
                            used_signals,
                        )
                        wires.append(
                            Wire(
                                adapter_name,
                                child_port.width,
                                (),
                                wire_parameter_map(
                                    child_key,
                                    modules[block.module_name],
                                    child_port,
                                    row,
                                ),
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
                    child_key,
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
        for row in range(integration.header_row + 1, sheet.max_row + 1):
            if skip_commented_integration_row(row):
                continue
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
                                port, parameter_maps.get(block_key(block))
                            )
                            if port.direction == "input"
                            else None
                        )
                        bind(block_key(block), port, expression, row)
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
                        code=width_mismatch_code(source_port_for_warning, port),
                    )
                signal_base = source_port.name
                signal_name = unique_name(f"w_{signal_base}", used_signals)
                numeric_widths = [
                    connection_numeric_width(port, all_macros)
                    for _, port in entries
                ]
                can_resize = all(width is not None for width in numeric_widths)
                maximum_width = (
                    max(width for width in numeric_widths if width is not None)
                    if can_resize
                    else None
                )
                parameter_width_network = (
                    AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH
                    and any(port_uses_parameter_width(port) for _, port in entries)
                )
                driver_width: int | None = None
                parameter_inline_adapter = False
                if len(drivers) == 1 and can_resize:
                    driver_index = entries.index(drivers[0])
                    driver_width = numeric_widths[driver_index]
                    parameter_inline_adapter = (
                        parameter_width_network
                        and driver_width is not None
                        and driver_width > 0
                        and all(
                            item_width is not None and item_width > 0
                            for item_width in numeric_widths
                        )
                        and all(
                            item_block.module_name != top.name
                            and (
                                (item_block, port) == drivers[0]
                                or port.direction == "input"
                                or (
                                    item_width is not None
                                    and item_width <= driver_width
                                )
                            )
                            for (item_block, port), item_width in zip(
                                entries, numeric_widths
                            )
                        )
                    )
                wire_block, wire_shape_port = width_source
                if maximum_width is not None and not parameter_inline_adapter:
                    source_index = entries.index(width_source)
                    if numeric_widths[source_index] != maximum_width:
                        maximum_index = numeric_widths.index(maximum_width)
                        wire_block, wire_shape_port = entries[maximum_index]
                wire_shape_parameter_map = wire_parameter_map(
                    block_key(wire_block),
                    modules[wire_block.module_name],
                    wire_shape_port,
                    row,
                )
                wires.append(
                    Wire(
                        name=signal_name,
                        width=wire_shape_port.width,
                        arrays=wire_shape_port.arrays,
                        parameter_map=wire_shape_parameter_map,
                        interface_type=(
                            wire_shape_port.interface_type.rsplit(".", 1)[0]
                            if wire_shape_port.interface_type
                            else None
                        ),
                        packed_dimensions=wire_shape_port.packed_dimensions,
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
                        parameter_inline_adapter
                        and driver_width is not None
                        and item_width is not None
                    ):
                        expression = fit_source_width(
                            signal_name,
                            driver_width,
                            item_width,
                            sized_zero_fill=True,
                        )
                    elif (
                        maximum_width is not None
                        and item_width is not None
                        and item_width < maximum_width
                    ):
                        expression = low_bits(signal_name, item_width)
                    bind(block_key(item_block), port, expression, row)

                if not all(interface_flags):
                    if len(drivers) == 0:
                        add_assignment(
                            signal_name,
                            connection_zero_value(
                                wire_shape_port, wire_shape_parameter_map
                            ),
                        )
                    elif (
                        maximum_width is not None
                        and len(drivers) == 1
                        and not parameter_inline_adapter
                    ):
                        driver_block, driver_port = drivers[0]
                        driver_index = entries.index((driver_block, driver_port))
                        driver_width = numeric_widths[driver_index]
                        if driver_width is not None and driver_width < maximum_width:
                            add_assignment(
                                f"{signal_name}[{maximum_width}-1:{driver_width}]",
                                (
                                    f"{maximum_width - driver_width}'b0"
                                    if parameter_width_network
                                    else "'0"
                                ),
                                driver_port.condition,
                            )

    # Named-port instantiations may omit ports, but explicitly tie/open all omitted
    # child ports to make the generated integration deterministic and lint-friendly.
    for child in child_instances:
        module = child_modules[child.key]
        for port in module.ports:
            if port.name not in bindings[child.key]:
                reporter.info(
                    f"集成页签 {sheet.name}: 未列出 {module.name}.{port.name}，"
                    "自动按未连接端口处理",
                    code="I_UNCONNECTED",
                )
                bindings[child.key][port.name] = Binding(
                    (
                        connection_zero_value(port, parameter_maps[child.key])
                        if port.direction == "input"
                        else None
                    ),
                    (port.condition,) if port.condition else (),
                )

    generate_counts: dict[str, str] = {}
    for child in child_instances:
        child_key = child.key
        child_name = child.module_name
        marker_spec = generate_specs.get(child_key)
        configured_width = configured_count_widths.get(child_key)
        if configured_width is not None:
            count_expression = configured_width.expression
            numeric_count = evaluate_int_expression(configured_width.default)
            generate_counts[child_key] = count_expression
            reporter.info(
                f"集成模块: {child_name} 使用显式例化次数 {count_expression}",
                code="I_INSTANCE",
            )
            if marker_spec and marker_spec.extents and numeric_count is not None:
                safe_extent = min(marker_spec.extents)
                if numeric_count > safe_extent:
                    reporter.warning(
                        f"集成模块: {child_name} 的例化次数 {count_expression} "
                        f"(匹配值 {numeric_count}) 超过 "
                        f"[{marker_spec.index}] 可解析安全范围 {safe_extent}，存在索引越界风险",
                        code="W_GENERATE_RANGE",
                    )
            continue
        parameter_na_count = parameter_na_generate_counts.get(child_key)
        if parameter_na_count is not None:
            generate_counts[child_key] = parameter_na_count
            reporter.info(
                f"集成模块: {child_name} 的 parameter NA 索引 generate "
                f"使用 {parameter_na_count}",
                code="I_INSTANCE",
            )
            continue
        if marker_spec is None:
            continue
        symbolic_extents = list(dict.fromkeys(marker_spec.extent_expressions))
        if len(symbolic_extents) == 1:
            count_expression = symbolic_extents[0]
            generate_counts[child_key] = count_expression
            reporter.info(
                f"集成模块: {child_name} 的 [{marker_spec.index}] generate "
                f"使用首维表达式 {count_expression}",
                code="I_INSTANCE",
            )
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
        generate_counts[child_key] = str(count)

    lines = render_module_header(top, all_macros, before_module_user=True)
    lines.extend(["", *user_code_block("before statement")])
    if integration_local_parameters:
        lines.append("")
        lines.append("// Integration-local parameters for NA and child-owned widths.")
        lines.append("// parameter NA 及子模块位宽上拉创建的 TOP 语句区局部参数。")
        local_name_width = max(len(name) for name in integration_local_parameters)
        for name, value in integration_local_parameters.items():
            dimensions = "".join(integration_local_parameter_dimensions[name])
            prefix = f"localparam {dimensions} " if dimensions else "localparam "
            lines.append(f"{prefix}{name:<{local_name_width}} = {value};")
    if wires:
        lines.append("")
        lines.append("// Internal connections and NA placeholder signals.")
        lines.append("// 子模块内部连线及 NA 占位信号。")
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
                f"{prefix:<{wire_prefix_width}} {wire.name:<{wire_name_width}}"
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
                + (f" {NA_CONNECTION_TODO}" if assignment.requires_todo else "")
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

    if generate_counts:
        lines.extend(["", "genvar i;"])
    lines.extend(["", *user_code_block("after statement")])

    for child in child_instances:
        module = child_modules[child.key]
        instance_name = instance_names[child.key]
        region_identity = (
            module.name
            if child.key == module.name
            else f"{module.name} {instance_name}"
        )
        lines.extend(["", *user_code_block(f"before {region_identity}")])
        instance_parameter_map = instance_parameter_maps[child.key]
        generate_count = generate_counts.get(child.key)
        commented_instance = child.key in integration.commented_child_names
        if commented_instance:
            lines.append(
                f"/* XLSX2VERILOG COMMENTED MODULE BEGIN: {region_identity}"
            )
        if generate_count is not None:
            index = generated_indices[child.key]
            lines.append("generate")
            lines.append(
                f"for ({index} = 0; {index} < {generate_count}; "
                f"{index} = {index} + 1) begin : G_{instance_name}"
            )
        if instance_parameter_map:
            lines.append(f"{module.name} #(")
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
            lines.append(f"{module.name} {instance_name} (")
        reserved_macros = set(all_macros)
        reserved_macros.update(
            port.condition
            for module in [top, *children]
            for port in module.ports
            if port.condition
        )
        append_instance_connections(
            lines, module, bindings[child.key], reserved_macros
        )
        lines.append(");")
        if generate_count is not None:
            lines.append("end")
            lines.append("endgenerate")
        if commented_instance:
            lines.append(
                f"XLSX2VERILOG COMMENTED MODULE END: {region_identity} */"
            )
        lines.extend(["", *user_code_block(f"after {region_identity}")])
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
    if ONLY_TOP and integration is None:
        reporter.error(
            "ONLY_TOP=True 但工作簿没有选中的有效集成页签",
            code="E_INTEGRATION",
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
            if not ONLY_TOP:
                for module in modules.values():
                    if module.name != top_name:
                        rendered[module.name] = render_stub(module, {})
            # Keep the integration TOP first in the returned path order.
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
        return DiffusionTarget("macro", f"`{macro.group(1)}")
    if IDENTIFIER_RE.fullmatch(text):
        return DiffusionTarget("parameter", text.upper())
    return None


def iter_editable_module_rows(
    workbook: Workbook, reporter: Reporter
) -> Iterable[
    tuple[Sheet, int, dict[str, int], dict[str, list[str]], str | None]
]:
    """Yield physical XLSX module rows while never consulting 修改 columns."""
    for sheet in workbook.sheets:
        header = find_module_header(sheet)
        if header is None:
            continue
        header_row, columns = header
        active_values: dict[str, list[str]] = {}
        active_section_kind: str | None = None
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
                active_section_kind = (
                    "parameter"
                    if is_parameter_category(category)
                    else "macro"
                    if is_macro_category(category)
                    else None
                )
            context = f"页签 {sheet.name} 第 {row} 行"
            active_values.update(template_values_in_row(sheet, row, context, reporter))
            if row_is_commented(sheet, row):
                continue
            yield (
                sheet,
                row,
                columns,
                dict(active_values),
                active_section_kind,
            )


def expanded_factor_targets(
    factor: str,
    domains: dict[str, list[str]],
) -> list[tuple[DiffusionTarget, int, int]]:
    """Return target, expansion index and expansion count for one factor."""
    variables = template_variables(factor)
    if not variables:
        target = canonical_dimension_symbol(preserve_macro_references(factor))
        return [(target, 0, 1)] if target else []
    if any(variable not in domains for variable in variables):
        return []
    combinations = list(itertools.product(*(domains[name] for name in variables)))
    result: list[tuple[DiffusionTarget, int, int]] = []
    for index, combination in enumerate(combinations):
        expansion = dict(zip(variables, combination))
        text = preserve_macro_references(
            substitute_template_expression(factor, expansion)
        )
        target = canonical_dimension_symbol(text)
        if target:
            result.append((target, index, len(combinations)))
    return result


def list_diffusible_variables(path: Path) -> tuple[list[DiffusionTarget], Reporter]:
    reporter = Reporter()
    workbook = XlsxReader().read(path, ignore_review_columns=False)
    found: dict[tuple[str, str], DiffusionTarget] = {}
    for sheet, row, columns, domains, section_kind in iter_editable_module_rows(
        workbook, reporter
    ):
        if section_kind:
            raw_name = clean(sheet.cell(row, columns["port"]))
            if section_kind == "macro" and not raw_name.startswith("`"):
                raw_name = "`" + raw_name
            for target, _, _ in expanded_factor_targets(
                raw_name, domains
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
    is_nonnegative_integer = text.isdigit()
    is_parenthesized = text.startswith("(") and text.endswith(")")
    if not (
        is_nonnegative_integer or is_parenthesized
    ) or evaluate_int_expression(text, allow_zero=True) is None:
        raise ValueError(
            "扩散值必须是非负整数，或可安全计算、结果非负且整体带括号的整数表达式"
        )
    return text


def range_values(value: Any, count: int) -> list[str] | None:
    text = clean(value)
    match = re.search(r"(?:范围\s*(?:是|为|[:：=])\s*)?\{\{?([^{}]+)\}\}?", text)
    if match is None:
        return None
    values = [item.strip() for item in re.split(r"[,，、;；]", match.group(1))]
    return values if len(values) == count and all(values) else None


def seeded_default(factor: str) -> str:
    number = evaluate_int_expression(factor, allow_zero=True)
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
            if evaluate_int_expression(scalar, allow_zero=True) is not None:
                existing = [scalar] * count
            else:
                existing = [str(UNKNOWN_WIDTH)] * count
        else:
            existing = [
                item
                if evaluate_int_expression(item, allow_zero=True) is not None
                else str(UNKNOWN_WIDTH)
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
        selected = DiffusionTarget("macro", f"`{requested[1:]}")
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
    for sheet, row, columns, domains, section_kind in iter_editable_module_rows(
        workbook, Reporter()
    ):
        if section_kind:
            raw_name = sheet.cell(row, columns["port"])
            if section_kind == "macro" and not clean(raw_name).startswith("`"):
                raw_name = "`" + clean(raw_name)
            replacement = spread_default_cell(
                raw_name,
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
        "退出",
    ]
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
        integration_arguments: list[str] = []
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
    return parser


def print_startup_banner(stream: TextIO | None = None) -> None:
    """Print the configured identification block with one shared centered width."""
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
