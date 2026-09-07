from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.case_render import CASE_TOOLS, SOLVER_TOOL_DIRS, render_case_files


class CaseRenderTests(unittest.TestCase):
    def test_generated_official_case_contains_two_stage_entrypoints(self) -> None:
        case = Path(__file__).resolve().parents[1] / "openfoam" / "cases" / "hs5-cfd-current"

        self.assertEqual(
            (Path(__file__).resolve().parents[1] / "openfoam" / "templates" / "Allrun.flow.tmpl").read_text(encoding="utf-8"),
            (case / "Allrun.flow").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            (Path(__file__).resolve().parents[1] / "openfoam" / "templates" / "run-two-stage.sh.tmpl").read_text(encoding="utf-8"),
            (case / "run-two-stage.sh").read_text(encoding="utf-8"),
        )

    def test_renders_nested_templates_and_copies_required_pure_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            templates = root / "templates"
            (templates / "system").mkdir(parents=True)
            (templates / "system" / "controlDict.tmpl").write_text("name [[CASE_ID]]\n", encoding="utf-8")
            (templates / "Allclean.tmpl").write_text("clean [[CASE_ID]]\n", encoding="utf-8")
            render_case_files(root / "case", {"CASE_ID": "static-case"}, templates)

            self.assertEqual("name static-case\n", (root / "case" / "system" / "controlDict").read_text(encoding="utf-8"))
            self.assertEqual("clean static-case\n", (root / "case" / "Allclean").read_text(encoding="utf-8"))
            self.assertTrue(set(CASE_TOOLS).issubset({path.name for path in (root / "case" / "tools").iterdir()}))
            self.assertTrue(set(SOLVER_TOOL_DIRS).issubset({path.name for path in (root / "case" / "tools").iterdir()}))
            self.assertIn("verify_mesh_contract.py", CASE_TOOLS)
            self.assertIn("flow_runner.py", CASE_TOOLS)
            self.assertTrue((root / "case" / "tools" / "fixed_phi_thermal_solver" / "fixedPhiThermalFoam.C").is_file())

    def test_allclean_targets_only_generated_mesh_artifacts(self) -> None:
        allclean = (Path(__file__).resolve().parents[1] / "openfoam" / "templates" / "Allclean.tmpl").read_text(encoding="utf-8")

        for generated in ("constant/polyMesh", "constant/fluid", "constant/aluminum", "processor*", "postProcessing", "log.*"):
            self.assertIn(generated, allclean)
        for protected in ("master.obj", "mrf.obj", "master_1.obj", "mrf-zone.obj", "aluminum-zone.obj", "hs5_cfd.openfoam.obj", ".mtl", "obj_region_manifest.json", "cad_state.json"):
            self.assertNotIn(protected, allclean)

    def test_shell_templates_use_lf_line_endings_for_remote_bash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            templates = root / "templates"
            templates.mkdir()
            (templates / "Allclean.tmpl").write_bytes(b"#!/usr/bin/env bash\nset -euo pipefail\n")

            render_case_files(root / "case", {"CASE_ID": "static-case"}, templates)

            self.assertNotIn(b"\r\n", (root / "case" / "Allclean").read_bytes())

    def test_stages_normalized_surface_for_v14_bare_tri_surface_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "case"
            templates = root / "templates"
            templates.mkdir()
            source = case / "constant" / "triSurface" / "hs5_cfd.openfoam.obj"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"g wall\nv 0 0 0\n")

            render_case_files(case, {"CASE_ID": "static-case"}, templates)

            staged = case / "constant" / "geometry" / "hs5_cfd.openfoam.obj"
            self.assertEqual(source.read_bytes(), staged.read_bytes())

    def test_stages_helper_profiles_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / "case"
            source_dir = case / "constant" / "triSurface"
            source_dir.mkdir(parents=True)
            mrf = b"mrf\r\n\x00profile"
            aluminum = b"aluminum\nprofile"
            (source_dir / "mrf.obj").write_bytes(mrf)
            (source_dir / "master_1.obj").write_bytes(aluminum)

            render_case_files(case, {}, Path(directory) / "empty-templates")

            self.assertEqual(mrf, (case / "constant" / "geometry" / "mrf-zone.obj").read_bytes())
            self.assertEqual(aluminum, (case / "constant" / "geometry" / "aluminum-zone.obj").read_bytes())


if __name__ == "__main__":
    unittest.main()
