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
from typing import Iterable


VERSION = "1.0"
DEFAULT_SUFFIXES = frozenset({".v", ".sv", ".vh", ".svh"})
USER_CODE_MARKER_RE = re.compile(
    r"(?m)^[ \t]*/\*USER CODE (BEGIN|END)[ \t]+(.+?)[ \t]*\*/[ \t]*\r?$"
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

    merged = new_text
    for region in reversed(new_regions):
        previous = old_by_key.get((region.label, region.occurrence))
        if previous is None:
            continue
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


def _target_for_source(
    source_is_file: bool,
    target: Path,
    relative_path: Path,
) -> Path:
    if source_is_file:
        return target / relative_path.name if target.is_dir() else target
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

    source_files = _source_files(source)
    source_is_file = source.is_file()
    plan: list[MergeEntry] = []
    diagnostics: list[Diagnostic] = []
    seen_targets: dict[str, Path] = {}
    for relative_path, source_path in source_files:
        target_path = _target_for_source(source_is_file, target, relative_path)
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

        new_text, _ = _read_utf8(source_path)
        original_bytes: bytes | None = None
        original_mode: int | None = None
        if target_path.exists():
            if not target_path.is_file():
                raise MergeError(f"目标不是普通文件: {target_path}")
            existing_text, original_bytes = _read_utf8(target_path)
            original_mode = stat.S_IMODE(target_path.stat().st_mode)
            merged_text, file_diagnostics = merge_verilog_text(
                new_text, existing_text, relative_path.as_posix()
            )
            diagnostics.extend(file_diagnostics)
        else:
            # Validate source markers even for newly created files.
            parse_user_code_regions(new_text, f"新文件 {relative_path.as_posix()}")
            merged_text = new_text
            diagnostics.append(
                Diagnostic("info", f"{relative_path.as_posix()}: 目标中不存在，将新建")
            )
        changed = original_bytes is None or merged_text.encode("utf-8") != original_bytes
        plan.append(
            MergeEntry(
                relative_path,
                source_path,
                target_path,
                merged_text,
                original_bytes,
                original_mode,
                changed,
            )
        )
    return plan, diagnostics


def _default_backup_directory(target: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = target.name if target.name else "project"
    return target.parent / f"{name}.xlsx2verilog_merger_backup" / stamp


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
    diagnostics: Iterable[Diagnostic] = (),
) -> MergeResult:
    """Apply a validated plan, rolling back every replaced file on failure."""
    result = MergeResult(check_only=check_only, diagnostics=list(diagnostics))
    changed_entries = [entry for entry in plan if entry.changed]
    result.unchanged = [entry.target_path for entry in plan if not entry.changed]
    result.changed = [entry.target_path for entry in changed_entries]
    result.created = [entry.target_path for entry in changed_entries if entry.created]
    if check_only or not changed_entries:
        return result

    target = _resolved(target_project)
    if create_backup and any(not entry.created for entry in changed_entries):
        backup_root = (
            _resolved(backup_directory)
            if backup_directory
            else _default_backup_directory(target)
        )
        for entry in changed_entries:
            if entry.created:
                continue
            backup_path = backup_root / entry.relative_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry.target_path, backup_path)
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
        diagnostics=diagnostics,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "将新生成的 Verilog 结构合并到已有项目，并保留旧文件 USER CODE 段"
        )
    )
    parser.add_argument("new_generated", type=Path, help="新的 .v/.sv 文件或生成目录")
    parser.add_argument("target_project", type=Path, help="要更新的已有文件或项目目录")
    parser.add_argument("--check", action="store_true", help="只检查并显示计划，不写文件")
    parser.add_argument("--no-backup", action="store_true", help="不保留持久备份；失败回滚仍有效")
    parser.add_argument("--backup-dir", type=Path, help="指定备份根目录")
    return parser


def _print_result(result: MergeResult) -> None:
    for item in result.diagnostics:
        stream = sys.stderr if item.level == "warning" else sys.stdout
        print(f"{item.level}[{item.message}]", file=stream)
    action = "检查" if result.check_only else "合并"
    print(
        f"{action}完成：更新 {len(result.changed)}，新建 {len(result.created)}，"
        f"不变 {len(result.unchanged)}。"
    )
    if result.backup_directory is not None:
        print(f"备份目录：{result.backup_directory}")


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.no_backup and args.backup_dir is not None:
        print("error[--no-backup 与 --backup-dir 不能同时使用]", file=sys.stderr)
        return 2
    try:
        result = merge_paths(
            args.new_generated,
            args.target_project,
            check_only=args.check,
            create_backup=not args.no_backup,
            backup_directory=args.backup_dir,
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
