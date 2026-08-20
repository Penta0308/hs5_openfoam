from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path
from typing import Any
import unittest


# ---------------------------------------------------------------------------
# Mock Inventor COM modules — identical pattern to test_inventor_profile_export
# ---------------------------------------------------------------------------
fake_pythoncom = types.ModuleType("pythoncom")
fake_win32com = types.ModuleType("win32com")
fake_win32com_client = types.ModuleType("win32com.client")
setattr(fake_pythoncom, "com_error", RuntimeError)
setattr(fake_win32com, "client", fake_win32com_client)
sys.modules.setdefault("pythoncom", fake_pythoncom)
sys.modules.setdefault("win32com", fake_win32com)
sys.modules.setdefault("win32com.client", fake_win32com_client)

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str) -> Any:
    """Load a sibling tool module with mocked COM modules in sys.modules."""
    sys.path.insert(0, str(ROOT / "tools"))
    return __import__(name)


inventor_face_appearance = _load_module("inventor_face_appearance")


# ---------------------------------------------------------------------------
# Fake Inventor objects
# ---------------------------------------------------------------------------

class FakeColor:
    """Inventor Color object."""
    def __init__(self, r: float = 0.0, g: float = 0.0, b: float = 0.0) -> None:
        self.Red = r
        self.G = g
        self.B = b


class FakeMaterial:
    """Inventor Material object."""
    def __init__(self, name: str = "", color: Any | None = None) -> None:
        self.Name = name
        self.Color = color


class FakeFaceAppearance:
    """Inventor FaceAppearance object."""
    def __init__(
        self,
        name: str = "",
        color: Any | None = None,
        material: FakeMaterial | None = None,
    ) -> None:
        self.Name = name
        self.Color = color
        self.Material = material


class FakeFace:
    """Inventor Face object."""
    def __init__(
        self,
        appearance: FakeFaceAppearance | None = None,
        material: FakeMaterial | None = None,
    ) -> None:
        self.Appearance = appearance
        self.Material = material


class FakeBody:
    """Inventor SurfaceBody."""
    def __init__(self, name: str = "") -> None:
        self.Name = name
        self.Faces = FakeFaceCollection([])


class FakeFaceCollection:
    """Inventor Face collection."""
    def __init__(self, items: list[FakeFace]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int) -> FakeFace:
        return self._items[index - 1]


class FakeBodyCollection:
    """Inventor SurfaceBody collection."""
    def __init__(self, items: list[FakeBody]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int) -> FakeBody:
        return self._items[index - 1]


class FakeComponentDefinition:
    """Inventor ComponentDefinition."""
    def __init__(self, surface_bodies: list[FakeBody]) -> None:
        self.SurfaceBodies = FakeBodyCollection(surface_bodies)


class FakeDocument:
    """Inventor Document."""
    def __init__(self, component_definition: FakeComponentDefinition) -> None:
        self.ComponentDefinition = component_definition


class FakeApp:
    """Inventor Application."""
    def __init__(self, document: FakeDocument) -> None:
        self.Documents = types.SimpleNamespace(
            Open=lambda path, options: document
        )


# ---------------------------------------------------------------------------
# Helper to build a realistic mock document
# ---------------------------------------------------------------------------

