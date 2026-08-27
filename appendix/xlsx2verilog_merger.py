#!/usr/bin/env python3
"""Safely merge newly generated Verilog into an existing generated project.

The tool is deliberately independent from ``xlsx2verilog.py``.  Its only
shared contract is the textual USER CODE marker format emitted by the main
generator.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Callable, Iterable


VERSION = "Version V3.45"
# 默认生产目标配置：
# 1. 相对路径以“启动 Python 时 terminal 的当前目录”为基准，例如：
#    DEFAULT_TARGET_PROJECT = Path("../../rtl")
# 2. Windows 绝对路径建议用 raw string，避免反斜杠转义，例如：
#    DEFAULT_TARGET_PROJECT = Path(r"D:\project\rtl")
# 3. 保持 None 时，命令行仍必须显式传入 target_project；脚本不会猜路径。
# 配置后可执行：python appendix/xlsx2verilog_merger.py ./generated
DEFAULT_TARGET_PROJECT: str | Path | None = None
DEFAULT_SUFFIXES = frozenset({".v", ".sv", ".vh", ".svh"})
USER_CODE_MARKER_RE = re.compile(
    r"(?m)^[ \t]*/\*USER CODE (BEGIN|END)[ \t]+(.+?)[ \t]*\*/[ \t]*\r?$"
)
VERILOG_COMMENT_RE = re.compile(r"//[^\r\n]*|/\*.*?\*/", re.DOTALL)
MODULE_BEGIN_RE = re.compile(
    r"^[ \t]*module[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_$]*)\b"
)
MODULE_END_RE = re.compile(r"^[ \t]*endmodule\b")
SIGNAL_DECLARATION_RE = re.compile(
    r"^[ \t]*(?:(?:input|output|inout)[ \t]+)?"
    r"(?P<kind>wire|reg)\b(?P<body>[^\r\n]*)"
)
SIGNAL_DECLARATOR_TAIL_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_$]*)"
    r"(?:[ \t]*\[[^\[\]\r\n]+\])*[ \t]*[,;]?[ \t]*$"
)
PARAMETER_DECLARATION_RE = re.compile(
    r"^[ \t]*(?P<kind>localparam|parameter)\b(?P<body>[^\r\n]*)"
)
PARAMETER_NAME_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_$]*)[ \t]*="
)
ASSIGNMENT_RE = re.compile(
    r"^[ \t]*assign[ \t]+(?P<lhs>[^=;\r\n]+?)[ \t]*=[^;\r\n]*;[ \t]*$"
)
USER_LINE_COMMENT_RE = re.compile(
    r"//[ \t]*USER[ \t]*[:：]", re.IGNORECASE
)
# Backward-compatible internal name used by the active-assign scanner.
USER_ASSIGN_COMMENT_RE = USER_LINE_COMMENT_RE
SIMPLE_ASSIGN_TARGET_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_$]*)(?:\[[^\[\]]+\])*$"
)
PORT_CONNECTION_RE = re.compile(
    r"^[ \t]*\.(?P<name>[A-Za-z_][A-Za-z0-9_$]*)"
    r"[ \t]*\(.*\)[ \t]*,?[ \t]*$"
)


class MergeError(ValueError):
    """A validation error that must stop the complete merge transaction."""


@dataclass(frozen=True)
class UserCodeRegion:
    label: str
    occurrence: int
    content_start: int
    content_end: int
    content: str


@dataclass(frozen=True)
class SignalDeclaration:
    module_name: str
    signal_name: str
    kind: str
    kind_start: int
    kind_end: int
    statement_start: int
    statement_end: int


@dataclass(frozen=True)
class ParameterDeclaration:
    module_name: str
    parameter_name: str
    kind: str
    kind_start: int
    kind_end: int
    statement_start: int
    statement_end: int


@dataclass(frozen=True)
class AssignmentStatement:
    module_name: str
    lhs: str
    root_signal: str | None
    statement: str
    statement_start: int
    statement_end: int
    user_owned: bool


@dataclass(frozen=True)
class PortConnection:
    module_name: str
    port_name: str
    statement_start: int
    statement_end: int


@dataclass(frozen=True)
class UserOwnedLine:
    kind: str
    module_name: str
    key: str
    root_signal: str | None
    statement: str


@dataclass(frozen=True)
class Diagnostic:
    level: str
    message: str


@dataclass
class MergeEntry:
    relative_path: Path
    source_path: Path
    target_path: Path
    merged_text: str
    original_bytes: bytes | None
    original_mode: int | None
    changed: bool

    @property
    def created(self) -> bool:
        return self.original_bytes is None


@dataclass
class MergeResult:
    changed: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    created: list[Path] = field(default_factory=list)
    backup_directory: Path | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    check_only: bool = False


def parse_user_code_regions(text: str, context: str) -> list[UserCodeRegion]:
    """Parse and validate every USER CODE region in one Verilog file."""
    markers = list(USER_CODE_MARKER_RE.finditer(text))
    begin_tokens = text.count("/*USER CODE BEGIN")
    end_tokens = text.count("/*USER CODE END")
    matched_begins = sum(match.group(1) == "BEGIN" for match in markers)
    matched_ends = sum(match.group(1) == "END" for match in markers)
    if begin_tokens != matched_begins or end_tokens != matched_ends:
        raise MergeError(f"{context}: USER CODE 标记格式损坏")

    regions: list[UserCodeRegion] = []
    occurrences: dict[str, int] = {}
    active: tuple[str, int] | None = None
    for marker in markers:
        kind = marker.group(1)
        label = marker.group(2).strip()
        if kind == "BEGIN":
            if active is not None:
                raise MergeError(f"{context}: USER CODE 段不允许嵌套")
            active = (label, marker.end())
            continue
        if active is None:
            raise MergeError(f"{context}: USER CODE END 缺少对应 BEGIN")
        begin_label, content_start = active
        if begin_label != label:
            raise MergeError(
                f"{context}: USER CODE BEGIN {begin_label!r} 与 END {label!r} 不匹配"
            )
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
        raise MergeError(
            f"{context}: USER CODE BEGIN {active[0]!r} 缺少对应 END"
        )
    return regions


def _mask_verilog_comments(text: str) -> str:
    """Hide comments without changing character offsets or line boundaries."""

    def replacement(match: re.Match[str]) -> str:
        return "".join(
            character if character in "\r\n" else " "
            for character in match.group(0)
        )

    return VERILOG_COMMENT_RE.sub(replacement, text)


def parse_signal_declarations(
    text: str,
    regions: list[UserCodeRegion],
    context: str,
) -> dict[tuple[str, str], SignalDeclaration]:
    """Find generated ``wire/reg`` declarations keyed by module and signal.

    This is intentionally a conservative line-oriented Verilog recognizer, not
    a compiler.  It covers the ANSI port and one-signal-per-line declarations
    emitted by xlsx2verilog while ignoring protected USER CODE contents.
    """
    masked = _mask_verilog_comments(text)
    protected_ranges = [
        (region.content_start, region.content_end) for region in regions
    ]

    def is_protected(position: int) -> bool:
        return any(start <= position < end for start, end in protected_ranges)

    declarations: dict[tuple[str, str], SignalDeclaration] = {}
    current_module: str | None = None
    offset = 0
    for line in masked.splitlines(keepends=True):
        if is_protected(offset):
            offset += len(line)
            continue
        module_match = MODULE_BEGIN_RE.match(line)
        if module_match is not None:
            if current_module is not None:
                raise MergeError(f"{context}: 检测到嵌套 module 声明")
            current_module = module_match.group("name")
            offset += len(line)
            continue
        if MODULE_END_RE.match(line) is not None:
            current_module = None
            offset += len(line)
            continue
        if current_module is None:
            offset += len(line)
            continue
        declaration_match = SIGNAL_DECLARATION_RE.match(line)
        if declaration_match is None:
            offset += len(line)
            continue
        body = declaration_match.group("body")
        name_match = SIGNAL_DECLARATOR_TAIL_RE.search(body)
        if name_match is None:
            offset += len(line)
            continue
        kind = declaration_match.group("kind")
        signal_name = name_match.group("name")
        key = (current_module, signal_name)
        declaration = SignalDeclaration(
            current_module,
            signal_name,
            kind,
            offset + declaration_match.start("kind"),
            offset + declaration_match.end("kind"),
            offset,
            offset + len(line.rstrip("\r\n")),
        )
        previous = declarations.get(key)
        if previous is not None and previous.kind != declaration.kind:
            raise MergeError(
                f"{context}: {current_module}.{signal_name} 同时声明为 "
                f"{previous.kind} 和 {declaration.kind}，无法安全匹配"
            )
        declarations.setdefault(key, declaration)
        offset += len(line)
    return declarations


def preserve_signal_declaration_kinds(
    new_text: str,
    existing_text: str,
    new_regions: list[UserCodeRegion],
    old_regions: list[UserCodeRegion],
    context: str,
) -> tuple[str, list[Diagnostic]]:
    """Carry old ``wire/reg`` choices into matching new declarations."""
    old_declarations = parse_signal_declarations(
        existing_text,
        old_regions,
        f"旧文件 {context}",
    )
    new_declarations = parse_signal_declarations(
        new_text,
        new_regions,
        f"新文件 {context}",
    )
    replacements: list[tuple[int, int, str]] = []
    diagnostics: list[Diagnostic] = []
    for key, new_declaration in new_declarations.items():
        old_declaration = old_declarations.get(key)
        if old_declaration is None or old_declaration.kind == new_declaration.kind:
            continue
        replacements.append(
            (
                new_declaration.kind_start,
                new_declaration.kind_end,
                old_declaration.kind,
            )
        )
        diagnostics.append(
            Diagnostic(
                "info",
                f"{context}: 保留 {key[0]}.{key[1]} 的 {old_declaration.kind} "
                f"声明类型（新生成版本为 {new_declaration.kind}）",
            )
        )
    preserved = new_text
    for start, end, kind in reversed(replacements):
        preserved = preserved[:start] + kind + preserved[end:]
    return preserved, diagnostics


def parse_parameter_declarations(
    text: str,
    regions: list[UserCodeRegion],
    context: str,
) -> dict[tuple[str, str], ParameterDeclaration]:
    """Find one-line parameter declarations keyed by module and name."""
    masked = _mask_verilog_comments(text)
    protected_ranges = [
        (region.content_start, region.content_end) for region in regions
    ]

    def is_protected(position: int) -> bool:
        return any(start <= position < end for start, end in protected_ranges)

    declarations: dict[tuple[str, str], ParameterDeclaration] = {}
    current_module: str | None = None
    offset = 0
    for line in masked.splitlines(keepends=True):
        if is_protected(offset):
            offset += len(line)
            continue
        module_match = MODULE_BEGIN_RE.match(line)
        if module_match is not None:
            if current_module is not None:
                raise MergeError(f"{context}: 检测到嵌套 module 声明")
            current_module = module_match.group("name")
            offset += len(line)
            continue
        if MODULE_END_RE.match(line) is not None:
            current_module = None
            offset += len(line)
            continue
        if current_module is None:
            offset += len(line)
            continue
        declaration_match = PARAMETER_DECLARATION_RE.match(line)
        if declaration_match is None:
            offset += len(line)
            continue
        name_match = PARAMETER_NAME_RE.search(declaration_match.group("body"))
        if name_match is None:
            offset += len(line)
            continue
        kind = declaration_match.group("kind")
        parameter_name = name_match.group("name")
        key = (current_module, parameter_name)
        declaration = ParameterDeclaration(
            current_module,
            parameter_name,
            kind,
            offset + declaration_match.start("kind"),
            offset + declaration_match.end("kind"),
            offset,
            offset + len(line.rstrip("\r\n")),
        )
        previous = declarations.get(key)
        if previous is not None and previous.kind != declaration.kind:
            raise MergeError(
                f"{context}: {current_module}.{parameter_name} 同时声明为 "
                f"{previous.kind} 和 {declaration.kind}，无法安全匹配"
            )
        declarations.setdefault(key, declaration)
        offset += len(line)
    return declarations


def preserve_parameter_declaration_kinds(
    new_text: str,
    existing_text: str,
    new_regions: list[UserCodeRegion],
    old_regions: list[UserCodeRegion],
    context: str,
) -> tuple[str, list[Diagnostic]]:
    """Carry old ``localparam/parameter`` choices into matching declarations."""
    old_declarations = parse_parameter_declarations(
        existing_text,
        old_regions,
        f"旧文件 {context}",
    )
    new_declarations = parse_parameter_declarations(
        new_text,
        new_regions,
        f"新文件 {context}",
    )
    replacements: list[tuple[int, int, str]] = []
    diagnostics: list[Diagnostic] = []
    for key, new_declaration in new_declarations.items():
        old_declaration = old_declarations.get(key)
        if old_declaration is None or old_declaration.kind == new_declaration.kind:
            continue
        replacements.append(
            (
                new_declaration.kind_start,
                new_declaration.kind_end,
                old_declaration.kind,
            )
        )
        diagnostics.append(
            Diagnostic(
                "info",
                f"{context}: 保留 {key[0]}.{key[1]} 的 "
                f"{old_declaration.kind} 参数类型"
                f"（新生成版本为 {new_declaration.kind}）",
            )
        )
    preserved = new_text
    for start, end, kind in reversed(replacements):
        preserved = preserved[:start] + kind + preserved[end:]
    return preserved, diagnostics


def parse_assignments(
    text: str,
    regions: list[UserCodeRegion],
    context: str,
    *,
    validate_user_markers: bool = False,
) -> list[AssignmentStatement]:
    """Find conservative one-line continuous assignments outside USER blocks."""
    masked = _mask_verilog_comments(text)
    protected_ranges = [
        (region.content_start, region.content_end) for region in regions
    ]

    def is_protected(position: int) -> bool:
        return any(start <= position < end for start, end in protected_ranges)

    assignments: list[AssignmentStatement] = []
    current_module: str | None = None
    offset = 0
    original_lines = text.splitlines(keepends=True)
    masked_lines = masked.splitlines(keepends=True)
    if len(original_lines) != len(masked_lines):
        raise MergeError(f"{context}: 注释屏蔽后行结构异常")
    for original_line, masked_line in zip(original_lines, masked_lines):
        user_owned = USER_ASSIGN_COMMENT_RE.search(original_line) is not None
        if is_protected(offset):
            offset += len(original_line)
            continue
        module_match = MODULE_BEGIN_RE.match(masked_line)
        if module_match is not None:
            if validate_user_markers and user_owned:
                raise MergeError(
                    f"{context}: //USER: 只支持模块内完整的单行 assign"
                )
            if current_module is not None:
                raise MergeError(f"{context}: 检测到嵌套 module 声明")
            current_module = module_match.group("name")
            offset += len(original_line)
            continue
        if MODULE_END_RE.match(masked_line) is not None:
            if validate_user_markers and user_owned:
                raise MergeError(
                    f"{context}: //USER: 只支持模块内完整的单行 assign"
                )
            current_module = None
            offset += len(original_line)
            continue
        if current_module is None:
            if validate_user_markers and user_owned:
                raise MergeError(
                    f"{context}: //USER: 必须标在模块内的单行 assign 末尾"
                )
            offset += len(original_line)
            continue
        assignment_match = ASSIGNMENT_RE.match(masked_line.rstrip("\r\n"))
        if assignment_match is None:
            if validate_user_markers and user_owned:
                raise MergeError(
                    f"{context}: //USER: 只支持模块内完整的单行 assign"
                )
            offset += len(original_line)
            continue
        lhs = re.sub(r"[ \t]+", "", assignment_match.group("lhs"))
        root_match = SIMPLE_ASSIGN_TARGET_RE.fullmatch(lhs)
        statement = original_line.rstrip("\r\n")
        assignments.append(
            AssignmentStatement(
                current_module,
                lhs,
                root_match.group("name") if root_match is not None else None,
                statement,
                offset,
                offset + len(statement),
                user_owned,
            )
        )
        offset += len(original_line)
    return assignments


def parse_port_connections(
    text: str,
    regions: list[UserCodeRegion],
    context: str,
) -> list[PortConnection]:
    """Find active one-line named associations outside USER CODE blocks."""
    masked = _mask_verilog_comments(text)
    protected_ranges = [
        (region.content_start, region.content_end) for region in regions
    ]

    def is_protected(position: int) -> bool:
        return any(start <= position < end for start, end in protected_ranges)

    connections: list[PortConnection] = []
    current_module: str | None = None
    offset = 0
    for line in masked.splitlines(keepends=True):
        if is_protected(offset):
            offset += len(line)
            continue
        module_match = MODULE_BEGIN_RE.match(line)
        if module_match is not None:
            if current_module is not None:
                raise MergeError(f"{context}: 检测到嵌套 module 声明")
            current_module = module_match.group("name")
            offset += len(line)
            continue
        if MODULE_END_RE.match(line) is not None:
            current_module = None
            offset += len(line)
            continue
        if current_module is None:
            offset += len(line)
            continue
        connection_match = PORT_CONNECTION_RE.match(line.rstrip("\r\n"))
        if connection_match is not None:
            connections.append(
                PortConnection(
                    current_module,
                    connection_match.group("name"),
                    offset,
                    offset + len(line.rstrip("\r\n")),
                )
            )
        offset += len(line)
    return connections


def parse_user_owned_lines(
    text: str,
    regions: list[UserCodeRegion],
    context: str,
) -> list[UserOwnedLine]:
    """Parse every supported one-line ``//USER:`` preservation request.

    Supported records are a complete active/commented assign, an
    active/commented wire/reg declaration, an active/commented parameter, and
    an active/commented named association. Commented records preserve the
    complete old line while using its stable module/name key.
    """
    masked = _mask_verilog_comments(text)
    protected_ranges = [
        (region.content_start, region.content_end) for region in regions
    ]

    def is_protected(position: int) -> bool:
        return any(start <= position < end for start, end in protected_ranges)

    original_lines = text.splitlines(keepends=True)
    masked_lines = masked.splitlines(keepends=True)
    if len(original_lines) != len(masked_lines):
        raise MergeError(f"{context}: 注释屏蔽后行结构异常")

    result: list[UserOwnedLine] = []
    current_module: str | None = None
    offset = 0
    for original_line, masked_line in zip(original_lines, masked_lines):
        if is_protected(offset):
            offset += len(original_line)
            continue
        module_match = MODULE_BEGIN_RE.match(masked_line)
        if module_match is not None:
            if current_module is not None:
                raise MergeError(f"{context}: 检测到嵌套 module 声明")
            current_module = module_match.group("name")
            offset += len(original_line)
            continue
        if MODULE_END_RE.match(masked_line) is not None:
            current_module = None
            offset += len(original_line)
            continue

        marker = USER_LINE_COMMENT_RE.search(original_line)
        if marker is None:
            offset += len(original_line)
            continue
        if current_module is None:
            raise MergeError(
                f"{context}: //USER: 必须位于模块内受支持的单行代码末尾"
            )

        before_marker = original_line[: marker.start()].rstrip()
        code = before_marker.lstrip()
        commented = code.startswith("//")
        if commented:
            code = code[2:].lstrip()
        statement = original_line.rstrip("\r\n")

        if re.match(r"^assign\b", code):
            if commented:
                body = re.sub(r"^assign\b", "", code, count=1).strip()
                lhs_text = body.split("=", 1)[0].strip().rstrip(";").strip()
            else:
                assignment_match = ASSIGNMENT_RE.match(code)
                lhs_text = (
                    assignment_match.group("lhs")
                    if assignment_match is not None
                    else ""
                )
            lhs = re.sub(r"[ \t]+", "", lhs_text)
            root_match = SIMPLE_ASSIGN_TARGET_RE.fullmatch(lhs)
            if root_match is None:
                raise MergeError(
                    f"{context}: //USER: assign 必须提供可识别的单一左值"
                )
            result.append(
                UserOwnedLine(
                    "assign",
                    current_module,
                    lhs,
                    root_match.group("name"),
                    statement,
                )
            )
            offset += len(original_line)
            continue

        declaration_match = SIGNAL_DECLARATION_RE.match(code)
        if declaration_match is not None:
            name_match = SIGNAL_DECLARATOR_TAIL_RE.search(
                declaration_match.group("body")
            )
            if name_match is not None:
                result.append(
                    UserOwnedLine(
                        "signal",
                        current_module,
                        name_match.group("name"),
                        None,
                        statement,
                    )
                )
                offset += len(original_line)
                continue

        parameter_match = PARAMETER_DECLARATION_RE.match(code)
        if parameter_match is not None:
            name_match = PARAMETER_NAME_RE.search(parameter_match.group("body"))
            if name_match is not None:
                result.append(
                    UserOwnedLine(
                        "parameter",
                        current_module,
                        name_match.group("name"),
                        None,
                        statement,
                    )
                )
                offset += len(original_line)
                continue

        connection_match = PORT_CONNECTION_RE.match(code)
        if connection_match is not None:
            result.append(
                UserOwnedLine(
                    "port",
                    current_module,
                    connection_match.group("name"),
                    None,
                    statement,
                )
            )
            offset += len(original_line)
            continue

        raise MergeError(
            f"{context}: //USER: 仅支持完整的单行 assign、"
            "单行 wire/reg 声明、单行 localparam/parameter 或单行实例端口连接"
        )
    return result


def preserve_user_owned_lines(
    new_text: str,
    existing_text: str,
    new_regions: list[UserCodeRegion],
    old_regions: list[UserCodeRegion],
    context: str,
) -> tuple[str, list[Diagnostic]]:
    """Keep supported old lines explicitly marked with ``//USER:``.

    Assigns prefer exact LHS and then a unique root signal. Declarations use
    ``module.signal``. Named associations use ``module.port`` and therefore
    deliberately reject a module containing multiple matching associations.
    """
    old_lines = parse_user_owned_lines(
        existing_text,
        old_regions,
        f"旧文件 {context}",
    )
    if not old_lines:
        return new_text, []
    new_assignments = parse_assignments(
        new_text,
        new_regions,
        f"新文件 {context}",
    )
    new_declarations = parse_signal_declarations(
        new_text,
        new_regions,
        f"新文件 {context}",
    )
    new_parameters = parse_parameter_declarations(
        new_text,
        new_regions,
        f"新文件 {context}",
    )
    new_connections = parse_port_connections(
        new_text,
        new_regions,
        f"新文件 {context}",
    )
    by_exact: dict[tuple[str, str], list[AssignmentStatement]] = {}
    by_root: dict[tuple[str, str], list[AssignmentStatement]] = {}
    for item in new_assignments:
        by_exact.setdefault((item.module_name, item.lhs), []).append(item)
        if item.root_signal is not None:
            by_root.setdefault((item.module_name, item.root_signal), []).append(item)
    connections_by_key: dict[tuple[str, str], list[PortConnection]] = {}
    for item in new_connections:
        connections_by_key.setdefault(
            (item.module_name, item.port_name), []
        ).append(item)

    replacements: list[tuple[int, int, str]] = []
    diagnostics: list[Diagnostic] = []
    claimed_positions: set[int] = set()
    for old_line in old_lines:
        label = f"{old_line.module_name}.{old_line.key}"
        fallback_note = ""
        if old_line.kind == "assign":
            exact_key = (old_line.module_name, old_line.key)
            assign_candidates = by_exact.get(exact_key, [])
            if not assign_candidates and old_line.root_signal is not None:
                root_key = (old_line.module_name, old_line.root_signal)
                assign_candidates = by_root.get(root_key, [])
                fallback_note = (
                    "（按唯一根信号匹配）" if assign_candidates else ""
                )
            if not assign_candidates:
                raise MergeError(
                    f"{context}: 新结构缺少 //USER: 手工 assign {label}"
                )
            if len(assign_candidates) != 1:
                raise MergeError(
                    f"{context}: //USER: 手工 assign {label} 在新结构中匹配到 "
                    f"{len(assign_candidates)} 条，无法安全迁移"
                )
            candidate_start = assign_candidates[0].statement_start
            candidate_end = assign_candidates[0].statement_end
            description = "手工 assign"
        elif old_line.kind == "signal":
            declaration = new_declarations.get(
                (old_line.module_name, old_line.key)
            )
            if declaration is None:
                raise MergeError(
                    f"{context}: 新结构缺少 //USER: signal 声明 {label}"
                )
            candidate_start = declaration.statement_start
            candidate_end = declaration.statement_end
            description = "signal 声明"
        elif old_line.kind == "parameter":
            parameter = new_parameters.get(
                (old_line.module_name, old_line.key)
            )
            if parameter is None:
                raise MergeError(
                    f"{context}: 新结构缺少 //USER: parameter 声明 {label}"
                )
            candidate_start = parameter.statement_start
            candidate_end = parameter.statement_end
            description = "parameter 声明"
        else:
            connection_candidates = connections_by_key.get(
                (old_line.module_name, old_line.key), []
            )
            if not connection_candidates:
                raise MergeError(
                    f"{context}: 新结构缺少 //USER: 实例端口连接 {label}"
                )
            if len(connection_candidates) != 1:
                raise MergeError(
                    f"{context}: //USER: 实例端口连接 {label} 在新结构中匹配到 "
                    f"{len(connection_candidates)} 条，无法安全迁移"
                )
            candidate_start = connection_candidates[0].statement_start
            candidate_end = connection_candidates[0].statement_end
            description = "实例端口连接"

        if candidate_start in claimed_positions:
            raise MergeError(
                f"{context}: 多条 //USER: 记录同时匹配 {label}"
            )
        claimed_positions.add(candidate_start)
        replacements.append(
            (candidate_start, candidate_end, old_line.statement)
        )
        diagnostics.append(
            Diagnostic(
                "info",
                f"{context}: 保留 {label} 的 //USER: {description}{fallback_note}",
            )
        )

    preserved = new_text
    for start, end, statement in reversed(replacements):
        preserved = preserved[:start] + statement + preserved[end:]
    return preserved, diagnostics


def merge_verilog_text(
    new_text: str,
    existing_text: str,
    context: str = "Verilog",
) -> tuple[str, list[Diagnostic]]:
    """Use the new structure while retaining old protected-region content."""
    old_regions = parse_user_code_regions(existing_text, f"旧文件 {context}")
    new_regions = parse_user_code_regions(new_text, f"新文件 {context}")
    diagnostics: list[Diagnostic] = []
    if not old_regions and existing_text != new_text:
        diagnostics.append(
            Diagnostic(
                "warning",
                f"{context}: 旧文件没有 USER CODE 段，旧文件内容将整体被新版本替换",
            )
        )
    if not new_regions and old_regions:
        raise MergeError(f"{context}: 新文件没有 USER CODE 段，无法安全保留旧用户代码")

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
        raise MergeError(
            f"{context}: 新结构缺少含用户内容的旧 USER CODE 段 ({labels})"
        )

    merged, declaration_diagnostics = preserve_signal_declaration_kinds(
        new_text,
        existing_text,
        new_regions,
        old_regions,
        context,
    )
    diagnostics.extend(declaration_diagnostics)
    # Migrated keywords and hand-written assignments can have different
    # lengths, so reparse USER offsets after every preservation layer.
    merged_regions = parse_user_code_regions(merged, f"新文件 {context}")
    merged, parameter_diagnostics = preserve_parameter_declaration_kinds(
        merged,
        existing_text,
        merged_regions,
        old_regions,
        context,
    )
    diagnostics.extend(parameter_diagnostics)
    merged_regions = parse_user_code_regions(merged, f"新文件 {context}")
    merged, assignment_diagnostics = preserve_user_owned_lines(
        merged,
        existing_text,
        merged_regions,
        old_regions,
        context,
    )
    diagnostics.extend(assignment_diagnostics)
    merged_regions = parse_user_code_regions(merged, f"新文件 {context}")
    for region in reversed(merged_regions):
        previous = old_by_key.get((region.label, region.occurrence))
        if previous is None:
            continue
        if previous.content.strip() and previous.content != region.content:
            diagnostics.append(
                Diagnostic(
                    "info",
                    f"{context}: 保留 USER CODE 段 "
                    f"{region.label}#{region.occurrence + 1}",
                )
            )
        merged = (
            merged[: region.content_start]
            + previous.content
            + merged[region.content_end :]
        )
    return merged, diagnostics


def _read_utf8(path: Path) -> tuple[str, bytes]:
    try:
        data = path.read_bytes()
        return data.decode("utf-8-sig"), data
    except (OSError, UnicodeError) as exc:
        raise MergeError(f"无法读取 UTF-8 文件 {path}: {exc}") from exc


def _resolved(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise MergeError(f"无法解析路径 {path}: {exc}") from exc


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _source_files(source: Path) -> list[tuple[Path, Path]]:
    if source.is_file():
        if source.is_symlink():
            raise MergeError(f"拒绝读取符号链接源文件: {source}")
        if source.suffix.casefold() not in DEFAULT_SUFFIXES:
            raise MergeError(
                f"源文件扩展名 {source.suffix!r} 不受支持；支持: "
                + ", ".join(sorted(DEFAULT_SUFFIXES))
            )
        return [(Path(source.name), source)]
    if not source.is_dir():
        raise MergeError(f"新生成路径不存在: {source}")
    files = [
        path
        for path in source.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.casefold() in DEFAULT_SUFFIXES
    ]
    if not files:
        raise MergeError(f"新生成目录中没有 Verilog 文件: {source}")
    return sorted(
        ((path.relative_to(source), path) for path in files),
        key=lambda item: item[0].as_posix().casefold(),
    )


def _recursive_target_index(
    target: Path,
    relevant_names: set[str],
) -> dict[str, Path]:
    """Index a production tree by case-insensitive file name.

    Only names present in the new-code source are indexed. Duplicate unrelated
    production files must not block a focused update.
    """
    by_name: dict[str, list[Path]] = {}
    for path in target.rglob("*"):
        key = path.name.casefold()
        if key not in relevant_names:
            continue
        if path.suffix.casefold() not in DEFAULT_SUFFIXES:
            continue
        if not (path.is_file() or path.is_symlink()):
            continue
        by_name.setdefault(key, []).append(path)
    duplicates = {
        name: sorted(paths, key=lambda item: str(item).casefold())
        for name, paths in by_name.items()
        if len(paths) > 1
    }
    if duplicates:
        details = "；".join(
            f"{paths[0].name}: " + "、".join(str(path) for path in paths)
            for paths in duplicates.values()
        )
        raise MergeError(f"目标目录中存在重名 Verilog 文件，无法按文件名定位：{details}")
    return {name: paths[0] for name, paths in by_name.items()}


def _target_for_source(
    source_is_file: bool,
    target: Path,
    relative_path: Path,
    target_index: dict[str, Path] | None = None,
) -> Path:
    if target.is_dir():
        if target_index is not None:
            matched = target_index.get(relative_path.name.casefold())
            if matched is not None:
                return matched
        return target / (relative_path.name if source_is_file else relative_path)
    if source_is_file:
        return target
    if target.exists() and not target.is_dir():
        raise MergeError("源路径是目录时，目标路径也必须是目录")
    return target / relative_path


def build_merge_plan(
    new_generated: Path,
    target_project: Path,
) -> tuple[list[MergeEntry], list[Diagnostic]]:
    """Build and fully validate a plan without changing the filesystem."""
    source = _resolved(new_generated)
    target = _resolved(target_project)
    if source == target:
        raise MergeError("新生成路径与目标项目路径不能相同")
    if source.is_dir() and _is_within(target, source):
        raise MergeError("目标项目不能位于新生成目录内部")
    target_is_directory = source.is_dir() or target.is_dir()
    if target_is_directory and _is_within(source, target):
        raise MergeError("新生成路径不能位于目标生产项目内部")

    source_files = _source_files(source)
    source_is_file = source.is_file()
    source_names: dict[str, list[Path]] = {}
    for _, source_path in source_files:
        source_names.setdefault(source_path.name.casefold(), []).append(source_path)
    duplicate_sources = {
        name: paths for name, paths in source_names.items() if len(paths) > 1
    }
    if duplicate_sources:
        details = "；".join(
            f"{paths[0].name}: " + "、".join(str(path) for path in paths)
            for paths in duplicate_sources.values()
        )
        raise MergeError(f"新代码中存在重名 Verilog 文件，文件名键不唯一：{details}")
    target_index = (
        _recursive_target_index(target, set(source_names))
        if target.is_dir()
        else None
    )
    plan: list[MergeEntry] = []
    diagnostics: list[Diagnostic] = []
    seen_targets: dict[str, Path] = {}
    for relative_path, source_path in source_files:
        target_path = _target_for_source(
            source_is_file, target, relative_path, target_index
        )
        if _resolved(source_path) == _resolved(target_path):
            raise MergeError(f"源文件与目标文件不能相同: {source_path}")
        if not source_is_file and not _is_within(_resolved(target_path), target):
            raise MergeError(f"目标路径通过符号链接越出项目目录: {target_path}")
        target_key = str(_resolved(target_path)).casefold()
        previous = seen_targets.get(target_key)
        if previous is not None:
            raise MergeError(
                f"源目录中存在大小写冲突，都会写入 {target_path}: "
                f"{previous}、{source_path}"
            )
        seen_targets[target_key] = source_path
        if target_path.is_symlink():
            raise MergeError(f"拒绝覆盖符号链接: {target_path}")

        target_relative_path = (
            target_path.relative_to(target) if target.is_dir() else relative_path
        )
        new_text, _ = _read_utf8(source_path)
        original_bytes: bytes | None = None
        original_mode: int | None = None
        if target_path.exists():
            if not target_path.is_file():
                raise MergeError(f"目标不是普通文件: {target_path}")
            existing_text, original_bytes = _read_utf8(target_path)
            original_mode = stat.S_IMODE(target_path.stat().st_mode)
            merged_text, file_diagnostics = merge_verilog_text(
                new_text, existing_text, target_relative_path.as_posix()
            )
            diagnostics.extend(file_diagnostics)
        else:
            # Validate source markers even for newly created files.
            parse_user_code_regions(
                new_text, f"新文件 {target_relative_path.as_posix()}"
            )
            merged_text = new_text
            diagnostics.append(
                Diagnostic(
                    "info",
                    f"{target_relative_path.as_posix()}: 目标中不存在，将新建",
                )
            )
        changed = original_bytes is None or merged_text.encode("utf-8") != original_bytes
        if original_bytes is not None:
            diagnostics.append(
                Diagnostic(
                    "info",
                    f"{target_relative_path.as_posix()}: "
                    + ("将覆盖目标文件" if changed else "合并后内容不变"),
                )
            )
        plan.append(
            MergeEntry(
                target_relative_path,
                source_path,
                target_path,
                merged_text,
                original_bytes,
                original_mode,
                changed,
            )
        )
    return plan, diagnostics


def _default_backup_directory(source: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = source.name if source.name else "new_generated"
    return source.parent / f"{name}.xlsx2verilog_merger_backup" / stamp


def _stage_text(path: Path, text: str, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{path.name}.",
        suffix=".xlsx2verilog_merger.tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode if mode is not None else 0o644)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _restore_original(entry: MergeEntry) -> None:
    if entry.original_bytes is None:
        entry.target_path.unlink(missing_ok=True)
        return
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{entry.target_path.name}.rollback.",
        suffix=".tmp",
        dir=entry.target_path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(entry.original_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, entry.original_mode or 0o644)
        os.replace(temporary, entry.target_path)
    finally:
        temporary.unlink(missing_ok=True)


def execute_merge_plan(
    plan: list[MergeEntry],
    target_project: Path,
    *,
    check_only: bool = False,
    create_backup: bool = True,
    backup_directory: Path | None = None,
    backup_anchor: Path | None = None,
    diagnostics: Iterable[Diagnostic] = (),
    confirm_entry: Callable[[MergeEntry], bool] | None = None,
) -> MergeResult:
    """Apply a validated plan, rolling back every replaced file on failure."""
    result = MergeResult(check_only=check_only, diagnostics=list(diagnostics))
    changed_entries = [entry for entry in plan if entry.changed]
    result.unchanged = [entry.target_path for entry in plan if not entry.changed]
    if not check_only and confirm_entry is not None:
        accepted: list[MergeEntry] = []
        for entry in changed_entries:
            if confirm_entry(entry):
                accepted.append(entry)
            else:
                result.skipped.append(entry.target_path)
                result.diagnostics.append(
                    Diagnostic(
                        "info",
                        f"{entry.relative_path.as_posix()}: 用户选择 N，已跳过",
                    )
                )
        changed_entries = accepted
    result.changed = [entry.target_path for entry in changed_entries]
    result.created = [entry.target_path for entry in changed_entries if entry.created]
    if check_only or not changed_entries:
        return result

    target = _resolved(target_project)
    if create_backup and any(not entry.created for entry in changed_entries):
        backup_root = (
            _resolved(backup_directory)
            if backup_directory
            else _default_backup_directory(
                _resolved(backup_anchor) if backup_anchor is not None else target
            )
        )
        for entry in changed_entries:
            if entry.created:
                continue
            backup_path = backup_root / entry.relative_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry.target_path, backup_path)
            result.diagnostics.append(
                Diagnostic(
                    "info",
                    f"{entry.relative_path.as_posix()}: 已备份旧生产文件到 "
                    f"{backup_path}",
                )
            )
        result.backup_directory = backup_root

    staged: dict[Path, Path] = {}
    replaced: list[MergeEntry] = []
    try:
        for entry in changed_entries:
            staged[entry.target_path] = _stage_text(
                entry.target_path,
                entry.merged_text,
                entry.original_mode,
            )
        for entry in changed_entries:
            temporary = staged.pop(entry.target_path)
            os.replace(temporary, entry.target_path)
            replaced.append(entry)
            result.diagnostics.append(
                Diagnostic(
                    "info",
                    f"{entry.relative_path.as_posix()}: 已写入合并结果",
                )
            )
    except BaseException as exc:
        rollback_errors: list[str] = []
        for entry in reversed(replaced):
            try:
                _restore_original(entry)
            except OSError as rollback_exc:
                rollback_errors.append(f"{entry.target_path}: {rollback_exc}")
        detail = f"；回滚失败: {'；'.join(rollback_errors)}" if rollback_errors else ""
        raise MergeError(f"写入失败，已回滚本轮已替换文件: {exc}{detail}") from exc
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    return result


def merge_paths(
    new_generated: Path,
    target_project: Path,
    *,
    check_only: bool = False,
    create_backup: bool = True,
    backup_directory: Path | None = None,
    confirm_entry: Callable[[MergeEntry], bool] | None = None,
) -> MergeResult:
    """Validate and merge one generated file or a complete generated tree."""
    source = _resolved(new_generated)
    target = _resolved(target_project)
    if backup_directory is not None:
        backup_root = _resolved(backup_directory)
        if source.is_dir() and _is_within(backup_root, source):
            raise MergeError("备份目录不能位于新生成目录内部")
        target_is_directory = source.is_dir() or target.is_dir()
        if target_is_directory and _is_within(backup_root, target):
            raise MergeError("备份目录不能位于目标项目目录内部")
    plan, diagnostics = build_merge_plan(new_generated, target_project)
    return execute_merge_plan(
        plan,
        target_project,
        check_only=check_only,
        create_backup=create_backup,
        backup_directory=backup_directory,
        backup_anchor=source,
        diagnostics=diagnostics,
        confirm_entry=confirm_entry,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "将新生成的 Verilog 结构合并到已有项目，并保留旧文件 USER CODE 段"
        )
    )
    parser.add_argument("new_generated", type=Path, help="新的 .v/.sv 文件或生成目录")
    parser.add_argument(
        "target_project",
        type=Path,
        nargs="?",
        help="要更新的已有文件或项目目录；省略时使用文件顶部 DEFAULT_TARGET_PROJECT",
    )
    parser.add_argument("--check", action="store_true", help="只检查并显示计划，不写文件")
    parser.add_argument("--no-backup", action="store_true", help="不保留持久备份；失败回滚仍有效")
    parser.add_argument("--backup-dir", type=Path, help="指定备份根目录")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def _print_result(result: MergeResult) -> None:
    for item in result.diagnostics:
        stream = sys.stderr if item.level == "warning" else sys.stdout
        print(f"{item.level}[{item.message}]", file=stream)
    action = "检查" if result.check_only else "合并"
    print(
        f"{action}完成：更新 {len(result.changed)}，新建 {len(result.created)}，"
        f"不变 {len(result.unchanged)}，跳过 {len(result.skipped)}。"
    )
    if result.backup_directory is not None:
        print(f"备份目录：{result.backup_directory}")
    if result.changed:
        print("\n生产文件与路径：")
        for path in result.changed:
            print(path.name)
            print(path.resolve())
            print()


def _confirm_entry(entry: MergeEntry) -> bool:
    action = "新建" if entry.created else "更新"
    prompt = f"{action} {entry.target_path.resolve()}？[Y/N]: "
    while True:
        try:
            answer = input(prompt).strip().casefold()
        except (EOFError, KeyboardInterrupt):
            print("\nwarning[未获得确认，按 N 跳过该文件]", file=sys.stderr)
            return False
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("请输入 Y 或 N。")


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.no_backup and args.backup_dir is not None:
        print("error[--no-backup 与 --backup-dir 不能同时使用]", file=sys.stderr)
        return 2
    target_project = args.target_project
    if target_project is None:
        if DEFAULT_TARGET_PROJECT is None or not str(DEFAULT_TARGET_PROJECT).strip():
            print(
                "error[未传入 target_project，且文件顶部 DEFAULT_TARGET_PROJECT 未配置]",
                file=sys.stderr,
            )
            return 2
        target_project = Path(DEFAULT_TARGET_PROJECT)
        print(f"info[使用文件顶部默认生产目标: {target_project}]")
    try:
        result = merge_paths(
            args.new_generated,
            target_project,
            check_only=args.check,
            create_backup=not args.no_backup,
            backup_directory=args.backup_dir,
            confirm_entry=None if args.check else _confirm_entry,
        )
    except MergeError as exc:
        print(f"error[{exc}]", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error[文件系统操作失败: {exc}]", file=sys.stderr)
        return 3
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
