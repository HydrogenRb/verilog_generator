from __future__ import annotations

import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from appendix.xvlink_core import (
    connection_suggestions,
    export_project,
    load_project,
    load_workbook_model,
    new_project,
    validate_project,
    write_integration_workbook,
)
from tests.run_review_matrix import module_sheet, write_xlsx
from xlsx2verilog import Reporter, parse_workbook


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "test.xlsx"


class AppendixCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_workbook_model(SAMPLE)
        cls.roles = {"top": "RISCV_TOP", "child_a": "RISCV_CORE_TEST", "child_b": "MEM_PHY"}

    def test_shared_parser_exposes_full_port_metadata_and_suggestions(self) -> None:
        self.assertEqual(len(self.model["modules"]), 3)
        core = next(module for module in self.model["modules"] if module["name"] == "RISCV_CORE_TEST")
        array = next(port for port in core["ports"] if port["name"] == "array")
        self.assertEqual(len(array["packed"]), 1)
        self.assertEqual(array["width"]["expression"], "`TEST_SIZE")
        suggestions = connection_suggestions(self.model, self.roles)
        self.assertTrue(any(item["confidence"] == "high" for item in suggestions))
        self.assertTrue(all(item["reasons"] for item in suggestions))

    def test_validation_blocks_multiple_outputs(self) -> None:
        project = new_project(self.model["source"], self.model["fingerprint"], self.roles)
        project["networks"] = [
            {
                "id": "bad-net",
                "endpoints": [
                    "RISCV_TOP:test_bus_sig1_valid",
                    "RISCV_CORE_TEST:test_bus_sig1_valid",
                    "MEM_PHY:test_bus_sig1_valid",
                ],
            }
        ]
        diagnostics = validate_project(self.model, project)
        self.assertTrue(any(item["code"] == "MULTI_DRIVER" and item["level"] == "error" for item in diagnostics))

    def test_export_is_atomic_preserves_module_xml_and_passes_generator_check(self) -> None:
        project = new_project(self.model["source"], self.model["fingerprint"], self.roles)
        project["confirmed_unconnected"] = [
            port["id"]
            for module in self.model["modules"]
            if module["name"] in {self.roles["child_a"], self.roles["child_b"]}
            for port in module["ports"]
        ]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "中文 导出.xlsx"
            result = export_project(self.model, project, str(output))
            self.assertTrue(Path(result["path"]).samefile(output))
            second_output = Path(temporary) / "再次导出.xlsx"
            export_project(self.model, project, str(second_output))
            self.assertEqual(output.read_bytes(), second_output.read_bytes())
            reporter = Reporter()
            _, _, integration = parse_workbook(output, reporter)
            self.assertFalse(reporter.has_errors)
            self.assertIsNotNone(integration)
            with zipfile.ZipFile(SAMPLE) as original, zipfile.ZipFile(output) as exported:
                # Existing worksheet parts other than the replaced first
                # integration sheet remain byte-for-byte unchanged.
                for name in ["xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml", "xl/worksheets/sheet4.xml"]:
                    self.assertEqual(original.read(name), exported.read(name))

    def test_source_overwrite_requires_explicit_authority(self) -> None:
        with self.assertRaisesRegex(ValueError, "禁止覆盖"):
            write_integration_workbook(SAMPLE, SAMPLE, {(1, 1): "TOP"}, validate=False)

    def test_export_adds_integration_sheet_when_source_has_only_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "modules only.xlsx"
            write_xlsx(
                source,
                [
                    ("TOP", module_sheet("TOP", [("clk", 1, None, "i")])),
                    ("A", module_sheet("A", [("clk", 1, None, "i")])),
                    ("B", module_sheet("B", [("clk", 1, None, "i")])),
                ],
            )
            model = load_workbook_model(source)
            roles = {"top": "TOP", "child_a": "A", "child_b": "B"}
            project = new_project(model["source"], model["fingerprint"], roles)
            project["confirmed_unconnected"] = ["A:clk", "B:clk"]
            output = root / "exported.xlsx"
            export_project(model, project, str(output))
            reporter = Reporter()
            _, _, integration = parse_workbook(output, reporter)
            self.assertFalse(reporter.has_errors)
            self.assertIsNotNone(integration)
            assert integration is not None
            self.assertEqual(integration.top_name, "TOP")
            self.assertEqual(integration.child_names, ["A", "B"])

    def test_three_thousand_port_suggestion_index_is_bounded(self) -> None:
        modules = []
        for module_name, direction in [("TOP", "input"), ("A", "input"), ("B", "input")]:
            ports = [
                {
                    "id": f"{module_name}:sig_{index}", "module": module_name, "name": f"sig_{index}",
                    "direction": direction, "width": {"kind": "literal", "expression": "8", "default": "8"},
                    "packed": [], "arrays": [], "interface": None, "category": "data",
                    "template_source": None, "template_values": [],
                }
                for index in range(1000)
            ]
            modules.append({"name": module_name, "ports": ports})
        model = {"modules": modules}
        start = time.perf_counter()
        suggestions = connection_suggestions(model, {"top": "TOP", "child_a": "A", "child_b": "B"})
        elapsed = time.perf_counter() - start
        # TOP input may fan out to two child inputs; two child inputs alone
        # are not a driven internal network and are intentionally not suggested.
        self.assertEqual(len(suggestions), 2000)
        self.assertLess(elapsed, 5.0)

    def test_visual_assets_are_offline_and_present(self) -> None:
        static = ROOT / "appendix" / "static"
        for name in ["designer.html", "designer.js", "viewer.html", "viewer.js", "common.css"]:
            content = (static / name).read_text(encoding="utf-8")
            self.assertNotIn("https://", content)
            self.assertGreater(len(content), 100)
        example = load_project(
            ROOT / "appendix" / "examples" / "09_version_2_demo.xvlink.json"
        )
        self.assertFalse(example["source_changed"])
        self.assertEqual(len(example["networks"]), 2)


if __name__ == "__main__":
    unittest.main()
