from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.extract_obj_regions import ObjRegionExtractionError, extract_region_file, extract_region_text


SOURCE = """mtllib ignored.mtl
v 0 0 0
v 1 0 0
v 0 1 0
v 0 0 1
v 2 0 0
v 2 1 0
g wall
f 1 2 3
g mrf
f 2/4/5 4/5/6 6/7/8
g aluminum
f 1 3 4
"""


class ExtractObjRegionsTests(unittest.TestCase):
    def test_extracts_only_requested_faces_with_reindexed_vertices(self) -> None:
        rendered = extract_region_text(SOURCE, "mrf")

        self.assertIn("g mrf\n", rendered)
        self.assertNotIn("mtllib", rendered)
        self.assertNotIn("g wall", rendered)
        self.assertNotIn("g aluminum", rendered)
        self.assertEqual(["v 1 0 0", "v 0 0 1", "v 2 1 0"], [line for line in rendered.splitlines() if line.startswith("v ")])
        self.assertEqual(["f 1 2 3"], [line for line in rendered.splitlines() if line.startswith("f ")])

    def test_file_extraction_uses_only_temporary_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "normalized.obj"
            output = Path(directory) / "geometry" / "aluminum-zone.obj"
            source.write_text(SOURCE, encoding="utf-8")
            extract_region_file(source, output, "aluminum")
            self.assertTrue(output.exists())
            self.assertEqual("f 1 2 3", next(line for line in output.read_text(encoding="utf-8").splitlines() if line.startswith("f ")))

    def test_rejects_missing_empty_and_mixed_groups(self) -> None:
        with self.assertRaisesRegex(ObjRegionExtractionError, "no faces"):
            extract_region_text("v 0 0 0\ng mrf\n", "mrf")
        with self.assertRaisesRegex(ObjRegionExtractionError, "mixed OBJ groups"):
            extract_region_text("v 0 0 0\nv 1 0 0\nv 0 1 0\ng mrf aluminum\nf 1 2 3\n", "mrf")
        with self.assertRaisesRegex(ObjRegionExtractionError, "region must"):
            extract_region_text(SOURCE, "wall")


if __name__ == "__main__":
    unittest.main()
