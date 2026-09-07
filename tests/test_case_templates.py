from __future__ import annotations

import importlib.util
import json
import os as _os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.case_render import render_case_files
from tools.template_render import render_text, render_file


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "openfoam" / "templates"
CASE_MANIFEST_TPL = TEMPLATE_DIR / "case_manifest.json.tmpl"
ALLRUN_TPL = TEMPLATE_DIR / "Allrun.tmpl"


# Static context that mirrors prepare_case() output.
# No live Inventor, no COM, no export — pure static render test.
STATIC_CONTEXT: dict[str, object] = {
    "CASE_ID": "finless-current",
    "GENERATED_AT": "2026-01-01T00:00:00+00:00",
    "CFD_IPT_JSON": r'"/c/Users/penta/Documents/a5100/hs5/hs5_cfd.ipt"',
    "MASTER_PROFILE_OBJ": "constant/triSurface/master.obj",
    "MRF_PROFILE_OBJ": "constant/triSurface/mrf.obj",
    "ALUMINUM_PROFILE_OBJ": "constant/triSurface/master_1.obj",
    "NORMALIZED_CFD_OBJ": "constant/triSurface/hs5_cfd.openfoam.obj",
    "MRF_ZONE_OBJ": "constant/geometry/mrf-zone.obj",
    "ALUMINUM_ZONE_OBJ": "constant/geometry/aluminum-zone.obj",
    "OBJ_REGION_MANIFEST": "obj_region_manifest.json",
    "LOCATION_IN_MESH_M": "(0.036 0.020 0)",
    "FAN_ORIGIN_M": "(0.036 0.020 0.004)",
    "PARAMETERS_JSON": json.dumps({"inletVelocity": {"expression": "10"}}, indent=2, ensure_ascii=False),
    "CFD_NAMING_CONTRACT_JSON": json.dumps({
        "master": "patch_inlet|patch_heatsource|patch_outlet|patch_blade|wall",
        "mrf": "mrf",
        "master_1": "aluminum",
    }, indent=2, ensure_ascii=False),
    "BLOCK_XMIN": "-0.4",
    "BLOCK_XMAX": "51.460863",
    "BLOCK_YMIN": "-0.4",
    "BLOCK_YMAX": "39.400001",
    "BLOCK_ZMIN": "-8.4",
    "BLOCK_ZMAX": "4.4",
    "BLOCK_NX": 130,
    "BLOCK_NY": 100,
    "BLOCK_NZ": 32,
}