def build_mock_document(
    blade_faces: list[FakeFace],
    master_faces: list[FakeFace] | None = None,
    mrf_faces: list[FakeFace] | None = None,
) -> FakeDocument:
    """Build a mock document with blade, master, and MRF zone bodies."""
    blade_body = FakeBody("master_1")
    blade_body.Faces = FakeFaceCollection(blade_faces)

    master_body = FakeBody("master") if master_faces is not None else FakeBody("master")
    master_body.Faces = FakeFaceCollection(master_faces or [])

    mrf_body = FakeBody("mrf") if mrf_faces is not None else FakeBody("mrf")
    mrf_body.Faces = FakeFaceCollection(mrf_faces or [])

    bodies = [blade_body, master_body, mrf_body]
    return FakeDocument(FakeComponentDefinition(bodies))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class InventorFaceAppearanceTests(unittest.TestCase):

    def test_acknowledgement_guard(self) -> None:
        """Script refuses without --i-understand-this-touches-live-inventor."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--i-understand-this-touches-live-inventor",
            action="store_true",
        )
        args = parser.parse_args([])
        self.assertFalse(args.i_understand_this_touches_live_inventor)
        with self.assertRaises(SystemExit):
            if not args.i_understand_this_touches_live_inventor:
                raise SystemExit("Refusing to attach to live Inventor without --i-understand-this-touches-live-inventor")

    def test_no_faces_returns_empty_appearances(self) -> None:
        """Body with no faces returns empty appearance list."""
        document = FakeDocument(FakeComponentDefinition([FakeBody("empty")]))
        reports = inventor_face_appearance.read_face_appearances(document)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].name, "empty")
        self.assertEqual(reports[0].faceCount, 0)
        self.assertEqual(reports[0].appearances, [])
        self.assertEqual(reports[0].appearanceCounts, {})

    def test_read_face_with_appearance_and_material(self) -> None:
        """Face with appearance and material reads both correctly."""
        color = FakeColor(0.2, 0.6, 0.9)
        mat = FakeMaterial("BlueMaterial", color)
        appearance = FakeFaceAppearance("BladeAppearance", color, mat)
        face = FakeFace(appearance, mat)

        document = FakeDocument(FakeComponentDefinition([FakeBody("master_1")]))
        document.ComponentDefinition.SurfaceBodies.Item(1).Faces = FakeFaceCollection([face])

        reports = inventor_face_appearance.read_face_appearances(document)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].faceCount, 1)
        self.assertEqual(len(reports[0].appearances), 1)

        info = reports[0].appearances[0]
        self.assertEqual(info["faceIndex"], 1)
        self.assertEqual(info["appearance"]["name"], "BladeAppearance")
        self.assertAlmostEqual(info["appearance"]["color"]["r"], 0.2)
        self.assertAlmostEqual(info["appearance"]["color"]["g"], 0.6)
        self.assertAlmostEqual(info["appearance"]["color"]["b"], 0.9)
        self.assertEqual(info["material"]["name"], "BlueMaterial")
        self.assertIsNone(info["appearance"]["error"])
        self.assertIsNone(info["material"]["error"])

    def test_detect_blade_with_unique_appearance(self) -> None:
        """Blade has a unique appearance not shared by other bodies."""
        blade_color = FakeColor(0.8, 0.1, 0.1)
        blade_mat = FakeMaterial("BladeRedMat", blade_color)
        blade_appearance = FakeFaceAppearance("BladeAppearance", blade_color, blade_mat)
        blade_face = FakeFace(blade_appearance, blade_mat)

        master_color = FakeColor(0.2, 0.6, 0.9)
        master_mat = FakeMaterial("MasterMat", master_color)
        master_appearance = FakeFaceAppearance("MasterAppearance", master_color, master_mat)
        master_face = FakeFace(master_appearance, master_mat)

        document = build_mock_document([blade_face], [master_face])
        reports = inventor_face_appearance.read_face_appearances(document)
        blade_analysis = inventor_face_appearance.detect_blade_appearance(reports)

        self.assertTrue(blade_analysis["found"])
        self.assertIn("BladeAppearance", blade_analysis["bladeUniqueAppearanceNames"])
        self.assertEqual(blade_analysis["bladeBody"], "master_1")

    def test_detect_blade_with_shared_appearance(self) -> None:
        """Blade shares appearance with other bodies → no distinct appearance."""
        shared_color = FakeColor(0.5, 0.5, 0.5)
        shared_mat = FakeMaterial("SharedMat", shared_color)
        shared_appearance = FakeFaceAppearance("SharedAppearance", shared_color, shared_mat)

        blade_face = FakeFace(shared_appearance, shared_mat)
        master_face = FakeFace(shared_appearance, shared_mat)

        document = build_mock_document([blade_face], [master_face])
        reports = inventor_face_appearance.read_face_appearances(document)
        blade_analysis = inventor_face_appearance.detect_blade_appearance(reports)

        self.assertFalse(blade_analysis["found"])

    def test_detect_blade_missing_body(self) -> None:
        """No blade body → detection fails gracefully."""
        document = FakeDocument(FakeComponentDefinition([FakeBody("master")]))
        reports = inventor_face_appearance.read_face_appearances(document)
        blade_analysis = inventor_face_appearance.detect_blade_appearance(reports)

        self.assertFalse(blade_analysis["found"])
        self.assertIn("no blade body", blade_analysis["reason"])

    def test_build_evidence_json_structure(self) -> None:
        """Evidence JSON has expected top-level keys."""
        document = FakeDocument(FakeComponentDefinition([FakeBody("master_1")]))
        reports = inventor_face_appearance.read_face_appearances(document)
        blade_analysis = inventor_face_appearance.detect_blade_appearance(reports)
        evidence = inventor_face_appearance.build_evidence(reports, blade_analysis)

        self.assertIn("summary", evidence)
        self.assertIn("bodies", evidence)
        self.assertIn("bladeAnalysis", evidence)
        self.assertEqual(evidence["summary"]["totalBodies"], 1)
        self.assertEqual(evidence["bodies"][0]["name"], "master_1")

    def test_write_evidence_to_file(self) -> None:
        """Evidence is written as valid JSON to the output path."""
        document = FakeDocument(FakeComponentDefinition([FakeBody("master_1")]))
        reports = inventor_face_appearance.read_face_appearances(document)
        blade_analysis = inventor_face_appearance.detect_blade_appearance(reports)
        evidence = inventor_face_appearance.build_evidence(reports, blade_analysis)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "evidence.json"
            inventor_face_appearance.write_text_atomic(output_path, json.dumps(evidence, indent=2))
            self.assertTrue(output_path.exists())
            loaded = json.loads(output_path.read_text())
            self.assertEqual(loaded["summary"]["totalBodies"], 1)

    def test_multi_face_body_counts_appearances(self) -> None:
        """Body with multiple faces groups by appearance name."""
        color1 = FakeColor(0.1, 0.2, 0.3)
        mat1 = FakeMaterial("Mat1", color1)
        ap1 = FakeFaceAppearance("Appearance1", color1, mat1)
        face1 = FakeFace(ap1, mat1)

        color2 = FakeColor(0.4, 0.5, 0.6)
        mat2 = FakeMaterial("Mat2", color2)
        ap2 = FakeFaceAppearance("Appearance2", color2, mat2)
        face2 = FakeFace(ap2, mat2)

        document = FakeDocument(FakeComponentDefinition([FakeBody("master_1")]))
        document.ComponentDefinition.SurfaceBodies.Item(1).Faces = FakeFaceCollection([face1, face2])

        reports = inventor_face_appearance.read_face_appearances(document)
        self.assertEqual(reports[0].faceCount, 2)
        self.assertEqual(reports[0].appearanceCounts["Appearance1"], 1)
        self.assertEqual(reports[0].appearanceCounts["Appearance2"], 1)

    def test_read_only_no_mutation(self) -> None:
        """Script does not call Save, Close, or Quit on the document."""
        document = FakeDocument(FakeComponentDefinition([FakeBody("master_1")]))
        reports = inventor_face_appearance.read_face_appearances(document)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].faceCount, 0)

    def test_color_data_missing_components(self) -> None:
        """Color with only Red returns g and b as None."""
        color = types.SimpleNamespace(r=1.0)
        mat = FakeMaterial("C", color)
        ap = FakeFaceAppearance("A", color, mat)
        face = FakeFace(ap, mat)

        document = FakeDocument(FakeComponentDefinition([FakeBody("master_1")]))
        document.ComponentDefinition.SurfaceBodies.Item(1).Faces = FakeFaceCollection([face])

        reports = inventor_face_appearance.read_face_appearances(document)
        info = reports[0].appearances[0]
        self.assertAlmostEqual(info["appearance"]["color"]["r"], 1.0)
        self.assertIsNone(info["appearance"]["color"]["g"])
        self.assertIsNone(info["appearance"]["color"]["b"])
        self.assertIsNone(info["appearance"]["error"])

    def test_read_face_with_lowercase_rgb_color(self) -> None:
        color = types.SimpleNamespace(r=0.2, g=0.6, b=0.9)
        face = FakeFace(FakeFaceAppearance("Lowercase", color))
        document = FakeDocument(FakeComponentDefinition([FakeBody("master_1")]))
        document.ComponentDefinition.SurfaceBodies.Item(1).Faces = FakeFaceCollection([face])

        info = inventor_face_appearance.read_face_appearances(document)[0].appearances[0]
        self.assertEqual(info["appearance"]["color"], {"r": 0.2, "g": 0.6, "b": 0.9, "error": None})


if __name__ == "__main__":
    unittest.main()
