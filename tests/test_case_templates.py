from __future__ import annotations

import importlib.util
import json
import os as _os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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
    "CFD_OBJ": "hs5_cfd.obj",
    "RAW_CFD_OBJ": "constant/triSurface/hs5_cfd.obj",
    "NORMALIZED_CFD_OBJ": "constant/triSurface/hs5_cfd.openfoam.obj",
    "OBJ_REGION_MANIFEST": "obj_region_manifest.json",
    "PARAMETERS_JSON": json.dumps({"inletVelocity": {"expression": "10"}}, indent=2, ensure_ascii=False),
    "CFD_NAMING_CONTRACT_JSON": json.dumps({
        "master": "patch_inlet|patch_heatsource|patch_outlet|wall",
        "mrf": "mrf",
        "master_1": "aluminum",
    }, indent=2, ensure_ascii=False),
}


class CaseTemplateRenderTests(unittest.TestCase):
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
        self.assertEqual("constant/triSurface/hs5_cfd.obj", manifest["geometry"]["rawCfdObj"])
        self.assertEqual("obj_region_manifest.json", manifest["geometry"]["objRegionManifest"])

    def test_case_manifest_has_all_six_regions(self) -> None:
        rendered = render_text(CASE_MANIFEST_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        manifest = json.loads(rendered)
        regions = manifest["openfoamRegions"]
        self.assertEqual(regions["patch_inlet"], "inlet")
        self.assertEqual(regions["patch_heatsource"], "heat_source")
        self.assertEqual(regions["patch_outlet"], "outlet")
        self.assertEqual(regions["wall"], "wall")
        self.assertEqual(regions["mrf"], "fan_mrf_zone")
        self.assertEqual(regions["aluminum"], "aluminum")
        self.assertEqual(len(regions), 6)

    def test_allrun_surfacecheck_uses_only_normalized_obj(self) -> None:
        rendered = render_text(ALLRUN_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        self.assertIn(
            "surfaceCheck constant/triSurface/hs5_cfd.openfoam.obj",
            rendered,
        )
        # No raw OBJ or MTL in the surfaceCheck command
        self.assertNotIn("hs5_cfd.obj", rendered.split("surfaceCheck")[0] if "surfaceCheck" in rendered else rendered)
        # The only surfaceCheck line should reference the normalized OBJ
        check_lines = [line for line in rendered.splitlines() if line.startswith("surfaceCheck")]
        self.assertEqual(len(check_lines), 1)
        self.assertIn("hs5_cfd.openfoam.obj", check_lines[0])

    def test_allrun_no_raw_obj_or_mtl_reference(self) -> None:
        rendered = render_text(ALLRUN_TPL.read_text(encoding="utf-8"), STATIC_CONTEXT)
        self.assertNotIn("hs5_cfd.obj", rendered)
        self.assertNotIn(".mtl", rendered)

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
        setattr(inventor_client, "export_obj", lambda *a, **k: None)
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

            # Raw provenance — README distinguishes raw export from runtime input
            self.assertIn("raw export provenance", readme_text)
            self.assertIn("hs5_cfd.obj", readme_text)

            # Normalized runtime input
            self.assertIn("hs5_cfd.openfoam.obj", readme_text)

            # Six region names
            self.assertIn("patch_inlet", readme_text)
            self.assertIn("patch_heatsource", readme_text)
            self.assertIn("patch_outlet", readme_text)
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
            self.assertEqual(len(manifest["openfoamRegions"]), 6)

            allrun = (output / "Allrun").read_text(encoding="utf-8")
            self.assertIn("surfaceCheck constant/triSurface/hs5_cfd.openfoam.obj", allrun)
            self.assertNotIn("[[", allrun)


if __name__ == "__main__":
    unittest.main()
