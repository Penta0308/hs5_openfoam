from __future__ import annotations

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.verify_mesh_contract import (
    MeshContractError,
    parse_cell_zones_text,
    validate_post_split,
    validate_post_split_files,
    validate_pre_split,
    validate_check_mesh_text,
    validate_surface_check_text,
)


BOUNDARY = """FoamFile { version 2.0; }\n2\n(\npatch_inlet\n{ type patch; nFaces 4; startFace 0; }\nwall\n{ type wall; nFaces 6; startFace 4; }\n)\n"""
CELL_ZONES = """FoamFile { version 2.0; }\n2\n(\nmrf\n{ type cellZone; cellLabels List<label> 2 ( 1 2 ); }\naluminum\n{ type cellZone; cellLabels List<label> 2 ( 3 4 ); }\n)\n"""
ALUMINUM_BOUNDARY = """FoamFile { version 2.0; }\n2\n(\npatch_heatsource\n{ type wall; nFaces 4; startFace 0; }\nfluid_to_aluminum\n{ type mappedWall; nFaces 6; startFace 4; }\n)\n"""


class VerifyMeshContractTests(unittest.TestCase):
    def test_check_mesh_allows_concavity_only_failure(self) -> None:
        text = "***Concave cells found\nFailed 1 mesh checks.\n"
        validate_check_mesh_text(text)

    def test_check_mesh_rejects_concavity_plus_other_failure(self) -> None:
        text = "Non-orthogonal faces: 10\nConcave cells found\nFailed 2 mesh checks.\n"
        with self.assertRaisesRegex(MeshContractError, "failed 2 mesh checks"):
            validate_check_mesh_text(text)

    def test_check_mesh_rejects_non_concavity_failure(self) -> None:
        text = "Non-orthogonal faces: 10\nFailed 1 mesh checks.\n"
        with self.assertRaisesRegex(MeshContractError, "failed 1 mesh checks"):
            validate_check_mesh_text(text)

    def test_check_mesh_emits_warning_for_concavity_only_failure(self) -> None:
        import contextlib
        text = "***Concave cells found\nFailed 1 mesh checks.\n"
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            validate_check_mesh_text(text)
        self.assertIn("concavity-only", captured.getvalue())

    def test_check_mesh_passes_when_ok(self) -> None:
        validate_check_mesh_text("Mesh OK.\nEnd\n")

    def test_pre_split_reads_disjoint_nonempty_ascii_zone_labels(self) -> None:
        validate_pre_split(BOUNDARY, CELL_ZONES)
        self.assertEqual({1, 2}, parse_cell_zones_text(CELL_ZONES)["mrf"])

    def test_pre_split_rejects_empty_or_intersecting_zones(self) -> None:
        with self.assertRaisesRegex(MeshContractError, "must not be empty"):
            validate_pre_split(BOUNDARY, CELL_ZONES.replace("2 ( 1 2 )", "0 ( )"))
        with self.assertRaisesRegex(MeshContractError, "overlap"):
            validate_pre_split(BOUNDARY, CELL_ZONES.replace("3 4", "2 3"))

    def test_cell_zone_parser_rejects_malformed_label_lists(self) -> None:
        malformed = CELL_ZONES.replace("2 ( 1 2 )", "3 ( 1 bad )")
        with self.assertRaisesRegex(MeshContractError, "non-integer|malformed"):
            parse_cell_zones_text(malformed)

    def test_surface_check_rejects_open_non_manifold_and_invalid_results(self) -> None:
        valid = "Surface is closed.\nNumber of open edges : 0\nNumber of non-manifold edges : 0\nNumber of invalid facets : 0\n"
        validate_surface_check_text(valid, "mrf-zone")
        for problem in ("Number of open edges : 1", "Number of non-manifold edges : 2", "Number of invalid facets : 3"):
            with self.assertRaises(MeshContractError):
                validate_surface_check_text("Surface is closed.\n" + problem, "mrf-zone")

    def test_surface_check_requires_closed_topology_confirmation(self) -> None:
        with self.assertRaisesRegex(MeshContractError, "closed"):
            validate_surface_check_text("Surface is not closed.\nNumber of open edges : 0", "aluminum-zone")

    def test_surface_check_accepts_no_illegal_triangles(self) -> None:
        validate_surface_check_text(
            "Surface has no illegal triangles.\nSurface is closed. All edges connected to two faces.\n",
            "mrf-zone",
        )

    def test_post_split_requires_aluminum_heat_source_and_nonempty_fluid_mrf(self) -> None:
        validate_post_split(BOUNDARY, CELL_ZONES, ALUMINUM_BOUNDARY)
        with self.assertRaisesRegex(MeshContractError, "owned by aluminum"):
            validate_post_split(BOUNDARY.replace("wall", "patch_heatsource"), CELL_ZONES, ALUMINUM_BOUNDARY)
        with self.assertRaisesRegex(MeshContractError, "missing required patch_heatsource"):
            validate_post_split(BOUNDARY, CELL_ZONES, ALUMINUM_BOUNDARY.replace("patch_heatsource", "wall"))
        with self.assertRaisesRegex(MeshContractError, "interface must be separate"):
            validate_post_split(
                BOUNDARY,
                CELL_ZONES,
                """FoamFile { version 2.0; }\n1\n(\npatch_heatsource\n{ type wall; nFaces 4; startFace 0; }\n)\n""",
            )
        with self.assertRaisesRegex(MeshContractError, "missing required mrf"):
            validate_post_split(BOUNDARY, CELL_ZONES.replace("mrf", "rotor"), ALUMINUM_BOUNDARY)
        with self.assertRaisesRegex(MeshContractError, "must not be empty"):
            validate_post_split(BOUNDARY, CELL_ZONES.replace("2 ( 1 2 )", "0 ( )"), ALUMINUM_BOUNDARY)

    def test_post_split_files_require_exactly_fluid_and_aluminum_mesh_regions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "constant"
            for region in ("fluid", "aluminum"):
                (root / region / "polyMesh").mkdir(parents=True)
            (root / "fluid" / "polyMesh" / "boundary").write_text(BOUNDARY, encoding="utf-8")
            (root / "fluid" / "polyMesh" / "cellZones").write_text(CELL_ZONES, encoding="utf-8")
            (root / "aluminum" / "polyMesh" / "boundary").write_text(ALUMINUM_BOUNDARY, encoding="utf-8")
            validate_post_split_files(
                root / "fluid" / "polyMesh" / "boundary",
                root / "fluid" / "polyMesh" / "cellZones",
                root / "aluminum" / "polyMesh" / "boundary",
            )

            (root / "mrf" / "polyMesh").mkdir(parents=True)
            with self.assertRaisesRegex(MeshContractError, "exactly aluminum and fluid.*mrf"):
                validate_post_split_files(
                    root / "fluid" / "polyMesh" / "boundary",
                    root / "fluid" / "polyMesh" / "cellZones",
                    root / "aluminum" / "polyMesh" / "boundary",
                )

    def test_post_split_files_reject_missing_aluminum_mesh(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "constant"
            (root / "fluid" / "polyMesh").mkdir(parents=True)
            (root / "fluid" / "polyMesh" / "boundary").write_text(BOUNDARY, encoding="utf-8")
            (root / "fluid" / "polyMesh" / "cellZones").write_text(CELL_ZONES, encoding="utf-8")
            missing_aluminum_boundary = root / "aluminum" / "polyMesh" / "boundary"
            with self.assertRaisesRegex(MeshContractError, "aluminum: missing mesh boundary"):
                validate_post_split_files(
                    root / "fluid" / "polyMesh" / "boundary",
                    root / "fluid" / "polyMesh" / "cellZones",
                    missing_aluminum_boundary,
                )


if __name__ == "__main__":
    unittest.main()
