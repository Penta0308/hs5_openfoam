from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from tools import obj_region_normalizer


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
FIXTURE = ROOT / "tests" / "fixtures" / "obj_regions" / "source.obj"


def load_subject():
    pythoncom = types.ModuleType("pythoncom")
    setattr(pythoncom, "CoInitialize", Mock())
    setattr(pythoncom, "CoUninitialize", Mock())
    inventor_client = types.ModuleType("inventor_client")
    setattr(inventor_client, "InventorPaths", Mock())
    setattr(inventor_client, "collect_state", Mock())
    setattr(inventor_client, "connect_inventor", Mock())
    setattr(inventor_client, "export_obj_profiles", Mock())
    setattr(inventor_client, "open_part", Mock())
    setattr(inventor_client, "write_text_atomic", Mock())
    template_render = types.ModuleType("template_render")
    setattr(template_render, "render_text", Mock())
    spec = importlib.util.spec_from_file_location("prepare_openfoam_case_test", TOOLS / "prepare_openfoam_case.py")
    assert spec is not None and spec.loader is not None
    subject = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "pythoncom": pythoncom,
            "inventor_client": inventor_client,
            "obj_region_normalizer": obj_region_normalizer,
            "template_render": template_render,
        },
    ):
        spec.loader.exec_module(subject)
    return subject, pythoncom


