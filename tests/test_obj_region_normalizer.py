from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import cast
import unittest
from unittest.mock import patch

from tools import obj_region_normalizer


FIXTURE = Path(__file__).parent / "fixtures" / "obj_regions" / "source.obj"
EXPECTED_COUNTS = {
    "patch_inlet": 1,
    "patch_heatsource": 1,
    "patch_outlet": 2,
    "patch_blade": 0,
    "wall": 5,
    "mrf": 1,
    "aluminum": 1,
}


class ObjRegionNormalizerTests(unittest.TestCase):
    def test_normalizes_fixture_and_emits_exact_manifest(self) -> None:
        source_text = FIXTURE.read_text(encoding="utf-8")
        result = obj_region_normalizer.normalize_obj_text(source_text)

        self.assertEqual(EXPECTED_COUNTS, result.manifest["regionFaceCounts"])
        self.assertEqual(
            [{"rgb": "12,34,56", "faceCount": 2, "mappedRole": "wall"}],
            result.manifest["unknownColors"],
        )
        self.assertEqual(
            ["unknown master RGB 12,34,56 mapped to wall (2 faces)"],
            result.manifest["warnings"],
        )
        self.assertEqual(1, result.manifest["schemaVersion"])
        self.assertEqual("constant/triSurface/master.obj", result.manifest["sourceObj"])
        self.assertEqual(
            "constant/triSurface/hs5_cfd.openfoam.obj",
            result.manifest["normalizedObj"],
        )
        self.assertEqual(
            [line for line in source_text.splitlines(keepends=True) if line.startswith("f ")],
            [line for line in result.obj_text.splitlines(keepends=True) if line.startswith("f ")],
        )
        self.assertNotIn("mtllib", result.obj_text)
        self.assertNotIn("usemtl", result.obj_text)
        group_names = [line.split()[1] for line in result.obj_text.splitlines() if line.startswith("g ")]
        self.assertTrue(group_names)
        self.assertTrue(set(group_names).issubset(EXPECTED_COUNTS))
        self.assertGreaterEqual(group_names.count("patch_outlet"), 2)
        self.assertGreaterEqual(group_names.count("wall"), 3)

    def test_invalid_sources_leave_no_outputs(self) -> None:
        invalid_sources = (
            "f 1 2 3\n",
            "g bad_group\nf 1 2 3\n",
            "g master extra\nf 1 2 3\n",
            "g master\nf 1 2 3\n",
            "g master\nusemtl 0,92,256\nf 1 2 3\n",
            "g master\nusemtl 0,92,255\nf 1 2 3\ng master_1\nf 1 2 3\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, source_text in enumerate(invalid_sources):
                source = root / f"invalid-{index}.obj"
                output = root / f"output-{index}.obj"
                manifest = root / f"manifest-{index}.json"
                source.write_text(source_text, encoding="utf-8")
                with self.subTest(source_text=source_text):
                    with self.assertRaises(obj_region_normalizer.NormalizationError):
                        obj_region_normalizer.normalize_obj_file(source, output, manifest)
                    self.assertFalse(output.exists())
                    self.assertFalse(manifest.exists())

    def test_second_replacement_failure_restores_both_originals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.obj"
            output = root / "normalized.obj"
            manifest = root / "manifest.json"
            source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            output.write_bytes(b"old normalized bytes\r\n")
            manifest.write_bytes(b"old manifest bytes\x00")
            original_replace = obj_region_normalizer.os.replace
            failed = False

            def fail_final_replacement(source_path: str | Path, destination_path: str | Path) -> None:
                nonlocal failed
                if Path(destination_path) == manifest and not failed:
                    failed = True
                    raise OSError("injected final replacement failure")
                original_replace(source_path, destination_path)

            with patch.object(obj_region_normalizer.os, "replace", fail_final_replacement):
                with self.assertRaises(OSError):
                    obj_region_normalizer.normalize_obj_file(source, output, manifest)

            self.assertEqual(b"old normalized bytes\r\n", output.read_bytes())
            self.assertEqual(b"old manifest bytes\x00", manifest.read_bytes())
            self.assertFalse(list(root.glob(".obj-region-*")))

    def test_file_output_writes_manifest_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "normalized.obj"
            manifest = root / "obj_region_manifest.json"
            obj_region_normalizer.normalize_obj_file(FIXTURE, output, manifest)
            self.assertEqual(EXPECTED_COUNTS, json.loads(manifest.read_text(encoding="utf-8"))["regionFaceCounts"])
            self.assertIn("g aluminum\n", output.read_text(encoding="utf-8"))

    def test_crlf_file_preserves_face_bytes_and_uses_crlf_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.obj"
            output = root / "normalized.obj"
            manifest = root / "obj_region_manifest.json"
            source_bytes = FIXTURE.read_bytes().replace(b"\n", b"\r\n")
            source.write_bytes(source_bytes)

            obj_region_normalizer.normalize_obj_file(source, output, manifest)
            output_bytes = output.read_bytes()

            self.assertEqual(_face_records(source_bytes), _face_records(output_bytes))
            self.assertIn(b"g patch_inlet\r\n", output_bytes)
            self.assertTrue(
                all(line.endswith(b"\r\n") for line in output_bytes.splitlines(keepends=True) if line.startswith(b"g "))
            )

    def test_normalizes_master_only_profile_without_helper_regions(self) -> None:
        result = obj_region_normalizer.normalize_obj_text(
            "v 0 0 0\ng master\nusemtl 0,92,255\nf 1 2 3\n"
        )

        counts = cast(dict[str, int], result.manifest["regionFaceCounts"])
        self.assertEqual(1, counts["patch_inlet"])
        self.assertEqual(0, counts["mrf"])
        self.assertEqual(0, counts["aluminum"])
        self.assertNotIn("mrf", result.obj_text)
        self.assertNotIn("aluminum", result.obj_text)

    def test_maps_exact_magenta_master_material_to_blade_patch(self) -> None:
        result = obj_region_normalizer.normalize_obj_text(
            "v 0 0 0\ng master\nusemtl 255,0,255\nf 1 2 3\n"
        )

        counts = cast(dict[str, int], result.manifest["regionFaceCounts"])
        self.assertEqual(1, counts["patch_blade"])
        self.assertIn("g patch_blade\n", result.obj_text)
        self.assertEqual([], result.manifest["unknownColors"])
        self.assertEqual([], result.manifest["warnings"])


def _face_records(data: bytes) -> list[bytes]:
    return [line for line in data.splitlines(keepends=True) if line.startswith(b"f ")]


if __name__ == "__main__":
    unittest.main()
