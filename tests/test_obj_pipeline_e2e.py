from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tools.obj_region_normalizer import normalize_obj_file
from tools.template_render import render_file


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "obj_regions" / "source.obj"
TEMPLATES = ROOT / "openfoam" / "templates"
EXPECTED_COUNTS = {
    "patch_inlet": 1,
    "patch_heatsource": 1,
    "patch_outlet": 2,
    "patch_blade": 0,
    "wall": 5,
    "mrf": 1,
    "aluminum": 1,
}
STATIC_CONTEXT: dict[str, object] = {
    "CASE_ID": "static-obj-pipeline",
    "GENERATED_AT": "2026-01-01T00:00:00+00:00",
    "CFD_IPT_JSON": '"static-only.ipt"',
    "MASTER_PROFILE_OBJ": "constant/triSurface/master.obj",
    "MRF_PROFILE_OBJ": "constant/triSurface/mrf.obj",
    "ALUMINUM_PROFILE_OBJ": "constant/triSurface/master_1.obj",
    "NORMALIZED_CFD_OBJ": "constant/triSurface/hs5_cfd.openfoam.obj",
    "MRF_ZONE_OBJ": "constant/geometry/mrf-zone.obj",
    "ALUMINUM_ZONE_OBJ": "constant/geometry/aluminum-zone.obj",
    "OBJ_REGION_MANIFEST": "obj_region_manifest.json",
    "LOCATION_IN_MESH_M": "(0.036 0.020 0)",
    "PARAMETERS_JSON": "{}",
    "CFD_NAMING_CONTRACT_JSON": "{}",
}


class ObjPipelineEndToEndTests(unittest.TestCase):
    def test_fixture_pipeline_normalizes_and_renders_linked_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / "case"
            surface_dir = case / "constant" / "triSurface"
            source = surface_dir / "master.obj"
            normalized = surface_dir / "hs5_cfd.openfoam.obj"
            region_manifest_path = case / "obj_region_manifest.json"
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, source)

            normalize_obj_file(source, normalized, region_manifest_path)
            render_file(TEMPLATES / "case_manifest.json.tmpl", case / "case_manifest.json", STATIC_CONTEXT)
            render_file(TEMPLATES / "Allrun.tmpl", case / "Allrun", STATIC_CONTEXT)

            region_manifest = json.loads(region_manifest_path.read_text(encoding="utf-8"))
            case_manifest = json.loads((case / "case_manifest.json").read_text(encoding="utf-8"))
            normalized_text = normalized.read_text(encoding="utf-8")
            allrun = (case / "Allrun").read_text(encoding="utf-8")

            self.assertEqual(EXPECTED_COUNTS, region_manifest["regionFaceCounts"])
            self.assertEqual(
                [{"rgb": "12,34,56", "faceCount": 2, "mappedRole": "wall"}],
                region_manifest["unknownColors"],
            )
            self.assertEqual(
                ["unknown master RGB 12,34,56 mapped to wall (2 faces)"],
                region_manifest["warnings"],
            )
            self.assertEqual("constant/triSurface/master.obj", region_manifest["sourceObj"])
            self.assertEqual("constant/triSurface/hs5_cfd.openfoam.obj", region_manifest["normalizedObj"])
            self.assertEqual(region_manifest["normalizedObj"], case_manifest["geometry"]["cfdObj"])
            self.assertEqual(region_manifest["sourceObj"], case_manifest["geometry"]["masterProfileObj"])
            self.assertEqual("obj_region_manifest.json", case_manifest["geometry"]["objRegionManifest"])
            self.assertEqual(set(EXPECTED_COUNTS), set(case_manifest["openfoamRegions"]))
            self.assertNotIn("mtllib", normalized_text)
            self.assertNotIn("usemtl", normalized_text)
            self.assertFalse(list(case.rglob("*.mtl")))
            self.assertIn('surfaceCheck "$CASE_DIR/constant/triSurface/hs5_cfd.openfoam.obj"', allrun)
            self.assertNotIn("[[", normalized_text + allrun + json.dumps(case_manifest))


if __name__ == "__main__":
    unittest.main()
