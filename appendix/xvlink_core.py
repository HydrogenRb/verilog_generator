"""Shared parse, rule, project and XLSX export services for the visual tools.

Only the Python standard library is used.  The module deliberately reuses the
root generator's parser and checker so the GUI cannot develop a second XLSX
dialect.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xlsx2verilog import (  # noqa: E402
    Reporter,
    clean,
    generate,
    parse_workbook,
    width_expression,
)


VERSION = "2.0.0"
DEFAULT_SHEET_NAME = "集成"
SS_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

ET.register_namespace("", SS_NS)
ET.register_namespace("r", REL_NS)


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def diagnostic_dict(item: Any) -> dict[str, str]:
    return {"level": item.level, "message": item.message}


def _width_dict(width: Any) -> dict[str, str]:
    return {
        "kind": width.kind,
        "expression": width.expression,
        "default": width.default,
    }


def load_workbook_model(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    reporter = Reporter()
    workbook, modules, integration = parse_workbook(path, reporter)
    result_modules: list[dict[str, Any]] = []
    for module in modules.values():
        ports = []
        for port in module.ports:
            ports.append(
                {
                    "id": f"{module.name}:{port.name}",
                    "module": module.name,
                    "sheet": module.sheet_name,
                    "name": port.name,
                    "direction": port.direction,
                    "width": _width_dict(port.width),
                    "packed": [_width_dict(item) for item in port.packed_dimensions],
                    "arrays": [_width_dict(item) for item in port.arrays],
                    "interface": port.interface_type,
                    "category": port.category,
                    "condition": port.condition,
                    "template_source": port.template_source,
                    "template_values": list(port.template_values),
                    "source_row": port.row,
                }
            )
        result_modules.append(
            {
                "name": module.name,
                "sheet": module.sheet_name,
                "port_count": len(ports),
                "ports": ports,
                "parameters": module.parameters,
                "macros": module.macros,
            }
        )
    result: dict[str, Any] = {
        "version": VERSION,
        "source": str(path),
        "fingerprint": file_fingerprint(path),
        "sheets": [sheet.name for sheet in workbook.sheets],
        "modules": result_modules,
        "diagnostics": [diagnostic_dict(item) for item in reporter.items],
        "integration": None,
    }
    if integration:
        result["integration"] = {
            "sheet": integration.sheet_name,
            "top": integration.top_name,
            "children": integration.child_names,
            "networks": read_integration_networks(workbook, integration),
        }
    return result


def read_integration_networks(workbook: Any, integration: Any) -> list[dict[str, Any]]:
    sheet = workbook.by_name(integration.sheet_name)
    if sheet is None:
        return []
    networks: list[dict[str, Any]] = []
    for group_index, group in enumerate(integration.groups):
        for row in range(integration.header_row + 1, sheet.max_row + 1):
            endpoints = []
            for block in group:
                name = clean(sheet.cell(row, block.port_column))
                if name:
                    endpoints.append(
                        {
                            "module": block.module_name,
                            "port": name,
                            "direction": clean(sheet.cell(row, block.direction_column)),
                        }
                    )
            if endpoints:
                networks.append(
                    {
                        "id": f"sheet-g{group_index + 1}-r{row}",
                        "endpoints": endpoints,
                        "source": "xlsx",
                    }
                )
    return networks


def _normal_name(name: str) -> str:
    return "".join(character.lower() for character in name if character.isalnum())


def _direction_compatible(left: dict[str, Any], right: dict[str, Any], top: str) -> bool:
    if bool(left.get("interface")) != bool(right.get("interface")):
        return False
    if left.get("interface") and left.get("interface", "").rsplit(".", 1)[0] != right.get(
        "interface", ""
    ).rsplit(".", 1)[0]:
        return False
    left_dir, right_dir = left["direction"], right["direction"]
    if left["module"] == top and left_dir == "input" and right_dir == "output":
        return False
    if right["module"] == top and right_dir == "input" and left_dir == "output":
        return False
    if left_dir == right_dir == "output" and top not in {left["module"], right["module"]}:
        return False
    return not (left_dir == "input" and right_dir == "input" and top not in {left["module"], right["module"]})


def _shape_key(port: dict[str, Any]) -> tuple[Any, ...]:
    if port.get("interface"):
        return ("if", port["interface"].rsplit(".", 1)[0])
    widths = [*port.get("packed", []), port["width"], *port.get("arrays", [])]
    return tuple((item["kind"], item["expression"], item["default"]) for item in widths)


def connection_suggestions(model: dict[str, Any], roles: dict[str, str]) -> list[dict[str, Any]]:
    """Return deterministic, explainable suggestions without mutating a project."""
    top = roles["top"]
    chosen = {top, roles["child_a"], roles["child_b"]}
    ports = [
        port
        for module in model["modules"]
        if module["name"] in chosen
        for port in module["ports"]
    ]
    port_by_id = {port["id"]: port for port in ports}
    candidate_pairs: set[tuple[str, str]] = set()
    indices: list[dict[tuple[Any, ...], list[str]]] = [{}, {}]
    for port in ports:
        indices[0].setdefault((_normal_name(port["name"]),), []).append(port["id"])
        if port.get("template_source"):
            indices[1].setdefault(
                (port["template_source"], *port.get("template_values", [])), []
            ).append(port["id"])
    for index in indices:
        for endpoint_ids in index.values():
            for left_index, left_id in enumerate(endpoint_ids):
                for right_id in endpoint_ids[left_index + 1 :]:
                    left, right = port_by_id[left_id], port_by_id[right_id]
                    if left["module"] != right["module"]:
                        candidate_pairs.add(tuple(sorted((left_id, right_id))))
    candidates: list[dict[str, Any]] = []
    for left_id, right_id in sorted(candidate_pairs):
        left, right = port_by_id[left_id], port_by_id[right_id]
        if not _direction_compatible(left, right, top):
            continue
        score = 40
        reasons = ["方向组合可连接 +40"]
        if left["name"] == right["name"]:
            score += 100
            reasons.append("端口名完全相同 +100")
        elif _normal_name(left["name"]) == _normal_name(right["name"]):
            score += 50
            reasons.append("分隔符归一后同名 +50")
        if (
            left.get("template_source")
            and left.get("template_source") == right.get("template_source")
            and left.get("template_values") == right.get("template_values")
        ):
            score += 100
            reasons.append("模板来源与展开值相同 +100")
        shape_equal = _shape_key(left) == _shape_key(right)
        if shape_equal:
            score += 40
            reasons.append("位宽与形状相同 +40")
        else:
            score -= 80
            reasons.append("位宽或形状不同 -80（需人工确认）")
        if left.get("category") == right.get("category"):
            score += 10
            reasons.append("分类相同 +10")
        confidence = "high" if score >= 170 and shape_equal else "medium" if score >= 90 else "low"
        candidates.append(
            {
                "id": hashlib.sha1(f"{left['id']}|{right['id']}".encode()).hexdigest()[:12],
                "endpoints": [left["id"], right["id"]],
                "score": score,
                "confidence": confidence,
                "reasons": reasons,
                "width_warning": not shape_equal,
            }
        )
    return sorted(candidates, key=lambda item: (-item["score"], item["endpoints"]))


def _port_index(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        port["id"]: port
        for module in model["modules"]
        for port in module["ports"]
    }


def validate_project(model: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    ports = _port_index(model)
    roles = project["roles"]
    top = roles["top"]
    diagnostics: list[dict[str, Any]] = []
    seen_inputs: dict[str, str] = {}
    for net in project.get("networks", []):
        endpoints = []
        modules = set()
        for endpoint_id in net.get("endpoints", []):
            port = ports.get(endpoint_id)
            if port is None:
                diagnostics.append(
                    {
                        "level": "error",
                        "code": "MISSING_PORT",
                        "net": net["id"],
                        "message": f"端点 {endpoint_id} 已不存在",
                    }
                )
                continue
            endpoints.append(port)
            if port["module"] in modules:
                diagnostics.append(
                    {
                        "level": "error",
                        "code": "DUP_MODULE",
                        "net": net["id"],
                        "message": (
                            f"网络 {net['id']} 在模块 {port['module']} 中包含多个端口，"
                            "当前集成表格无法无损表达"
                        ),
                    }
                )
            modules.add(port["module"])
            if port["direction"] == "input" and port["id"] in seen_inputs:
                diagnostics.append(
                    {
                        "level": "error",
                        "code": "DUP_INPUT",
                        "net": net["id"],
                        "message": (
                            f"输入 {port['id']} 已连接到网络 "
                            f"{seen_inputs[port['id']]}"
                        ),
                    }
                )
            elif port["direction"] == "input":
                seen_inputs[port["id"]] = net["id"]
        for index, left in enumerate(endpoints):
            for right in endpoints[index + 1 :]:
                if not _direction_compatible(left, right, top):
                    diagnostics.append(
                        {
                            "level": "error",
                            "code": "DIRECTION",
                            "net": net["id"],
                            "message": (
                                f"方向或 interface 冲突："
                                f"{left['id']} ↔ {right['id']}"
                            ),
                        }
                    )
                if _shape_key(left) != _shape_key(right):
                    diagnostics.append(
                        {
                            "level": "warning",
                            "code": "WIDTH",
                            "net": net["id"],
                            "message": (
                                f"位宽/形状不一致：{left['id']} ↔ "
                                f"{right['id']}；V2 将使用低位适配"
                            ),
                        }
                    )
        outputs = [
            port
            for port in endpoints
            if port["direction"] == "output" and port["module"] != top
        ]
        bidirectional = any(
            port["direction"] in {"inout", "interface"} for port in endpoints
        )
        if top not in modules and len(outputs) == 0 and endpoints and not bidirectional:
            diagnostics.append(
                {
                    "level": "warning",
                    "code": "NO_DRIVER",
                    "net": net["id"],
                    "message": f"子模块内部网络 {net['id']} 没有 output 驱动端",
                }
            )
        if len(outputs) > 1:
            diagnostics.append(
                {
                    "level": "error",
                    "code": "MULTI_DRIVER",
                    "net": net["id"],
                    "message": f"网络 {net['id']} 存在多个 output 驱动端",
                }
            )
    connected = {
        item
        for net in project.get("networks", [])
        for item in net.get("endpoints", [])
    }
    confirmed = set(project.get("confirmed_unconnected", []))
    chosen_children = {roles["child_a"], roles["child_b"]}
    for port in ports.values():
        if (
            port["module"] in chosen_children
            and port["id"] not in connected
            and port["id"] not in confirmed
        ):
            diagnostics.append(
                {
                    "level": "warning",
                    "code": "UNCONFIRMED_OPEN",
                    "port": port["id"],
                    "message": (
                        f"子模块端口 {port['id']} 未连接且尚未确认；"
                        "input 导出接零，output/inout/interface 导出空连接"
                    ),
                }
            )
    return diagnostics


def new_project(source: str, fingerprint: str, roles: dict[str, str]) -> dict[str, Any]:
    return {
        "format": "xvlink",
        "version": VERSION,
        "source": source,
        "fingerprint": fingerprint,
        "roles": roles,
        "networks": [],
        "rejected_suggestions": [],
        "confirmed_unconnected": [],
        "layout": {},
    }


def save_project(path_value: str | Path, project: dict[str, Any]) -> Path:
    path = Path(path_value).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(project, stream, ensure_ascii=False, indent=2, sort_keys=True)
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def load_project(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    with path.open(encoding="utf-8") as stream:
        project = json.load(stream)
    if project.get("format") != "xvlink":
        raise ValueError("不是可识别的 .xvlink.json 项目")
    source = Path(project["source"])
    project["source_changed"] = source.exists() and file_fingerprint(
        source
    ) != project.get("fingerprint")
    return project


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def build_integration_cells(
    model: dict[str, Any], project: dict[str, Any]
) -> dict[tuple[int, int], str]:
    roles = project["roles"]
    top, child_a, child_b = roles["top"], roles["child_a"], roles["child_b"]
    module_order = [top, child_a, child_b]
    ports = _port_index(model)
    cells: dict[tuple[int, int], str] = {}

    def put_block(column: int, module: str) -> None:
        cells[(1, column)] = module
        cells[(2, column)] = "端口名"
        cells[(2, column + 1)] = "i/o"

    for index, module in enumerate(module_order):
        put_block(1 + index * 2, module)
    put_block(8, child_a)
    put_block(10, child_b)
    put_block(13, child_a)
    put_block(16, child_b)

    top_row = 3
    internal_row = 3
    connected: set[str] = set()
    for net in sorted(project.get("networks", []), key=lambda item: item["id"]):
        endpoints = [ports[item] for item in net.get("endpoints", []) if item in ports]
        connected.update(port["id"] for port in endpoints)
        has_top = any(port["module"] == top for port in endpoints)
        row = top_row if has_top else internal_row
        columns = (
            {top: 1, child_a: 3, child_b: 5}
            if has_top
            else {child_a: 8, child_b: 10}
        )
        for port in endpoints:
            column = columns.get(port["module"])
            if column is not None:
                cells[(row, column)] = port["name"]
                cells[(row, column + 1)] = port["direction"]
        if has_top:
            top_row += 1
        else:
            internal_row += 1

    # Explicitly enumerate every open child port.  Confirmation affects GUI
    # diagnostics, not deterministic XLSX semantics.
    child_rows = {child_a: 3, child_b: 3}
    child_columns = {child_a: 13, child_b: 16}
    for module in model["modules"]:
        if module["name"] not in child_columns:
            continue
        for port in module["ports"]:
            if port["id"] in connected:
                continue
            row = child_rows[module["name"]]
            column = child_columns[module["name"]]
            cells[(row, column)] = port["name"]
            cells[(row, column + 1)] = port["direction"]
            child_rows[module["name"]] += 1
    return cells


def _sheet_xml(cells: dict[tuple[int, int], str]) -> bytes:
    root = ET.Element(f"{{{SS_NS}}}worksheet")
    data = ET.SubElement(root, f"{{{SS_NS}}}sheetData")
    rows: dict[int, list[tuple[int, str]]] = {}
    for (row, column), value in cells.items():
        if value != "":
            rows.setdefault(row, []).append((column, str(value)))
    for row_number in sorted(rows):
        row_node = ET.SubElement(data, f"{{{SS_NS}}}row", {"r": str(row_number)})
        for column, value in sorted(rows[row_number]):
            cell = ET.SubElement(
                row_node,
                f"{{{SS_NS}}}c",
                {
                    "r": f"{_column_name(column)}{row_number}",
                    "t": "inlineStr",
                },
            )
            inline = ET.SubElement(cell, f"{{{SS_NS}}}is")
            text = ET.SubElement(inline, f"{{{SS_NS}}}t")
            text.text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _normalized_sheet_target(target: str) -> str:
    target = target.replace("\\", "/")
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return "xl/" + target.lstrip("/")


def write_integration_workbook(
    source_value: str | Path,
    output_value: str | Path,
    cells: dict[tuple[int, int], str],
    *,
    sheet_name: str = DEFAULT_SHEET_NAME,
    allow_overwrite_source: bool = False,
    validate: bool = True,
) -> tuple[Path, list[dict[str, str]]]:
    """Clone a workbook, replace/add one sheet, validate, then atomically publish."""
    source = Path(source_value).expanduser().resolve()
    output = Path(output_value).expanduser().resolve()
    if source == output and not allow_overwrite_source:
        raise ValueError("默认禁止覆盖源 XLSX；请使用另存为，或明确启用覆盖")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as archive:
        entries = {
            info.filename: (info, archive.read(info.filename))
            for info in archive.infolist()
        }
    workbook_root = ET.fromstring(entries["xl/workbook.xml"][1])
    rels_root = ET.fromstring(entries["xl/_rels/workbook.xml.rels"][1])
    types_root = ET.fromstring(entries["[Content_Types].xml"][1])
    relation_targets = {
        node.attrib.get("Id", ""): node.attrib.get("Target", "")
        for node in rels_root
    }
    sheets_node = next(
        node
        for node in workbook_root.iter()
        if node.tag.rsplit("}", 1)[-1] == "sheets"
    )
    sheet_node = next(
        (node for node in sheets_node if node.attrib.get("name") == sheet_name),
        None,
    )
    if sheet_node is not None:
        relation_id = next(
            value
            for key, value in sheet_node.attrib.items()
            if key.rsplit("}", 1)[-1] == "id"
        )
        target_path = _normalized_sheet_target(relation_targets[relation_id])
    else:
        used_ids = {node.attrib.get("Id", "") for node in rels_root}
        relation_number = 1
        while f"rId{relation_number}" in used_ids:
            relation_number += 1
        relation_id = f"rId{relation_number}"
        used_sheet_ids = [int(node.attrib.get("sheetId", "0")) for node in sheets_node]
        sheet_id = max(used_sheet_ids, default=0) + 1
        used_paths = set(entries)
        path_number = 1
        while f"xl/worksheets/sheet{path_number}.xml" in used_paths:
            path_number += 1
        target_path = f"xl/worksheets/sheet{path_number}.xml"
        ET.SubElement(
            sheets_node,
            f"{{{SS_NS}}}sheet",
            {
                "name": sheet_name,
                "sheetId": str(sheet_id),
                f"{{{REL_NS}}}id": relation_id,
            },
        )
        ET.SubElement(
            rels_root,
            f"{{{PKG_REL_NS}}}Relationship",
            {
                "Id": relation_id,
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                "Target": target_path.removeprefix("xl/"),
            },
        )
        part_name = "/" + target_path
        if not any(node.attrib.get("PartName") == part_name for node in types_root):
            ET.SubElement(
                types_root,
                f"{{{CT_NS}}}Override",
                {
                    "PartName": part_name,
                    "ContentType": (
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.worksheet+xml"
                    ),
                },
            )

    replacements = {
        "xl/workbook.xml": ET.tostring(
            workbook_root, encoding="utf-8", xml_declaration=True
        ),
        "xl/_rels/workbook.xml.rels": ET.tostring(
            rels_root, encoding="utf-8", xml_declaration=True
        ),
        "[Content_Types].xml": ET.tostring(
            types_root, encoding="utf-8", xml_declaration=True
        ),
        target_path: _sheet_xml(cells),
    }
    with tempfile.NamedTemporaryFile(
        dir=output.parent, suffix=".xlsx.tmp", delete=False
    ) as stream:
        temporary = Path(stream.name)
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            for name, (info, content) in entries.items():
                if name not in replacements:
                    archive.writestr(info, content)
            for name, content in replacements.items():
                info = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, content)
        diagnostics: list[dict[str, str]] = []
        if validate:
            _, reporter = generate(
                temporary, output.parent / ".xvlink-check", check_only=True
            )
            diagnostics = [diagnostic_dict(item) for item in reporter.items]
            if reporter.has_errors:
                messages = "\n".join(
                    item["message"]
                    for item in diagnostics
                    if item["level"] == "错误"
                )
                raise ValueError(f"导出后 xlsx2verilog 校验失败：\n{messages}")
        os.replace(temporary, output)
        return output, diagnostics
    finally:
        if temporary.exists():
            temporary.unlink()


def export_project(
    model: dict[str, Any],
    project: dict[str, Any],
    output: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    diagnostics = validate_project(model, project)
    errors = [item for item in diagnostics if item["level"] == "error"]
    if errors:
        raise ValueError("项目存在阻断性错误：\n" + "\n".join(item["message"] for item in errors))
    cells = build_integration_cells(model, project)
    sheet_name = (
        (model.get("integration") or {}).get("sheet") or DEFAULT_SHEET_NAME
    )
    path, checker = write_integration_workbook(
        model["source"],
        output,
        cells,
        sheet_name=sheet_name,
        allow_overwrite_source=overwrite,
    )
    return {
        "path": str(path),
        "diagnostics": diagnostics,
        "checker_diagnostics": checker,
        "preview": cells_to_preview(cells),
    }


def cells_to_preview(cells: dict[tuple[int, int], str]) -> list[list[str]]:
    max_row = max((row for row, _ in cells), default=0)
    max_column = max((column for _, column in cells), default=0)
    return [
        [cells.get((row, column), "") for column in range(1, max_column + 1)]
        for row in range(1, max_row + 1)
    ]
