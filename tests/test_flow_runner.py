from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.flow_runner import parse_flow_log


class FlowRunnerTests(unittest.TestCase):
    def test_parses_final_residuals_from_explicitly_converged_run(self) -> None:
        result = parse_flow_log(
            """
Solving for p, Initial residual = 0.1, Final residual = 9e-06, No Iterations 2
Solving for Ux, Initial residual = 0.1, Final residual = 8e-06, No Iterations 1
Solving for Uy, Initial residual = 0.1, Final residual = 7e-06, No Iterations 1
Solving for Uz, Initial residual = 0.1, Final residual = 6e-06, No Iterations 1
PIMPLE: converged in 3 iterations
End
"""
        )

        self.assertTrue(result.converged)
        self.assertTrue(result.solver_reported_convergence)
        self.assertEqual({"p": 9e-06, "Ux": 8e-06, "Uy": 7e-06, "Uz": 6e-06}, result.final_residuals)

    def test_end_at_cap_is_not_convergence_even_with_all_final_residuals(self) -> None:
        result = parse_flow_log(
            """
Time = 200
Solving for p, Initial residual = 0.1, Final residual = 9e-06, No Iterations 2
Solving for Ux, Initial residual = 0.1, Final residual = 8e-06, No Iterations 1
Solving for Uy, Initial residual = 0.1, Final residual = 7e-06, No Iterations 1
Solving for Uz, Initial residual = 0.1, Final residual = 6e-06, No Iterations 1
End
"""
        )

        self.assertFalse(result.converged)
        self.assertFalse(result.solver_reported_convergence)
        self.assertTrue(result.reached_end)

    def test_stable_patch_flux_history_converges_without_solver_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            for time, rate in enumerate((1.000, 1.004, 0.996, 1.003, 0.999, 1.002), start=1):
                time_dir = case / str(time)
                time_dir.mkdir()
                time_dir.joinpath("phi").write_text(
                    "boundaryField\n{\n"
                    f"patch_inlet\n{{\nvalue nonuniform List<scalar> 1\n(\n{-rate}\n)\n;\n}}\n"
                    f"patch_outlet\n{{\nvalue nonuniform List<scalar> 1\n(\n{rate * 1.0005}\n)\n;\n}}\n}}\n",
                    encoding="utf-8",
                )
            result = parse_flow_log("End\n", case)

        self.assertTrue(result.converged)
        self.assertTrue(result.integral_converged)
        self.assertAlmostEqual(1.002 * 1.00025, result.flow_rate)
        self.assertLess(result.flow_rate_variation, 0.015)
        self.assertLess(result.mass_imbalance, 0.001)
        self.assertEqual(6, result.sample_count)

    def test_high_final_residual_is_not_converged_despite_solver_marker(self) -> None:
        result = parse_flow_log(
            "Solving for p, Initial residual = 0.1, Final residual = 2e-05, No Iterations 2\n"
            "Solving for Ux, Initial residual = 0.1, Final residual = 8e-06, No Iterations 1\n"
            "Solving for Uy, Initial residual = 0.1, Final residual = 7e-06, No Iterations 1\n"
            "Solving for Uz, Initial residual = 0.1, Final residual = 6e-06, No Iterations 1\n"
            "PIMPLE: converged in 3 iterations\n"
        )

        self.assertTrue(result.solver_reported_convergence)
        self.assertFalse(result.converged)


if __name__ == "__main__":
    unittest.main()
