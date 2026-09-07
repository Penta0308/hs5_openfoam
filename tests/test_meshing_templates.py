from __future__ import annotations

import re
import unittest
from pathlib import Path


SYSTEM_TEMPLATES = Path(__file__).resolve().parents[1] / "openfoam" / "templates" / "system"


class MeshingTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control = (SYSTEM_TEMPLATES / "controlDict.tmpl").read_text(encoding="utf-8")
        self.block = (SYSTEM_TEMPLATES / "blockMeshDict.tmpl").read_text(encoding="utf-8")
        self.snappy = (SYSTEM_TEMPLATES / "snappyHexMeshDict.tmpl").read_text(encoding="utf-8")
        self.create_zones = (SYSTEM_TEMPLATES / "createZonesDict.tmpl").read_text(encoding="utf-8")

    def test_control_dict_has_foam_multirun_steady_metadata(self) -> None:
        expected_entries = {
            "application": "foamMultiRun",
            "startFrom": "latestTime",
            "startTime": "0",
            "stopAt": "endTime",
            "endTime": "1000",
            "deltaT": "1",
            "adjustTimeStep": "no",
            "writeControl": "timeStep",
            "writeInterval": "100",
            "writeFormat": "ascii",
            "writePrecision": "12",
        }
        for key, value in expected_entries.items():
            self.assertRegex(self.control, rf"\b{key}\s+{re.escape(value)}\s*;")

    def test_block_mesh_uses_obj_derived_mm_bounds_scale_and_cells(self) -> None:
        self.assertRegex(self.block, r"\bconvertToMeters\s+0\.001\s*;")
        for token in (
            "[[BLOCK_XMIN]]", "[[BLOCK_XMAX]]", "[[BLOCK_YMIN]]", "[[BLOCK_YMAX]]",
            "[[BLOCK_ZMIN]]", "[[BLOCK_ZMAX]]", "[[BLOCK_NX]]", "[[BLOCK_NY]]", "[[BLOCK_NZ]]",
        ):
            self.assertIn(token, self.block)
        self.assertEqual(6, len(re.findall(r"\b[xyz](?:min|max)\s*\{\s*type wall;", self.block)))

    def test_snappy_uses_single_normalized_tri_surface_in_meters(self) -> None:
        self.assertRegex(self.snappy, r"\btype\s+triSurface\s*;")
        self.assertRegex(self.snappy, r"\bhs5_cfd\.openfoam\.obj\b")
        self.assertRegex(self.snappy, r'\bfile\s+"hs5_cfd\.openfoam\.obj"\s*;')
        self.assertRegex(self.snappy, r"\bscale\s+0\.001\s*;")
        self.assertNotIn("hs5_cfd.obj", self.snappy.replace("hs5_cfd.openfoam.obj", ""))

    def test_snappy_explicitly_maps_only_master_physical_regions_with_required_patch_types(self) -> None:
        for region in ("patch_inlet", "patch_heatsource", "patch_outlet", "patch_blade", "wall"):
            self.assertRegex(self.snappy, rf"(?ms)^\s*{region}\s*\{{.*?\bname\s+{region}\s*;")

        for region in ("patch_inlet", "patch_outlet"):
            self.assertRegex(
                self.snappy,
                rf"(?ms)^\s*{region}\s*\{{.*?patchInfo\s*\{{\s*type\s+patch\s*;\s*\}}",
            )
        for region in ("patch_heatsource", "patch_blade", "wall"):
            self.assertRegex(
                self.snappy,
                rf"(?ms)^\s*{region}\s*\{{.*?patchInfo\s*\{{\s*type\s+wall\s*;\s*\}}",
            )
        for helper in ("mrf", "aluminum"):
            self.assertNotRegex(self.snappy, rf"(?m)^\s*{helper}\s*\{{")

    def test_snappy_preserves_refinement_and_dynamic_location_contract(self) -> None:
        self.assertIn("0.2 mm bulk surface intent", self.snappy)
        self.assertNotIn("mrf", self.snappy)
        self.assertNotIn("aluminum", self.snappy)
        self.assertRegex(self.snappy, r"\baddLayers\s+false\s*;")
        self.assertRegex(self.snappy, r"\blocationInMesh\s+\[\[LOCATION_IN_MESH_M\]\]\s*;")
        self.assertRegex(self.snappy, r"\bminTetQuality\s+1e-15\s*;")
        self.assertRegex(self.snappy, r"\bminTwist\s+0\.02\s*;")
        self.assertRegex(self.snappy, r"\bpatch_blade\s*\{\s*level\s+\(2 2\)\s*;\s*\}")

    def test_create_zones_uses_two_top_level_v14_inside_surface_generators(self) -> None:
        self.assertNotIn("topoSet", self.create_zones)
        for name in ("mrf", "aluminum"):
            self.assertRegex(
                self.create_zones,
                rf"(?ms)^\s*{name}\s*\{{\s*type\s+insideSurface\s*;\s*"
                rf"zoneType\s+cell\s*;\s*surface\s+closedTriSurface\s*;\s*"
                rf"file\s+\"{name}-zone\.obj\"\s*;\s*scale\s+0\.001\s*;",
            )


if __name__ == "__main__":
    unittest.main()
