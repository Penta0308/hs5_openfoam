from __future__ import annotations

import unittest
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from tools import verify_location_in_mesh
from tools.verify_location_in_mesh import LocationValidationError, parse_point, validate_location_file, validate_location_text


def cube(group: str, low: float, high: float, start: int) -> tuple[list[str], list[str]]:
    vertices = [(low, low, low), (high, low, low), (high, high, low), (low, high, low), (low, low, high), (high, low, high), (high, high, high), (low, high, high)]
    vertex_lines = [f"v {x} {y} {z}" for x, y, z in vertices]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return vertex_lines, [f"g {group}", *("f " + " ".join(str(start + index) for index in face) for face in faces)]


outer_vertices, outer_faces = cube("wall", 0, 100, 1)
solid_vertices, solid_faces = cube("aluminum", 40, 60, 9)
helper_vertices, helper_faces = cube("master_1", 40, 60, 1)
OBJ = "\n".join([*outer_vertices, *solid_vertices, *outer_faces, *solid_faces]) + "\n"
MASTER_ONLY_OBJ = "\n".join([*outer_vertices, *outer_faces]) + "\n"
ALUMINUM_HELPER_OBJ = "\n".join([*helper_vertices, *helper_faces]) + "\n"


class VerifyLocationInMeshTests(unittest.TestCase):
    def test_accepts_air_point_even_when_mrf_membership_is_present(self) -> None:
        validate_location_text(OBJ, (0.02, 0.02, 0.02))

    def test_accepts_blade_patch_as_a_physical_boundary(self) -> None:
        validate_location_text(MASTER_ONLY_OBJ.replace("g wall", "g patch_blade"), (0.02, 0.02, 0.02))

    def test_rejects_outside_and_aluminum_points(self) -> None:
        with self.assertRaisesRegex(LocationValidationError, "outside"):
            validate_location_text(OBJ, (0.12, 0.02, 0.02))
        with self.assertRaisesRegex(LocationValidationError, "aluminum"):
            validate_location_text(OBJ, (0.05, 0.05, 0.05))

    def test_master_only_boundary_rejects_point_inside_standalone_aluminum_helper(self) -> None:
        with self.assertRaisesRegex(LocationValidationError, "excluded closed surface"):
            validate_location_text(
                MASTER_ONLY_OBJ,
                (0.05, 0.05, 0.05),
                excluded_closed_surface_texts=[ALUMINUM_HELPER_OBJ],
            )

    def test_master_only_boundary_accepts_air_point_outside_standalone_aluminum_helper(self) -> None:
        validate_location_text(
            MASTER_ONLY_OBJ,
            (0.02, 0.02, 0.02),
            excluded_closed_surface_texts=[ALUMINUM_HELPER_OBJ],
        )

    def test_file_validation_supports_multiple_explicit_helper_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.obj"
            helper = root / "aluminum-zone.obj"
            master.write_text(MASTER_ONLY_OBJ, encoding="utf-8")
            helper.write_text(ALUMINUM_HELPER_OBJ, encoding="utf-8")

            with self.assertRaisesRegex(LocationValidationError, "excluded closed surface"):
                validate_location_file(master, (0.05, 0.05, 0.05), excluded_closed_surfaces=[helper])

    def test_cli_forwards_explicit_closed_surface_exclusions(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "verify_location_in_mesh.py",
                "--obj",
                "master.obj",
                "--exclude-closed-surface",
                "aluminum-zone.obj",
                "--point",
                "(0.02 0.02 0.02)",
            ],
        ), patch.object(verify_location_in_mesh, "validate_location_file") as validate:
            verify_location_in_mesh.main()

        self.assertEqual(Path("master.obj"), validate.call_args.args[0])
        self.assertEqual((0.02, 0.02, 0.02), validate.call_args.args[1])
        self.assertEqual([Path("aluminum-zone.obj")], validate.call_args.kwargs["excluded_closed_surfaces"])

    def test_parses_rendered_openfoam_meter_point(self) -> None:
        self.assertEqual((0.036, 0.02, 0.0), parse_point("(0.036 0.02 0)"))
        with self.assertRaisesRegex(LocationValidationError, "OpenFOAM syntax"):
            parse_point("0.036 0.02 0")


if __name__ == "__main__":
    unittest.main()