class CaseTemplateRenderTests(unittest.TestCase):
    def test_cht_templates_render_a_complete_two_region_smoke_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for template in TEMPLATE_DIR.rglob("*.tmpl"):
                destination = output / template.relative_to(TEMPLATE_DIR).with_suffix("")
                render_file(template, destination, STATIC_CONTEXT)

            required = {
                "constant/regionProperties",
                "constant/fluid/MRFProperties",
                "constant/fluid/physicalProperties",
                "constant/fluid/thermophysicalProperties",
                "constant/aluminum/physicalProperties",
                "constant/fluid/turbulenceProperties",
                "constant/aluminum/thermophysicalProperties",
                "0/fluid/U", "0/fluid/p", "0/fluid/T", "0/fluid/k", "0/fluid/omega", "0/fluid/nut", "0/fluid/alphat",
                "0/aluminum/T",
                "system/fluid/fvSchemes", "system/fluid/fvSolution",
                "system/aluminum/fvSchemes", "system/aluminum/fvSolution",
            }
            self.assertTrue(all((output / path).is_file() for path in required))
            self.assertTrue(all("[[" not in (output / path).read_text(encoding="utf-8") for path in required))

    def test_cht_conditions_preserve_the_split_interface_power_and_mrf_contract(self) -> None:
        def rendered(path: str) -> str:
            return render_text((TEMPLATE_DIR / path).read_text(encoding="utf-8"), STATIC_CONTEXT)

        region_properties = rendered("constant/regionProperties.tmpl")
        mrf = rendered("constant/fluid/MRFProperties.tmpl")
        fluid_physical = rendered("constant/fluid/physicalProperties.tmpl")
        aluminum_physical = rendered("constant/aluminum/physicalProperties.tmpl")
        fluid_u = rendered("0/fluid/U.tmpl")
        fluid_t = rendered("0/fluid/T.tmpl")
        aluminum_t = rendered("0/aluminum/T.tmpl")
        alphat = rendered("0/fluid/alphat.tmpl")
        fluid_schemes = rendered("system/fluid/fvSchemes.tmpl")
        fluid_solution = rendered("system/fluid/fvSolution.tmpl")
        control = rendered("system/controlDict.tmpl")

        self.assertIn("fluid (fluid)", region_properties)
        self.assertIn("solid (aluminum)", region_properties)
        self.assertIn("cellZone            mrf;", mrf)
        self.assertIn("origin              (0.036 0.020 0.004);", mrf)
        self.assertIn("axis                (0 0 1);", mrf)
        self.assertIn("omega               1256.63706144;", mrf)
        self.assertIn("nonRotatingPatches  ();", mrf)
        self.assertIn("patch_blade  { type MRFnoSlip;", fluid_u)
        self.assertNotIn("MRFnoSlip", aluminum_t)
        self.assertIn("type compressible::alphatWallFunction;", alphat)
        self.assertIn("thermoType", fluid_physical)
        self.assertIn("type            heRhoThermo;", fluid_physical)
        self.assertIn("equationOfState rhoConst;", fluid_physical)
        self.assertIn("equationOfState { rho 1.204; }", fluid_physical)
        self.assertIn("type            heSolidThermo;", aluminum_physical)
        self.assertIn("ddtSchemes { default steadyState; }", fluid_schemes)
        self.assertIn("flow false;", fluid_solution)
        self.assertIn("thermophysics true;", fluid_solution)
        self.assertIn("nOuterCorrectors 2;", fluid_solution)
        self.assertIn("residualControl", fluid_solution)
        self.assertRegex(fluid_solution, r"\bh\s+1e-5;")
        for field in ("p", "U", "k", "omega"):
            self.assertNotRegex(fluid_solution, rf"\b{field}\s+1e-5;")
        self.assertIn("e 1e-6;", rendered("system/aluminum/fvSolution.tmpl"))
        self.assertIn("fluid_to_aluminum { type coupledTemperature;", fluid_t)
        self.assertIn("aluminum_to_fluid { type coupledTemperature;", aluminum_t)
        self.assertIn("mode power;", aluminum_t)
        self.assertIn("Q uniform 5;", aluminum_t)
        self.assertNotIn("q uniform 4", aluminum_t)
        self.assertIn("wall { type zeroGradient; }", aluminum_t)
        self.assertIn("application     foamMultiRun;", control)
        self.assertIn("fluid       fluid;", control)
        self.assertIn("aluminum    solid;", control)
        self.assertIn("startFrom       latestTime;", control)
        self.assertIn("endTime         1000;", control)
        self.assertIn("deltaT          1;", control)
        self.assertIn("adjustTimeStep  no;", control)
        self.assertIn("writeFormat     ascii;", control)
        self.assertIn("writeInterval   100;", control)
        self.assertNotIn("maxCo", control)
        self.assertNotIn("chtMultiRegionFoam", rendered("Allrun.tmpl"))

    def test_renderer_creates_paraview_marker_and_two_region_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "case"
            render_case_files(output, STATIC_CONTEXT, TEMPLATE_DIR)

            expected_paths = {
                "hs5-cht.foam",
                "constant/regionProperties",
                "constant/fluid/MRFProperties",
                "constant/fluid/physicalProperties",
                "constant/aluminum/physicalProperties",
                "0/fluid/alphat",
                "system/fluid/fvSchemes",
                "system/fluid/fvSolution",
                "system/aluminum/fvSchemes",
                "system/aluminum/fvSolution",
                "system/controlDict",
            }
            self.assertTrue(all((output / path).is_file() for path in expected_paths))
            self.assertEqual("", (output / "hs5-cht.foam").read_text(encoding="utf-8"))
    def test_case_manifest_is_valid_json(self) -> None:
        rendered = render_text(CASE_MANIFEST_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        manifest = json.loads(rendered)
        self.assertIsInstance(manifest, dict)
        self.assertIn("geometry", manifest)

    def test_case_manifest_points_to_normalized_obj(self) -> None:
        rendered = render_text(CASE_MANIFEST_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        manifest = json.loads(rendered)
        self.assertEqual(
            "constant/triSurface/hs5_cfd.openfoam.obj",
            manifest["geometry"]["cfdObj"],
        )
        self.assertEqual("constant/triSurface/master.obj", manifest["geometry"]["masterProfileObj"])
        self.assertEqual("constant/geometry/mrf-zone.obj", manifest["geometry"]["mrfZoneObj"])
        self.assertEqual("obj_region_manifest.json", manifest["geometry"]["objRegionManifest"])

    def test_case_manifest_has_all_seven_regions(self) -> None:
        rendered = render_text(CASE_MANIFEST_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        manifest = json.loads(rendered)
        regions = manifest["openfoamRegions"]
        self.assertEqual(regions["patch_inlet"], "inlet")
        self.assertEqual(regions["patch_heatsource"], "heat_source")
        self.assertEqual(regions["patch_outlet"], "outlet")
        self.assertEqual(regions["patch_blade"], "blade_cavity_wall")
        self.assertEqual(regions["wall"], "wall")
        self.assertEqual(regions["mrf"], "fan_mrf_zone")
        self.assertEqual(regions["aluminum"], "aluminum")
        self.assertEqual(len(regions), 7)

    def test_allrun_surfacecheck_uses_only_normalized_obj(self) -> None:
        rendered = render_text(ALLRUN_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        self.assertIn(
            'surfaceCheck "$CASE_DIR/constant/triSurface/hs5_cfd.openfoam.obj"',
            rendered,
        )
        # No raw OBJ or MTL in the surfaceCheck command
        self.assertNotIn("hs5_cfd.obj", rendered.split("surfaceCheck")[0] if "surfaceCheck" in rendered else rendered)
        check_lines = [line for line in rendered.splitlines() if line.startswith("surfaceCheck")]
        self.assertEqual(len(check_lines), 3)
        self.assertIn("hs5_cfd.openfoam.obj", check_lines[0])

    def test_allrun_excludes_staged_aluminum_helper_from_location_in_mesh(self) -> None:
        rendered = render_text(ALLRUN_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        location_command = next(line for line in rendered.splitlines() if "verify_location_in_mesh.py" in line)
        self.assertIn('--exclude-closed-surface "$CASE_DIR/constant/geometry/aluminum-zone.obj"', location_command)

    def test_allrun_uses_staged_helpers_without_profile_extraction(self) -> None:
        rendered = render_text(ALLRUN_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        self.assertNotIn("extract_obj_regions.py", rendered)
        self.assertNotIn(".mtl", rendered)
        self.assertIn('surfaceCheck "$CASE_DIR/constant/geometry/mrf-zone.obj"', rendered)
        self.assertIn('surfaceCheck "$CASE_DIR/constant/geometry/aluminum-zone.obj"', rendered)

    def test_no_unresolved_tokens_in_manifest(self) -> None:
        rendered = render_text(CASE_MANIFEST_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        self.assertNotIn("[[", rendered)

    def test_no_unresolved_tokens_in_allrun(self) -> None:
        rendered = render_text(ALLRUN_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        self.assertNotIn("[[", rendered)

    def test_readme_distinguishes_raw_and_normalized(self) -> None:
        """Verify copy_readme() produces correct content without live Inventor."""
        import importlib.util
        import types

        # Build fake modules so prepare_openfoam_case.py can be loaded without
        # importing pythoncom or other Inventor-dependent code.
        pythoncom = types.ModuleType("pythoncom")
        setattr(pythoncom, "CoInitialize", lambda *a, **k: None)
        setattr(pythoncom, "CoUninitialize", lambda *a, **k: None)

        inventor_client = types.ModuleType("inventor_client")
        setattr(inventor_client, "InventorPaths", type("InventorPaths", (), {}))
        setattr(inventor_client, "collect_state", lambda *a, **k: {})
        setattr(inventor_client, "connect_inventor", lambda *a, **k: None)
        setattr(inventor_client, "export_obj_profiles", lambda *a, **k: None)
        setattr(inventor_client, "open_part", lambda *a, **k: None)

        # write_text_atomic must actually write to disk so the generated README
        # can be read and asserted against.
        setattr(
            inventor_client,
            "write_text_atomic",
            lambda path: _os.makedirs(path.parent, exist_ok=True)
            and path.write_text("placeholder", encoding="utf-8"),
        )

        obj_region_normalizer = types.ModuleType("obj_region_normalizer")
        setattr(obj_region_normalizer, "normalize_obj_file", lambda *a, **k: None)

        template_render = types.ModuleType("template_render")
        setattr(template_render, "render_text", lambda *a, **k: "")

        spec = importlib.util.spec_from_file_location(
            "prepare_openfoam_case_test",
            Path(__file__).resolve().parents[1] / "tools" / "prepare_openfoam_case.py",
        )
        assert spec is not None and spec.loader is not None
        subject = importlib.util.module_from_spec(spec)
        import sys
        with patch.dict(sys.modules, {
            "pythoncom": pythoncom,
            "inventor_client": inventor_client,
            "obj_region_normalizer": obj_region_normalizer,
            "template_render": template_render,
        }):
            spec.loader.exec_module(subject)

        # Call copy_readme() on a real temp directory — no live Inventor.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            subject.copy_readme(output)
            readme = output / "README.md"
            self.assertTrue(readme.exists(), "copy_readme() did not write README.md")
            readme_text = readme.read_text(encoding="utf-8")

            self.assertIn("canonical profile exports", readme_text)
            self.assertIn("master.obj", readme_text)
            self.assertIn("mrf.obj", readme_text)
            self.assertIn("master_1.obj", readme_text)

            # Normalized runtime input
            self.assertIn("hs5_cfd.openfoam.obj", readme_text)

            # Six region names
            self.assertIn("patch_inlet", readme_text)
            self.assertIn("patch_heatsource", readme_text)
            self.assertIn("patch_outlet", readme_text)
            self.assertIn("patch_blade", readme_text)
            self.assertIn("wall", readme_text)
            self.assertIn("mrf", readme_text)
            self.assertIn("aluminum", readme_text)

    def test_render_into_temp_directory_produces_valid_artifacts(self) -> None:
        """Full pipeline: render templates into a temp dir, parse JSON, check paths."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            render_file(CASE_MANIFEST_TPL, output / "case_manifest.json", STATIC_CONTEXT)
            render_file(ALLRUN_TPL, output / "Allrun", STATIC_CONTEXT)

            manifest = json.loads((output / "case_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                "constant/triSurface/hs5_cfd.openfoam.obj",
                manifest["geometry"]["cfdObj"],
            )
            self.assertIn("openfoamRegions", manifest)
            self.assertEqual(len(manifest["openfoamRegions"]), 7)

            allrun = (output / "Allrun").read_text(encoding="utf-8")
            self.assertIn('surfaceCheck "$CASE_DIR/constant/triSurface/hs5_cfd.openfoam.obj"', allrun)
            self.assertNotIn("[[", allrun)

    def test_allrun_fails_before_meshing_when_preflight_gates_fail(self) -> None:
        rendered = render_text(ALLRUN_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        self.assertIn("set -euo pipefail", rendered)
        self.assertLess(rendered.index("source /opt/openfoam14/etc/bashrc"), rendered.index("set -euo pipefail"))
        ordered = (
            "verify_location_in_mesh.py",
            'surfaceCheck "$CASE_DIR/constant/triSurface/hs5_cfd.openfoam.obj"',
            "log.surfaceCheck.mrf-zone",
            "log.surfaceCheck.aluminum-zone",
            "--require-closed mrf-zone",
            "--require-closed aluminum-zone",
            "blockMesh",
            "log.checkMesh",
            "--check-mesh log.checkMesh",
            "createZones",
            "--pre-split --boundary constant/polyMesh/boundary --cell-zones constant/polyMesh/cellZones",
        )
        positions = [rendered.index(item) for item in ordered]
        self.assertEqual(positions, sorted(positions))
        split_command = "splitMeshRegions -cellZones aluminum -defaultRegion fluid | tee log.splitMeshRegions"
        post_split_gate = (
            '--post-split --fluid-boundary constant/fluid/polyMesh/boundary '
            '--fluid-cell-zones constant/fluid/polyMesh/cellZones '
            '--aluminum-boundary constant/aluminum/polyMesh/boundary'
        )
        self.assertIn(split_command, rendered)
        self.assertIn("checkMesh -region fluid -allTopology -allGeometry | tee log.checkMesh.fluid", rendered)
        self.assertIn("checkMesh -region aluminum -allTopology -allGeometry | tee log.checkMesh.aluminum", rendered)
        self.assertIn(post_split_gate, rendered)
        self.assertLess(rendered.index("--pre-split"), rendered.index(split_command))
        self.assertLess(rendered.index(split_command), rendered.index(post_split_gate))


if __name__ == "__main__":
    unittest.main()
