"""Static documentation assertions for the two-stage CFD workflow.

These tests verify that the operational docs in openfoam/LEARNINGS.md cover
the required topics: official path, local/remote commands, --fresh behaviour,
result JSON keys, and the rule that a capped endTime stage is non-converged.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.result_metrics import RESULT_FIELDS

LEARNINGS = (
    Path(__file__).resolve().parents[1] / "openfoam" / "LEARNINGS.md"
).read_text(encoding="utf-8")


class TwoStageDocTests(unittest.TestCase):
    """Verify required documentation wording is present in LEARNINGS.md."""

    def test_official_case_path_is_named(self) -> None:
        self.assertIn("hs5-cfd-current", LEARNINGS)

    def test_local_command_mentions_two_stage_runner(self) -> None:
        self.assertIn("python tools/run_two_stage_cfd.py --case", LEARNINGS)

    def test_remote_command_mentions_run_two_stage_sh(self) -> None:
        self.assertIn("bash run-two-stage.sh", LEARNINGS)

    def test_fresh_flag_is_documented(self) -> None:
        self.assertIn("--fresh", LEARNINGS)

    def test_fresh_does_not_touch_protected_inputs(self) -> None:
        self.assertIn("0/", LEARNINGS)
        self.assertIn("constant/fluid/", LEARNINGS)
        self.assertIn("constant/aluminum/", LEARNINGS)

    def test_mesh_flag_is_documented(self) -> None:
        self.assertIn("--mesh", LEARNINGS)

    def test_result_json_keys_are_listed(self) -> None:
        for key in RESULT_FIELDS:
            self.assertIn(key, LEARNINGS, f"result.json key {key!r} missing from docs")

    def test_capped_endtime_is_non_converged(self) -> None:
        lower = LEARNINGS.lower()
        self.assertIn("non-converged", lower)
        self.assertIn("endtime", lower)

    def test_status_success_and_non_converged_are_both_mentioned(self) -> None:
        self.assertIn('"success"', LEARNINGS)
        self.assertIn('"non_converged"', LEARNINGS)

    def test_protected_directories_are_explicit(self) -> None:
        lower = LEARNINGS.lower()
        self.assertIn("never deleted", lower)
        self.assertIn("never touches", lower)
        self.assertIn("only generated numeric time directories", lower)


if __name__ == "__main__":
    unittest.main()
