#!/usr/bin/env python3
"""Run the three Tech Review 2 inspection rounds and write a concise report."""

from __future__ import annotations

import io
import re
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.run_review_matrix import run_matrix  # noqa: E402
from xlsx2verilog import Reporter, TEMPLATE_RE, generate, parse_workbook  # noqa: E402


SAMPLE = ROOT / "test.xlsx"
SAMPLE_OUTPUT = ROOT / "generated"
REAL_TESTS = [
    ROOT / "review_test_cases/07_real_test_1/ibex_if_stage_3children.xlsx",
    ROOT / "review_test_cases/08_real_test_2/01_core_layer.xlsx",
    ROOT / "review_test_cases/08_real_test_2/02_if_stage_layer.xlsx",
]


@dataclass
class RoundResult:
    title: str
    checks: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems

    def check(self, condition: bool, success: str, failure: str) -> None:
        if condition:
            self.checks.append(success)
        else:
            self.problems.append(failure)


def feature_round() -> tuple[RoundResult, list[Path], Reporter]:
    result = RoundResult("第一轮：新功能与边界输入检视")
    paths, reporter = generate(SAMPLE, SAMPLE_OUTPUT)
    result.check(not reporter.has_errors, "最新 test.xlsx 无错误生成 3 个模块", "test.xlsx 生成失败")
    result.check(len(paths) == 3, "生成文件数为 3", f"生成文件数错误：{len(paths)}")
    top = (SAMPLE_OUTPUT / "RISCV_TOP.v").read_text(encoding="utf-8") if paths else ""
    core = (
        (SAMPLE_OUTPUT / "RISCV_CORE_TEST.v").read_text(encoding="utf-8")
        if paths
        else ""
    )
    for signal in ("sig1", "sig2", "sig3"):
        result.check(
            f"test_bus_{signal}_dat" in top,
            f"{{{{i}}}} 已展开 {signal}",
            f"缺少 {{{{i}}}} 展开端口 {signal}",
        )
        result.check(
            re.search(rf"(?m)^`define DW_{signal}\s+114$", top) is not None,
            f"DW_{signal} 不确定位宽使用 114",
            f"DW_{signal} 未使用 114 占位",
        )
        result.check(
            f"test_bus2_{signal}_dat" in top
            and f"test_bus2_{signal}_valid" in top,
            f"{{{{j}}}} 已展开 {signal}，畸形 valid 模板已恢复",
            f"缺少 {{{{j}}}} 展开端口 {signal}",
        )
    result.check(
        re.search(r"(?m)^\s*sky_cs_if\.mst\s+chi_if_risc,$", top)
        and re.search(r"(?m)^\s*\.chi_if_risc\s+\(chi_if_risc\s*\),$", top),
        "interface 声明和实例连接正确",
        "interface 声明或连接缺失",
    )
    result.check(
        re.search(
            r"(?m)^\s*wire\s+\[`LANE_NUM\s*-1:0\]\[`Test_size\s*-1:0\]\s+w_array;$",
            top,
        )
        and re.search(
            r"(?m)^\s*output wire\s+\[`LANE_NUM\s*-1:0\]\[`Test_size\s*-1:0\]\s+array,?$",
            core,
        ),
        "带空格乘号已按原顺序转换为多维 packed array",
        "多维 packed array 声明不符合预期",
    )
    legacy_fallback_warnings = [
        item
        for item in reporter.items
        if item.level == "警告" and "位宽默认值无法确定" in item.message
    ]
    result.check(
        len(legacy_fallback_warnings) == 9,
        "9 个模板宏位宽均产生明确的 114 告警",
        f"旧模板 114 告警数量错误：{len(legacy_fallback_warnings)}",
    )
    unresolved_warnings = [
        item for item in reporter.items if "未绑定模板变量 i" in item.message
    ]
    result.check(
        len(unresolved_warnings) == 6,
        "j 端口中的未绑定 i 位宽产生 6 个明确的 114 告警",
        f"未绑定 i 告警数量错误：{len(unresolved_warnings)}",
    )
    return result, paths, reporter


def regression_round() -> RoundResult:
    result = RoundResult("第二轮：历史功能和 real_test 回归")
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    unit_result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    result.check(
        unit_result.wasSuccessful(),
        f"unittest {unit_result.testsRun}/{unit_result.testsRun} 通过",
        f"unittest 失败：failures={len(unit_result.failures)}, errors={len(unit_result.errors)}",
    )
    with tempfile.TemporaryDirectory() as temporary:
        matrix = run_matrix(Path(temporary) / "review_matrix")
    result.check(
        len(matrix) == 6 and all(item.passed for item in matrix),
        "Tech Review 1 matrix 6/6 通过",
        "Tech Review 1 matrix 存在回归失败",
    )
    for workbook in REAL_TESTS:
        paths, reporter = generate(
            workbook,
            ROOT / "unused-tech-review2-check",
            strict=True,
            check_only=True,
        )
        relative = workbook.relative_to(ROOT)
        result.check(
            bool(paths) and not reporter.has_errors and not reporter.has_warnings,
            f"{relative} --strict 通过",
            f"{relative} --strict 失败",
        )
    return result


