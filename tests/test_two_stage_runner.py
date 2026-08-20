from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.run_two_stage_cfd import FLOW_FIELDS, run_two_stage


ROOT = Path(__file__).resolve().parents[1]


def scalar_field(internal: str, boundaries: str = "") -> str:
    return (
        "FoamFile\n{\n    format ascii;\n}\n"
        f"internalField nonuniform List<scalar> 3\n(\n{internal}\n)\n;\n"
        f"boundaryField\n{{\n{boundaries}}}\n"
    )


def patch(name: str, values: str) -> str:
    return f"{name}\n{{\nvalue nonuniform List<scalar> 2\n(\n{values}\n)\n;\n}}\n"


class TwoStageRunnerTests(unittest.TestCase):
    def test_flow_entrypoint_restores_cht_system_files_after_flow(self) -> None:
        entrypoint = (ROOT / "openfoam" / "templates" / "Allrun.flow.tmpl").read_text(encoding="utf-8")

        self.assertIn('install -m 0644 "$CASE_DIR/system/controlDict" "$BACKUP_DIR/controlDict"', entrypoint)
        self.assertIn('install -m 0644 "$CASE_DIR/system/fluid/fvSolution" "$BACKUP_DIR/fvSolution"', entrypoint)
        self.assertIn('install -m 0644 "$BACKUP_DIR/controlDict" "$CASE_DIR/system/controlDict"', entrypoint)
        self.assertIn('install -m 0644 "$BACKUP_DIR/fvSolution" "$CASE_DIR/system/fluid/fvSolution"', entrypoint)
        self.assertIn("trap restore_cht EXIT", entrypoint)
        self.assertLess(entrypoint.index("trap restore_cht EXIT"), entrypoint.index("python3 \"$CASE_DIR/tools/flow_runner.py\" log.flow"))

    def test_help_has_no_case_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "run_two_stage_cfd.py"), "--help"],
                cwd=case,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("--case", result.stdout)
            self.assertEqual([], list(case.iterdir()))

    def populate_flow_time(self, case: Path) -> Path:
        fluid = case / "200" / "fluid"
        fluid.mkdir(parents=True)
        for field in FLOW_FIELDS:
            (fluid / field).write_text(f"flow-{field}\n", encoding="utf-8")
        return fluid

    def populate_initial_flow_fields(self, case: Path) -> None:
        fluid = case / "0" / "fluid"
        fluid.mkdir(parents=True, exist_ok=True)
        for field in FLOW_FIELDS:
            (fluid / field).write_text(f"initial-{field}\n", encoding="utf-8")

    def assert_initial_flow_fields(self, case: Path) -> None:
        for field in FLOW_FIELDS:
            self.assertEqual(f"initial-{field}\n", (case / "0" / "fluid" / field).read_text(encoding="utf-8"))

    def populate_metrics(self, case: Path) -> None:
        fluid = case / "200" / "fluid"
        aluminum = case / "200" / "aluminum"
        aluminum.mkdir()
        (fluid / "T").write_text(scalar_field("293\n310\n300"), encoding="utf-8")
        (aluminum / "T").write_text(scalar_field("293\n325\n300"), encoding="utf-8")
        (fluid / "p").write_text(
            scalar_field("1\n2\n3", patch("patch_inlet", "107\n105") + patch("patch_outlet", "100\n100")),
            encoding="utf-8",
        )
        (fluid / "phi").write_text(scalar_field("1\n2\n3", patch("patch_inlet", "-0.01\n-0.02")), encoding="utf-8")
        properties = case / "constant" / "fluid"
        properties.mkdir(parents=True, exist_ok=True)
        (properties / "thermophysicalProperties").write_text(
            "FoamFile { format ascii; }\nequationOfState { rho 1.2; }\n", encoding="utf-8"
        )

    def test_happy_path_copies_latest_flow_fields_runs_thermal_and_preserves_fresh_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            self.populate_initial_flow_fields(case)
            (case / "0" / "keep").write_text("protected", encoding="utf-8")
            (case / "constant" / "aluminum").mkdir(parents=True)
            (case / "constant" / "aluminum" / "keep").write_text("protected", encoding="utf-8")
            (case / "50").mkdir()
            commands: list[tuple[str, ...]] = []

            def fake_run(command: tuple[str, ...], cwd: Path) -> int:
                commands.append(tuple(command))
                if command == ("bash", "Allrun.flow"):
                    (cwd / "log.flow").write_text(
                        "\n".join(
                            f"Solving for {field}, Final residual = 9e-06" for field in ("p", "U", "k", "omega")
                        ) + "\nPIMPLE: converged\n",
                        encoding="utf-8",
                    )
                    self.populate_flow_time(cwd)
                else:
                    source = cwd / "200" / "fluid"
                    for field in FLOW_FIELDS:
                        self.assertEqual((source / field).read_text(encoding="utf-8"), (cwd / "0" / "fluid" / field).read_text(encoding="utf-8"))
                    (cwd / "log.thermal").write_text(
                        "Solving for h, Final residual = 9e-06\nSolving for e, Final residual = 9e-07\nsolution converged\n",
                        encoding="utf-8",
                    )
                    self.populate_metrics(cwd)
                return 0

            self.assertEqual(0, run_two_stage(case, fresh=True, run_command=fake_run))
            self.assertEqual([("bash", "Allrun.flow"), ("foamMultiRun",)], commands)
            self.assertFalse((case / "50").exists())
            self.assertEqual("protected", (case / "0" / "keep").read_text(encoding="utf-8"))
            self.assertEqual("protected", (case / "constant" / "aluminum" / "keep").read_text(encoding="utf-8"))
            self.assert_initial_flow_fields(case)
            result = json.loads((case / "result.json").read_text(encoding="utf-8"))
            self.assertEqual("success", result["status"])
            self.assertTrue(result["flowConverged"])
            self.assertTrue(result["thermalConverged"])

    def test_flow_failure_writes_result_and_never_runs_thermal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            commands: list[tuple[str, ...]] = []

            def fake_run(command: tuple[str, ...], cwd: Path) -> int:
                commands.append(tuple(command))
                (cwd / "log.flow").write_text("Solving for p, Final residual = 1e-02\nEnd\n", encoding="utf-8")
                return 1

            self.assertEqual(1, run_two_stage(case, run_command=fake_run))
            self.assertEqual([("bash", "Allrun.flow")], commands)
            result = json.loads((case / "result.json").read_text(encoding="utf-8"))
            self.assertFalse(result["flowConverged"])
            self.assertFalse(result["thermalConverged"])
            self.assertEqual("non_converged", result["status"])

    def test_thermal_process_failure_writes_non_converged_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            self.populate_initial_flow_fields(case)

            def fake_run(command: tuple[str, ...], cwd: Path) -> int:
                if command == ("bash", "Allrun.flow"):
                    (cwd / "log.flow").write_text(
                        "\n".join(
                            f"Solving for {field}, Final residual = 9e-06" for field in ("p", "U", "k", "omega")
                        ) + "\nPIMPLE: converged\n",
                        encoding="utf-8",
                    )
                    self.populate_flow_time(cwd)
                    return 0
                for field in FLOW_FIELDS:
                    self.assertEqual(f"flow-{field}\n", (cwd / "0" / "fluid" / field).read_text(encoding="utf-8"))
                return 2

            self.assertEqual(1, run_two_stage(case, run_command=fake_run))
            result = json.loads((case / "result.json").read_text(encoding="utf-8"))
            self.assertTrue(result["flowConverged"])
            self.assertFalse(result["thermalConverged"])
            self.assertEqual("non_converged", result["status"])
            self.assert_initial_flow_fields(case)


if __name__ == "__main__":
    unittest.main()