class PrepareOpenFoamCaseTests(unittest.TestCase):
    def test_prepare_case_rejects_unconfirmed_access_before_com_initialization(self) -> None:
        subject, pythoncom = load_subject()

        with self.assertRaisesRegex(RuntimeError, "inventor_access_confirmed=True"):
            subject.prepare_case(Path("hs5_cfd.ipt"), Path("unused"), visible=False, inventor_access_confirmed=False)

        pythoncom.CoInitialize.assert_not_called()
        pythoncom.CoUninitialize.assert_not_called()

    def test_prepare_case_exports_normalizes_then_renders_with_artifact_context(self) -> None:
        subject, _ = load_subject()
        calls: list[str] = []
        state = {
            "cfd": {
                "parameters": {
                    "OfanX": {"expression": "36 mm"},
                    "OfanY": {"expression": "20 mm"},
                    "OfanZ": {"expression": "4 mm"},
                }
            },
            "cfdNamingContract": {},
        }

        def export_profiles(_app, _document, destination: Path) -> tuple[Path, Path, Path]:
            calls.append("export")
            destination.mkdir(parents=True, exist_ok=True)
            master, mrf, aluminum = destination / "master.obj", destination / "mrf.obj", destination / "master_1.obj"
            master.write_text("g master\nusemtl 0,92,255\nf 1 2 3\n", encoding="utf-8")
            mrf.write_bytes(b"mrf profile bytes\n")
            aluminum.write_bytes(b"aluminum profile bytes\n")
            return master, mrf, aluminum

        def normalize_raw(*args, **kwargs):
            calls.append("normalize")
            return obj_region_normalizer.normalize_obj_file(*args, **kwargs)

        def render(_output: Path, context: dict[str, object]) -> None:
            calls.append("render")
            self.assertEqual(context["MASTER_PROFILE_OBJ"], "constant/triSurface/master.obj")
            self.assertEqual(context["MRF_ZONE_OBJ"], "constant/geometry/mrf-zone.obj")
            self.assertEqual(context["NORMALIZED_CFD_OBJ"], "constant/triSurface/hs5_cfd.openfoam.obj")
            self.assertEqual(context["OBJ_REGION_MANIFEST"], "obj_region_manifest.json")
            self.assertEqual(context["LOCATION_IN_MESH_M"], "(0.036 0.02 -0.001)")
            self.assertEqual(context["FAN_ORIGIN_M"], "(0.036 0.02 0.004)")

        with self.enterContext(patch.object(subject, "connect_inventor", return_value=object())), \
             self.enterContext(patch.object(subject, "collect_state", return_value=state)), \
             self.enterContext(patch.object(subject, "open_part", return_value=object())), \
              self.enterContext(patch.object(subject, "export_obj_profiles", side_effect=export_profiles)), \
             self.enterContext(patch.object(subject, "normalize_obj_file", side_effect=normalize_raw)), \
             self.enterContext(patch.object(subject, "render_templates", side_effect=render)), \
             self.enterContext(patch.object(subject, "copy_readme")), \
             self.enterContext(patch.object(subject, "write_text_atomic")):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "case"
                subject.prepare_case(Path("hs5_cfd.ipt"), output, visible=False, inventor_access_confirmed=True)
                self.assertTrue((output / "constant" / "triSurface" / "master.obj").exists())
                self.assertTrue((output / "constant" / "triSurface" / "mrf.obj").exists())
                self.assertTrue((output / "constant" / "triSurface" / "master_1.obj").exists())
                self.assertTrue((output / "constant" / "triSurface" / "hs5_cfd.openfoam.obj").exists())
                self.assertTrue((output / "obj_region_manifest.json").exists())

        self.assertEqual(calls, ["export", "normalize", "render"])

    def test_render_context_derives_location_from_saved_parameters(self) -> None:
        subject, _ = load_subject()
        state = {
            "cfd": {"parameters": {"OfanX": {"expression": "36.000 mm"}, "OfanY": {"expression": "20.000 mm"}, "OfanZ": {"expression": "4.000 mm"}}},
            "cfdNamingContract": {},
        }

        context = subject.render_context(Path("historical.ipt"), Path("case"), state, "static")

        self.assertEqual(context["LOCATION_IN_MESH_M"], "(0.036 0.02 -0.001)")
        self.assertEqual(context["FAN_ORIGIN_M"], "(0.036 0.02 0.004)")

    def test_render_context_rejects_nonliteral_location_without_inventor_access(self) -> None:
        subject, _ = load_subject()
        state = {
            "cfd": {"parameters": {"OfanX": {"expression": "d4"}, "OfanY": {"expression": "20 mm"}}},
            "cfdNamingContract": {},
        }

        with self.assertRaisesRegex(ValueError, "millimetre literal"):
            subject.render_context(Path("historical.ipt"), Path("case"), state, "static")

    def test_normalizer_failure_prevents_rendering(self) -> None:
        subject, _ = load_subject()
        calls: list[str] = []
        state = {"cfd": {"parameters": {}}, "cfdNamingContract": {}}

        def export_profiles(_app, _document, destination: Path) -> tuple[Path, Path, Path]:
            calls.append("export")
            destination.mkdir(parents=True, exist_ok=True)
            master = destination / "master.obj"
            master.write_text("g master\nf 1 2 3\n", encoding="utf-8")
            return master, destination / "mrf.obj", destination / "master_1.obj"

        def fail_normalization(*_args, **_kwargs) -> None:
            calls.append("normalize")
            raise obj_region_normalizer.NormalizationError("bad exported OBJ")

        with self.enterContext(patch.object(subject, "connect_inventor", return_value=object())), \
             self.enterContext(patch.object(subject, "collect_state", return_value=state)), \
             self.enterContext(patch.object(subject, "open_part", return_value=object())), \
              self.enterContext(patch.object(subject, "export_obj_profiles", side_effect=export_profiles)), \
             self.enterContext(patch.object(subject, "normalize_obj_file", side_effect=fail_normalization)), \
             self.enterContext(patch.object(subject, "render_templates")) as render, \
             self.enterContext(patch.object(subject, "copy_readme")) as copy_readme, \
             self.enterContext(patch.object(subject, "write_text_atomic")) as write_state:
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(obj_region_normalizer.NormalizationError, "bad exported OBJ"):
                    subject.prepare_case(Path("hs5_cfd.ipt"), Path(directory) / "case", visible=False, inventor_access_confirmed=True)

        self.assertEqual(calls, ["export", "normalize"])
        render.assert_not_called()
        copy_readme.assert_not_called()
        write_state.assert_not_called()

    def test_prepare_case_rejects_non_repository_cfd_before_com_initialization(self) -> None:
        subject, pythoncom = load_subject()

        with self.assertRaisesRegex(RuntimeError, "only repository CFD file"):
            subject.prepare_case(Path("other.ipt"), Path("unused"), visible=False, inventor_access_confirmed=True)

        pythoncom.CoInitialize.assert_not_called()

    def test_main_forwards_cli_acknowledgement_to_prepare_case(self) -> None:
        subject, _ = load_subject()
        expected_state = {"cfd": {"bodies": [], "parameters": {}, "namedFaces": []}}

        with patch.object(subject, "reset_output_dir") as reset, \
             patch.object(subject, "prepare_case", return_value=expected_state) as prepare, \
             patch.object(sys, "argv", ["prepare_openfoam_case.py", "--i-understand-this-touches-live-inventor"]):
            subject.main()

        reset.assert_called_once()
        self.assertTrue(prepare.call_args.kwargs["inventor_access_confirmed"])


if __name__ == "__main__":
    unittest.main()