def static_round(paths: list[Path]) -> RoundResult:
    result = RoundResult("第三轮：生成 Verilog 独立静态检视")
    reporter = Reporter()
    _, modules, integration = parse_workbook(SAMPLE, reporter)
    expected_files = {f"{name}.v" for name in modules}
    actual_files = {path.name for path in paths}
    result.check(actual_files == expected_files, "生成文件集合与模块集合一致", "生成文件集合不一致")

    for name, module in modules.items():
        path = SAMPLE_OUTPUT / f"{name}.v"
        if not path.exists():
            result.problems.append(f"缺少 {path.name}")
            continue
        text = path.read_text(encoding="utf-8")
        result.check(
            len(re.findall(rf"(?m)^module\s+{re.escape(name)}\b", text)) == 1
            and text.count("endmodule") == 1,
            f"{path.name} 模块边界唯一",
            f"{path.name} 模块边界错误",
        )
        result.check(
            text.count("(") == text.count(")"),
            f"{path.name} 括号平衡",
            f"{path.name} 括号不平衡",
        )
        header = text[text.find("module ") : text.find(");")]
        header_without_comments = "\n".join(
            line.split("//", 1)[0] for line in header.splitlines()
        )
        missing_ports = [
            port.name
            for port in module.ports
            if len(
                re.findall(
                    rf"\b{re.escape(port.name)}\b", header_without_comments
                )
            )
            != 1
        ]
        result.check(
            not missing_ports,
            f"{path.name} 端口列表完整且无重复",
            f"{path.name} 端口列表异常：{missing_ports}",
        )
        if integration and module.name == integration.top_name:
            continue
        for port in module.ports:
            if port.direction != "output":
                continue
            target = rf"assign\s+{re.escape(port.name)}(?:\s*\[|\s*=)"
            result.check(
                re.search(target, text) is not None,
                f"{path.name}.{port.name} output 已赋零",
                f"{path.name}.{port.name} output 未赋零",
            )

    if integration:
        top = (SAMPLE_OUTPUT / f"{integration.top_name}.v").read_text(encoding="utf-8")
        for child_name in integration.child_names:
            child = modules[child_name]
            match = re.search(
                rf"\bu_{re.escape(child_name.lower())}\s*\((.*?)\n\s*\);",
                top,
                re.S,
            )
            if not match:
                result.problems.append(f"缺少实例 u_{child_name.lower()}")
                continue
            body = match.group(1)
            duplicates = [
                port.name
                for port in child.ports
                if len(re.findall(rf"\.{re.escape(port.name)}\s+\(", body)) != 1
            ]
            result.check(
                not duplicates,
                f"{child_name} 每个端口恰好连接一次",
                f"{child_name} 端口连接数量异常：{duplicates}",
            )
        result.check(
            TEMPLATE_RE.search(top) is None,
            "生成代码中不再存在命名模板占位符",
            "生成代码仍包含命名模板占位符",
        )
    result.check(
        not list(SAMPLE_OUTPUT.glob("*.tmp")),
        "生成目录无残留临时文件",
        "生成目录存在残留 .tmp 文件",
    )
    return result


def write_report(path: Path, rounds: list[RoundResult], diagnostics: Reporter) -> None:
    lines = [
        "# Tech Review 2 三轮检视报告",
        "",
        "本报告由 `tests/run_tech_review2_review.py` 生成，覆盖新功能、历史回归和生成代码静态结构。",
        "",
        "## 汇总",
        "",
        "| 轮次 | 结果 | 检视点 |",
        "|---|---|---|",
    ]
    for item in rounds:
        lines.append(
            f"| {item.title} | {'通过' if item.passed else '失败'} | "
            f"{len(item.checks)} 项通过，{len(item.problems)} 项问题 |"
        )
    for item in rounds:
        lines.extend(["", f"## {item.title}", ""])
        lines.extend(f"- PASS：{message}" for message in item.checks)
        lines.extend(f"- FAIL：{message}" for message in item.problems)
    sentinel = [item.message for item in diagnostics.items if "占位值 114" in item.message]
    width_mismatches = [item.message for item in diagnostics.items if "位宽不匹配" in item.message]
    multiple_drivers = [item.message for item in diagnostics.items if "多个子模块驱动端" in item.message]
    lines.extend(
        [
            "",
            "## 诊断分类",
            "",
            f"- 脚本问题：{'0' if all(item.passed for item in rounds) else '见 FAIL 项'}。",
            f"- XLSX 待确认数据：{len(sentinel)} 条模板位宽无法确定，已告警并使用 114。",
            f"- 项目定义差异：样例保留 {len(width_mismatches)} 条 APB 位宽不匹配告警。",
            f"- 项目定义待确认：{len(multiple_drivers)} 个 test_bus valid TOP output 同时连接 CORE/MEM output，已生成但明确告警多驱动。",
            "- 工具边界：当前环境未安装 iverilog/verilator，第三轮使用独立静态结构检视；interface 的最终编译仍需项目提供对应 interface 定义。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    first, paths, diagnostics = feature_round()
    second = regression_round()
    third = static_round(paths)
    rounds = [first, second, third]
    report = ROOT / "review_test_cases/TechReview2检视报告.md"
    write_report(report, rounds, diagnostics)
    for item in rounds:
        print(f"{'PASS' if item.passed else 'FAIL'} {item.title}")
    print(f"报告: {report}")
    return 0 if all(item.passed for item in rounds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
