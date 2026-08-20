from __future__ import annotations

import unittest

from tools.flow_runner import parse_flow_log


class FlowRunnerTests(unittest.TestCase):
    def test_parses_final_residuals_from_explicitly_converged_run(self) -> None:
        result = parse_flow_log(
            """
Solving for p, Initial residual = 0.1, Final residual = 9e-06, No Iterations 2
Solving for Ux, Initial residual = 0.1, Final residual = 1e-06, No Iterations 1
Solving for U, Initial residual = 0.1, Final residual = 8e-06, No Iterations 1
Solving for k, Initial residual = 0.1, Final residual = 7e-06, No Iterations 1
Solving for omega, Initial residual = 0.1, Final residual = 6e-06, No Iterations 1
PIMPLE: converged in 3 iterations
End
"""
        )

        self.assertTrue(result.converged)
        self.assertTrue(result.solver_reported_convergence)
        self.assertEqual({"p": 9e-06, "U": 8e-06, "k": 7e-06, "omega": 6e-06}, result.final_residuals)

    def test_end_at_cap_is_not_convergence_even_with_all_final_residuals(self) -> None:
        result = parse_flow_log(
            """
Time = 200
Solving for p, Initial residual = 0.1, Final residual = 9e-06, No Iterations 2
Solving for U, Initial residual = 0.1, Final residual = 8e-06, No Iterations 1
Solving for k, Initial residual = 0.1, Final residual = 7e-06, No Iterations 1
Solving for omega, Initial residual = 0.1, Final residual = 6e-06, No Iterations 1
End
"""
        )

        self.assertFalse(result.converged)
        self.assertFalse(result.solver_reported_convergence)
        self.assertTrue(result.reached_end)

    def test_high_final_residual_is_not_converged_despite_solver_marker(self) -> None:
        result = parse_flow_log(
            "Solving for p, Initial residual = 0.1, Final residual = 2e-05, No Iterations 2\n"
            "Solving for U, Initial residual = 0.1, Final residual = 8e-06, No Iterations 1\n"
            "Solving for k, Initial residual = 0.1, Final residual = 7e-06, No Iterations 1\n"
            "Solving for omega, Initial residual = 0.1, Final residual = 6e-06, No Iterations 1\n"
            "PIMPLE: converged in 3 iterations\n"
        )

        self.assertTrue(result.solver_reported_convergence)
        self.assertFalse(result.converged)


if __name__ == "__main__":
    unittest.main()
